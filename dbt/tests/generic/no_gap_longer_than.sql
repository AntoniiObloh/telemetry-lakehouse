{#-
    Generic test: no silence longer than max_gap_minutes between consecutive
    values of a timestamp column, per partition (e.g. per device).
    Returns one row per gap that is too long.
-#}
{% test no_gap_longer_than(model, column_name, partition_by, max_gap_minutes) %}

WITH ordered_points AS (
    SELECT
        {{ partition_by }} AS partition_value,
        {{ column_name }} AS point_at,
        lag({{ column_name }}) OVER (
            PARTITION BY {{ partition_by }}
            ORDER BY {{ column_name }}
        ) AS previous_point_at
    FROM {{ model }}
),

gaps AS (
    SELECT
        partition_value,
        previous_point_at,
        point_at,
        (unix_millis(point_at) - unix_millis(previous_point_at))
        / 60000.0 AS gap_minutes
    FROM ordered_points
    WHERE previous_point_at IS NOT NULL
)

SELECT
    partition_value,
    previous_point_at,
    point_at,
    gap_minutes
FROM gaps
WHERE gap_minutes > {{ max_gap_minutes }}

{% endtest %}
