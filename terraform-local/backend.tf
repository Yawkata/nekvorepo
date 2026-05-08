terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "chrono-vcs-terraform-state"
    key          = "local/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
