"""Pure simulation core: devices + window + defect config -> batches of events in memory.

No I/O here (no files, no network, no printing), so tests are fast and the same result can
go to any sink. A batch is what the devices "sent" at a 5-minute boundary (sent_at).

Batch content depends only on (seed, config, sent_at), not on the window: events are
simulated from 48 h 5 min before the window start, so late events and duplicates that
originate before the window still arrive inside it. Anything due after the window end shows
up in the next run, so two consecutive runs equal one run over the combined window (given an
explicit gap_start; a derived one depends on the window).
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from generator import GeneratorError
from generator.devices import Device
from generator.readings import event_id, event_rng, reading

SLOT_MS = 30_000  # one reading per device
BATCH_MS = 300_000  # 5 minutes
MS_PER_HOUR = 3_600_000
LATE_MIN_MS = MS_PER_HOUR
LATE_MAX_MS = 48 * MS_PER_HOUR
LOOKBACK_MS = LATE_MAX_MS + BATCH_MS

FIRMWARE = "1.3.0"
OUTLIER_TEMPERATURES = (999.0, -80.0)
DEFAULT_DRIFT_FROM_MS = int(datetime(2026, 10, 8, tzinfo=UTC).timestamp() * 1000)


@dataclass(frozen=True)
class DefectConfig:
    """Seed and the five defects; the defaults are the values from spec section 3.1."""

    seed: int = 42
    dup_rate: float = 0.02
    late_rate: float = 0.01
    outlier_rate: float = 0.005
    drift_from_ms: int | None = DEFAULT_DRIFT_FROM_MS
    gap_device: str | None = "sensor-03"
    gap_hours: float = 3.0
    gap_start_ms: int | None = None  # None: derived from the seed inside the window

    def __post_init__(self):
        for name in ("dup_rate", "late_rate", "outlier_rate"):
            if not 0 <= getattr(self, name) <= 1:
                raise GeneratorError(f"{name} must be within 0..1")
        if self.gap_hours < 0:
            raise GeneratorError("gap_hours must not be negative")

    @classmethod
    def none(cls, seed: int = 42) -> "DefectConfig":
        """No defects at all: a clean 30 s cadence."""
        return cls(
            seed=seed,
            dup_rate=0.0,
            late_rate=0.0,
            outlier_rate=0.0,
            drift_from_ms=None,
            gap_device=None,
            gap_hours=0.0,
        )


@dataclass(frozen=True)
class Batch:
    sent_at: int  # epoch ms, a multiple of 5 minutes
    records: list[dict]  # sorted by (ts, device_id, event_id)


@dataclass(frozen=True)
class SimulationResult:
    batches: list[Batch]
    gap: tuple[int, int] | None  # [start, end) in epoch ms, None if no gap
    events: int  # records written, copies included
    duplicates: int  # duplicate copies written
    late: int  # late events written
    outliers: int  # outlier events written


def resolve_gap(
    devices: list[Device], start_ms: int, end_ms: int, config: DefectConfig
) -> tuple[int, int] | None:
    """Gap interval [start, end) for the gap device, or None if the gap is off."""
    if not config.gap_device or config.gap_hours <= 0:
        return None
    if config.gap_device not in {d.device_id for d in devices}:
        raise GeneratorError(
            f"gap device '{config.gap_device}' is not in the device list"
        )
    duration = int(config.gap_hours * MS_PER_HOUR)
    gap_start = config.gap_start_ms
    if gap_start is None:
        span = end_ms - start_ms - duration
        if span < 0:
            raise GeneratorError("window is shorter than the gap")
        offset = int(random.Random(f"{config.seed}:gap").random() * (span + 1))
        gap_start = start_ms + offset // SLOT_MS * SLOT_MS
    return gap_start, gap_start + duration


def simulate(
    devices: list[Device], start_ms: int, end_ms: int, config: DefectConfig
) -> SimulationResult:
    """Batches with sent_at in (start_ms, end_ms], one every 5 minutes."""
    if start_ms >= end_ms:
        raise GeneratorError("start must be before end")
    if start_ms % BATCH_MS or end_ms % BATCH_MS:
        raise GeneratorError("start and end must be aligned to 5 minutes")
    gap = resolve_gap(devices, start_ms, end_ms, config)

    buckets: dict[int, list[dict]] = {
        sent_at: [] for sent_at in range(start_ms + BATCH_MS, end_ms + 1, BATCH_MS)
    }
    duplicates = late = outliers = 0

    for device in devices:
        in_gap = gap is not None and device.device_id == config.gap_device
        for ts in range(start_ms - LOOKBACK_MS, end_ms, SLOT_MS):
            if in_gap and gap[0] <= ts < gap[1]:
                continue

            # Draw order is fixed and every draw happens even when its rate is 0, so
            # changing one rate never changes which events the other defects hit.
            rng = event_rng(config.seed, device.device_id, ts)
            clean = reading(device, ts, rng)
            outlier_draw, outlier_kind = rng.random(), rng.random()
            late_draw, late_delay = rng.random(), rng.random()
            dup_draw, dup_where = rng.random(), rng.random()

            is_outlier = outlier_draw < config.outlier_rate
            is_late = late_draw < config.late_rate
            delay = 0
            if is_late:
                delay = LATE_MIN_MS + int(late_delay * (LATE_MAX_MS - LATE_MIN_MS))
            arrival = (ts + delay) // BATCH_MS * BATCH_MS + BATCH_MS
            copy_arrival = None
            if dup_draw < config.dup_rate:
                copy_arrival = arrival if dup_where < 0.5 else arrival + BATCH_MS

            original_in = arrival in buckets
            copy_in = copy_arrival in buckets
            if not (original_in or copy_in):
                continue

            temperature = clean.temperature
            if is_outlier:
                temperature = OUTLIER_TEMPERATURES[int(outlier_kind * 2)]
            record = {
                "event_id": event_id(config.seed, device.device_id, ts),
                "device_id": device.device_id,
                "ts": ts,
                "sent_at": arrival,
                "temperature": temperature,
                "humidity": clean.humidity,
                "battery": clean.battery,
            }
            if config.drift_from_ms is not None and ts >= config.drift_from_ms:
                record["firmware"] = FIRMWARE

            if original_in:
                buckets[arrival].append(record)
                late += is_late
                outliers += is_outlier
            if copy_in:
                buckets[copy_arrival].append(dict(record, sent_at=copy_arrival))
                duplicates += 1

    batches = []
    for sent_at, records in buckets.items():
        records.sort(key=lambda r: (r["ts"], r["device_id"], r["event_id"]))
        batches.append(Batch(sent_at=sent_at, records=records))
    events = sum(len(b.records) for b in batches)
    return SimulationResult(
        batches=batches,
        gap=gap,
        events=events,
        duplicates=duplicates,
        late=late,
        outliers=outliers,
    )
