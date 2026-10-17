"""One publisher run.

Normal run:
1. Read the watermark: server attribute publisher_watermark_ms of the Pipeline asset.
2. Read srv_thingsboard rows with ts_ms > watermark - PUBLISH_LOOKBACK_HOURS (see
   payloads.since_ms for why the lookback is needed).
3. DEVICE rows: one array per device to the device HTTP API, token from devices.tokens.json.
4. ASSET rows: asset telemetry through the REST API.
5. Pipeline telemetry: rows_published and gold_lag_min.
6. The new watermark is written last, so a failed run is repeated by the next one.

Backfill (a Window): steps 3 and 4 for the rows with ts_ms in the window. The watermark is
neither read nor written and no Pipeline telemetry is sent, so normal runs carry on from
where they were and the Pipeline charts show only normal runs.

ThingsBoard overwrites a value with the same entity, key and ts, so repeating is safe.
"""

from dataclasses import dataclass

from publisher.config import Settings
from publisher.gold import Gold
from publisher.payloads import Plan, build_plan, gold_lag_min, since_ms
from publisher.thingsboard import Admin, DeviceApi, load_tokens

PIPELINE_ASSET = "Pipeline"


@dataclass(frozen=True)
class Window:
    """Backfill range: start_ms <= ts_ms < end_ms."""

    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Summary:
    watermark_ms: int
    since_ms: int
    new_watermark_ms: int
    rows: int
    devices: int
    assets: int
    gold_lag_min: float | None
    dry_run: bool
    plan: Plan
    window: Window | None = None


def publish(
    settings: Settings,
    gold: Gold,
    admin: Admin,
    device_api: DeviceApi,
    now_ms: int,
    dry_run: bool = False,
    window: Window | None = None,
) -> Summary:
    pipeline_id = admin.asset_id(PIPELINE_ASSET)
    if window is None:
        watermark = admin.read_watermark(pipeline_id)
        since = since_ms(watermark, settings.lookback_hours)
        plan = build_plan(gold.rows_since(since), watermark)
        lag = gold_lag_min(gold.max_ts_ms(), now_ms)
    else:
        # The watermark stays out of it: neither the read nor the write.
        watermark, since, lag = 0, window.start_ms, None
        plan = build_plan(gold.rows_between(window.start_ms, window.end_ms), 0)

    # Resolve every target before the first write: a missing token or asset sends nothing.
    tokens = load_tokens(settings.tokens_file, plan.devices) if plan.devices else {}
    asset_ids = {name: admin.asset_id(name) for name in plan.assets}
    summary = Summary(
        watermark_ms=watermark,
        since_ms=since,
        new_watermark_ms=watermark if window else plan.watermark_ms,
        rows=plan.rows,
        devices=len(plan.devices),
        assets=len(plan.assets),
        gold_lag_min=lag,
        dry_run=dry_run,
        plan=plan,
        window=window,
    )
    if dry_run:
        return summary

    for device, device_points in plan.devices.items():
        device_api.send(device, tokens[device], device_points)
    for asset, asset_points in plan.assets.items():
        admin.save_telemetry(asset_ids[asset], asset_points)
    if window is not None:
        return summary

    pipeline_values: dict[str, float] = {"rows_published": plan.rows}
    if lag is not None:
        pipeline_values["gold_lag_min"] = lag
    admin.save_telemetry(pipeline_id, [{"ts": now_ms, "values": pipeline_values}])
    admin.write_watermark(pipeline_id, plan.watermark_ms)
    return summary
