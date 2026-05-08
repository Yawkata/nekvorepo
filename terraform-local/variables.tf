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