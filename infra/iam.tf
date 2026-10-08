data "aws_caller_identity" "current" {}

locals {
  uc_role_name = "tl-uc-access"
  # Built from the account id instead of aws_iam_role.uc_access.arn: a role that
  # references itself would be a dependency cycle.
  uc_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.uc_role_name}"
}

# --- Role for the Unity Catalog storage credential ---------------------------------------

data "aws_iam_policy_document" "uc_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.uc_master_role_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.uc_external_id]
    }
  }

  # AWS rejects a trust policy that names a principal that does not exist yet, so the
  # self-assume statement only goes in during pass 2, when the role already exists.
  dynamic "statement" {
    for_each = var.self_assume ? [1] : []

    content {
      actions = ["sts:AssumeRole"]

      principals {
        type        = "AWS"
        identifiers = [local.uc_role_arn]
      }
    }
  }
}

resource "aws_iam_role" "uc_access" {
  name               = local.uc_role_name
  assume_role_policy = data.aws_iam_policy_document.uc_trust.json
}

data "aws_iam_policy_document" "uc_access" {
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.raw.arn]
  }

  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.raw.arn}/*"]
  }

  statement {
    actions   = ["sts:AssumeRole"]
    resources = [local.uc_role_arn]
  }
}

resource "aws_iam_role_policy" "uc_access" {
  name   = "uc-access-raw-bucket"
  role   = aws_iam_role.uc_access.id
  policy = data.aws_iam_policy_document.uc_access.json
}

# --- User for the generator --------------------------------------------------------------
# The access key is created by hand, never in Terraform: a key resource would put the
# secret into the state file.

resource "aws_iam_user" "generator" {
  name = "tl-generator"
}

data "aws_iam_policy_document" "generator" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
}

resource "aws_iam_user_policy" "generator" {
  name   = "put-raw-objects"
  user   = aws_iam_user.generator.name
  policy = data.aws_iam_policy_document.generator.json
}
