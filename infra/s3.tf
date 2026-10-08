# The bucket lives in var.data_region (the Databricks metastore region), not in the
# provider region; AWS provider 6.x allows a per-resource region.
resource "aws_s3_bucket" "raw" {
  region = var.data_region
  bucket = "telemetry-lakehouse-${var.suffix}"
}

resource "aws_s3_bucket_public_access_block" "raw" {
  region = var.data_region
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  region = var.data_region
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  region = var.data_region
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "expire-raw-after-30-days"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = 30
    }
  }
}
