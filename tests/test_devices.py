"""devices.csv loading and validation."""

import pytest

from generator import GeneratorError
from generator.devices import load_devices

HEADER = "device_id,site,model,latitude,longitude\n"


def load(tmp_path, body, header=HEADER):
    path = tmp_path / "devices.csv"
    path.write_text(header + body, encoding="utf-8")
    return load_devices(path)


def test_real_csv_has_five_devices(devices):
    assert [d.device_id for d in devices] == [f"sensor-0{i}" for i in range(1, 6)]


def test_valid_file(tmp_path):
    (device,) = load(tmp_path, "s1,site-a,TH-100,50.1,30.2\n")
    assert (device.device_id, device.latitude, device.longitude) == ("s1", 50.1, 30.2)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (",site-a,TH-100,50,30\n", r"devices.csv:2: empty device_id"),
        ("s1,,TH-100,50,30\n", "empty site"),
        ("s1,site-a,,50,30\n", "empty model"),
        ("s1,site-a,TH-100,abc,30\n", "bad latitude/longitude"),
        ("s1,site-a,TH-100,95,30\n", "latitude out of range"),
        ("s1,site-a,TH-100,50,200\n", "longitude out of range"),
        ("s1,site-a,TH-100,50,30\ns1,site-a,TH-100,50,30\n", "duplicate device_id"),
        ("", "no devices"),
    ],
)
def test_invalid_rows(tmp_path, body, message):
    with pytest.raises(GeneratorError, match=message):
        load(tmp_path, body)


def test_missing_columns_and_file(tmp_path):
    with pytest.raises(GeneratorError, match="missing columns: model"):
        load(tmp_path, "", header="device_id,site,latitude,longitude\n")
    with pytest.raises(GeneratorError, match="not found"):
        load_devices(tmp_path / "nope.csv")
