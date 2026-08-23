"""
Static guards on the public read-only demo.

The submission needs --allow-unauthenticated, so the deployed app is
reachable by anyone. These tests read app.py as text rather than running
Streamlit: what matters is that no action which writes to the shared
warehouse, spends model credit, or touches YouTube can be reached without
passing a demo-mode gate first. A new button added later without a guard
is exactly the regression worth catching, and it would not show up in any
behavioural test that runs with demo mode off.
"""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
# Whitespace-normalised, so a call reformatted across several lines still
# matches. The guard is what matters, not how black would lay it out.
FLAT = " ".join(SOURCE.split())

# Buttons that cost money, write to ClickHouse, or publish. Keyed by the
# label as it appears in the source.
GUARDED_ACTIONS = [
    "⚡ EXECUTE PIPELINE",     # Gemini + Lyria + Imagen + TTS
    "🗂️ Run Batch",           # the same, several times over
    "🚀 Publish",              # uploads to a real YouTube channel
    "🗑️ Reject",               # writes a decision to ClickHouse
    "✅ Approve",              # writes a decision to ClickHouse
    "Delete forever",          # irreversible local deletion
    "🔄 Sync Actual Performance",   # YouTube Data API quota
]


@pytest.mark.parametrize("label", GUARDED_ACTIONS)
def test_every_costly_action_is_gated(label):
    assert f'demo_locked( "{label}"' in FLAT or f'demo_locked("{label}"' in FLAT, (
        f"{label!r} can be triggered on the public demo — wrap it in demo_locked()"
    )


def test_the_sql_console_is_removed_not_merely_disabled():
    """
    A visible-but-disabled textarea advertises a write-enabled SQL endpoint
    and invites someone to probe it. In demo mode the console must not be
    rendered at all.
    """
    console = SOURCE[SOURCE.index("SQL Query Console") - 400:]
    assert "if DEMO_MODE:" in console[:600]
    assert "execute_custom_query" in console


def test_demo_mode_defaults_to_off():
    """A developer running locally must get the full app with no setup."""
    assert 'os.getenv("NORNPULSE_DEMO_MODE", "0")' in SOURCE


def test_every_page_shows_the_banner():
    """A judge landing on any page should know why actions are stood down."""
    for page in ("page_home", "page_create", "page_review", "page_intelligence"):
        body = SOURCE[SOURCE.index(f"def {page}("):]
        body = body[:body.index("\ndef ")] if "\ndef " in body else body
        assert "demo_banner()" in body, f"{page} has no demo banner"


def test_the_deploy_script_enables_demo_mode():
    """
    The gate is worthless if the deployment does not switch it on, and this
    is the one place that decides.
    """
    script = (APP.parent / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    assert "NORNPULSE_DEMO_MODE=1" in script
    assert "--allow-unauthenticated" in script, (
        "if the service ever stops being public, revisit whether demo mode is still needed"
    )


def test_guarded_labels_match_their_real_buttons():
    """
    demo_locked renders its own button, so a typo in the label would show a
    disabled control that looks unrelated to the one it replaces.
    """
    for label in GUARDED_ACTIONS:
        escaped = re.escape(label)
        assert re.search(rf'st\.button\( ?f?"{escaped}"', FLAT), (
            f"no real st.button matches the guarded label {label!r}"
        )


def test_ingestion_is_gated_by_demo_mode():
    """
    Regression guard. The demo gate covered the Execute button and missed
    ingestion, which is a spend path of its own: pasting a link starts a
    download and a paid Gemini transcription immediately, with no button
    press. It is also impossible on Cloud Run — YouTube bot-blocks
    datacenter IPs — so the field could only ever take money and fail.
    """
    block = SOURCE[SOURCE.index("1️⃣ Source"):]
    block = block[:block.index("Batch Mode")]
    assert "if DEMO_MODE:" in block, "the source field is reachable on the public demo"
    assert "yt_url_locked" in block
    # The real input must sit on the else branch, not run unconditionally.
    assert block.index("if DEMO_MODE:") < block.index('key="yt_url"')
