"""Synthetic IoT telemetry generator: batch JSONL files with data defects, live MQTT stream."""


class GeneratorError(Exception):
    """Expected failure with a message that is safe to print (never contains secrets)."""
