provider "aws" {
  region = "eu-central-1"

  default_tags {
    tags = {
      project = "telemetry-lakehouse"
    }
  }
}
