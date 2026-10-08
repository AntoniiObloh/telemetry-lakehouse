"""Reproducibility, composability and validation of the pure core."""

import hashlib
from pathlib import Path

import pytest

from generator import GeneratorError
from generator.batch import BATCH_MS, DefectConfig, simulate
from generator.sinks import FileSink
from tests.conftest import START, to_ms


def no_gap(seed: int) -> DefectConfig:
    return DefectConfig(seed=seed, gap_device=None)


def write_hashes(devices, directory: Path, seed: int) -> list[tuple[str, str]]:
    result = simulate(devices, START, START + 3_600_000, no_gap(seed))
    sink = FileSink(directory)
    for batch in result.batches:
        sink.write(batch)
    return sorted(
        (str(p.relative_to(directory)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in directory.rglob("*.jsonl")
    )


def test_same_seed_gives_byte_identical_files(devices, tmp_path):
    first = write_hashes(devices, tmp_path / "a", seed=42)
    second = write_hashes(devices, tmp_path / "b", seed=42)
    assert len(first) == 12
    assert first == second


def test_other_seed_gives_other_files(devices, tmp_path):
    first = write_hashes(devices, tmp_path / "a", seed=42)
    other = write_hashes(devices, tmp_path / "b", seed=43)
    assert [name for name, _ in first] == [name for name, _ in other]
    assert first != other


def test_two_runs_equal_one_run_over_the_combined_window(devices):
    a, b, c = to_ms("2026-10-05"), to_ms("2026-10-06"), to_ms("2026-10-07")
    config = DefectConfig(seed=42, gap_start_ms=to_ms("2026-10-05T10:00"))
    whole = simulate(devices, a, c, config)
    first = simulate(devices, a, b, config)
    second = simulate(devices, b, c, config)
    assert first.batches + second.batches == whole.batches


def test_records_are_sorted_and_batches_cover_the_window(sim):
    sent = [b.sent_at for b in sim.batches]
    assert sent == list(range(sent[0], sent[-1] + 1, BATCH_MS))
    for batch in sim.batches:
        keys = [(r["ts"], r["device_id"], r["event_id"]) for r in batch.records]
        assert keys == sorted(keys)
        assert all(r["sent_at"] == batch.sent_at for r in batch.records)


def test_report_counters_match_the_records(sim):
    assert sim.events == sum(len(b.records) for b in sim.batches)
    assert sim.duplicates > 0 and sim.late > 0 and sim.outliers > 0


def test_changing_one_rate_does_not_move_the_other_defects(devices):
    end = START + 3_600_000
    base = simulate(devices, START, end, no_gap(42))
    no_dups = simulate(
        devices, START, end, DefectConfig(seed=42, gap_device=None, dup_rate=0.0)
    )
    assert no_dups.outliers == base.outliers
    assert no_dups.late == base.late
    assert no_dups.duplicates == 0


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (START, START, "before end"),
        (START + 1, START + BATCH_MS, "5 minutes"),
        (START, START + BATCH_MS + 1, "5 minutes"),
    ],
)
def test_bad_window_is_rejected(devices, start, end, message):
    with pytest.raises(GeneratorError, match=message):
        simulate(devices, start, end, no_gap(42))


def test_bad_config_is_rejected(devices):
    with pytest.raises(GeneratorError, match="dup_rate"):
        DefectConfig(dup_rate=1.5)
    with pytest.raises(GeneratorError, match="not in the device list"):
        simulate(devices, START, START + 4 * 3_600_000, DefectConfig(gap_device="nope"))
    with pytest.raises(GeneratorError, match="shorter than the gap"):
        simulate(devices, START, START + BATCH_MS, DefectConfig())
