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
