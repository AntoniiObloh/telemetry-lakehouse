"""Shared fixtures: one 7-day simulation (seed 42, default defects) for the whole session."""

from datetime import UTC, datetime

import pytest

from generator.batch import LOOKBACK_MS, DefectConfig, simulate
from generator.devices import load_devices


def to_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


START = to_ms("2026-10-05")
END = to_ms("2026-10-12")
# Rates are measured only where both lookback and arrival fit in the window.
STABLE_END = END - LOOKBACK_MS


@pytest.fixture(scope="session")
def devices():
    return load_devices()


@pytest.fixture(scope="session")
def config():
    return DefectConfig(seed=42)


@pytest.fixture(scope="session")
def sim(devices, config):
    return simulate(devices, START, END, config)


@pytest.fixture(scope="session")
def groups(sim):
    """Records grouped by (device_id, ts): the original plus its duplicate copy, if any.

    Each group is sorted by sent_at, so group[0] is the original and group[1:] are copies.
    Only events in the stable range are kept.
    """
    found: dict[tuple[str, int], list[dict]] = {}
    for batch in sim.batches:
        for record in batch.records:
            found.setdefault((record["device_id"], record["ts"]), []).append(record)
    return [
        sorted(group, key=lambda r: r["sent_at"])
        for (_, ts), group in found.items()
        if START <= ts < STABLE_END
    ]
