---
paths:
  - "dbt/**"
---

# dbt project rules

- Naming: `stg_<entity>`, `int_<entity>_<verb>`, `fct_<grain>`, `dim_<entity>`,
  `mart_<topic>`, `srv_<consumer>`.
- Every model has a yml entry: description, `unique` + `not_null` on its key
  (`dbt_utils.unique_combination_of_columns` for a composite key), column descriptions.
- Sources only via `source()`, models only via `ref()`; no hard-coded catalog or schema.
  Schemas come from `macros/generate_schema_name.sql`: target dev builds into `dbt_dev`,
  other targets into the custom schema (`silver`, `gold`).
- The profile must set `catalog`, otherwise Databricks falls back to `hive_metastore`.
- SQL: UPPERCASE keywords, literals and data types (enforced by `.sqlfluff`), function
  names as they are; explicit column lists; CTEs, one step each; timestamps UTC with
  suffix `_at`.
- Run only with target dev: `uv run dbt build -s <model> --project-dir dbt --profiles-dir dbt`.
  Prod runs only from CI.
- Run dbt and sqlfluff from PowerShell: Git Bash rewrites `DATABRICKS_HTTP_PATH`
  (`/sql/...` becomes `C:/Program Files/Git/sql/...`) and every connection fails with
  OpenSession 404.
- Lint before done: `make dbt-lint` (models, snapshots, tests).
- Tests for defects the generator injects on purpose (gaps, outliers, firmware history)
  use `severity: warn`; every other test fails the build.
- `srv_thingsboard` has an enforced contract and an `accepted_values` test on `key`:
  changing it means changing the publisher and the ThingsBoard dashboards too.
