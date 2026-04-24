variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type        = string
  description = "Prefix for every resource name."
  default     = "chrono-vcs"
}

variable "environment" {
  type        = string
  description = "Deployment environment tag (dev/staging/prod)."
  default     = "prod"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "allowed_ips" {
  type        = list(string)
  description = "CIDRs allowed to reach the EKS public API endpoint. Keep tight."
  default     = []
}

variable "ses_sender_email" {
  type        = string
  description = "Verified SES sender address. AWS emails a confirmation link on first apply."
  default     = "f.ermenkov@gmail.com"
}

variable "frontend_url" {
  type        = string
  description = "Public URL embedded in invite emails."
  default     = "https://chronovcs.com"
}

variable "domain_name" {
  type        = string
  description = "Apex domain for the public site. A Route 53 public zone is created for this name."
  default     = "chronovcs.com"
}

variable "create_alb_alias_records" {
  type        = bool
  description = <<-EOT
    Create Route 53 A-alias records pointing chronovcs.com + www at the ALB.
    Requires the `chrono` Ingress (and therefore its ALB) to already exist.
    Leave false on first apply; flip to true on a subsequent apply once the
    Ingress has reconciled.
  EOT
  default     = true
}

variable "cluster_version" {
  type        = string
  description = "EKS control-plane version."
  default     = "1.31"
}

variable "node_instance_types" {
  type        = list(string)
  description = "Instance types for the managed node group. Multiple types help Spot stability."
  default     = ["t3.large", "t3a.large", "m5.large"]
}

variable "node_group_min_size" {
  type    = number
  default = 3
}

variable "node_group_max_size" {
  type    = number
  default = 12
}

variable "node_group_desired_size" {
  type    = number
  default = 3
}
