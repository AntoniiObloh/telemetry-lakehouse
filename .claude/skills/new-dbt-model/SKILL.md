---
name: new-dbt-model
description: Create a new dbt model with yml, tests and a build check. Use when asked to add or create a dbt model, mart or aggregate. Not for explaining or reviewing existing models.
---

Paths below are relative to the repo root. Run dbt and sqlfluff from PowerShell: Git Bash
rewrites DATABRICKS_HTTP_PATH and every connection fails with OpenSession 404.

1. Read `.claude/skills/new-dbt-model/references/models.md` to see existing models and pick
   the layer and name. Reuse an existing model through `ref()` instead of repeating its logic.
2. Write the SQL in `dbt/models/<layer>/` following `.claude/rules/dbt.md`. A new layer
   folder also needs its `+schema` / `+materialized` config in `dbt/dbt_project.yml`.
3. Write the yml entry in that folder's `_<layer>.yml`: model description, column
   descriptions, and tests (`unique` + `not_null` on the key at minimum;
   `dbt_utils.unique_combination_of_columns` for a composite key).
4. Run: `uv run dbt build -s <model> --project-dir dbt --profiles-dir dbt --target dev`
5. Run: `uv run sqlfluff lint dbt/models/<layer>/<model>.sql`
6. Append the model to `.claude/skills/new-dbt-model/references/models.md`
   (name, layer, grain, key, purpose; one table row).
7. Report: files created, build result, test results, anything left as warn.
