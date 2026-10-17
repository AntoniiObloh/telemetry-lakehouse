# GitHub Actions -> AWS without stored keys: the job gets an OIDC token from GitHub and
# exchanges it for short-lived credentials of tl-github-plan. Only pull_request runs of
# this one repository can assume the role, and it can only read: terraform plan, not apply.

locals {
  github_oidc_host = "token.actions.githubusercontent.com"
  plan_role_name   = "tl-github-plan"
  state_key        = "infra/terraform.tfstate" # versions.tf, backend "s3"
  state_bucket_arn = "arn:aws:s3:::${var.tfstate_bucket}"
  account_id       = data.aws_caller_identity.current.account_id
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.github_oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    # sub of a pull_request run; pushes, tags and other repositories get other values.
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:sub"
      values   = ["repo:${var.github_repo}:pull_request"]
    }
  }
}

resource "aws_iam_role" "github_plan" {
  name                 = local.plan_role_name
  assume_role_policy   = data.aws_iam_policy_document.github_plan_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_plan" {
  statement {
    sid       = "ReadState"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "ReadStateFile"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket_arn}/${local.state_key}"]
  }

  # use_lockfile: plan writes <key>.tflock while it runs and deletes it at the end.
  statement {
    sid       = "StateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/${local.state_key}.tflock"]
  }

  # Bucket configuration only; no object in the raw bucket is readable.
  statement {
    sid = "ReadRawBucketConfig"
    actions = [
      "s3:GetBucket*",
      "s3:GetLifecycleConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetAccelerateConfiguration",
      "s3:GetReplicationConfiguration",
    ]
    resources = [aws_s3_bucket.raw.arn]
  }

  statement {
    sid = "ReadProjectIam"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:GetUser",
      "iam:GetUserPolicy",
      "iam:ListUserPolicies",
      "iam:ListAttachedUserPolicies",
      "iam:GetOpenIDConnectProvider",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/tl-*",
      "arn:aws:iam::${local.account_id}:user/tl-*",
      aws_iam_openid_connect_provider.github.arn,
    ]
  }

  statement {
    sid       = "ReadBudget"
    actions   = ["budgets:ViewBudget", "budgets:ListTagsForResource"]
    resources = ["arn:aws:budgets::${local.account_id}:budget/${aws_budgets_budget.monthly.name}"]
  }
}

resource "aws_iam_role_policy" "github_plan" {
  name   = "read-project-and-state"
  role   = aws_iam_role.github_plan.id
  policy = data.aws_iam_policy_document.github_plan.json
}
