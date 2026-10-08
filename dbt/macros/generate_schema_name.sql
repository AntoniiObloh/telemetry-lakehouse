{#
    dev: every model and seed goes to target.schema (dbt_dev), ignoring custom schemas.
    Other targets: the model's custom schema as is (silver, gold), not dbt's default
    "<target.schema>_<custom>" concatenation; target.schema when none is set.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if target.name == 'dev' or custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
