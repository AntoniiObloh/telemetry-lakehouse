"""A whole publisher run with HTTP mocks: device API via responses, tb-rest-client mocked."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
import responses
from tb_rest_client.rest import ApiException

from publisher import PublisherError, __main__
from publisher.config import Settings
from publisher.payloads import Row
from publisher.run import Window, publish
from publisher.thingsboard import WATERMARK_KEY, Admin, DeviceApi, load_tokens

TB_URL = "http://tb.test:8080"
T0 = 1_790_812_800_000  # 2026-10-01T00:00Z
MIN = 60_000
HOUR = 60 * MIN
BUILT = T0 + HOUR + 15 * MIN  # computed_at of the dbt build
NOW = T0 + HOUR + 30 * MIN
TOKENS = {"sensor-01": "fake-token-01", "sensor-02": "fake-token-02"}
ROWS = [
    Row("DEVICE", "sensor-01", T0, "temp_avg_1h", 21.5),
    Row("DEVICE", "sensor-01", T0, "readings_1h", 120.0),
    Row("DEVICE", "sensor-01", BUILT, "health_score", 90.0),
    Row("DEVICE", "sensor-02", T0, "temp_avg_1h", 22.0),
    Row("ASSET", "Site A", T0, "site_temp_avg_1h", 21.7),
    Row("ASSET", "Fleet", BUILT, "fleet_health_avg", 88.0),
]


class FakeGold:
    def __init__(self, rows: list[Row], newest: int | None = BUILT):
        self.rows = rows
        self.newest = newest
        self.since: list[int] = []
        self.windows: list[tuple[int, int]] = []

    def rows_since(self, since_ms: int) -> list[Row]:
        self.since.append(since_ms)
        return [row for row in self.rows if row.ts_ms > since_ms]

    def rows_between(self, start_ms: int, end_ms: int) -> list[Row]:
        self.windows.append((start_ms, end_ms))
        return [row for row in self.rows if start_ms <= row.ts_ms < end_ms]

    def max_ts_ms(self) -> int | None:
        return self.newest


def tb_client(watermark: int | None = None, missing: tuple[str, ...] = ()) -> MagicMock:
    """RestClientCE stand-in: assets by name, the watermark attribute, recorded writes."""
    client = MagicMock()

    def get_tenant_asset(name):
        if name in missing:
            raise ApiException(status=404, reason="Not Found")
        return SimpleNamespace(id=f"id-{name}")

    client.get_tenant_asset.side_effect = get_tenant_asset
    client.get_attributes_by_scope.return_value = (
        [] if watermark is None else [{"key": WATERMARK_KEY, "value": watermark}]
    )
    return client


@pytest.fixture
def settings(tmp_path) -> Settings:
    tokens_file = tmp_path / "devices.tokens.json"
    tokens_file.write_text(json.dumps(TOKENS), encoding="utf-8")
    return Settings(
        gold_catalog="lakehouse",
        gold_schema="dbt_dev",
        databricks_host="dbc-test.cloud.databricks.com",
        databricks_http_path="/sql/1.0/warehouses/abc",
        tb_url=TB_URL,
        tb_username="tenant@example.test",
        tokens_file=tokens_file,
        lookback_hours=48,
        databricks_token="fake-databricks-token",
        tb_password="fake-tb-password",
    )


@pytest.fixture
def http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        for token in TOKENS.values():
            mock.add(responses.POST, f"{TB_URL}/api/v1/{token}/telemetry", status=200)
        yield mock


def posted(http) -> dict[str, list]:
    """Device API bodies by token."""
    bodies: dict[str, list] = {}
    for call in http.calls:
        token = call.request.url.split("/api/v1/")[1].split("/")[0]
        bodies.setdefault(token, []).extend(json.loads(call.request.body))
    return bodies


def run(settings, client, gold, dry_run=False, window=None):
    return publish(
        settings, gold, Admin(client), DeviceApi(TB_URL), NOW, dry_run, window
    )


def test_first_run_publishes_everything(settings, http):
    client = tb_client()
    summary = run(settings, client, FakeGold(ROWS))

    assert posted(http) == {
        "fake-token-01": [
            {"ts": T0, "values": {"readings_1h": 120.0, "temp_avg_1h": 21.5}},
            {"ts": BUILT, "values": {"health_score": 90.0}},
        ],
        "fake-token-02": [{"ts": T0, "values": {"temp_avg_1h": 22.0}}],
    }
    telemetry = [c.args for c in client.save_entity_telemetry.call_args_list]
    assert telemetry == [
        ("id-Fleet", "ANY", [{"ts": BUILT, "values": {"fleet_health_avg": 88.0}}]),
        ("id-Site A", "ANY", [{"ts": T0, "values": {"site_temp_avg_1h": 21.7}}]),
        (
            "id-Pipeline",
            "ANY",
            [{"ts": NOW, "values": {"rows_published": 6, "gold_lag_min": 15.0}}],
        ),
    ]
    assert summary.rows == 6
    assert (summary.devices, summary.assets) == (2, 2)


def test_watermark_is_written_last(settings, http):
    client = tb_client()
    run(settings, client, FakeGold(ROWS))
    last = client.mock_calls[-1]
    assert last == (
        "save_entity_attributes_v2",
        ("id-Pipeline", "SERVER_SCOPE", {WATERMARK_KEY: BUILT}),
        {},
    )


def test_gold_is_read_from_the_watermark_minus_the_lookback(settings, http):
    gold = FakeGold(ROWS)
    run(settings, tb_client(watermark=BUILT), gold)
    assert gold.since == [BUILT - 48 * HOUR]


def test_a_rerun_sends_the_same_values(settings, http):
    run(settings, tb_client(), FakeGold(ROWS))
    first = posted(http)
    http.calls.reset()
    run(settings, tb_client(watermark=BUILT), FakeGold(ROWS))
    assert posted(http) == first


def test_rejected_device_post_keeps_the_watermark(settings, http):
    http.replace(responses.POST, f"{TB_URL}/api/v1/fake-token-01/telemetry", status=500)
    client = tb_client()
    with pytest.raises(
        PublisherError, match="sensor-01: telemetry rejected with HTTP 500"
    ):
        run(settings, client, FakeGold(ROWS))
    client.save_entity_attributes_v2.assert_not_called()
    client.save_entity_telemetry.assert_not_called()


def test_errors_never_show_the_token(settings, http):
    http.replace(
        responses.POST,
        f"{TB_URL}/api/v1/fake-token-01/telemetry",
        body=requests.ConnectionError("refused: fake-token-01"),
    )
    with pytest.raises(PublisherError) as exc:
        run(settings, tb_client(), FakeGold(ROWS))
    assert "fake-token-01" not in str(exc.value)
    assert "sensor-01: cannot reach ThingsBoard (ConnectionError)" in str(exc.value)


def test_missing_token_sends_nothing(settings, http):
    settings.tokens_file.write_text(json.dumps({"sensor-01": "x"}), encoding="utf-8")
    client = tb_client()
    with pytest.raises(PublisherError, match="no token for sensor-02"):
        run(settings, client, FakeGold(ROWS))
    assert len(http.calls) == 0
    client.save_entity_telemetry.assert_not_called()


def test_missing_asset_sends_nothing(settings, http):
    client = tb_client(missing=("Site A",))
    with pytest.raises(PublisherError, match="asset 'Site A' not found"):
        run(settings, client, FakeGold(ROWS))
    assert len(http.calls) == 0
    client.save_entity_telemetry.assert_not_called()


def test_dry_run_writes_nothing(settings, http):
    client = tb_client()
    summary = run(settings, client, FakeGold(ROWS), dry_run=True)
    assert summary.rows == 6
    assert len(http.calls) == 0
    client.save_entity_telemetry.assert_not_called()
    client.save_entity_attributes_v2.assert_not_called()


def test_dry_run_lists_every_point(settings, http):
    summary = run(settings, tb_client(), FakeGold(ROWS), dry_run=True)
    assert __main__.plan_lines(summary) == [
        "DEVICE sensor-01",
        "  2026-10-01T00:00Z  readings_1h=120.0  temp_avg_1h=21.5",
        "  2026-10-01T01:15Z  health_score=90.0",
        "DEVICE sensor-02",
        "  2026-10-01T00:00Z  temp_avg_1h=22.0",
        "ASSET Fleet",
        "  2026-10-01T01:15Z  fleet_health_avg=88.0",
        "ASSET Site A",
        "  2026-10-01T00:00Z  site_temp_avg_1h=21.7",
    ]


def test_empty_gold_still_reports_to_the_pipeline(settings, http):
    client = tb_client(watermark=T0)
    run(settings, client, FakeGold([], newest=None))
    assert len(http.calls) == 0
    client.save_entity_telemetry.assert_called_once_with(
        "id-Pipeline", "ANY", [{"ts": NOW, "values": {"rows_published": 0}}]
    )
    client.save_entity_attributes_v2.assert_called_once_with(
        "id-Pipeline", "SERVER_SCOPE", {WATERMARK_KEY: T0}
    )


def test_long_series_is_split_into_requests(settings, http):
    rows = [
        Row("DEVICE", "sensor-01", T0 + i * MIN, "temp_avg_1h", 1.0)
        for i in range(1200)
    ]
    run(settings, tb_client(), FakeGold(rows))
    sizes = [len(json.loads(call.request.body)) for call in http.calls]
    assert sizes == [500, 500, 200]


def test_tokens_file_problems(tmp_path):
    missing = tmp_path / "devices.tokens.json"
    with pytest.raises(PublisherError, match="not found"):
        load_tokens(missing, ["sensor-01"])
    missing.write_text("{not json", encoding="utf-8")
    with pytest.raises(PublisherError, match="invalid JSON"):
        load_tokens(missing, ["sensor-01"])


def test_cli_stops_on_a_missing_env_file(capsys, tmp_path):
    assert __main__.main(["--env-file", str(tmp_path / ".env.cloud")]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_reports_missing_settings(monkeypatch, capsys, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# no TB_* here\n", encoding="utf-8")
    for name in ("TB_URL", "TB_USERNAME", "TB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert __main__.main(["--env-file", str(env_file)]) == 1
    assert "FAIL missing settings" in capsys.readouterr().err


# backfill


def test_backfill_sends_only_the_window(settings, http):
    client = tb_client(watermark=BUILT)
    gold = FakeGold(ROWS)
    summary = run(settings, client, gold, window=Window(T0, T0 + HOUR))

    assert gold.windows == [(T0, T0 + HOUR)]
    assert gold.since == []
    assert posted(http) == {
        "fake-token-01": [
            {"ts": T0, "values": {"readings_1h": 120.0, "temp_avg_1h": 21.5}}
        ],
        "fake-token-02": [{"ts": T0, "values": {"temp_avg_1h": 22.0}}],
    }
    client.save_entity_telemetry.assert_called_once_with(
        "id-Site A", "ANY", [{"ts": T0, "values": {"site_temp_avg_1h": 21.7}}]
    )
    assert summary.rows == 4


def test_backfill_leaves_the_watermark_alone(settings, http):
    client = tb_client(watermark=BUILT)
    run(settings, client, FakeGold(ROWS), window=Window(T0, T0 + HOUR))
    client.get_attributes_by_scope.assert_not_called()
    client.save_entity_attributes_v2.assert_not_called()
    # No Pipeline telemetry either: rows_published charts only normal runs.
    pipeline_calls = [
        c
        for c in client.save_entity_telemetry.call_args_list
        if c.args[0] == "id-Pipeline"
    ]
    assert pipeline_calls == []


def test_normal_run_after_a_backfill_continues_from_the_watermark(settings, http):
    run(
        settings,
        tb_client(watermark=BUILT),
        FakeGold(ROWS),
        window=Window(T0, T0 + HOUR),
    )
    gold = FakeGold(ROWS)
    run(settings, tb_client(watermark=BUILT), gold)
    assert gold.since == [BUILT - 48 * HOUR]


def test_backfill_dry_run_writes_nothing(settings, http):
    client = tb_client()
    summary = run(
        settings, client, FakeGold(ROWS), dry_run=True, window=Window(T0, BUILT + 1)
    )
    assert summary.rows == 6
    assert len(http.calls) == 0
    client.save_entity_telemetry.assert_not_called()


def test_cli_window_covers_both_days():
    window = __main__.backfill_window(
        __main__.parse_day("2026-10-01"), __main__.parse_day("2026-10-07")
    )
    assert window == Window(1_790_812_800_000, 1_790_812_800_000 + 7 * 24 * HOUR)
    assert __main__.backfill_window(None, None) is None


@pytest.mark.parametrize(
    ("first", "last", "message"),
    [
        ("2026-10-01", None, "go together"),
        (None, "2026-10-07", "go together"),
        ("2026-10-07", "2026-10-01", "before --from"),
    ],
)
def test_cli_window_errors(first, last, message):
    days = [__main__.parse_day(d) if d else None for d in (first, last)]
    with pytest.raises(PublisherError, match=message):
        __main__.backfill_window(*days)


def test_report_names_the_backfill(settings, http, capsys):
    summary = run(settings, tb_client(), FakeGold(ROWS), window=Window(T0, T0 + HOUR))
    __main__.report(summary)
    out = capsys.readouterr().out
    assert (
        "backfill  2026-10-01T00:00Z .. 2026-10-01T01:00Z  (watermark untouched)" in out
    )
    assert not any(line.startswith("watermark") for line in out.splitlines())
    assert "gold_lag_min" not in out
