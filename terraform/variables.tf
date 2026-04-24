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
  description = "Public URL embedded in invite emails. Update after ALB DNS is known."
  default     = "http://k8s-chrono-chrono-cff4d0ad1c-2036054473.us-east-1.elb.amazonaws.com/"
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
