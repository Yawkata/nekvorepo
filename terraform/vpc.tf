# VPC Flow Logs log group — only created when flow logs are enabled.
# DEV:  flow logs disabled (cost saving). Uncomment enable_flow_log block
#       in the VPC module below and un-comment this resource for PROD.
#
# resource "aws_cloudwatch_log_group" "flow_log" {
#   name              = "/aws/vpc-flow-logs/${var.project_name}"
#   retention_in_days = 30
# }

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.project_name}-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["us-east-1a", "us-east-1b", "us-east-1c"]
  public_subnets  = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]    # Bastion sits here
  private_subnets = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"] # Future EKS nodes

  # DEV: Use public subnets for DB so we don't need a NAT Gateway to reach it
  # PROD: database_subnets = ["10.0.20.0/24", "10.0.21.0/24", "10.0.22.0/24"]
  database_subnets = ["10.0.20.0/24", "10.0.21.0/24", "10.0.22.0/24"]

  # 1. This creates a separate route table for the DB subnets
  create_database_subnet_route_table = true

  # 2. This adds the 0.0.0.0/0 -> Internet Gateway route to that table
  create_database_internet_gateway_route = true

  create_database_subnet_group = true
  enable_dns_hostnames         = true
  enable_dns_support           = true

  # EKS nodes live in private subnets and need egress to ECR / EKS API /
  # STS / S3. A single NAT Gateway (~$32/mo + data) is the pragmatic
  # staging default. For prod, flip single_nat_gateway=false to get one
  # NAT per AZ (removes the single-AZ failure domain, triples the cost).
  enable_nat_gateway = true
  single_nat_gateway = true

  # AWS Load Balancer Controller auto-discovers subnets by these tags.
  # Public subnets host internet-facing ALBs; private subnets host
  # internal ALBs and are used by EKS as node subnets.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }

  enable_flow_log = false
  # enable_flow_log                      = true
  # create_flow_log_cloudwatch_iam_role  = true
  # create_flow_log_cloudwatch_log_group = true
  # flow_log_max_aggregation_interval    = 60
}