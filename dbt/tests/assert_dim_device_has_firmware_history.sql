{{ config(severity='warn') }}

-- Schema drift: dim_device should keep the firmware history (SCD2). Warns for
-- every device whose snapshot has seen only one firmware value so far, e.g.
-- when the first snapshot ran after the drift had already happened.
WITH firmware_versions AS (
    SELECT
        device_id,
        count(DISTINCT coalesce(firmware, '<none>')) AS firmware_versions
    FROM {{ ref('dim_device') }}
    GROUP BY device_id
)

SELECT
    device_id,
    firmware_versions
FROM firmware_versions
WHERE firmware_versions < 2
