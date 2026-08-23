# agent/global_benchmarks.py
"""
⚡ NornPulse: Global YouTube grounding (global_benchmarks.py)
Norn Labs (nornlabs.ai)

Materialises facts from ClickHouse's public YouTube dataset (4.56 billion
rows, reachable via remoteSecure with the anonymous `demo` user) into
compact local tables, so the pipeline and the dashboard can ground their
decisions in real global data instead of hand-written seed rows.

Why materialise rather than query live:

* The playground enforces a hard 120s server-side execution cap. Anything
  scanning a wide String column across 4.5B rows exceeds it every time.
* A live dependency on a public endpoint is a demo that breaks when that
  endpoint is busy. Materialised facts keep working.
* These are slow-moving structural facts. Recomputing them per page load
  would be absurd; per release is plenty.

Honest scope: the crawl behind this dataset ran 2021-11-27 to 2021-12-13,
so view counts are frozen at late 2021 and it predates mature Shorts
behaviour. It is good evidence for *structural* questions — does
captioning correlate with reach, how does reach scale with channel size,
which upload days travel furthest — and it is NOT evidence about today's
Shorts algorithm. There is also no duration column, so Shorts cannot be
isolated from long-form; nothing here should be described as a Shorts
benchmark.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

import agent.clickhouse_mcp_client as ch

logger = logging.getLogger("nornpulse.global")

REMOTE = "remoteSecure('sql-clickhouse.clickhouse.com:9440','youtube.youtube','demo','')"
TABLE = "global_youtube_benchmarks"

# 1/N sampling via a hash of the id. The playground's 120s cap is the
# binding constraint, not accuracy: even 1/500 leaves ~9M videos, far more
# than enough to separate these effects.
DEFAULT_DIVISOR = 2000

# Shared quality floor. Sub-1000-view videos are dominated by uploads that
# were never distributed at all, and they drown out the signal.
_BASE_FILTER = "view_count > 1000 AND uploader_sub_count > 0"


# Channel size is the dominant confounder in this dataset. Captioned
# videos, age-restricted videos and verified uploaders all skew heavily
# toward large established channels, so a marginal aggregate reports the
# channel's audience rather than the effect being measured — subtitles
# look like +15% median views while simultaneously showing *lower*
# views-per-subscriber. Any fact meant as evidence for a production
# decision has to be read within a size band, not across all of YouTube.
SIZE_BAND_EXPR = """multiIf(uploader_sub_count < 100, '0-100',
           uploader_sub_count < 1000, '100-1k',
           uploader_sub_count < 10000, '1k-10k',
           uploader_sub_count < 100000, '10k-100k',
           uploader_sub_count < 1000000, '100k-1M', '1M+')"""


@dataclass
class Fact:
    """One materialisable dimension: a name, and the SQL expression that buckets it."""
    dimension: str
    bucket_expr: str
    extra_filter: str = ""
    divisor: int = DEFAULT_DIVISOR
    note: str = ""
    # When set, the fact is computed within each channel-size band and the
    # band is stored alongside the bucket, so the confounder is controlled
    # rather than averaged over.
    stratify_by_size: bool = False


FACTS: List[Fact] = [
    Fact(
        "has_subtitles", "toString(has_subtitles)", stratify_by_size=True,
        note="Does shipping captions correlate with reach, within a channel-size band? "
             "Grounds Skuld's burned-in subtitles.",
    ),
    Fact(
        "channel_size_band", SIZE_BAND_EXPR,
        note="What reach is realistic for a channel of a given size. Grounds the forecast.",
    ),
    Fact(
        # Stratified for the same reason has_subtitles is. Unbanded, the two
        # available metrics disagree outright: weekends look best on
        # views-per-subscriber and *worst* on median views, because weekend
        # uploads skew toward small hobbyist channels. Comparing days within
        # a size band is the only way to get one coherent answer.
        "upload_weekday", "toString(toDayOfWeek(upload_date))",
        extra_filter="AND upload_date > '2019-01-01'", stratify_by_size=True,
        note="Which upload day travels furthest, within a channel-size band. "
             "Grounds publish scheduling.",
    ),
    Fact(
        "comments_enabled", "toString(is_comments_enabled)", stratify_by_size=True,
        note="Whether leaving comments open correlates with reach.",
    ),
    Fact(
        "uploader_badge", "toString(uploader_badges)", divisor=5000,
        note="Verified / Official Artist status versus reach.",
    ),
]


def _fact_query(fact: Fact) -> str:
    size_band = SIZE_BAND_EXPR if fact.stratify_by_size else "''"
    group_by = "bucket, size_band" if fact.stratify_by_size else "bucket"
    return f"""
    SELECT
        {ch.sql_literal(fact.dimension)} AS dimension,
        {fact.bucket_expr} AS bucket,
        {size_band} AS size_band,
        count() AS sample_videos,
        round(median(view_count), 2) AS median_views,
        round(quantile(0.10)(view_count), 2) AS p10_views,
        round(quantile(0.90)(view_count), 2) AS p90_views,
        round(median(view_count / greatest(uploader_sub_count, 1)), 4) AS median_views_per_sub,
        round(avg(like_count / greatest(view_count, 1)) * 100, 4) AS like_rate_pct,
        {fact.divisor} AS sample_divisor
    FROM {REMOTE}
    WHERE cityHash64(id) % {fact.divisor} = 0 AND {_BASE_FILTER} {fact.extra_filter}
    GROUP BY {group_by}
    HAVING sample_videos > 100
    ORDER BY {group_by}
    """


def ensure_table() -> None:
    ch.run_query(f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        dimension LowCardinality(String),
        bucket LowCardinality(String),
        size_band LowCardinality(String) DEFAULT '',
        sample_videos UInt64,
        median_views Float64,
        p10_views Float64 DEFAULT 0,
        p90_views Float64 DEFAULT 0,
        median_views_per_sub Float64,
        like_rate_pct Float64,
        sample_divisor UInt32,
        note String DEFAULT '',
        computed_at DateTime DEFAULT now()
    ) ENGINE = MergeTree()
    ORDER BY (dimension, bucket, size_band, computed_at);
    """)
    # An earlier materialisation created this table without size_band.
    # DROP is disabled by policy, so widen it in place.
    ch.run_query(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
                 f"size_band LowCardinality(String) DEFAULT ''")
    for column in ("p10_views", "p90_views"):
        ch.run_query(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {column} Float64 DEFAULT 0")


def materialise_fact(fact: Fact) -> Optional[pd.DataFrame]:
    """
    Run one fact's aggregation against the remote dataset and store it.
    Returns the rows written, or None if the query could not complete —
    a fact that times out is skipped, never allowed to abort the run.
    """
    logger.info(f"Materialising '{fact.dimension}' (1/{fact.divisor} sample)...")
    try:
        df = ch.run_query_df(_fact_query(fact))
    except Exception as e:
        logger.warning(f"Fact '{fact.dimension}' did not complete: {ch._unwrap_exception(e)[:200]}")
        return None

    if df.empty:
        logger.warning(f"Fact '{fact.dimension}' returned no buckets.")
        return None

    values = ", ".join(
        "(" + ", ".join([
            ch.sql_literal(fact.dimension),
            ch.sql_literal(str(r["bucket"])),
            ch.sql_literal(str(r.get("size_band", "") or "")),
            ch.sql_literal(int(r["sample_videos"])),
            ch.sql_literal(float(r["median_views"])),
            ch.sql_literal(float(r.get("p10_views", 0) or 0)),
            ch.sql_literal(float(r.get("p90_views", 0) or 0)),
            ch.sql_literal(float(r["median_views_per_sub"])),
            ch.sql_literal(float(r["like_rate_pct"])),
            ch.sql_literal(int(fact.divisor)),
            ch.sql_literal(fact.note),
        ]) + ")"
        for _, r in df.iterrows()
    )
    ch.run_query(
        f"INSERT INTO {TABLE} (dimension, bucket, size_band, sample_videos, median_views, "
        f"p10_views, p90_views, median_views_per_sub, like_rate_pct, sample_divisor, note) "
        f"VALUES {values}"
    )
    return df


def materialise_all(facts: Optional[List[Fact]] = None) -> Dict[str, Optional[pd.DataFrame]]:
    ensure_table()
    return {f.dimension: materialise_fact(f) for f in (facts or FACTS)}


def load_facts(dimension: Optional[str] = None) -> pd.DataFrame:
    """
    Read the most recent materialisation of each bucket. Rows are appended
    rather than updated (MergeTree has no cheap in-place UPDATE), so the
    latest computed_at per (dimension, bucket) is the live value.
    """
    where = f"WHERE dimension = {ch.sql_literal(dimension)}" if dimension else ""
    try:
        return ch.run_query_df(f"""
            SELECT * FROM (
                SELECT * FROM {TABLE} {where}
                ORDER BY dimension, bucket, size_band, computed_at DESC
                LIMIT 1 BY dimension, bucket, size_band
            ) ORDER BY dimension, size_band, bucket
        """)
    except Exception as e:
        logger.warning(f"Could not read global benchmarks: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def _select(facts: Optional[pd.DataFrame], dimension: str) -> pd.DataFrame:
    """
    Pick one dimension out of a preloaded frame, or fetch it if the caller
    didn't preload. Preloading matters: each ClickHouse call spawns its own
    mcp-clickhouse subprocess, so four accessors meant four subprocesses to
    read a table of a few dozen rows.
    """
    if facts is None:
        return load_facts(dimension)
    if facts.empty or "dimension" not in facts.columns:
        return pd.DataFrame()
    return facts[facts["dimension"] == dimension]


def size_band_for(subscriber_count: int) -> str:
    """Which materialised band a channel of this size falls into."""
    for limit, name in ((100, "0-100"), (1000, "100-1k"), (10_000, "1k-10k"),
                        (100_000, "10k-100k"), (1_000_000, "100k-1M")):
        if subscriber_count < limit:
            return name
    return "1M+"


def subtitle_lift(size_band: str = "0-100",
                  facts: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    """
    Evidence for burning in captions, read *within* one channel-size band.

    Reading this across all of YouTube is actively misleading: captioned
    videos skew hard toward large established channels, so the marginal
    comparison reports the channel's existing audience rather than any
    effect of captioning. Measured that way subtitles appear to lift
    median views ~15% while simultaneously showing five times *lower*
    views-per-subscriber — a textbook Simpson's paradox. Comparing like
    for like inside a band is the only honest read, and the band that
    matters for a new channel is the smallest one.

    Returns None when the band hasn't been materialised, so callers omit
    the claim rather than invent one.
    """
    df = _select(facts, "has_subtitles")
    if df.empty or "size_band" not in df.columns:
        return None
    df = df[df["size_band"] == size_band]
    if set(df["bucket"]) != {"true", "false"}:
        return None

    on = df[df["bucket"] == "true"].iloc[0]
    off = df[df["bucket"] == "false"].iloc[0]
    if not off["median_views"]:
        return None
    return {
        "size_band": size_band,
        "views_lift_pct": (on["median_views"] / off["median_views"] - 1) * 100,
        "like_lift_pct": ((on["like_rate_pct"] / off["like_rate_pct"] - 1) * 100
                          if off["like_rate_pct"] else None),
        "median_views_with": float(on["median_views"]),
        "median_views_without": float(off["median_views"]),
        "sample_videos": int(on["sample_videos"] + off["sample_videos"]),
    }


def expected_reach(subscriber_count: int,
                   facts: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    """
    What reach is realistic for a channel this size, from the global data.

    This is the number that makes a virality score mean something for a
    channel with no audience: an abstract 0-100 has no referent, whereas
    "the median video from a 0-100 subscriber channel gets N views" does.
    """
    band = size_band_for(subscriber_count)
    df = _select(facts, "channel_size_band")
    if df.empty:
        return None
    row = df[df["bucket"] == band]
    if row.empty:
        return None
    row = row.iloc[0]
    return {
        "size_band": band,
        "median_views": float(row["median_views"]),
        "median_views_per_sub": float(row["median_views_per_sub"]),
        "like_rate_pct": float(row["like_rate_pct"]),
        "sample_videos": int(row["sample_videos"]),
    }


WEEKDAY_NAMES = {"1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday",
                 "5": "Friday", "6": "Saturday", "7": "Sunday"}


def weekday_facts(size_band: str, facts: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Upload-day rows for one channel-size band."""
    df = _select(facts, "upload_weekday")
    if df.empty or "size_band" not in df.columns:
        return pd.DataFrame()
    return df[df["size_band"] == size_band]


def best_upload_days(top_n: int = 2, size_band: str = "0-100",
                     facts: Optional[pd.DataFrame] = None) -> Optional[List[Dict[str, Any]]]:
    """
    The best upload days *for a channel of this size*, ranked by median
    views.

    Ranked by median views rather than views-per-subscriber deliberately:
    within a band the subscriber counts are already comparable, so raw
    reach is the meaningful quantity, and it is the same unit the forecast
    is expressed in. Ranking one thing by views-per-sub and forecasting in
    views is how the two ended up contradicting each other.
    """
    df = weekday_facts(size_band, facts)
    if df.empty:
        return None
    df = df.sort_values("median_views", ascending=False).head(top_n)
    return [{"day": WEEKDAY_NAMES.get(str(r["bucket"]), str(r["bucket"])),
             "median_views": float(r["median_views"]),
             "views_per_sub": float(r["median_views_per_sub"]),
             "sample_videos": int(r["sample_videos"])} for _, r in df.iterrows()]


def forecast_reach(
    subscriber_count: int,
    has_subtitles: bool = True,
    upload_day: Optional[str] = None,
    facts: Optional[pd.DataFrame] = None,
    age_days: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    What reach is plausible for this clip, from the global data.

    Returns a p50/p90 range for a channel of this size, adjusted by the
    factors we actually measured, plus the derivation so a reader can see
    where each number came from.

    Read this as "comparable videos got this much", not "this clip will
    get this much". Every input is correlational: the size band is a
    population median, the subtitle factor is measured within that band
    (so channel size is controlled), and the weekday factor is not banded
    at all — it is a whole-population ratio applied on top, which is the
    weakest link in the chain and is labelled as such in `components`.
    Multiplying two correlational ratios does not make a causal model, and
    nothing here accounts for the actual content of the clip.

    Returns None when the size band hasn't been materialised, so callers
    show nothing rather than a fabricated range.
    """
    facts = load_facts() if facts is None else facts
    band = size_band_for(subscriber_count)

    base = _select(facts, "channel_size_band")
    base = base[base["bucket"] == band] if not base.empty else base
    if base.empty:
        return None
    row = base.iloc[0]

    p50 = float(row["median_views"])
    p90 = float(row.get("p90_views", 0) or 0)
    p10 = float(row.get("p10_views", 0) or 0)
    if not p50:
        return None

    components: List[Dict[str, Any]] = [{
        "factor": "Channel size",
        "detail": f"{band} subscribers",
        "multiplier": 1.0,
        "basis": f"{int(row['sample_videos']):,} videos",
        "banded": True,
    }]
    multiplier = 1.0

    if has_subtitles:
        lift = subtitle_lift(band, facts=facts)
        if lift and lift["median_views_without"]:
            factor = lift["median_views_with"] / lift["median_views_without"]
            multiplier *= factor
            components.append({
                "factor": "Kinetic subtitles",
                "detail": f"{(factor - 1) * 100:+.1f}% median views in this band",
                "multiplier": factor,
                "basis": f"{lift['sample_videos']:,} videos",
                "banded": True,
            })

    if upload_day:
        weekday = weekday_facts(band, facts)
        if not weekday.empty:
            inverse = {v: k for k, v in WEEKDAY_NAMES.items()}
            key = inverse.get(upload_day, upload_day)
            day_row = weekday[weekday["bucket"].astype(str) == str(key)]
            overall = float(weekday["median_views"].median())
            if not day_row.empty and overall:
                factor = float(day_row.iloc[0]["median_views"]) / overall
                multiplier *= factor
                components.append({
                    "factor": f"Publishing on {upload_day}",
                    "detail": f"{(factor - 1) * 100:+.1f}% vs an average day in this band",
                    "multiplier": factor,
                    "basis": f"{int(day_row.iloc[0]['sample_videos']):,} videos",
                    "banded": True,
                })

    result = {
        "size_band": band,
        "p10": p10 * multiplier,
        "p50": p50 * multiplier,
        "p90": p90 * multiplier,
        "base_p50": p50,
        "multiplier": multiplier,
        "components": components,
        "sample_videos": int(row["sample_videos"]),
    }

    # Every figure above is a lifetime median — the dataset observed each
    # video once, at whatever age it happened to be. A clip published this
    # morning has not had the time those videos had, so comparing the two
    # is a category error rather than a forecast miss.
    if age_days is not None:
        maturity = maturity_fraction(age_days, band, facts)
        if maturity:
            result["age_days"] = age_days
            result["maturity"] = maturity
            result["expected_by_now_p50"] = result["p50"] * maturity["fraction"]
            result["too_early_to_judge"] = maturity["extrapolated"]
    return result


# ---------------------------------------------------------------------------
# Hook patterns — the one fact that needs the title column
# ---------------------------------------------------------------------------
# Grounding Verðandi's hook taxonomy in real titles rather than the seeded
# video_hook_retention rows requires reading `title`, which is the one
# expensive column in a 4.5-billion-row table. Every whole-table approach
# exceeds the playground's 120s cap: a hash-modulo sample still scans `id`,
# and a numeric-modulo predicate still scans `view_count` and then reads
# title for the survivors — measured at 247s.
#
# What works is the sort key. The remote table is ordered by uploader, so a
# range predicate on uploader prunes on the primary index. Sampling several
# lettered windows and unioning them gives a spread across the uploader
# space at about three seconds per window, instead of one biased block from
# whichever channels happen to sort first.
#
# Language has to be filtered too. detectLanguage is disabled on the
# playground (error 344), and without a filter the sample is dominated by
# non-English titles that an English regex cannot classify — which is
# exactly how an earlier attempt produced zero contrarian_claim rows across
# 2.9M videos and quietly put 94% of everything in "plain".

HOOK_TABLE = TABLE
UPLOADER_WINDOWS = ["0", "A", "C", "E", "G", "I", "K", "M",
                    "O", "Q", "S", "U", "W", "Y", "z"]
WINDOW_LIMIT = 220_000

# ASCII-only plus at least one English function word. Crude, but it is a
# filter rather than a classifier: it only has to keep the sample in the
# language the patterns below are written for.
_ENGLISH = (
    "length(title) = lengthUTF8(title) AND "
    "match(lower(title), '(^| )(the|a|an|of|to|in|is|you|your|how|why|what|"
    "this|that|with|and|for|my|it|on|are)( |$)')"
)

# Ordered most-specific first, since multiIf takes the first match. A title
# like "Why X?" is a curiosity gap that happens to end in a question mark,
# so curiosity_gap is tested before direct_question.
_HOOK_CLASSIFIER = """multiIf(
    match(title, '^[0-9]') OR match(lower(title), '[0-9]+ ?(%|percent|million|billion|x |times)'), 'shock_stat',
    match(lower(title), '(actually|the truth|myth|wrong|never |stop |nobody|no one tells)'), 'contrarian_claim',
    match(lower(title), '(mistake|problem|struggling|failing|worst|stop making)'), 'problem_agitation',
    match(lower(title), '( vs | versus |like a |is basically|is just a)'), 'metaphor_analogy',
    match(lower(title), '(^| )(why|how|what|secret|reason)( |$)'), 'curiosity_gap',
    position(title, '?') > 0, 'direct_question',
    match(lower(title), '(^| )(i |we |my |our )'), 'story_in_medias_res',
    'plain')"""


def _window_union(columns: str, extra_filter: str = "") -> str:
    """
    Sample the remote table across lettered uploader ranges.

    The table is sorted by uploader, so a range predicate prunes on the
    primary index; a predicate on any other column (uploader_sub_count,
    a date) does not, and a full scan exceeds the 120s cap. Several
    windows unioned spread the sample instead of taking whichever
    channels sort first.
    """
    return "\n    UNION ALL\n".join(
        f"""
    SELECT {columns}
    FROM {REMOTE}
    WHERE uploader >= {ch.sql_literal(low)} AND uploader < {ch.sql_literal(high)}
      AND {_BASE_FILTER} {extra_filter}
    LIMIT {WINDOW_LIMIT}
    """
        for low, high in zip(UPLOADER_WINDOWS, UPLOADER_WINDOWS[1:])
    )


def hook_pattern_query() -> str:
    union = _window_union("title, view_count, like_count, uploader_sub_count")
    return f"""
    SELECT
        'hook_pattern' AS dimension,
        {_HOOK_CLASSIFIER} AS bucket,
        {SIZE_BAND_EXPR} AS size_band,
        count() AS sample_videos,
        round(median(view_count), 2) AS median_views,
        round(quantile(0.10)(view_count), 2) AS p10_views,
        round(quantile(0.90)(view_count), 2) AS p90_views,
        round(median(view_count / greatest(uploader_sub_count, 1)), 4) AS median_views_per_sub,
        round(avg(like_count / greatest(view_count, 1)) * 100, 4) AS like_rate_pct,
        0 AS sample_divisor
    FROM ({union})
    WHERE {_ENGLISH}
    GROUP BY bucket, size_band
    HAVING sample_videos > 200
    ORDER BY bucket, size_band
    """


def materialise_hook_patterns() -> Optional[pd.DataFrame]:
    """Materialise the hook-pattern fact. Returns the stored rows, or None."""
    ensure_table()
    logger.info(f"Materialising hook patterns over {len(UPLOADER_WINDOWS) - 1} uploader windows...")
    try:
        df = ch.run_query_df(hook_pattern_query())
    except Exception as e:
        logger.warning(f"Hook-pattern fact did not complete: {ch._unwrap_exception(e)[:200]}")
        return None
    if df.empty:
        return None

    note = ("Title-pattern hooks from English-language titles, sampled across "
            f"{len(UPLOADER_WINDOWS) - 1} uploader ranges and read within a channel-size band.")
    values = ", ".join(
        "(" + ", ".join([
            ch.sql_literal("hook_pattern"), ch.sql_literal(str(r["bucket"])),
            ch.sql_literal(str(r["size_band"])), ch.sql_literal(int(r["sample_videos"])),
            ch.sql_literal(float(r["median_views"])), ch.sql_literal(float(r["p10_views"])),
            ch.sql_literal(float(r["p90_views"])), ch.sql_literal(float(r["median_views_per_sub"])),
            ch.sql_literal(float(r["like_rate_pct"])), ch.sql_literal(0), ch.sql_literal(note),
        ]) + ")"
        for _, r in df.iterrows()
    )
    ch.run_query(
        f"INSERT INTO {TABLE} (dimension, bucket, size_band, sample_videos, median_views, "
        f"p10_views, p90_views, median_views_per_sub, like_rate_pct, sample_divisor, note) "
        f"VALUES {values}")
    return df


# Below this, a bucket's median is too noisy to headline. The thinnest
# real bucket has ~300 videos against ~160,000 for "plain", and ranking
# purely on median hands the top slot to whichever small bucket got lucky.
HOOK_MIN_SAMPLE = 1000


def hook_benchmarks(size_band: str = "0-100",
                    facts: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Hook-pattern rows for one channel-size band, best median reach first."""
    df = _select(facts, "hook_pattern")
    if df.empty or "size_band" not in df.columns:
        return pd.DataFrame()
    return df[df["size_band"] == size_band].sort_values("median_views", ascending=False)


def best_hook(size_band: str = "0-100", facts: Optional[pd.DataFrame] = None,
              min_sample: int = HOOK_MIN_SAMPLE) -> Optional[Dict[str, Any]]:
    """
    The best-performing hook pattern that is actually well-sampled, with
    its lift over an unstyled title.

    Ranking on median alone promotes thin buckets: problem_agitation tops
    the 0-100 band on 310 videos while curiosity_gap sits just behind it on
    9,100. The first is a coin flip, the second is a finding.
    """
    rows = hook_benchmarks(size_band, facts)
    if rows.empty:
        return None
    plain = rows[rows["bucket"] == "plain"]
    baseline = float(plain.iloc[0]["median_views"]) if not plain.empty else 0.0

    eligible = rows[(rows["sample_videos"] >= min_sample) & (rows["bucket"] != "plain")]
    if eligible.empty:
        return None
    best = eligible.iloc[0]
    return {
        "hook": str(best["bucket"]),
        "median_views": float(best["median_views"]),
        "sample_videos": int(best["sample_videos"]),
        "lift_pct": ((best["median_views"] / baseline - 1) * 100) if baseline else None,
        "size_band": size_band,
        "thin_buckets": int((rows["sample_videos"] < min_sample).sum()),
    }


# ---------------------------------------------------------------------------
# Age curve — what "expected reach" means before a clip has had time
# ---------------------------------------------------------------------------
# Every median in this module is a lifetime figure: the dataset observed
# each video once, at whatever age it happened to be. Comparing a clip
# published this morning against a lifetime median is not a forecast miss,
# it is a category error — three of ours sat at 1-3 views against a 2,455
# forecast and the cross-validation chart read as catastrophic failure.
#
# The dataset can answer this directly, because it carries both upload_date
# and fetch_date: the gap is the video's age when its view count was taken.
# Grouping by that gap gives an observed growth curve, and the ratio of a
# young bucket's median to the mature bucket's is the fraction of lifetime
# reach a clip of that age should have accumulated.

AGE_BUCKETS = ["0-1d", "1-3d", "3-7d", "7-14d", "14-30d", "30-90d", "90-365d", "365d+"]
MATURE_BUCKET = "365d+"

_AGE_BUCKET_EXPR = """multiIf(
    age <= 1, '0-1d', age <= 3, '1-3d', age <= 7, '3-7d', age <= 14, '7-14d',
    age <= 30, '14-30d', age <= 90, '30-90d', age <= 365, '90-365d', '365d+')"""


def age_curve_query() -> str:
    union = _window_union(
        "upload_date, fetch_date, view_count, uploader_sub_count",
        "AND upload_date > '2015-01-01'")
    return f"""
    SELECT
        'age_at_observation' AS dimension,
        {_AGE_BUCKET_EXPR} AS bucket,
        {SIZE_BAND_EXPR} AS size_band,
        count() AS sample_videos,
        round(median(view_count), 2) AS median_views,
        round(quantile(0.10)(view_count), 2) AS p10_views,
        round(quantile(0.90)(view_count), 2) AS p90_views,
        0 AS median_views_per_sub,
        0 AS like_rate_pct,
        0 AS sample_divisor
    FROM (
        SELECT dateDiff('day', upload_date, toDate(fetch_date)) AS age,
               view_count, uploader_sub_count
        FROM ({union})
    )
    WHERE age >= 0
    GROUP BY bucket, size_band
    HAVING sample_videos > 200
    ORDER BY bucket, size_band
    """


def materialise_age_curve() -> Optional[pd.DataFrame]:
    ensure_table()
    logger.info("Materialising the view-growth curve by video age...")
    try:
        df = ch.run_query_df(age_curve_query())
    except Exception as e:
        logger.warning(f"Age-curve fact did not complete: {ch._unwrap_exception(e)[:200]}")
        return None
    if df.empty:
        return None

    note = ("Median views by video age at observation, from upload_date vs "
            "fetch_date, within a channel-size band.")
    values = ", ".join(
        "(" + ", ".join([
            ch.sql_literal("age_at_observation"), ch.sql_literal(str(r["bucket"])),
            ch.sql_literal(str(r["size_band"])), ch.sql_literal(int(r["sample_videos"])),
            ch.sql_literal(float(r["median_views"])), ch.sql_literal(float(r["p10_views"])),
            ch.sql_literal(float(r["p90_views"])), ch.sql_literal(0.0),
            ch.sql_literal(0.0), ch.sql_literal(0), ch.sql_literal(note),
        ]) + ")"
        for _, r in df.iterrows()
    )
    ch.run_query(
        f"INSERT INTO {TABLE} (dimension, bucket, size_band, sample_videos, median_views, "
        f"p10_views, p90_views, median_views_per_sub, like_rate_pct, sample_divisor, note) "
        f"VALUES {values}")
    return df


def age_bucket_for(age_days: float) -> str:
    for limit, name in ((1, "0-1d"), (3, "1-3d"), (7, "3-7d"), (14, "7-14d"),
                        (30, "14-30d"), (90, "30-90d"), (365, "90-365d")):
        if age_days <= limit:
            return name
    return MATURE_BUCKET


def maturity_fraction(age_days: float, size_band: str = "0-100",
                      facts: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    """
    What share of its lifetime reach a clip this old should have by now.

    The young buckets are frequently missing: the 2021 crawl caught few
    videos in their first week, and for small channels nothing under
    7 days clears the sample threshold — which is precisely the age a
    just-published clip is. When the clip is younger than anything
    measured, this reports the youngest measured bucket and marks the
    result `extrapolated`, because the true figure is *below* that and a
    caller must not present it as a measurement. Nothing here invents a
    curve between buckets.

    Returns None when the band has no curve at all.
    """
    df = _select(facts, "age_at_observation")
    if df.empty or "size_band" not in df.columns:
        return None
    df = df[df["size_band"] == size_band]
    if df.empty:
        return None

    mature = df[df["bucket"] == MATURE_BUCKET]
    if mature.empty or not float(mature.iloc[0]["median_views"]):
        return None
    mature_views = float(mature.iloc[0]["median_views"])

    order = {b: i for i, b in enumerate(AGE_BUCKETS)}
    present = sorted((b for b in df["bucket"] if b in order), key=lambda b: order[b])
    if not present:
        return None

    wanted = age_bucket_for(age_days)
    extrapolated = wanted not in present
    bucket = wanted if not extrapolated else present[0]
    # Only extrapolate downwards. A clip older than every measured bucket
    # is simply mature.
    if extrapolated and order[wanted] > order[present[0]]:
        bucket, extrapolated = present[-1], False

    row = df[df["bucket"] == bucket].iloc[0]
    return {
        "bucket": bucket,
        "requested_bucket": wanted,
        "extrapolated": extrapolated,
        "youngest_measured_bucket": present[0],
        "fraction": min(float(row["median_views"]) / mature_views, 1.0),
        "median_views_at_age": float(row["median_views"]),
        "mature_median_views": mature_views,
        "sample_videos": int(row["sample_videos"]),
    }


def too_early_to_judge(age_days: float, size_band: str = "0-100",
                       facts: Optional[pd.DataFrame] = None) -> bool:
    """
    Whether a clip is younger than anything the growth curve measured.

    Such a clip cannot be scored against a forecast — not because it is
    underperforming, but because there is no observation to compare it
    with. Plotting it anyway is what made three hours-old clips read as
    catastrophic misses on the cross-validation chart.
    """
    maturity = maturity_fraction(age_days, size_band, facts)
    return bool(maturity and maturity["extrapolated"])
