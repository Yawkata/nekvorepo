###############################################################################
# VPC — 3 AZs, 3 NAT gateways (no SPOF), private/public/DB subnets, flow logs
###############################################################################

locals {
  azs = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.project_name}-vpc"
  cidr = "10.0.0.0/16"

  azs              = local.azs
  public_subnets   = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  private_subnets  = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"]
  database_subnets = ["10.0.20.0/24", "10.0.21.0/24", "10.0.22.0/24"]

  # Database subnets are truly private — no route to the IGW.
  create_database_subnet_route_table     = true
  create_database_internet_gateway_route = false
  create_database_subnet_group           = true

  enable_dns_hostnames = true
  enable_dns_support   = true

  # HA egress for private subnets — one NAT per AZ eliminates the cross-AZ SPOF.
  enable_nat_gateway     = true
  single_nat_gateway     = false
  one_nat_gateway_per_az = true

  # VPC Flow Logs → CloudWatch (30 d retention). Required for SEC in Well-Architected.
  enable_flow_log                                 = true
  create_flow_log_cloudwatch_iam_role             = true
  create_flow_log_cloudwatch_log_group            = true
  flow_log_max_aggregation_interval               = 60
  flow_log_cloudwatch_log_group_retention_in_days = 30

  # Tags required by the AWS Load Balancer Controller and Cluster Autoscaler.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}