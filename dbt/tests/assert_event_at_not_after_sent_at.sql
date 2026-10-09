-- Late events: a late event has an old event_at and a new sent_at, but no event
-- can be sent before it was measured. Fails on any row with event_at > sent_at.
SELECT
    event_id,
    device_id,
    event_at,
    sent_at
FROM {{ ref('stg_telemetry') }}
WHERE event_at > sent_at
