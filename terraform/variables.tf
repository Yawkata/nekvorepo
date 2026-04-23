variable "aws_region" {
  default = "us-east-1"
}

variable "project_name" {
  type        = string
  description = "The name of the project, used as a prefix for resource naming"
  default     = "chrono-vcs"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "allowed_ips" {
  type        = list(string)
  description = "List of developer IPs"
  default     = []
}

variable "ses_sender_email" {
  type        = string
  description = "Verified SES sender address (e.g. noreply@example.com). AWS sends a confirmation email to this address on first apply."
  default     = "f.ermenkov@gmail.com"
}

variable "frontend_url" {
  type        = string
  description = "Public URL of the frontend application, used to build invite accept links in emails (e.g. https://app.example.com). No trailing slash."
  default     = "http://localhost:3000"
}

variable "domain_name" {
  type        = string
  description = "Apex domain you intend to register (e.g. chronovcs.com). The Route53 hosted zone, DNSSEC, and ACM certificate are provisioned for this domain and its wildcard subdomain. No trailing dot. Leave blank before a domain is chosen — DNS/ACM resources will be skipped."
  default     = ""
}

variable "environment" {
  type        = string
  description = "Deployment environment (staging or prod). Used as a tag on new resources."
  default     = "staging"
}

variable "await_acm_validation" {
  type        = bool
  description = "If true, Terraform blocks apply until ACM finishes DNS validation. Requires the domain's NS records to be delegated at the registrar first. Leave false until the domain is registered and delegated, otherwise apply will hang for 60 minutes and fail."
  default     = false
}