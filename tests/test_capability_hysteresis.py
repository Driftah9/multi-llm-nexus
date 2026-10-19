"""Flap guard on the earned qualification bar (QUALIFY_CONFIRM hysteresis).

A near-bar model must not oscillate in/out of demanding-role standing on a short noisy
run. The display EWMA (score()) still moves on every grade; only the qualification gate
is latched. Provider names are synthetic so the shipped baseline can't seed them."""
import json
import tempfile

from src.orchestration import capability_map as cmod
from src.orchestration.capability_map import CapabilityMap, QUALIFY_BAR, QUALIFY_MIN_SAMPLES

D, P = "reasoning", "flapper-test-model"


def _cm():
    return CapabilityMap(path=tempfile.mktemp())


def _seat(cm, score, count=QUALIFY_MIN_SAMPLES, latch=None):
    """Place a provider at a given earned score/volume, optionally with a latch."""
    cm.data["research"].setdefault(D, {})[P] = score
    cm.data["grades"].setdefault(D, {})[P] = count
    if latch is not None:
        cm.data["qual_latch"].setdefault(D, {})[P] = {"above": latch, "streak": 0, "bar": QUALIFY_BAR}


def test_legacy_row_without_latch_keeps_live_behavior():
    cm = _cm()
    _seat(cm, 0.62)
    assert cm.is_qualified(D, P) is True
    _seat(cm, 0.58)
    assert cm.is_qualified(D, P) is False


def test_single_good_grade_does_not_promote_near_bar_model():
    cm = _cm()
    _seat(cm, 0.56, count=6)                    # the live github-gpt4o shape
    cm.update(D, P, 0.9)                        # 0.56 -> 0.628, over the bar once
    assert cm.score(D, P) >= QUALIFY_BAR       # display moves immediately
    assert cm.is_qualified(D, P) is False      # gate waits for confirmation


def test_promotion_after_confirm_consecutive():
    cm = _cm()
    _seat(cm, 0.56, count=6)
    for i in range(cmod.QUALIFY_CONFIRM):
        assert cm.is_qualified(D, P) is False
        cm.update(D, P, 0.9)
    assert cm.is_qualified(D, P) is True


def test_interrupted_run_resets_streak():
    cm = _cm()
    _seat(cm, 0.56, count=6)
    cm.update(D, P, 0.9)                        # 0.628 above
    cm.update(D, P, 0.9)                        # 0.682 above (streak 2)
    cm.update(D, P, 0.1)                        # 0.566 below -> streak reset
    assert cm.qualification_state(D, P)["streak"] == 0
    cm.update(D, P, 0.9)                        # 0.633 above, streak 1
    assert cm.is_qualified(D, P) is False


def test_one_bad_grade_does_not_demote_qualified_model():
    cm = _cm()
    _seat(cm, 0.64, count=10, latch=True)
    cm.update(D, P, 0.4)                        # 0.64 -> 0.592, dips under once
    assert cm.score(D, P) < QUALIFY_BAR
    assert cm.is_qualified(D, P) is True       # noise, held
    cm.update(D, P, 0.9)                        # recovers -> streak cleared
    assert cm.qualification_state(D, P) == {"above": True, "streak": 0, "confirm": cmod.QUALIFY_CONFIRM}


def test_sustained_decline_demotes():
    cm = _cm()
    _seat(cm, 0.64, count=10, latch=True)
    for _ in range(cmod.QUALIFY_CONFIRM):
        cm.update(D, P, 0.4)                    # 0.592 -> 0.554 -> 0.523, all in band
    assert cm.score(D, P) >= QUALIFY_BAR - cmod.PROBE_BAND
    assert cm.is_qualified(D, P) is False


def test_collapse_below_probe_band_demotes_immediately():
    cm = _cm()
    _seat(cm, 0.62, count=10, latch=True)
    cm.update(D, P, 0.0)                        # 0.62 -> 0.496, still in band: held
    assert cm.is_qualified(D, P) is True
    cm.update(D, P, 0.0)                        # -> 0.397 < bar-band: collapse
    assert cm.is_qualified(D, P) is False


def test_fresh_model_still_graduates_at_volume_floor():
    """Guard must not delay a clearly strong newcomer past QUALIFY_MIN_SAMPLES."""
    cm = _cm()
    for _ in range(QUALIFY_MIN_SAMPLES):
        cm.update(D, P, 1.0)
    assert cm.is_qualified(D, P) is True


def test_pending_promotion_is_a_probe_candidate_and_latched_model_is_not():
    cm = _cm()
    _seat(cm, 0.56, count=6)
    cm.update(D, P, 0.9)                        # over bar, unconfirmed
    assert cm.probe_candidates(D, [P]) == [P]
    other = "held-test-model"
    cm.data["research"][D][other] = 0.59
    cm.data["grades"][D][other] = 10
    cm.data["qual_latch"][D][other] = {"above": True, "streak": 1, "bar": QUALIFY_BAR}
    assert cm.qualified(D, [other]) == [other]
    assert cm.probe_candidates(D, [other]) == []


def test_bar_change_invalidates_latch(monkeypatch):
    cm = _cm()
    _seat(cm, 0.62, count=10, latch=False)     # latched below at old bar
    assert cm.is_qualified(D, P) is False
    monkeypatch.setattr(cmod, "QUALIFY_BAR", 0.5)
    assert cm.earned_qualified(D, P, bar=0.5) is True   # stale latch ignored -> live


def test_confirm_one_disables_guard(monkeypatch):
    monkeypatch.setattr(cmod, "QUALIFY_CONFIRM", 1)
    cm = _cm()
    _seat(cm, 0.56, count=6)
    cm.update(D, P, 0.9)
    assert cm.is_qualified(D, P) is True


def test_latch_persists_across_save_load():
    cm = _cm()
    _seat(cm, 0.56, count=6)
    cm.update(D, P, 0.9)
    cm.save()
    on_disk = json.loads(cm.path.read_text())
    assert on_disk["qual_latch"][D][P]["streak"] == 1
    cm2 = CapabilityMap(path=cm.path)
    cm2.update(D, P, 0.9)
    cm2.update(D, P, 0.9)
    assert cm2.is_qualified(D, P) is True


def test_record_grade_path_feeds_latch():
    cm = _cm()
    _seat(cm, 0.56, count=6)
    cm.record_grade(P, D, 0.9)
    assert cm.qualification_state(D, P)["streak"] == 1
