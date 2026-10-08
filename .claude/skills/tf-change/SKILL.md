---
name: tf-change
description: Prepare and summarize a Terraform change in infra/. Use when asked to change, add or review AWS infrastructure. Never applies.
---

1. Run `terraform -chdir=infra fmt` and `terraform -chdir=infra validate`.
   If validate says the directory is not initialized, ask the user to run
   `terraform -chdir=infra init`; do not run it yourself.
2. Run `terraform -chdir=infra plan -out=tf.plan`.
   Run it in Bash too: PowerShell 5.1 splits `-out=tf.plan` at the dot and plan fails with
   "Too many command line arguments". If plan exits non-zero, the plan file is partial:
   delete `infra/tf.plan`, report the error and stop (skip step 3).
3. Summarize only addresses and actions:
   `terraform -chdir=infra show -json tf.plan | jq -r '.resource_changes[] | "\(.change.actions|join(",")) \(.address)"'`
   Run it in Bash, not PowerShell 5.1 (it strips the inner double quotes). Lines marked
   `no-op` are unchanged; leave them out of the report.
4. Report: what changes, what is destroyed or replaced (highlight it), cost or security impact.
   A replace shows as `delete,create` (or `create,delete`); a plain `delete` is a destroy.
5. Never run `terraform apply` or `destroy`. Tell the user the exact apply command instead.
