"""Live mode without a network: payload shape and token loading (fake tokens only)."""

import json

import pytest

from generator import GeneratorError
from generator.live import build_payload, load_tokens

FAKE_TOKEN = "FAKE-TOKEN-do-not-leak"
TS = 1791158400000


def write_tokens(path, names):
    path.write_text(json.dumps({name: FAKE_TOKEN for name in names}), encoding="utf-8")


def test_payload_has_only_the_three_fields(devices):
    payload = build_payload(devices[0], TS, seed=42)
    assert set(payload) == {"ts", "values"}
    assert payload["ts"] == TS
    assert set(payload["values"]) == {"temperature", "humidity", "battery"}


def test_payload_is_deterministic_and_clean(devices):
    assert build_payload(devices[0], TS, 42) == build_payload(devices[0], TS, 42)
    assert 10 <= build_payload(devices[0], TS, 42)["values"]["temperature"] <= 35


def test_load_tokens_returns_a_token_per_device(devices, tmp_path):
    path = tmp_path / "devices.tokens.json"
    write_tokens(path, [d.device_id for d in devices])
    tokens = load_tokens(path, devices)
    assert list(tokens) == [d.device_id for d in devices]


def test_missing_token_names_the_device_but_not_the_value(devices, tmp_path):
    path = tmp_path / "devices.tokens.json"
    write_tokens(path, [d.device_id for d in devices[:-1]])
    with pytest.raises(GeneratorError) as excinfo:
        load_tokens(path, devices)
    assert devices[-1].device_id in str(excinfo.value)
    assert FAKE_TOKEN not in str(excinfo.value)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_bad_tokens_file_is_reported_without_content(devices, tmp_path, content):
    path = tmp_path / "devices.tokens.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(GeneratorError) as excinfo:
        load_tokens(path, devices)
    assert content not in str(excinfo.value)


def test_missing_tokens_file_points_to_provisioning(devices, tmp_path):
    with pytest.raises(GeneratorError, match="make tb-provision"):
        load_tokens(tmp_path / "devices.tokens.json", devices)
