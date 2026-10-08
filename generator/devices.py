"""Device list: reads and validates generator/devices.csv."""

import csv
from dataclasses import dataclass
from pathlib import Path

from generator import GeneratorError

DEVICES_CSV = Path(__file__).resolve().parent / "devices.csv"
COLUMNS = ("device_id", "site", "model", "latitude", "longitude")


@dataclass(frozen=True)
class Device:
    device_id: str
    site: str
    model: str
    latitude: float
    longitude: float


def load_devices(path: Path = DEVICES_CSV) -> list[Device]:
    """Read the CSV and validate it; returns devices in file order."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise GeneratorError(
                    f"{path.name}: missing columns: {', '.join(missing)}"
                )
            rows = list(reader)
    except FileNotFoundError as exc:
        raise GeneratorError(f"{path.name} not found") from exc

    devices = []
    for line_no, row in enumerate(rows, start=2):
        devices.append(_parse_row(path.name, line_no, row))
    if not devices:
        raise GeneratorError(f"{path.name}: no devices")
    ids = [d.device_id for d in devices]
    if len(set(ids)) != len(ids):
        raise GeneratorError(f"{path.name}: duplicate device_id values")
    return devices


def _parse_row(file_name: str, line_no: int, row: dict) -> Device:
    where = f"{file_name}:{line_no}"
    values = {c: (row.get(c) or "").strip() for c in COLUMNS}
    for column in ("device_id", "site", "model"):
        if not values[column]:
            raise GeneratorError(f"{where}: empty {column}")
    try:
        latitude = float(values["latitude"])
        longitude = float(values["longitude"])
    except ValueError as exc:
        raise GeneratorError(f"{where}: bad latitude/longitude") from exc
    if not -90 <= latitude <= 90:
        raise GeneratorError(f"{where}: latitude out of range -90..90")
    if not -180 <= longitude <= 180:
        raise GeneratorError(f"{where}: longitude out of range -180..180")
    return Device(
        device_id=values["device_id"],
        site=values["site"],
        model=values["model"],
        latitude=latitude,
        longitude=longitude,
    )
