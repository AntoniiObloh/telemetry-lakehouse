"""Publisher: srv_thingsboard (gold) -> ThingsBoard telemetry. One run is described in run.py."""


class PublisherError(Exception):
    """Expected failure with a message that is safe to print (no tokens, no passwords)."""
