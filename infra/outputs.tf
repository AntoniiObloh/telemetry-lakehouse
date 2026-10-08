output "raw_bucket_name" {
  description = "Raw telemetry bucket; this is the generator's RAW_BUCKET."
  value       = aws_s3_bucket.raw.bucket
}

output "uc_role_arn" {
  description = "ARN of tl-uc-access, for the Databricks storage credential."
  value       = aws_iam_role.uc_access.arn
}

output "generator_user_name" {
  description = "IAM user for the generator; create its access key by hand."
  value       = aws_iam_user.generator.name
}
