"""
Unit tests for weaving generated footage into a cut clip.

Nothing is generated and, in most of these, ffmpeg is not run. The
properties worth guarding are the expensive ones: that a blocked opener
costs nothing, that inputs which disagree about size or audio are
reconciled before they are concatenated, and that a failed join raises
rather than leaving a truncated file where a working clip used to be.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from agent import weaver


HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _file(tmp_path, name, content=b"not really a video"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


# --- the prompt -------------------------------------------------------------

def test_the_opener_prompt_forbids_readable_text():
    """
    Generated text renders as garbled pseudo-writing, and it would sit
    directly under the burned-in hook banner.
    """
    prompt = weaver.opener_prompt("NASA's Plan For A Moon Base", "moon base")
    lowered = prompt.lower()
    for banned in ("no text", "no captions", "no titles", "no logos"):
        assert banned in lowered


def test_the_prompt_prefers_the_topic_over_the_title():
    assert "moon base" in weaver.opener_prompt("Some Title", "moon base")


def test_the_prompt_survives_a_missing_topic():
    assert "Some Title" in weaver.opener_prompt("Some Title", "")


def test_the_prompt_asks_for_one_continuous_shot():
    """Two seconds is not enough for a cut, and asking for one gets one."""
    assert "no cuts" in weaver.opener_prompt("x").lower()


# --- not spending money -----------------------------------------------------

def test_a_blocked_opener_never_reaches_the_generator(tmp_path, monkeypatch):
    """
    The rights check runs before generation for the same reason it does in
    the trend loop: a refusal found after the call has been paid for is a
    refusal found too late.
    """
    calls = {"n": 0}

    def _boom(**kw):
        calls["n"] += 1
        raise AssertionError("should not have generated")

    monkeypatch.setattr("agent.footage.generate_with_veo", _boom)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    clip = _file(tmp_path, "clip.mp4")
    with pytest.raises(weaver.WeaveError, match="Rights check blocked"):
        weaver.add_generated_opener(clip, "Peter Griffin does taxes", "c1")
    assert calls["n"] == 0


# --- joining ----------------------------------------------------------------

def test_a_missing_input_is_refused_before_ffmpeg(tmp_path):
    clip = _file(tmp_path, "clip.mp4")
    with pytest.raises(weaver.WeaveError, match="missing or empty"):
        weaver.weave_opener(clip, tmp_path / "nope.mp4", tmp_path / "out.mp4")


def test_an_empty_input_is_refused(tmp_path):
    clip = _file(tmp_path, "clip.mp4")
    empty = _file(tmp_path, "empty.mp4", b"")
    with pytest.raises(weaver.WeaveError, match="missing or empty"):
        weaver.weave_opener(clip, empty, tmp_path / "out.mp4")


@pytest.mark.parametrize("asked,expected", [
    (2.0, 2.0),
    (99.0, weaver.MAX_OPENER_SEC),
    (0.0, 0.5),
    (-3.0, 0.5),
])
def test_the_opener_length_is_clamped(asked, expected, tmp_path, monkeypatch):
    """An opener longer than the clip would bury the borrowed footage."""
    seen = {}

    def _run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"joined")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(weaver.subprocess, "run", _run)
    monkeypatch.setattr(weaver, "_has_audio", lambda p: True)

    result = weaver.weave_opener(
        _file(tmp_path, "clip.mp4"), _file(tmp_path, "op.mp4"),
        tmp_path / "out.mp4", opener_sec=asked)
    assert result.opener_sec == expected


def test_an_opener_without_audio_gets_a_silent_track(tmp_path, monkeypatch):
    """
    concat refuses a mix of inputs with and without audio, and fails as a
    filter-graph error rather than as a silent segment.
    """
    seen = {}

    def _run(cmd, **kw):
        seen["graph"] = cmd[cmd.index("-filter_complex") + 1]
        Path(cmd[-1]).write_bytes(b"joined")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(weaver.subprocess, "run", _run)
    monkeypatch.setattr(weaver, "_has_audio", lambda p: "clip" in str(p))

    weaver.weave_opener(_file(tmp_path, "clip.mp4"), _file(tmp_path, "op.mp4"),
                        tmp_path / "out.mp4")
    assert "anullsrc" in seen["graph"]
    # Crossfaded rather than butted together, so the silent track has to
    # survive acrossfade too, not just concat.
    assert "acrossfade" in seen["graph"]


def test_both_inputs_are_scaled_to_the_same_frame(tmp_path, monkeypatch):
    """
    Veo returns 720x1280 and Skuld renders 1080x1920. Concatenating streams
    that disagree about size produces a file that plays for exactly as long
    as its first segment.
    """
    seen = {}

    def _run(cmd, **kw):
        seen["graph"] = cmd[cmd.index("-filter_complex") + 1]
        Path(cmd[-1]).write_bytes(b"joined")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(weaver.subprocess, "run", _run)
    monkeypatch.setattr(weaver, "_has_audio", lambda p: True)

    weaver.weave_opener(_file(tmp_path, "clip.mp4"), _file(tmp_path, "op.mp4"),
                        tmp_path / "out.mp4")
    assert seen["graph"].count(f"scale={weaver.TARGET_W}:{weaver.TARGET_H}") == 2
    # Cropped to cover, not padded: black bars at the join read as a fault.
    assert f"crop={weaver.TARGET_W}:{weaver.TARGET_H}" in seen["graph"]


def test_a_failed_join_raises_rather_than_leaving_a_stub(tmp_path, monkeypatch):
    """
    Unlike the finishing pass, a half-joined video is not a lesser version
    of the result. Writing one would be worse than keeping the clip that
    already worked.
    """
    def _run(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, b"", b"filter graph is wrong")

    monkeypatch.setattr(weaver.subprocess, "run", _run)
    monkeypatch.setattr(weaver, "_has_audio", lambda p: True)

    with pytest.raises(weaver.WeaveError, match="could not join"):
        weaver.weave_opener(_file(tmp_path, "clip.mp4"), _file(tmp_path, "op.mp4"),
                            tmp_path / "out.mp4")


def test_a_silently_empty_output_is_caught(tmp_path, monkeypatch):
    def _run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(weaver.subprocess, "run", _run)
    monkeypatch.setattr(weaver, "_has_audio", lambda p: True)

    with pytest.raises(weaver.WeaveError, match="is empty"):
        weaver.weave_opener(_file(tmp_path, "clip.mp4"), _file(tmp_path, "op.mp4"),
                            tmp_path / "out.mp4")


# --- one real join ----------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_a_real_join_has_both_segments(tmp_path):
    """
    The mocked tests check the graph; this checks that ffmpeg accepts it.
    Deliberately mismatched on purpose: 720x1280 silent against 1080x1920
    with audio, which is exactly what Veo and Skuld produce.
    """
    opener = tmp_path / "op.mp4"
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=720x1280:rate=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    "-y", str(opener)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1080x1920:rate=30:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", "-y", str(clip)], check=True, capture_output=True)

    out = tmp_path / "woven.mp4"
    weaver.weave_opener(clip, opener, out, opener_sec=2.0)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True).stdout
    assert f"{weaver.TARGET_W},{weaver.TARGET_H}" in probe
    duration = float(probe.strip().splitlines()[-1])
    assert 7.5 < duration < 8.5, f"expected ~8s (2 + 6), got {duration}"


# --- wired into the pipeline ------------------------------------------------

def test_the_opener_is_off_unless_asked_for():
    """
    Every opener is a paid Veo call on a clip that otherwise costs only
    ffmpeg. Defaulting it on would bill six generations a day that nobody
    requested.
    """
    import inspect
    from agent.verdandi_orchestrator import VerdandiOrchestrator

    for fn in (VerdandiOrchestrator.orchestrate_generation, VerdandiOrchestrator.orchestrate_batch):
        assert inspect.signature(fn).parameters["opener_sec"].default == 0.0


def test_broll_is_off_unless_asked_for():
    """Same reasoning as the opener: a real Veo call per clip, so it
    stays opt-in rather than becoming the default six times a day."""
    import inspect
    from agent.verdandi_orchestrator import VerdandiOrchestrator

    for fn in (VerdandiOrchestrator.orchestrate_generation, VerdandiOrchestrator.orchestrate_batch):
        assert inspect.signature(fn).parameters["broll"].default is False


def test_a_failed_weave_keeps_the_rendered_clip():
    """
    weave_opener raises so a half-joined file is never written; the
    pipeline catches that so losing the opener never costs the clip.
    """
    import inspect
    from agent.verdandi_orchestrator import VerdandiOrchestrator

    source = inspect.getsource(VerdandiOrchestrator._make_tools)
    assert "keeping the clip as" in source
    # The recorded path is the variable the weave may reassign, not the
    # raw render result -- otherwise a successful weave would be discarded.
    assert '"output_video_path": rendered_path,' in source


# --- the join itself --------------------------------------------------------
#
# A hard cut from generated footage to borrowed footage announces the seam.
# A reviewer described it as cutting "unexpectedly at the second second":
# nothing in the opener prepares the eye for the change.

def _graph_for(tmp_path, monkeypatch, opener_sec):
    seen = {}

    def _run(cmd, **kw):
        seen["graph"] = cmd[cmd.index("-filter_complex") + 1]
        Path(cmd[-1]).write_bytes(b"joined")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(weaver.subprocess, "run", _run)
    monkeypatch.setattr(weaver, "_has_audio", lambda p: True)
    weaver.weave_opener(_file(tmp_path, "clip.mp4"), _file(tmp_path, "op.mp4"),
                        tmp_path / "out.mp4", opener_sec=opener_sec)
    return seen["graph"]


def test_the_two_shots_are_dissolved_not_butted(tmp_path, monkeypatch):
    graph = _graph_for(tmp_path, monkeypatch, 2.0)
    assert "xfade=transition=fade" in graph
    assert "concat=" not in graph


def test_the_dissolve_starts_before_the_opener_ends(tmp_path, monkeypatch):
    """
    xfade's offset is where the transition begins in the first input. Set
    to the full opener length the fade would start as the opener ended and
    overrun into nothing.
    """
    graph = _graph_for(tmp_path, monkeypatch, 2.0)
    expected_offset = 2.0 - weaver.CROSSFADE_SEC
    assert f"offset={expected_offset:.2f}" in graph


def test_a_very_short_opener_falls_back_to_a_hard_cut(tmp_path, monkeypatch):
    """
    The dissolve cannot be longer than the shot it dissolves out of. At the
    minimum opener length there is nothing left to fade with, and a hard
    cut is better than a malformed filter graph.
    """
    graph = _graph_for(tmp_path, monkeypatch, 0.5)
    assert "concat=n=2:v=1:a=1" in graph
    assert "xfade" not in graph


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_the_dissolve_shortens_the_result_by_its_own_length(tmp_path):
    """
    Overlapping is the point: opener + clip - fade, not opener + clip. If
    this came out as a plain sum the shots were not actually overlapping.
    """
    opener, clip = tmp_path / "op.mp4", tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=720x1280:rate=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    "-y", str(opener)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1080x1920:rate=30:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", "-y", str(clip)], check=True, capture_output=True)

    out = tmp_path / "woven.mp4"
    weaver.weave_opener(clip, opener, out, opener_sec=2.0)
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip())
    expected = 2.0 + 6.0 - weaver.CROSSFADE_SEC
    assert abs(duration - expected) < 0.6, f"expected ~{expected}s, got {duration}"


# =============================================================================
# Generated B-roll under narration
# =============================================================================

# --- the prompt --------------------------------------------------------------

def test_the_broll_prompt_forbids_readable_text():
    prompt = weaver.broll_prompt("a web of glowing quantum loops")
    lowered = prompt.lower()
    for banned in ("no text", "no captions", "no titles", "no logos"):
        assert banned in lowered


def test_the_broll_prompt_asks_for_one_continuous_shot():
    assert "no cuts" in weaver.broll_prompt("x").lower()


def test_the_broll_prompt_survives_an_empty_concept():
    assert weaver.broll_prompt("") != ""


# --- retiming cues to the clip's own start -----------------------------------

def test_cues_are_retimed_relative_to_the_clip():
    cues = [(27.0, "a"), (31.0, "b"), (35.0, "c")]
    relative = weaver.clip_relative_cues(cues, clip_start_sec=27.0, clip_end_sec=38.0)
    assert relative == [(0.0, "a"), (4.0, "b"), (8.0, "c")]


def test_cues_outside_the_clip_window_are_dropped():
    cues = [(10.0, "before"), (30.0, "inside"), (60.0, "after")]
    relative = weaver.clip_relative_cues(cues, clip_start_sec=25.0, clip_end_sec=40.0)
    assert relative == [(5.0, "inside")]


def test_blank_cue_text_is_dropped():
    cues = [(0.0, "real text"), (2.0, "   ")]
    relative = weaver.clip_relative_cues(cues, clip_start_sec=0.0, clip_end_sec=5.0)
    assert relative == [(0.0, "real text")]


# --- identify_broll_moment: failing toward "do not spend" --------------------

def _stub_genai(monkeypatch, payload, raises=None):
    class _Resp:
        text = payload if isinstance(payload, str) else __import__("json").dumps(payload)

    class _Models:
        def generate_content(self, **kw):
            if raises:
                raise raises
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")


_CUES = [(0.0, "space itself is made of discrete loops")]


def test_missing_key_returns_none_not_a_fabricated_moment(monkeypatch):
    """
    Unlike critic.py's REVISE-on-failure, there is no unsafe direction
    here: skipping a possible cutaway costs nothing but missed upside, so
    this fails toward not spending, not toward asking a human.
    """
    monkeypatch.delenv("NORNPULSE_USE_VERTEX", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert weaver.identify_broll_moment(_CUES, 0.0, 10.0) is None


def test_api_error_returns_none(monkeypatch):
    _stub_genai(monkeypatch, {}, raises=RuntimeError("network is down"))
    assert weaver.identify_broll_moment(_CUES, 0.0, 10.0) is None


def test_unreadable_response_returns_none(monkeypatch):
    _stub_genai(monkeypatch, "not json")
    assert weaver.identify_broll_moment(_CUES, 0.0, 10.0) is None


def test_no_moment_is_a_real_answer_not_an_error(monkeypatch):
    _stub_genai(monkeypatch, {"has_moment": False, "reason": "already concrete"})
    assert weaver.identify_broll_moment(_CUES, 0.0, 10.0) is None


def test_a_claimed_moment_with_no_usable_timing_is_discarded(monkeypatch):
    _stub_genai(monkeypatch, {"has_moment": True, "visual_concept": "x"})
    assert weaver.identify_broll_moment(_CUES, 0.0, 10.0) is None


def test_a_span_outside_the_clip_is_discarded(monkeypatch):
    _stub_genai(monkeypatch, {"has_moment": True, "start_sec": 8.0, "end_sec": 15.0,
                              "visual_concept": "x"})
    assert weaver.identify_broll_moment(_CUES, 0.0, 10.0) is None


def test_a_valid_moment_is_returned(monkeypatch):
    _stub_genai(monkeypatch, {"has_moment": True, "start_sec": 1.0, "end_sec": 3.0,
                              "visual_concept": "glowing quantum loops",
                              "reason": "abstract physics, no camera can show it"})
    moment = weaver.identify_broll_moment(_CUES, 0.0, 10.0)
    assert moment["start_sec"] == 1.0
    assert moment["visual_concept"] == "glowing quantum loops"


def test_span_length_is_clamped_to_the_configured_bounds(monkeypatch):
    """A model landing just outside the soft limit is a rounding
    disagreement, not a reason to throw away an otherwise-good pick."""
    _stub_genai(monkeypatch, {"has_moment": True, "start_sec": 1.0, "end_sec": 9.0,
                              "visual_concept": "x"})
    moment = weaver.identify_broll_moment(_CUES, 0.0, 10.0)
    span = moment["end_sec"] - moment["start_sec"]
    assert span <= weaver.MAX_BROLL_SEC


def test_the_cues_actually_reach_the_prompt(monkeypatch):
    seen = {}

    class _Resp:
        text = '{"has_moment": false}'

    class _Models:
        def generate_content(self, **kw):
            seen["contents"] = kw.get("contents", "")
            return _Resp()

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    weaver.identify_broll_moment(
        [(0.0, "space itself is made of discrete loops")], 0.0, 10.0)
    assert "discrete loops" in seen["contents"]


def test_empty_transcript_never_calls_the_model(monkeypatch):
    called = {"n": 0}

    class _Models:
        def generate_content(self, **kw):
            called["n"] += 1
            raise AssertionError("should not have been reached")

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    assert weaver.identify_broll_moment([], 0.0, 10.0) is None
    assert called["n"] == 0


# --- insert_broll: the splice itself -----------------------------------------

def test_missing_inputs_are_refused_before_ffmpeg(tmp_path):
    with pytest.raises(weaver.WeaveError):
        weaver.insert_broll(tmp_path / "nope.mp4", tmp_path / "nope2.mp4",
                            1.0, 2.0, tmp_path / "out.mp4")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_a_span_past_the_clip_end_is_rejected(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1080x1920:rate=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    "-y", str(clip)], check=True, capture_output=True)
    broll = _file(tmp_path, "broll.mp4")
    with pytest.raises(weaver.WeaveError, match="does not fit"):
        weaver.insert_broll(clip, broll, 2.0, 10.0, tmp_path / "out.mp4")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_a_real_splice_preserves_duration_and_audio(tmp_path):
    """
    The two guarantees that actually matter: total duration is unchanged
    (the swap is 1:1 in time, not an insertion), and the audio is
    bit-identical to the original -- proving it was stream-copied, not
    re-encoded, and never touched the B-roll's own generated audio at all.
    """
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1080x1920:rate=30:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", "-y", str(clip)], check=True, capture_output=True)
    broll = tmp_path / "broll.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=720x1280:rate=24:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", "-y", str(broll)], check=True, capture_output=True)

    out = tmp_path / "out.mp4"
    woven = weaver.insert_broll(clip, broll, 3.0, 5.0, out)
    assert woven.broll_sec == 2.0

    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip())
    assert abs(duration - 8.0) < 0.3, f"expected ~8.0s (unchanged), got {duration}"

    orig_audio = tmp_path / "orig.wav"
    new_audio = tmp_path / "new.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip),
                    "-vn", "-acodec", "pcm_s16le", str(orig_audio)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out),
                    "-vn", "-acodec", "pcm_s16le", str(new_audio)], check=True)
    assert orig_audio.read_bytes() == new_audio.read_bytes()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_a_splice_reaching_the_clip_end_has_no_after_segment(tmp_path):
    """The span ending exactly at the clip's own end is the n=2 concat
    path (no [vafter]), not a special case that should fail."""
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1080x1920:rate=30:d=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    "-y", str(clip)], check=True, capture_output=True)
    broll = tmp_path / "broll.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=720x1280:rate=24:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    "-y", str(broll)], check=True, capture_output=True)
    out = tmp_path / "out.mp4"
    weaver.insert_broll(clip, broll, 3.0, 5.0, out)
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip())
    assert abs(duration - 5.0) < 0.3


# --- add_generated_broll: the entry point ------------------------------------

def test_no_moment_returns_the_clip_unchanged_without_calling_veo(tmp_path, monkeypatch):
    clip = _file(tmp_path, "clip.mp4")
    monkeypatch.setattr(weaver, "identify_broll_moment", lambda *a, **kw: None)

    called = {"n": 0}
    import agent.footage as fg
    monkeypatch.setattr(fg, "generate_with_veo",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))

    woven = weaver.add_generated_broll(clip, "clip_1", [], 0.0, 10.0)
    assert not woven.generated
    assert woven.path == clip
    assert called["n"] == 0


def test_a_blocked_cutaway_never_reaches_the_generator(tmp_path, monkeypatch):
    clip = _file(tmp_path, "clip.mp4")
    monkeypatch.setattr(weaver, "identify_broll_moment", lambda *a, **kw: {
        "start_sec": 1.0, "end_sec": 3.0, "visual_concept": "x", "reason": "y"})

    from agent import watchdog as wd
    monkeypatch.setattr(wd, "check_text", lambda **kw: wd.Verdict(level=wd.BLOCK, reasons=["nope"]))

    called = {"n": 0}
    import agent.footage as fg
    monkeypatch.setattr(fg, "generate_with_veo",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))

    with pytest.raises(weaver.WeaveError):
        weaver.add_generated_broll(clip, "clip_1", [], 0.0, 10.0)
    assert called["n"] == 0
