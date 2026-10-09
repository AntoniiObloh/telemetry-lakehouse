-- Latest known firmware per device; null for devices that never reported one.
WITH firmware_readings AS (
    SELECT
        device_id,
        firmware,
        event_at
    FROM {{ ref('stg_telemetry') }}
    WHERE firmware IS NOT NULL
),

latest_firmware AS (
    SELECT
        device_id,
        firmware,
        event_at AS firmware_seen_at
    FROM firmware_readings
    QUALIFY row_number() OVER (
        PARTITION BY device_id
        ORDER BY event_at DESC
    ) = 1
)

SELECT
    devices.device_id,
    latest_firmware.firmware,
    latest_firmware.firmware_seen_at
FROM {{ ref('devices') }} AS devices
LEFT JOIN latest_firmware
    ON devices.device_id = latest_firmware.device_id
