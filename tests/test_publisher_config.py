"""Publisher settings: environment, thingsboard/.env, checks. All values are fakes."""

from pathlib import Path

import pytest

from publisher import PublisherError
from publisher.config import load_settings

BASE_ENV = {
    "DATABRICKS_HOST": "dbc-test.cloud.databricks.com",
    "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc",
    "DATABRICKS_TOKEN": "fake-databricks-token",
    "TB_URL": "http://tb.test:8080",
    "TB_USERNAME": "tenant@example.test",
    "TB_PASSWORD": "fake-tb-password",
}


@pytest.fixture
def no_file(tmp_path) -> Path:
    return tmp_path / "missing.env"


def test_defaults_point_at_the_dev_schema(no_file):
    settings = load_settings(BASE_ENV, no_file)
    assert settings.gold_table == "`lakehouse`.`dbt_dev`.`srv_thingsboard`"
    assert settings.lookback_hours == 48
    assert settings.tokens_file.name == "devices.tokens.json"


def test_catalog_and_schema_come_from_the_environment(no_file):
    env = {**BASE_ENV, "GOLD_CATALOG": "lakehouse", "GOLD_SCHEMA": "gold"}
    assert (
        load_settings(env, no_file).gold_table == "`lakehouse`.`gold`.`srv_thingsboard`"
    )


@pytest.mark.parametrize("schema", ["dbt_dev; DROP TABLE x", "dbt-dev", "`x`", ""])
def test_schema_must_be_a_plain_identifier(no_file, schema):
    if not schema:  # empty values count as unset and fall back to the default
        assert load_settings({**BASE_ENV, "GOLD_SCHEMA": schema}, no_file)
        return
    with pytest.raises(PublisherError, match="GOLD_SCHEMA must be a plain identifier"):
        load_settings({**BASE_ENV, "GOLD_SCHEMA": schema}, no_file)


def test_tb_values_come_from_the_env_file_and_the_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'TB_URL=http://from-file:8080/\nTB_USERNAME="file-user"\n'
        "TB_PASSWORD=file-password\nPOSTGRES_PASSWORD=not-read\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in BASE_ENV.items() if not k.startswith("TB_")}
    env["TB_USERNAME"] = "env-user"
    settings = load_settings(env, env_file)
    assert settings.tb_url == "http://from-file:8080"
    assert settings.tb_username == "env-user"
    assert settings.tb_password == "file-password"


def test_the_dbt_token_variable_works_too(no_file):
    env = {k: v for k, v in BASE_ENV.items() if k != "DATABRICKS_TOKEN"}
    env["DBT_ENV_SECRET_DATABRICKS_TOKEN"] = "fake-dbt-token"
    assert load_settings(env, no_file).databricks_token == "fake-dbt-token"


def test_missing_settings_are_named(no_file):
    env = {"DATABRICKS_HOST": "dbc-test.cloud.databricks.com"}
    with pytest.raises(PublisherError) as exc:
        load_settings(env, no_file)
    for name in ("DATABRICKS_HTTP_PATH", "TB_URL", "TB_PASSWORD", "DATABRICKS_TOKEN"):
        assert name in str(exc.value)


def test_host_scheme_is_dropped(no_file):
    env = {**BASE_ENV, "DATABRICKS_HOST": "https://dbc-test.cloud.databricks.com/"}
    assert (
        load_settings(env, no_file).databricks_host == "dbc-test.cloud.databricks.com"
    )


def test_secrets_stay_out_of_repr(no_file):
    text = repr(load_settings(BASE_ENV, no_file))
    assert "fake-databricks-token" not in text
    assert "fake-tb-password" not in text


@pytest.mark.parametrize(
    ("value", "message"), [("two", "a number"), ("-1", "negative")]
)
def test_lookback_must_be_a_non_negative_number(no_file, value, message):
    with pytest.raises(PublisherError, match=message):
        load_settings({**BASE_ENV, "PUBLISH_LOOKBACK_HOURS": value}, no_file)


@pytest.mark.parametrize(
    ("env_name", "tokens_name"),
    [(".env", "devices.tokens.json"), (".env.cloud", "devices.cloud.tokens.json")],
)
def test_tokens_file_follows_the_env_file(tmp_path, env_name, tokens_name):
    env_file = tmp_path / env_name
    env_file.write_text("TB_URL=https://tb.test\n", encoding="utf-8")
    assert load_settings(BASE_ENV, env_file).tokens_file == tmp_path / tokens_name


def test_tb_tokens_file_wins(tmp_path):
    env = {**BASE_ENV, "TB_TOKENS_FILE": "/app/thingsboard/devices.tokens.json"}
    settings = load_settings(env, tmp_path / ".env.cloud")
    assert settings.tokens_file == Path("/app/thingsboard/devices.tokens.json")
