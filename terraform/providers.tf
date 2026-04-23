terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Route53 DNSSEC key-signing keys MUST live in us-east-1 regardless of the
# cluster region. CloudFront + ACM for CloudFront also require us-east-1.
# We declare an aliased provider so the constraint is explicit; today it is
# the same region as the default provider, but this keeps us portable.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}