{#
    dev and ci: every model and seed goes to target.schema (dbt_dev, dbt_ci_pr_<n>),
    ignoring custom schemas, so a PR build never touches silver or gold.
    Other targets: the model's custom schema as is (silver, gold), not dbt's default
    "<target.schema>_<custom>" concatenation; target.schema when none is set.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if target.name in ('dev', 'ci') or custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
