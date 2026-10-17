"""Pure core: gold rows -> ThingsBoard payloads, the next watermark and the gold lag.

No I/O here, so the tests need neither Databricks nor ThingsBoard.
"""

from collections import defaultdict
from dataclasses import dataclass

from publisher import PublisherError

MS_PER_MINUTE = 60_000
MS_PER_HOUR = 3_600_000
ENTITY_TYPES = ("DEVICE", "ASSET")

# ThingsBoard telemetry with timestamps: [{"ts": <epoch ms>, "values": {key: value}}].
Points = list[dict]


@dataclass(frozen=True)
class Row:
    """One row of srv_thingsboard."""

    entity_type: str
    entity_name: str
    ts_ms: int
    key: str
    value: float


@dataclass(frozen=True)
class Plan:
    devices: dict[str, Points]  # device name -> points
    assets: dict[str, Points]  # asset name -> points
    rows: int  # values to publish
    watermark_ms: int  # the watermark to store after a successful run


def since_ms(watermark_ms: int, lookback_hours: float) -> int:
    """Exclusive lower bound of ts_ms for the gold query.

    Hourly keys carry the start of the hour, and dbt rebuilds that hour while it fills
    up and again when late events arrive (up to 48 h). A plain ts_ms > watermark would
    publish an hour once, half full, and never again, so the last lookback_hours are
    re-sent. ThingsBoard overwrites the same key and ts, so that is harmless.
    """
    return max(0, watermark_ms - int(lookback_hours * MS_PER_HOUR))


def points(values_by_ts: dict[int, dict[str, float]]) -> Points:
    return [
        {"ts": ts, "values": dict(sorted(values.items()))}
        for ts, values in sorted(values_by_ts.items())
    ]


def build_plan(rows: list[Row], watermark_ms: int) -> Plan:
    """Group by entity, then by ts: keys with the same ts share one point."""
    grouped = {
        entity_type: defaultdict(lambda: defaultdict(dict))
        for entity_type in ENTITY_TYPES
    }
    for row in rows:
        if row.entity_type not in grouped:
            raise PublisherError(f"unknown entity_type '{row.entity_type}' in gold")
        grouped[row.entity_type][row.entity_name][row.ts_ms][row.key] = row.value
    newest = max((row.ts_ms for row in rows), default=watermark_ms)
    return Plan(
        devices={
            name: points(by_ts) for name, by_ts in sorted(grouped["DEVICE"].items())
        },
        assets={
            name: points(by_ts) for name, by_ts in sorted(grouped["ASSET"].items())
        },
        rows=len(rows),
        watermark_ms=max(watermark_ms, newest),
    )


def gold_lag_min(max_ts_ms: int | None, now_ms: int) -> float | None:
    """Minutes from the newest ts_ms in gold to now; None for an empty table.

    State keys carry the dbt build time, so this is the time since gold was last built.
    """
    if max_ts_ms is None:
        return None
    return round((now_ms - max_ts_ms) / MS_PER_MINUTE, 1)
