"""Clean sensor signal: temperature, humidity, battery for a device at a given time.

Everything here is a pure function of (seed, device, ts). Each event gets its own RNG seeded
from "<seed>:<device_id>:<ts>", so a reading never depends on what was generated before it.
Only Random.random() is used: it is the one method Python keeps reproducible across versions.
"""

import math
import random
import uuid
import zlib
from dataclasses import dataclass

from generator.devices import Device

# Fixed namespace so event_id values are stable across runs and machines.
EVENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "telemetry-lakehouse/generator")

# Draws taken from the event RNG by reading(). Callers draw defect decisions after these,
# so the count must stay fixed or every later draw (outlier, late, duplicate) would shift.
READING_DRAWS = 3

MS_PER_HOUR = 3_600_000
MS_PER_DAY = 24 * MS_PER_HOUR

BASE_TEMPERATURE = {"TH-100": 21.0, "TH-200": 23.0}
DEFAULT_BASE_TEMPERATURE = 22.0
DAILY_AMPLITUDE = 3.0  # degrees C; the daily peak is at 15:00 UTC
NOISE_SIGMA = 0.3
HUMIDITY_BASE = 50.0
HUMIDITY_PER_DEGREE = -2.0  # warmer air reads drier
HUMIDITY_NOISE = 1.5  # uniform +-
HUMIDITY_RANGE = (20.0, 90.0)
BATTERY_CYCLE_DAYS = 30  # drains from 100 to 21, then the battery is "replaced"
BATTERY_DRAIN = 80


@dataclass(frozen=True)
class Reading:
    temperature: float
    humidity: float
    battery: int


def event_key(seed: int, device_id: str, ts_ms: int) -> str:
    return f"{seed}:{device_id}:{ts_ms}"


def event_rng(seed: int, device_id: str, ts_ms: int) -> random.Random:
    """Independent RNG for one event; string seeds are hashed, so this is stable."""
    return random.Random(event_key(seed, device_id, ts_ms))


def event_id(seed: int, device_id: str, ts_ms: int) -> str:
    """Deterministic UUID for one event; a duplicate is a copy and keeps the same id."""
    return str(uuid.uuid5(EVENT_NAMESPACE, event_key(seed, device_id, ts_ms)))


def reading(device: Device, ts_ms: int, rng: random.Random) -> Reading:
    """Clean reading at ts_ms. Consumes exactly READING_DRAWS values from rng."""
    # Box-Muller from two uniform draws: gaussian noise without random.gauss().
    u1, u2 = rng.random(), rng.random()
    noise = (
        NOISE_SIGMA
        * math.sqrt(-2.0 * math.log(1.0 - u1))
        * math.cos(2.0 * math.pi * u2)
    )
    humidity_noise = (rng.random() * 2.0 - 1.0) * HUMIDITY_NOISE

    base = BASE_TEMPERATURE.get(device.model, DEFAULT_BASE_TEMPERATURE)
    hour = (ts_ms % MS_PER_DAY) / MS_PER_HOUR
    daily = DAILY_AMPLITUDE * math.sin(2.0 * math.pi * (hour - 9.0) / 24.0)
    temperature = base + daily + noise

    humidity = (
        HUMIDITY_BASE + HUMIDITY_PER_DEGREE * (temperature - base) + humidity_noise
    )
    low, high = HUMIDITY_RANGE
    humidity = min(max(humidity, low), high)

    return Reading(
        temperature=round(temperature, 2),
        humidity=round(humidity, 1),
        battery=battery_level(device.device_id, ts_ms),
    )


def battery_level(device_id: str, ts_ms: int) -> int:
    """Slow linear drain over a cycle; a function of time only, not of any state.

    Each device gets i
uv run pytest generatorts own phase, so the five batteries do not all reset together.
    """
    phase_days = zlib.crc32(device_id.encode("utf-8")) % BATTERY_CYCLE_DAYS
    cycle_ms = BATTERY_CYCLE_DAYS * MS_PER_DAY
    position = (ts_ms + phase_days * MS_PER_DAY) % cycle_ms
    return 100 - position * BATTERY_DRAIN // cycle_ms
