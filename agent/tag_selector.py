# agent/tag_selector.py
"""
⚡ NornPulse: Grounded tag selection (tag_selector.py)
Norn Labs (nornlabs.ai)

Chooses the tags a clip ships with, and says where each one came from.

Every upload used to carry the same four hardcoded strings
(["AI", "NornPulse", "Shorts", "Tech"]), which told YouTube nothing about
the clip and made tag performance impossible to measure: with no variation
across clips there is nothing to correlate against reach.

The obvious fix — take the most frequent tags from the trending snapshot
and apply them — is worse than the problem. Tags that describe something
the video is not about are keyword stuffing, which YouTube's spam policy
prohibits and which risks the channel rather than the clip. Trending data
is not a source of tags.

So the rule here is inverted: **candidates come from the clip, trending
data only ranks and validates them.** A tag is emitted because the clip is
about it. The snapshot then tells us whether that term is actually in
circulation right now, and with what reach — which is a MEASURED claim and
is labelled as one. A relevant term the snapshot has never seen is still
emitted, but as MODEL: it describes the clip, and nothing more is claimed.

This keeps tags inside the same provenance discipline as every other
decision, and makes tag lift measurable later without ever having shipped
a tag the clip could not justify.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from agent import clickhouse_mcp_client as ch
from agent import provenance as pv

logger = logging.getLogger(__name__)

TABLE = "clip_tags"

# YouTube rejects an upload whose tags exceed 500 characters in total
# (separators counted). Stop well short: the request fails as a whole, so
# losing the video to a two-character overrun is a bad trade.
MAX_TAG_CHARS = 420
MAX_TAGS = 12

# Tag text limits. A single tag over 30 characters reads as a sentence and
# is ignored by search; under 3 it matches noise.
MIN_TAG_LEN = 3
MAX_TAG_LEN = 30

# Always shipped. "Shorts" is how YouTube routes a vertical video into the
# Shorts shelf, so it is structural rather than descriptive — a PRIOR, not
# a claim about this clip.
STRUCTURAL_TAGS = ["Shorts"]

# Function words, generic verbs and adverbs. A tag built from these
# describes every video and distinguishes none, and a list of them is what
# makes an automated tag set obvious at a glance. They also serve a second
# purpose below: they are the delimiters that carve prose into phrases.
_STOPWORDS = {
    # articles, pronouns, conjunctions, prepositions
    "a", "an", "the", "and", "or", "but", "if", "so", "as", "at", "by",
    "for", "from", "in", "into", "of", "off", "on", "onto", "out", "over",
    "to", "up", "with", "within", "without", "about", "after", "before",
    "between", "through", "under", "against", "during", "than", "then",
    "inside", "outside", "above", "below", "behind", "beyond", "around",
    "across", "along", "near", "nearby", "next", "upon", "toward",
    "towards", "onto", "amongst", "among", "besides", "beside", "via",
    "there", "here", "where", "when", "while", "that", "this", "these",
    "those", "it", "its", "it's", "he", "she", "they", "them", "their",
    "we", "us", "our", "ours", "you", "your", "yours", "i", "me", "my",
    "him", "her", "his", "who", "whom", "whose", "which", "what", "why",
    "how", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "too",
    "very", "just", "also", "even", "still", "yet", "ever", "never",
    # auxiliaries and generic verbs
    "is", "am", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "having", "do", "does", "did", "doing", "done", "will",
    "would", "shall", "should", "can", "could", "may", "might", "must",
    "get", "gets", "got", "make", "makes", "made", "making", "go",
    "goes", "going", "went", "come", "comes", "came", "take", "takes",
    "took", "know", "knows", "knew", "think", "thinks", "thought",
    "see", "sees", "saw", "look", "looks", "want", "wants", "need",
    "needs", "say", "says", "said", "tell", "tells", "told", "use",
    "uses", "used", "let", "lets", "put", "give", "gives", "keep",
    "turn", "turns", "become", "becomes", "means",
    # vague adverbs and intensifiers
    "actually", "really", "basically", "literally", "fundamentally",
    "essentially", "simply", "truly", "quite", "rather", "almost",
    "always", "often", "sometimes", "usually", "probably", "maybe",
    "perhaps", "definitely", "certainly", "completely", "totally",
    "absolutely", "entirely", "extremely", "incredibly",
    # generic nouns that carry no topic
    "thing", "things", "stuff", "way", "ways", "kind", "sort", "lot",
    "lots", "bit", "part", "parts", "time", "times", "day", "days",
    "year", "years", "people", "person", "guy", "guys", "someone",
    "something", "anything", "everything", "nothing",
    # words about the medium rather than the subject
    "video", "videos", "clip", "clips", "watch", "watching", "channel",
    "subscribe", "like", "likes", "comment", "share", "episode",
    "content", "footage",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\u2019\-]*")
# Prose is carved into phrases at punctuation as well as at stopwords: a
# phrase must not straddle a clause boundary or it stops being a term
# anyone would search for.
_SEGMENT_RE = re.compile(r"[^A-Za-z0-9'\u2019\- ]+")

# A tag of more than three words is a sentence fragment, not a search term.
MAX_PHRASE_WORDS = 3


def normalise(tag: str) -> str:
    """Lowercase, strip a leading hash and surrounding punctuation."""
    return tag.strip().lstrip("#").strip().lower()


def _is_usable(term: str) -> bool:
    """Whether a single extracted term is worth emitting at all."""
    if not (MIN_TAG_LEN <= len(term) <= MAX_TAG_LEN):
        return False
    words = term.split()
    if any(w in _STOPWORDS for w in words):
        return False
    if all(w.isdigit() for w in words):
        return False
    return True


def _phrases(text: str) -> List[str]:
    """
    Contiguous runs of content words, longest first.

    Stopwords and punctuation are boundaries, so "Are we living inside a
    white hole?" yields "living" and "white hole" rather than eight
    separate words — and "white hole" is the term someone would actually
    search for. Runs longer than MAX_PHRASE_WORDS are windowed rather than
    dropped, so a long noun pile still contributes its parts.
    """
    found: List[str] = []
    for segment in _SEGMENT_RE.split(text or ""):
        run: List[str] = []
        for word in _WORD_RE.findall(segment):
            lowered = word.lower()
            if lowered in _STOPWORDS or len(lowered) < 2:
                if run:
                    found.extend(_windows(run))
                    run = []
                continue
            run.append(lowered)
        if run:
            found.extend(_windows(run))
    return found


def _windows(run: List[str]) -> List[str]:
    """Every phrase of 1..MAX_PHRASE_WORDS words in a content run, longest first."""
    out: List[str] = []
    for size in range(min(len(run), MAX_PHRASE_WORDS), 0, -1):
        for i in range(len(run) - size + 1):
            out.append(" ".join(run[i:i + size]))
    return out


def candidate_terms(clip: Dict[str, Any], extra_text: str = "") -> List[str]:
    """
    Terms the clip is demonstrably about, in descending order of authority.

    Explicit structured metadata first, because those are commitments the
    pipeline already made. Then phrases from the title, which is the most
    concentrated statement of what the clip is about. Then phrases from the
    surrounding copy, ranked by how often they recur — a term repeated
    across the caption and transcript is more likely the actual subject
    than one mentioned once in passing.
    """
    ordered: List[str] = []
    seen = set()

    def push(value: Optional[str]) -> None:
        if not value:
            return
        term = normalise(str(value)).replace("_", " ")
        term = " ".join(term.split())
        if term and term not in seen and _is_usable(term):
            seen.add(term)
            ordered.append(term)

    push(clip.get("topic_category"))
    for explicit in clip.get("tags") or []:
        push(explicit)

    for phrase in _phrases(str(clip.get("hook_title") or "")):
        push(phrase)

    body = " ".join(str(clip.get(f) or "") for f in
                    ("social_caption", "hook_text", "description"))
    if extra_text:
        body = f"{body} {extra_text}"

    counts: Dict[str, int] = {}
    for phrase in _phrases(body):
        counts[phrase] = counts.get(phrase, 0) + 1
    for phrase, _n in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0].split()))):
        push(phrase)

    return _demote_subsumed(ordered)


def _demote_subsumed(terms: List[str]) -> List[str]:
    """
    Move single words that an emitted phrase already contains to the back.

    Extraction yields "white hole" alongside "white" and "hole", because
    every window of a content run is a candidate. Leading with all three
    spends three slots on one concept and reads as stuffing. The phrase is
    the better search term, so it goes first.

    They are demoted rather than dropped, though: "universe" and "science"
    are high-volume standalone terms, and deleting them because some phrase
    happens to contain them left clips with four tags when twelve were
    available. As filler they only appear once the phrases have had their
    pick, and a demoted word that turns out to be trending is still
    promoted back into the measured tier by select_tags.
    """
    covered = {
        word
        for term in terms
        if " " in term
        for word in term.split()
    }
    primary = [t for t in terms if " " in t or t not in covered]
    filler = [t for t in terms if " " not in t and t in covered]
    return primary + filler


def _trending_lookup(trending: Optional[pd.DataFrame]) -> Dict[str, Dict[str, float]]:
    """Index the trending snapshot by normalised tag for O(1) validation."""
    if trending is None or trending.empty or "tag" not in trending.columns:
        return {}
    index: Dict[str, Dict[str, float]] = {}
    for _, row in trending.iterrows():
        key = normalise(str(row["tag"]))
        if not key:
            continue
        videos = float(row.get("videos") or 0)
        median_views = float(row.get("median_views") or 0)
        # top_tags already folds case, but a caller may pass any frame in.
        # Merge rather than overwrite: letting a later row win silently
        # understated a tag's evidence and pushed it down the ranking.
        prior = index.get(key)
        if prior:
            prior["videos"] = max(prior["videos"], videos)
            prior["median_views"] = max(prior["median_views"], median_views)
        else:
            index[key] = {"videos": videos, "median_views": median_views}
    return index


def _fits(tags: Sequence[str], candidate: str) -> bool:
    """Whether adding candidate keeps the whole list under YouTube's cap."""
    projected = sum(len(t) for t in tags) + len(tags) + len(candidate)
    return projected <= MAX_TAG_CHARS


def select_tags(
    clip: Dict[str, Any],
    trending: Optional[pd.DataFrame] = None,
    limit: int = MAX_TAGS,
    extra_text: str = "",
) -> Tuple[List[str], List[pv.Decision]]:
    """
    Pick this clip's tags, with a provenance record for each.

    Returns (tags, decisions). Tags are returned in the casing YouTube will
    receive; decisions carry the level and the evidence behind each one.

    Ranking, when a trending snapshot is available, is by how many trending
    videos carry the term — a term in wide current circulation is a better
    bet than one that is merely relevant. Relevance is never traded away
    for reach: a term that does not describe the clip is not a candidate in
    the first place.
    """
    index = _trending_lookup(trending)
    candidates = candidate_terms(clip, extra_text=extra_text)

    # Structural tags ship unconditionally, so their slots come off the
    # budget up front rather than pushing the list past the limit.
    budget = max(1, limit - len(STRUCTURAL_TAGS))

    measured: List[Tuple[str, Dict[str, float]]] = []
    model: List[str] = []
    for term in candidates:
        hit = index.get(term)
        if hit and hit["videos"] > 0:
            measured.append((term, hit))
        else:
            model.append(term)

    # In-circulation terms first, most widely carried first.
    measured.sort(key=lambda pair: (-pair[1]["videos"], -pair[1]["median_views"]))

    tags: List[str] = []
    decisions: List[pv.Decision] = []

    for term, stats in measured:
        if len(tags) >= budget or not _fits(tags, term):
            break
        tags.append(term)
        decisions.append(pv.Decision(
            step="Tag",
            choice=term,
            level=pv.MEASURED,
            evidence=(
                f"carried by {int(stats['videos'])} currently-trending videos, "
                f"median {int(stats['median_views']):,} views"
            ),
            sample=int(stats["videos"]),
        ))

    for term in model:
        if len(tags) >= budget or not _fits(tags, term):
            break
        tags.append(term)
        decisions.append(pv.Decision(
            step="Tag",
            choice=term,
            level=pv.MODEL,
            evidence="describes this clip; not present in the current trending snapshot",
        ))

    for term in STRUCTURAL_TAGS:
        if normalise(term) in {normalise(t) for t in tags}:
            continue
        if not _fits(tags, term):
            break
        tags.append(term)
        decisions.append(pv.Decision(
            step="Tag",
            choice=term,
            level=pv.PRIOR,
            evidence="structural: how YouTube routes a vertical video onto the Shorts shelf",
        ))

    return tags, decisions


# --------------------------------------------------------------------------
# Persistence
#
# Tags live in their own table rather than as a column on
# published_clip_outcomes. That table is append-only and re-inserted on
# every stats sync, so every column on it has to be carried forward by
# hand — a pattern that has already silently dropped the forecast once and
# restamped published_at once. Tags are written exactly once, at publish,
# and never need to survive a sync, so keeping them out of that row avoids
# the whole class of bug. Lift analysis joins on youtube_video_id.
# --------------------------------------------------------------------------

def ensure_table() -> None:
    ch.run_query(f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        clip_id String,
        youtube_video_id String,
        tags Array(String),
        measured_tags Array(String),
        published_at DateTime DEFAULT now()
    ) ENGINE = MergeTree()
    ORDER BY (youtube_video_id, clip_id);
    """)


def record_clip_tags(clip_id: str, youtube_video_id: str, tags: Sequence[str],
                     decisions: Optional[Sequence[pv.Decision]] = None) -> bool:
    """Store what a clip actually shipped with, so tag lift is measurable."""
    if not tags:
        return False
    measured = [
        d.choice for d in (decisions or []) if d.level == pv.MEASURED
    ]
    try:
        ensure_table()
        array = lambda vs: "[" + ", ".join(ch.sql_literal(v) for v in vs) + "]"
        ch.run_query(
            f"INSERT INTO {TABLE} (clip_id, youtube_video_id, tags, measured_tags) VALUES ("
            + ", ".join([
                ch.sql_literal(clip_id),
                ch.sql_literal(youtube_video_id),
                array(list(tags)),
                array(measured),
            ])
            + ")"
        )
        return True
    except Exception as e:
        # A clip that published successfully must not be reported as failed
        # because its tag bookkeeping did not land.
        logger.warning(f"Could not record tags for {clip_id}: {ch._unwrap_exception(e)[:160]}")
        return False


def tag_lift(min_clips: int = 3) -> pd.DataFrame:
    """
    Median reach of our own published clips by tag.

    Deliberately thin evidence for now — with a few dozen clips this cannot
    support a conclusion, and min_clips exists so the UI can decline to show
    a tag that has only been tried once or twice.
    """
    try:
        return ch.run_query_df(f"""
            SELECT tag,
                   count() AS clips,
                   round(median(o.actual_view_count)) AS median_views
            FROM {TABLE} AS t
            ARRAY JOIN t.tags AS tag
            INNER JOIN (
                SELECT youtube_video_id, actual_view_count FROM (
                    SELECT youtube_video_id, actual_view_count
                    FROM published_clip_outcomes
                    WHERE NOT video_unavailable
                    ORDER BY youtube_video_id, row_written_at DESC
                    LIMIT 1 BY youtube_video_id
                )
            ) AS o ON o.youtube_video_id = t.youtube_video_id
            GROUP BY tag
            HAVING clips >= {int(min_clips)}
            ORDER BY median_views DESC
        """)
    except Exception as e:
        logger.warning(f"Could not read tag lift: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()
