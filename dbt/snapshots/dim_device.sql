{% snapshot dim_device %}

    {#-
        SCD2 device dimension: device attributes from the seed plus the latest
        firmware. A change in any check column closes the current row
        (dbt_valid_to) and opens a new one, so the firmware history is kept.
        Changes are recorded at snapshot run time.
    -#}
    {{
        config(
            unique_key='device_id',
            strategy='check',
            check_cols=['site', 'model', 'latitude', 'longitude', 'firmware']
        )
    }}

    SELECT
        devices.device_id,
        devices.site,
        devices.model,
        devices.latitude,
        devices.longitude,
        device_firmware.firmware
    FROM {{ ref('devices') }} AS devices
    LEFT JOIN {{ ref('int_device_firmware_latest') }} AS device_firmware
        ON devices.device_id = device_firmware.device_id

{% endsnapshot %}
