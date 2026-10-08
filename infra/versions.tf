terraform {
  required_version = "~> 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # The bucket name cannot use variables, so it is passed at init time:
  #   terraform -chdir=infra init -backend-config=backend.hcl
  backend "s3" {
    key          = "infra/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}
