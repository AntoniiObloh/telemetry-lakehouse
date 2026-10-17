# 0006. GitHub OIDC, not stored AWS keys, in CI

Status: Accepted, 2026-10-17.

## Context

The `plan` job in `.github/workflows/ci.yml` runs `terraform plan` on every pull request and
comments a summary. It needs AWS access to read the state in `tl-tfstate-<suffix>` and the
project's resources. Stored access keys in GitHub secrets live until someone rotates them,
and a leaked key works from anywhere.

## Decision

`infra/github.tf` adds an IAM OIDC provider for `token.actions.githubusercontent.com` and a
role, `tl-github-plan`, that trusts only tokens with `aud = sts.amazonaws.com` and
`sub = repo:AntoniiObloh/telemetry-lakehouse:pull_request`. The job asks GitHub for an OIDC
token (`permissions: id-token: write`) and exchanges it with
`aws-actions/configure-aws-credentials` for credentials valid for at most an hour. The
role can only read: the state file, the configuration of the raw bucket (no objects), the
project's IAM entities (`tl-*`) and the budget; its only write is the state lock file.

## Alternatives

- **An IAM user with an access key in GitHub secrets.** Simplest, but a long-lived secret;
  scoping it to pull requests is impossible.
- **One role for plan and apply.** Apply in CI would need write access to IAM and S3;
  apply stays a human step (project rule), so CI gets a read-only role.
- **Trusting `repo:<repo>:*`.** Would also let pushes and other branches assume the role;
  more than plan on a PR needs.

## Consequences

- No AWS key exists in GitHub; `AWS_PLAN_ROLE_ARN` is an identifier, not a secret.
- A job on push, on a fork or in another repository gets a different `sub` and is refused.
- The permission list is minimal by hand: a new resource type in `infra/` will fail the
  plan with `AccessDenied` until its read action is added to the role (a `tf-change`).
- Plan still needs the values of `terraform.tfvars` in CI, as `TF_VAR_*` secrets.
- An account allows one OIDC provider per URL: if another project already created it, it
  must be imported instead of created.
- Databricks access in CI (sqlfluff, dbt-ci, deploy) still uses a token secret; this record
  covers AWS only.
