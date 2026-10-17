"""Publisher core: grouping, lookback, watermark and lag (no I/O)."""

import pytest

from publisher import PublisherError
from publisher.payloads import Row, build_plan, gold_lag_min, since_ms

T0 = 1_790_812_800_000  # 2026-10-01T00:00Z
MIN = 60_000
HOUR = 60 * MIN


def test_first_run_reads_everything():
    assert since_ms(0, 48) == 0


def test_lookback_is_subtracted_from_the_watermark():
    assert since_ms(T0, 48) == T0 - 48 * HOUR
    assert since_ms(T0, 0) == T0


def test_lookback_never_goes_below_zero():
    assert since_ms(HOUR, 48) == 0


def test_keys_with_the_same_ts_share_one_point():
    plan = build_plan(
        [
            Row("DEVICE", "sensor-01", T0 + HOUR, "temp_avg_1h", 22.0),
            Row("DEVICE", "sensor-01", T0, "temp_avg_1h", 21.5),
            Row("DEVICE", "sensor-01", T0, "readings_1h", 120.0),
        ],
        watermark_ms=0,
    )
    assert plan.devices == {
        "sensor-01": [
            {"ts": T0, "values": {"readings_1h": 120.0, "temp_avg_1h": 21.5}},
            {"ts": T0 + HOUR, "values": {"temp_avg_1h": 22.0}},
        ]
    }
    assert plan.assets == {}
    assert plan.rows == 3


def test_devices_and_assets_are_split_by_entity():
    plan = build_plan(
        [
            Row("DEVICE", "sensor-02", T0, "temp_avg_1h", 22.0),
            Row("DEVICE", "sensor-01", T0, "temp_avg_1h", 21.5),
            Row("ASSET", "Site A", T0, "site_temp_avg_1h", 21.7),
            Row("ASSET", "Fleet", T0, "devices_offline", 1.0),
        ],
        watermark_ms=0,
    )
    assert list(plan.devices) == ["sensor-01", "sensor-02"]
    assert list(plan.assets) == ["Fleet", "Site A"]


def test_watermark_is_the_newest_ts():
    plan = build_plan(
        [
            Row("DEVICE", "sensor-01", T0, "temp_avg_1h", 21.5),
            Row("ASSET", "Fleet", T0 + 75 * MIN, "fleet_health_avg", 88.0),
        ],
        watermark_ms=0,
    )
    assert plan.watermark_ms == T0 + 75 * MIN


def test_watermark_never_moves_back():
    # Rows re-sent from the lookback window are older than the stored watermark.
    plan = build_plan(
        [Row("DEVICE", "sensor-01", T0, "temp_avg_1h", 21.5)],
        watermark_ms=T0 + HOUR,
    )
    assert plan.watermark_ms == T0 + HOUR


def test_no_rows_keeps_the_watermark():
    plan = build_plan([], watermark_ms=T0)
    assert (plan.rows, plan.watermark_ms, plan.devices, plan.assets) == (0, T0, {}, {})


def test_unknown_entity_type_fails():
    with pytest.raises(PublisherError, match="unknown entity_type 'CUSTOMER'"):
        build_plan([Row("CUSTOMER", "x", T0, "k", 1.0)], watermark_ms=0)


def test_gold_lag_in_minutes():
    assert gold_lag_min(T0, T0 + 15 * MIN + 30_000) == 15.5
    assert gold_lag_min(None, T0) is None
