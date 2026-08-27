# agent/review_queue.py
"""
⚡ NornPulse: Human review decisions (review_queue.py)
Norn Labs (nornlabs.ai)

Records the approve/reject decision a human makes on a staged clip,
along with the comment explaining it, and archives the clip's files
rather than deleting them.

Decisions live in a single JSON ledger next to the renders
(output_clips/review_decisions.json) and are mirrored best-effort into
ClickHouse. The JSON is the source of truth on purpose: the dashboard
must still show what you decided when ClickHouse is unreachable, which
it demonstrably is from time to time. The ClickHouse copy is for
analytics — correlating rejection comments against hook types and
visual treatments is how the prompts get better.

Rejection archives rather than deletes. A render costs real Gemini,
Lyria and FFmpeg time, and a rejected clip is the most useful training
signal the system produces; throwing it away on a mis-click is the one
irreversible thing in this flow.
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nornpulse.review")

OUTPUT_DIR = Path("output_clips")
LEDGER_PATH = OUTPUT_DIR / "review_decisions.json"
PUBLISHED_URLS_PATH = OUTPUT_DIR / "published_urls.json"
REJECTED_DIR = OUTPUT_DIR / "rejected"
PUBLISHED_DIR = OUTPUT_DIR / "published"

APPROVED = "approved"
REJECTED = "rejected"
VALID_STATUSES = (APPROVED, REJECTED)

# Every artifact Skuld/Heimdall/Bragi leaves behind for one clip.
_ARTIFACT_SUFFIXES = ("_9x16.mp4", "_subs.ass", "_thumb.jpg", "_thumb.png", "_metadata.json")


class ReviewError(ValueError):
    """Raised when a decision is malformed — an unknown status, or no clip id."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_ledger(path: Path = LEDGER_PATH) -> Dict[str, Dict[str, Any]]:
    """
    Read the decision ledger. A corrupt or unreadable ledger returns empty
    rather than raising: losing the audit trail must not take down the
    dashboard, and the next write will rebuild a valid file.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Review ledger at {path} unreadable ({e}); starting from empty.")
        return {}


def _save_ledger(ledger: Dict[str, Dict[str, Any]], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so an interrupted write can't truncate the ledger.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def get_decision(clip_id: str, path: Path = LEDGER_PATH) -> Optional[Dict[str, Any]]:
    return load_ledger(path).get(clip_id)


def list_decisions(status: Optional[str] = None, path: Path = LEDGER_PATH) -> List[Dict[str, Any]]:
    rows = list(load_ledger(path).values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: r.get("decided_at", ""), reverse=True)


def record_decision(
    clip_id: str,
    status: str,
    comment: str = "",
    source: str = "ui",
    extra: Optional[Dict[str, Any]] = None,
    path: Path = LEDGER_PATH,
    mirror_to_clickhouse: bool = True,
) -> Dict[str, Any]:
    """
    Record an approve/reject decision. `source` distinguishes a dashboard
    click from an email reply, which matters when reconciling a clip that
    was acted on in both places.
    """
    if not clip_id:
        raise ReviewError("A decision needs a clip_id.")
    if status not in VALID_STATUSES:
        raise ReviewError(f"Unknown review status '{status}'; expected one of {VALID_STATUSES}.")

    entry = {
        "clip_id": clip_id,
        "status": status,
        "comment": (comment or "").strip(),
        "source": source,
        "decided_at": _now(),
        **(extra or {}),
    }

    ledger = load_ledger(path)
    # Keep the prior decision rather than silently overwriting history —
    # a clip rejected by email and then approved in the UI is a real
    # sequence someone will need to explain later.
    if clip_id in ledger:
        entry["previous"] = {k: v for k, v in ledger[clip_id].items() if k != "previous"}
    ledger[clip_id] = entry
    _save_ledger(ledger, path)

    if mirror_to_clickhouse:
        _mirror_to_clickhouse(entry)
    return entry


def _mirror_to_clickhouse(entry: Dict[str, Any]) -> bool:
    """Best-effort analytics copy. Never raises — the JSON ledger already holds the decision."""
    try:
        import agent.clickhouse_mcp_client as ch

        ch.run_query("""
        CREATE TABLE IF NOT EXISTS clip_review_decisions (
            clip_id String,
            status LowCardinality(String),
            comment String,
            source LowCardinality(String),
            decided_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (decided_at, clip_id);
        """)
        ch.run_query(
            "INSERT INTO clip_review_decisions (clip_id, status, comment, source) VALUES ("
            + ", ".join([
                ch.sql_literal(entry["clip_id"]),
                ch.sql_literal(entry["status"]),
                ch.sql_literal(entry["comment"]),
                ch.sql_literal(entry["source"]),
            ]) + ")"
        )
        return True
    except Exception as e:
        logger.warning(f"Could not mirror review decision to ClickHouse (kept in the JSON ledger): {e}")
        return False


def _move_artifacts(clip_id: str, dest_dir: Path, output_dir: Path = OUTPUT_DIR) -> List[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for suffix in _ARTIFACT_SUFFIXES:
        src = output_dir / f"{clip_id}{suffix}"
        if not src.exists():
            continue
        try:
            shutil.move(str(src), str(dest_dir / src.name))
            moved.append(src.name)
        except OSError as e:
            logger.warning(f"Could not move {src.name} to {dest_dir}: {e}")
    return moved


def archive_rejected(clip_id: str, output_dir: Path = OUTPUT_DIR) -> List[str]:
    """Move a rejected clip's artifacts into output_clips/rejected/."""
    return _move_artifacts(clip_id, output_dir / REJECTED_DIR.name, output_dir)


def archive_published(clip_id: str, output_dir: Path = OUTPUT_DIR) -> List[str]:
    """
    Move a published clip's artifacts into output_clips/published/.

    The dashboard used to unlink the render immediately after a successful
    upload, so the local copy of a clip that just went live disappeared
    with no way to re-check what was actually published.
    """
    return _move_artifacts(clip_id, output_dir / PUBLISHED_DIR.name, output_dir)


def review_history(limit: int = 20, path: Path = LEDGER_PATH) -> List[Dict[str, Any]]:
    """
    Recent human decisions, newest first, for display.

    Reads the ClickHouse mirror rather than the JSON ledger, because the
    ledger lives in output_clips/ — gitignored and excluded from the
    deployed image — so on the public demo it is simply absent. That is why
    the Review page renders empty there while the pipeline's whole claim is
    that a human decides. The decisions themselves are mirrored on every
    write, so the warehouse can answer when the disk cannot.

    Falls back to the local ledger when ClickHouse is unreachable, so a
    workstation with no warehouse still shows its own history.

    One row per clip: a clip rejected and later re-decided has several rows
    and only the latest is its actual state.
    """
    rows: List[Dict[str, Any]] = []
    try:
        import agent.clickhouse_mcp_client as ch

        result = ch.run_query(f"""
            SELECT d.clip_id, d.status, d.comment, d.source, d.decided_at,
                   o.youtube_video_id, o.youtube_url, o.actual_view_count,
                   o.video_unavailable
            FROM (
                SELECT * FROM clip_review_decisions
                ORDER BY clip_id, decided_at DESC LIMIT 1 BY clip_id
            ) AS d
            LEFT JOIN (
                SELECT * FROM published_clip_outcomes
                ORDER BY clip_id, row_written_at DESC LIMIT 1 BY clip_id
            ) AS o ON d.clip_id = o.clip_id
            ORDER BY d.decided_at DESC
            LIMIT {int(limit)}
        """)
        cols = result["columns"]
        rows = [dict(zip(cols, r)) for r in result["rows"]]
    except Exception as e:
        logger.info(f"Review history unavailable from ClickHouse ({str(e)[:80]}); "
                    f"falling back to the local ledger.")
        for entry in list_decisions(path=path)[:limit]:
            rows.append({
                "clip_id": entry.get("clip_id", ""),
                "status": entry.get("status", ""),
                "comment": entry.get("comment", ""),
                "source": entry.get("source", ""),
                "decided_at": entry.get("decided_at", ""),
                "youtube_video_id": entry.get("youtube_video_id", ""),
                "youtube_url": entry.get("youtube_url", ""),
                "actual_view_count": 0,
                "video_unavailable": 0,
            })
    return rows


def published_urls(path: Path = LEDGER_PATH) -> List[Dict[str, Any]]:
    """Every clip in the ledger that reached YouTube, oldest first."""
    rows = [e for e in load_ledger(path).values()
            if e.get("status") == APPROVED and e.get("youtube_url")]
    rows.sort(key=lambda e: e.get("decided_at") or "")
    return [{
        "clip_id": e["clip_id"],
        "video_id": e.get("youtube_video_id", ""),
        "url": e["youtube_url"],
        "published_at": e.get("decided_at", ""),
        "source": e.get("source", ""),
    } for e in rows]


def write_published_urls(path: Path = LEDGER_PATH,
                         out_path: Path = PUBLISHED_URLS_PATH) -> List[Dict[str, Any]]:
    """
    Rewrite published_urls.json from the ledger.

    Derived, not accumulated. Two different runners used to append to this
    file independently and publish_staged.py truncated it to just its own
    batch, so a file named like a permanent record of everything published
    in fact held whichever subset wrote last — it listed one clip while the
    ledger held several. Regenerating it from the ledger means the two
    cannot disagree, and that a runner which forgets to call this leaves
    the file stale rather than wrong.
    """
    rows = published_urls(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows


# ---------------------------------------------------------------------------
# Discovery — one view over renders, sidecar metadata, and decisions
# ---------------------------------------------------------------------------
# Clips live in three places: output_clips/ (rendered, not yet decided),
# output_clips/rejected/ and output_clips/published/. The dashboard needs
# them as one list annotated with what was decided and why, which means
# joining three sources: the file on disk, the sidecar JSON Verðandi wrote
# beside it, and this module's ledger.

PENDING = "pending"

_LOCATIONS = {
    "staged": None,               # the output dir itself
    "rejected": REJECTED_DIR.name,
    "published": PUBLISHED_DIR.name,
}

_THUMB_SUFFIXES = ("_thumb.jpg", "_thumb.png")


def _clip_dir(location: str, output_dir: Path) -> Path:
    sub = _LOCATIONS.get(location)
    return output_dir if sub is None else output_dir / sub


def load_clip(clip_id: str, location: str = "staged",
              output_dir: Path = OUTPUT_DIR,
              ledger_path: Path = LEDGER_PATH) -> Optional[Dict[str, Any]]:
    """
    Everything known about one clip: its render, its generation metadata,
    and the human decision recorded against it.

    `state` is taken from the ledger when there is a decision, because the
    ledger is the record of intent; the directory only reflects where the
    files happened to be moved, and a failed move would otherwise silently
    rewrite history.
    """
    directory = _clip_dir(location, output_dir)
    video = directory / f"{clip_id}_9x16.mp4"
    if not video.exists():
        return None

    metadata: Dict[str, Any] = {}
    sidecar = directory / f"{clip_id}_metadata.json"
    if sidecar.exists():
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Sidecar metadata for {clip_id} unreadable ({e}); showing the render alone.")

    thumbnail = next(
        (str(directory / f"{clip_id}{suffix}") for suffix in _THUMB_SUFFIXES
         if (directory / f"{clip_id}{suffix}").exists()), None)

    decision = get_decision(clip_id, path=ledger_path)
    return {
        "clip_id": clip_id,
        "location": location,
        "state": (decision or {}).get("status") or PENDING,
        "video_path": str(video),
        "thumbnail_path": thumbnail,
        "size_mb": round(video.stat().st_size / 1048576, 1),
        "modified_at": datetime.fromtimestamp(video.stat().st_mtime, timezone.utc),
        "metadata": metadata,
        "decision": decision,
    }


def list_clips(state: Optional[str] = None, output_dir: Path = OUTPUT_DIR,
               ledger_path: Path = LEDGER_PATH) -> List[Dict[str, Any]]:
    """
    Every rendered clip across all three locations, newest first.

    Deliberately not a recursive glob over output_dir: that would also
    sweep up unrelated renders (unit-test fixtures, one-off experiments)
    living in subdirectories, and it would lose the location distinction
    the dashboard needs.
    """
    clips: List[Dict[str, Any]] = []
    seen: set = set()
    for location in _LOCATIONS:
        directory = _clip_dir(location, output_dir)
        if not directory.is_dir():
            continue
        for video in sorted(directory.glob("*_9x16.mp4")):
            clip_id = video.name[: -len("_9x16.mp4")]
            # A clip archived while a stale copy remains staged should be
            # shown once, in the place its decision put it.
            if clip_id in seen:
                continue
            clip = load_clip(clip_id, location, output_dir, ledger_path)
            if clip:
                seen.add(clip_id)
                clips.append(clip)

    clips.sort(key=lambda c: c["modified_at"], reverse=True)
    return [c for c in clips if state is None or c["state"] == state]


def state_counts(output_dir: Path = OUTPUT_DIR,
                 ledger_path: Path = LEDGER_PATH) -> Dict[str, int]:
    counts = {PENDING: 0, APPROVED: 0, REJECTED: 0}
    for clip in list_clips(output_dir=output_dir, ledger_path=ledger_path):
        counts[clip["state"]] = counts.get(clip["state"], 0) + 1
    return counts


def delete_clip(clip_id: str, location: str = "staged",
                output_dir: Path = OUTPUT_DIR) -> List[str]:
    """
    Permanently remove a clip's artifacts.

    Kept separate from rejection on purpose: rejecting archives, because a
    render costs real API spend and its comment is the useful part. This is
    the deliberate, irreversible version, and the dashboard asks twice.
    """
    directory = _clip_dir(location, output_dir)
    removed = []
    for suffix in _ARTIFACT_SUFFIXES:
        path = directory / f"{clip_id}{suffix}"
        if path.exists():
            try:
                path.unlink()
                removed.append(path.name)
            except OSError as e:
                logger.warning(f"Could not delete {path.name}: {e}")
    return removed
