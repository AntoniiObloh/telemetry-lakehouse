"""thingsboard/dashboards: file names, and Fleet / Pipeline ops hang together."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TB_DIR = Path(__file__).resolve().parent.parent / "thingsboard"
DASHBOARDS = TB_DIR / "dashboards"

# dashboards.py imports its sibling provision.py, as it does when run as a script.
sys.path.insert(0, str(TB_DIR))
_spec = importlib.util.spec_from_file_location(
    "tb_dashboards", TB_DIR / "dashboards.py"
)
tb_dashboards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tb_dashboards)


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("Fleet", "Fleet.json"),
        ("Pipeline ops", "Pipeline ops.json"),
        ('Ops: a/b "c"?', "Ops_ a_b _c__.json"),
    ],
)
def test_file_name_is_the_title(title, name):
    assert tb_dashboards.file_name(title) == name


def load(name: str) -> dict:
    return json.loads((DASHBOARDS / name).read_text(encoding="utf-8"))["configuration"]


def widget_keys(config: dict) -> set[str]:
    keys = set()
    for widget in config["widgets"].values():
        for datasource in widget["config"].get("datasources") or []:
            keys |= {key["name"] for key in datasource.get("dataKeys") or []}
    return keys


@pytest.mark.parametrize("name", ["Fleet.json", "Pipeline ops.json"])
def test_every_reference_resolves(name):
    config = load(name)
    aliases = set(config["entityAliases"])
    for widget in config["widgets"].values():
        sources = list(widget["config"].get("datasources") or [])
        if "alarmSource" in widget["config"]:
            sources.append(widget["config"]["alarmSource"])
        for source in sources:
            assert source["entityAliasId"] in aliases, widget["config"]["title"]
        for actions in (widget["config"].get("actions") or {}).values():
            for action in actions:
                if action["type"] == "openDashboardState":
                    assert action["targetDashboardStateId"] in config["states"]
    for state in config["states"].values():
        for layout in state["layouts"].values():
            assert set(layout["widgets"]) <= set(config["widgets"])


def test_fleet_matches_the_task():
    config = load("Fleet.json")
    aliases = {a["alias"]: a["filter"] for a in config["entityAliases"].values()}
    assert aliases["All sensors"]["deviceTypes"] == ["sensor-th"]
    assert set(config["states"]) == {"default", "device"}
    assert {"latitude", "longitude", "site", "health_score", "battery_last"} <= (
        widget_keys(config)
    )
    assert {"temperature", "temp_avg_1h", "battery"} <= widget_keys(config)


def test_pipeline_ops_matches_the_task():
    config = load("Pipeline ops.json")
    (alias,) = config["entityAliases"].values()
    assert alias["filter"]["assetTypes"] == ["pipeline"]
    assert {"gold_lag_min", "rows_published"} <= widget_keys(config)


@pytest.mark.parametrize(
    ("env_name", "tokens_name"),
    [
        (".env", "devices.tokens.json"),
        (".env.cloud", "devices.cloud.tokens.json"),
        (".env.staging", "devices.staging.tokens.json"),
    ],
)
def test_provision_tokens_file_follows_the_env_file(env_name, tokens_name):
    provision = sys.modules["provision"]
    tokens = provision.tokens_file_for(TB_DIR / env_name)
    assert tokens == TB_DIR / tokens_name
    assert tokens.name.endswith(".tokens.json")  # .gitignore and the Read deny rule


def test_missing_env_file_fails_before_any_request(capsys):
    assert tb_dashboards.main(["export", "--env-file", str(TB_DIR / ".env.nope")]) == 1
    assert "not found" in capsys.readouterr().err
