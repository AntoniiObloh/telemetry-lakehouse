{#
    Drops the PR schema after a CI build (CASCADE: every table and view in it).
    Refuses anything but target ci with a dbt_ci_pr_* schema, so it cannot drop dev or prod.
    dbt run-operation drop_ci_schema --target ci
#}
{% macro drop_ci_schema() %}
    {%- if target.name != 'ci' or not target.schema.startswith('dbt_ci_pr_') -%}
        {{ exceptions.raise_compiler_error(
            "drop_ci_schema only drops dbt_ci_pr_* schemas on target ci, got "
            ~ target.name ~ "." ~ target.schema
        ) }}
    {%- endif -%}
    {% do adapter.drop_schema(api.Relation.create(database=target.database, schema=target.schema)) %}
    {{ log("dropped schema " ~ target.database ~ "." ~ target.schema, info=True) }}
{% endmacro %}
