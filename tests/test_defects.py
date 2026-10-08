"""One test group per data defect, all with the fixed seed 42."""

import pytest

from generator.batch import (
    BATCH_MS,
    MS_PER_HOUR,
    SLOT_MS,
    DefectConfig,
    resolve_gap,
    simulate,
)
from tests.conftest import END, START, to_ms

DRIFT_FROM = to_ms("2026-10-08")


def within(measured: float, expected: float, tolerance: float) -> bool:
    return abs(measured - expected) <= expected * tolerance


def test_duplicate_rate(groups):
    rate = sum(len(g) > 1 for g in groups) / len(groups)
    assert within(rate, 0.02, 0.15), rate


def test_duplicates_are_identical_copies(groups):
    dups = [g for g in groups if len(g) > 1]
    assert dups
    for original, *copies in dups:
        assert len(copies) == 1
        copy = copies[0]
        assert {k: v for k, v in copy.items() if k != "sent_at"} == {
            k: v for k, v in original.items() if k != "sent_at"
        }
        assert copy["sent_at"] - original["sent_at"] in (0, BATCH_MS)


def test_late_rate(groups):
    rate = sum(g[0]["sent_at"] - g[0]["ts"] > BATCH_MS for g in groups) / len(groups)
    assert within(rate, 0.01, 0.20), rate


def test_late_and_on_time_delays(groups):
    for group in groups:
        delay = group[0]["sent_at"] - group[0]["ts"]
        if delay > BATCH_MS:
            assert MS_PER_HOUR <= delay <= 48 * MS_PER_HOUR + BATCH_MS
        else:
            assert 0 < delay <= BATCH_MS


def test_drift_only_after_the_date(sim):
    for batch in sim.batches:
        for record in batch.records:
            if record["ts"] < DRIFT_FROM:
                assert "firmware" not in record
            else:
                assert record["firmware"] == "1.3.0"


def test_drift_boundary(sim):
    by_ts = {
        r["ts"]: r
        for b in sim.batches
        for r in b.records
        if r["device_id"] == "sensor-01"
    }
    assert "firmware" not in by_ts[DRIFT_FROM - SLOT_MS]
    assert by_ts[DRIFT_FROM]["firmware"] == "1.3.0"


@pytest.fixture(scope="module")
def gap_run(devices):
    """Own 3-day window with an explicit early gap.

    A late event arrives up to 48 h after its ts, so the gap must end more than 48 h before
    the window end; otherwise some of its events would arrive in the next run.
    """
    end = to_ms("2026-10-08")
    config = DefectConfig(seed=42, gap_start_ms=to_ms("2026-10-05T06:00"))
    return simulate(devices, START, end, config)


def test_gap_has_no_events(gap_run):
    gap_start, gap_end = gap_run.gap
    assert gap_end - gap_start == 3 * MS_PER_HOUR
    for batch in gap_run.batches:
        for record in batch.records:
            if record["device_id"] == "sensor-03":
                assert not gap_start <= record["ts"] < gap_end


def test_other_devices_are_complete_in_the_gap(gap_run):
    gap_start, gap_end = gap_run.gap
    expected = (gap_end - gap_start) // SLOT_MS
    seen: dict[str, set[int]] = {}
    for batch in gap_run.batches:
        for r in batch.records:
            if gap_start <= r["ts"] < gap_end:
                seen.setdefault(r["device_id"], set()).add(r["ts"])
    assert "sensor-03" not in seen
    assert len(seen) == 4
    for device_id, stamps in seen.items():
        assert len(stamps) == expected, device_id


def test_gap_device_resumes_around_the_gap(gap_run):
    gap_start, gap_end = gap_run.gap
    stamps = {
        r["ts"]
        for b in gap_run.batches
        for r in b.records
        if r["device_id"] == "sensor-03"
    }
    assert any(gap_start - BATCH_MS <= ts < gap_start for ts in stamps)
    assert any(gap_end <= ts < gap_end + BATCH_MS for ts in stamps)


def test_default_gap_is_inside_the_window_and_stable(devices, config):
    first = resolve_gap(devices, START, END, config)
    assert first == resolve_gap(devices, START, END, config)
    assert START <= first[0] and first[1] <= END
    assert (first[0] - START) % SLOT_MS == 0
    assert first != resolve_gap(devices, START, END, DefectConfig(seed=7))


def test_outlier_rate_and_values(groups):
    rate = sum(g[0]["temperature"] in (999.0, -80.0) for g in groups) / len(groups)
    assert within(rate, 0.005, 0.25), rate
    for group in groups:
        temperature = group[0]["temperature"]
        assert temperature in (999.0, -80.0) or 10 <= temperature <= 35


def test_no_defects_is_a_clean_cadence(devices):
    day = 24 * MS_PER_HOUR
    result = simulate(devices, START, START + day, DefectConfig.none(seed=42))
    assert (result.duplicates, result.late, result.outliers) == (0, 0, 0)
    counts: dict[str, int] = {}
    for batch in result.batches:
        for r in batch.records:
            counts[r["device_id"]] = counts.get(r["device_id"], 0) + 1
            assert "firmware" not in r
            assert 0 < r["sent_at"] - r["ts"] <= BATCH_MS
            assert 10 <= r["temperature"] <= 35
    assert counts == {d.device_id: 2880 for d in devices}
