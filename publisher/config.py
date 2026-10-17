"""Settings from the environment; locally TB_* may also come from thingsboard/.env, or from
another env file (thingsboard/.env.cloud for ThingsBoard Cloud).

Environment variables take precedence over the file. Secrets stay out of repr().
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from publisher import PublisherError

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "thingsboard" / ".env"
DEFAULT_TOKENS_FILE = ROOT / "thingsboard" / "devices.tokens.json"
TB_KEYS = ("TB_URL", "TB_USERNAME", "TB_PASSWORD")
TABLE = "srv_thingsboard"
DEFAULT_CATALOG = "lakehouse"
DEFAULT_SCHEMA = "dbt_dev"
DEFAULT_LOOKBACK_HOURS = 48.0
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Settings:
    gold_catalog: str
    gold_schema: str
    databricks_host: str
    databricks_http_path: str
    tb_url: str
    tb_username: str
    tokens_file: Path
    lookback_hours: float
    databricks_token: str = field(repr=False)
    tb_password: str = field(repr=False)

    @property
    def gold_table(self) -> str:
        """Backtick-quoted catalog.schema.table; both parts are checked identifiers."""
        return f"`{self.gold_catalog}`.`{self.gold_schema}`.`{TABLE}`"


def tokens_file_for(env_file: Path) -> Path:
    """The tokens file provision.py writes for that env file.

    .env -> devices.tokens.json, .env.cloud -> devices.cloud.tokens.json.
    """
    instance = (
        env_file.name.removeprefix(".env.") if env_file.name.startswith(".env.") else ""
    )
    name = f"devices.{instance}.tokens.json" if instance else "devices.tokens.json"
    return env_file.parent / name


def read_env_file(path: Path) -> dict[str, str]:
    """TB_* from KEY=VALUE lines; every other key in the file is ignored."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in TB_KEYS:
            values[key] = value
    return values


def identifier(name: str, value: str) -> str:
    """Catalog and schema go into the SQL text, so only plain identifiers pass."""
    if not IDENTIFIER.fullmatch(value):
        raise PublisherError(f"{name} must be a plain identifier, got '{value}'")
    return value


def load_settings(
    environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> Settings:
    environ = os.environ if environ is None else environ
    env_file = ENV_FILE if env_file is None else env_file
    values = read_env_file(env_file)
    values.update({key: value for key, value in environ.items() if value})
    # The dbt profile's variable works too, so one token serves dbt and the publisher.
    token = values.get("DATABRICKS_TOKEN") or values.get(
        "DBT_ENV_SECRET_DATABRICKS_TOKEN"
    )
    missing = [
        name
        for name in ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", *TB_KEYS)
        if not values.get(name)
    ]
    if not token:
        missing.append("DATABRICKS_TOKEN")
    if missing:
        raise PublisherError(
            f"missing settings: {', '.join(missing)} "
            "(environment; TB_* may also come from thingsboard/.env)"
        )
    try:
        lookback_hours = float(
            values.get("PUBLISH_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS)
        )
    except ValueError:
        raise PublisherError("PUBLISH_LOOKBACK_HOURS must be a number") from None
    if lookback_hours < 0:
        raise PublisherError("PUBLISH_LOOKBACK_HOURS must not be negative")
    return Settings(
        gold_catalog=identifier(
            "GOLD_CATALOG", values.get("GOLD_CATALOG", DEFAULT_CATALOG)
        ),
        gold_schema=identifier(
            "GOLD_SCHEMA", values.get("GOLD_SCHEMA", DEFAULT_SCHEMA)
        ),
        databricks_host=values["DATABRICKS_HOST"].removeprefix("https://").rstrip("/"),
        databricks_http_path=values["DATABRICKS_HTTP_PATH"],
        tb_url=values["TB_URL"].rstrip("/"),
        tb_username=values["TB_USERNAME"],
        tokens_file=Path(values.get("TB_TOKENS_FILE") or tokens_file_for(env_file)),
        lookback_hours=lookback_hours,
        databricks_token=token,
        tb_password=values["TB_PASSWORD"],
    )
