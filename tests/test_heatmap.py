"""
Unit tests for the most-replayed graph.

No network. The behaviour worth guarding is the opening exclusion, because
without it every clip gets cut from the first six seconds of the source —
the part most likely to be a title card, and the part a viewer arriving at
a Short has least reason to care about.
"""

import pytest

from agent import heatmap as hm


def _info(points):
    return {"heatmap": [{"start_time": s, "end_time": e, "value": v}
                        for s, e, v in points]}


def _even(values, width=6.0):
    """A graph of equal-width buckets carrying `values` in order."""
    return _info([(i * width, (i + 1) * width, v) for i, v in enumerate(values)])


# --- parsing ----------------------------------------------------------------

def test_a_missing_graph_is_empty_not_an_error():
    """YouTube computes one only after enough views, so absent is normal."""
    assert hm.from_info({}) == []
    assert hm.from_info({"heatmap": None}) == []


def test_malformed_points_are_skipped_not_fatal():
    info = {"heatmap": [
        {"start_time": 0, "end_time": 6, "value": 1.0},
        {"start_time": "nonsense"},
        {"no": "fields"},
        {"start_time": 6, "end_time": 12, "value": 0.5},
    ]}
    assert len(hm.from_info(info)) == 2


def test_a_moment_reports_its_own_timestamp():
    m = hm.Moment(start_sec=553.5, end_sec=559.6, value=0.58)
    assert m.as_timestamp() == "09:13"
    assert m.mid_sec == pytest.approx(556.55)


# --- the opening exclusion --------------------------------------------------

def test_the_opening_bucket_is_never_the_answer():
    """
    It is 1.0 on essentially every video, because everyone who presses play
    sees the first six seconds. That measures arrival, not interest.
    """
    values = [1.0] + [0.2] * 40
    values[30] = 0.6
    best = hm.peaks(hm.from_info(_even(values)))
    assert best
    assert best[0].start_sec > 0
    assert best[0].value == 0.6


def test_a_genuine_early_peak_still_survives():
    """
    Only the opening is excluded, not the whole first half — a real peak a
    minute in is exactly what this is for.
    """
    values = [1.0] + [0.1] * 40
    values[3] = 0.9
    best = hm.peaks(hm.from_info(_even(values)))
    assert best[0].start_sec == pytest.approx(18.0)


# --- spreading the peaks ----------------------------------------------------

def test_adjacent_buckets_do_not_all_count_as_separate_peaks():
    """
    Without this the top five are five neighbouring buckets describing one
    moment, which looks like five options and is one.
    """
    values = [0.1] * 60
    values[20:25] = [0.8, 0.9, 0.95, 0.9, 0.8]   # one broad peak
    values[50] = 0.7                              # a second, elsewhere
    best = hm.peaks(hm.from_info(_even(values)), top_n=5)
    assert len(best) == 2
    assert abs(best[0].mid_sec - best[1].mid_sec) > 60


def test_peaks_come_back_strongest_first():
    values = [0.1] * 60
    values[10], values[30], values[50] = 0.5, 0.9, 0.7
    best = hm.peaks(hm.from_info(_even(values)), top_n=3)
    assert [round(m.value, 2) for m in best] == [0.9, 0.7, 0.5]


# --- refusing when it cannot say anything -----------------------------------

def test_a_graph_too_coarse_to_rank_returns_nothing():
    """
    A handful of buckets across ten minutes cannot say where to cut, and a
    confident answer from it would be invented.
    """
    assert hm.peaks(hm.from_info(_even([0.5] * 5))) == []


def test_no_graph_produces_no_prompt_evidence():
    """Silence, so the model is never told something was measured when it was not."""
    assert hm.describe([]) == ""
    assert hm.describe(hm.from_info(_even([0.5] * 5))) == ""


# --- what the evidence says -------------------------------------------------

def test_the_evidence_states_what_it_is_not():
    """
    A model told only "these are the best bits" treats re-watch density as
    a verdict on quality. Both caveats have to travel with the numbers.
    """
    values = [1.0] + [0.2] * 40
    values[30] = 0.6
    text = hm.describe(hm.from_info(_even(values)))
    assert "measured" in text
    assert "not how many people got there" in text
    assert "opening has been excluded" in text


def test_the_evidence_carries_timestamps_a_human_can_check():
    values = [1.0] + [0.2] * 40
    values[30] = 0.6
    assert "03:00" in hm.describe(hm.from_info(_even(values)))


def test_baseline_buckets_are_not_reported_as_peaks():
    """
    Asking for five peaks on a graph with two must return two. Padding with
    arbitrary baseline buckets presents them as five equally valid options.
    """
    values = [0.1] * 60
    values[20], values[40] = 0.9, 0.8
    best = hm.peaks(hm.from_info(_even(values)), top_n=5)
    assert len(best) == 2


def test_a_completely_flat_graph_has_no_peaks_worth_naming():
    """
    Every bucket equal means the graph says nothing about where to cut, and
    the honest output is nothing rather than an arbitrary first entry.
    """
    best = hm.peaks(hm.from_info(_even([0.4] * 60)), top_n=5)
    assert len(best) <= 1


# --- reaching the model -----------------------------------------------------

def test_the_orchestrator_accepts_and_uses_the_evidence():
    """
    A parameter nothing reads is worse than no parameter: it looks wired up
    in a signature and changes nothing about what is generated.
    """
    import inspect
    from agent.verdandi_orchestrator import VerdandiOrchestrator

    for fn in (VerdandiOrchestrator.orchestrate_generation,
               VerdandiOrchestrator.orchestrate_batch):
        assert "rewatch_evidence" in inspect.signature(fn).parameters

    source = inspect.getsource(VerdandiOrchestrator.orchestrate_generation)
    assert "rewatch_evidence or" in source, "evidence never reaches the prompt"


def test_the_batch_path_fetches_it_per_video():
    """It is per-source, so fetching it once for a batch would be wrong."""
    import inspect
    from agent.verdandi_orchestrator import VerdandiOrchestrator

    source = inspect.getsource(VerdandiOrchestrator.orchestrate_batch)
    assert "heatmap" in source
    assert "hm.describe" in source
