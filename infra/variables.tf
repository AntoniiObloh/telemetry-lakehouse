variable "suffix" {
  description = "Makes bucket names globally unique: telemetry-lakehouse-<suffix>."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$", var.suffix))
    error_message = "suffix must be 3-30 characters: lowercase letters, digits and hyphens, starting and ending with a letter or digit."
  }
}

variable "alert_email" {
  description = "Where AWS Budgets sends the 80% and 100% alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must look like an email address."
  }
}

variable "uc_master_role_arn" {
  description = "ARN of the Databricks Unity Catalog master role that may assume tl-uc-access."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:", var.uc_master_role_arn))
    error_message = "uc_master_role_arn must be an IAM ARN (arn:aws:iam::<12-digit account>:...)."
  }
}

variable "uc_external_id" {
  description = "External ID from the Databricks storage credential. Pass 1 uses the placeholder \"0000\"."
  type        = string
  default     = "0000"
  sensitive   = true
}

variable "data_region" {
  description = "AWS region of the raw bucket. Must match the Databricks metastore region (select current_metastore()): serverless egress control only reaches external locations in that region."
  type        = string
  default     = "us-east-2"
}

variable "self_assume" {
  description = "Let tl-uc-access assume itself (a Unity Catalog requirement). Turn on in pass 2, once the role exists."
  type        = bool
  default     = false
}

variable "tfstate_bucket" {
  description = "State bucket created by hand in step 4.1 (tl-tfstate-<suffix>, the bucket in backend.hcl). tl-github-plan reads the state and writes its lock file there."
  type        = string

  validation {
    condition     = can(regex("^tl-tfstate-[a-z0-9-]+$", var.tfstate_bucket))
    error_message = "tfstate_bucket must be the state bucket name, tl-tfstate-<suffix>."
  }
}

variable "github_repo" {
  description = "owner/name of the only repository whose pull_request runs may assume tl-github-plan."
  type        = string
  default     = "AntoniiObloh/telemetry-lakehouse"
}
