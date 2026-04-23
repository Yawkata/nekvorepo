# ---------------------------------------------------------------------------
# EKS — the Kubernetes control plane.
#
# EKS is "Kubernetes as a managed service." AWS runs the three control-plane
# nodes (the apiserver, scheduler, controller-manager, and etcd) across three
# AZs, patches them, and backs up etcd. We only own the "data plane":
# the worker nodes (EC2) where our pods actually run.
#
# Why an EKS-first architecture for this project:
#   - Three microservices + a frontend + a migrations Job all want to share
#     infra primitives (networking, secrets, autoscaling, rolling updates,
#     health checks). Kubernetes was designed for exactly this shape.
#   - Spec says Karpenter, HPA, and KEDA — those ship as k8s controllers.
#   - We keep portability: the same manifests run in kind/minikube locally.
# ---------------------------------------------------------------------------

locals {
  cluster_name = "${var.project_name}-${var.environment}"
}

# ---------------------------------------------------------------------------
# KMS key — envelope encryption for Kubernetes Secrets.
#
# By default, EKS stores Secret objects in etcd encrypted with an AWS-managed
# key. Envelope encryption with a customer-managed KMS key adds a second
# layer: YOU own the key, you can audit every decrypt call in CloudTrail,
# and you can revoke access instantly by disabling the key. 2026 standard
# for any cluster touching production data.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "eks_secrets" {
  description             = "Envelope-encryption key for EKS secrets (${local.cluster_name})"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_kms_alias" "eks_secrets" {
  name          = "alias/${local.cluster_name}-eks-secrets"
  target_key_id = aws_kms_key.eks_secrets.key_id
}

# ---------------------------------------------------------------------------
# Control-plane IAM role.
#
# EKS itself (not your pods) assumes this role to manage ENIs, CloudWatch
# log streams, and the worker node lifecycle. The AmazonEKSClusterPolicy
# AWS-managed policy is the minimum-required permission set; we do not
# attach AmazonEKSVPCResourceController because we don't use SecurityGroupsForPods.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "eks_cluster" {
  name = "${local.cluster_name}-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ---------------------------------------------------------------------------
# Control-plane log group.
#
# Pre-create with finite retention so logs don't accumulate forever at
# $0.50/GB/month. Retention: 30 days (Well-Architected: keep an auditable
# window, expire by policy). If EKS creates this group implicitly, its
# default retention is "Never Expire" — a cost footgun.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "eks_cluster" {
  name              = "/aws/eks/${local.cluster_name}/cluster"
  retention_in_days = 30

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# The cluster itself.
# ---------------------------------------------------------------------------

resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  version  = "1.31"
  role_arn = aws_iam_role.eks_cluster.arn

  # 2026 standard auth mode. "API" disables the legacy aws-auth ConfigMap
  # entirely — every kubectl user is granted access via an aws_eks_access_entry
  # resource (IAM-native, auditable, no in-cluster YAML to drift).
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  vpc_config {
    subnet_ids              = module.vpc.private_subnets
    endpoint_private_access = true
    endpoint_public_access  = true
    # Public endpoint is IAM-authenticated via Access Entries; leaving it
    # open to 0.0.0.0/0 is AWS's own default and is acceptable here because
    # CI runners (GitHub Actions) have dynamic egress IPs. Lock down via
    # var.eks_public_access_cidrs only if you move CI to a fixed-IP runner
    # or a VPC-resident self-hosted runner. Note: restricting this WILL
    # break the in-cluster Helm provider from GitHub-hosted runners.
    public_access_cidrs = length(var.eks_public_access_cidrs) > 0 ? var.eks_public_access_cidrs : ["0.0.0.0/0"]
  }

  encryption_config {
    provider {
      key_arn = aws_kms_key.eks_secrets.arn
    }
    resources = ["secrets"]
  }

  # Ship every control-plane log type to CloudWatch. Audit logs in particular
  # are required for any compliance framework (SOC2 / ISO 27001).
  enabled_cluster_log_types = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler",
  ]

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_cloudwatch_log_group.eks_cluster,
  ]
}

# ---------------------------------------------------------------------------
# Access entry for the Terraform caller.
#
# With authentication_mode=API, nobody can kubectl into the cluster until
# their IAM identity is mapped via an access entry.
#
# Subtlety: when Terraform runs in GitHub Actions via OIDC, the caller is
# an *assumed-role session* — its ARN looks like
#   arn:aws:sts::<acct>:assumed-role/GitHubActions-OIDC-Role/<session-name>
# Access entries reject session ARNs; they require the underlying IAM role
# or user ARN. aws_iam_session_context.issuer_arn strips the session suffix
# and gives us the role ARN. This works transparently for local IAM users
# too (issuer_arn == caller ARN in that case).
# ---------------------------------------------------------------------------

data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

resource "aws_eks_access_entry" "admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = data.aws_iam_session_context.current.issuer_arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = data.aws_iam_session_context.current.issuer_arn
  # EKS access policies live in their own ARN namespace (arn:aws:eks:...),
  # not the IAM policy namespace. Using an IAM ARN here returns
  # InvalidParameterException at apply time.
  policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.admin]
}

# ---------------------------------------------------------------------------
# Additional human-operator cluster admins (from var.cluster_admin_principal_arns).
#
# The GitHubActions-OIDC-Role above only works inside CI. Developers need
# their own IAM user/role ARNs listed here so kubectl works from laptops.
# Root account ARNs are deliberately unsupported — AWS treats root specially
# and you should never be using root credentials for day-to-day work.
# ---------------------------------------------------------------------------

resource "aws_eks_access_entry" "extra_admins" {
  for_each = toset(var.cluster_admin_principal_arns)

  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "extra_admins" {
  for_each = toset(var.cluster_admin_principal_arns)

  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.extra_admins]
}

# ---------------------------------------------------------------------------
# Outputs for downstream modules (add-ons, Pod Identity, kubectl).
# ---------------------------------------------------------------------------

output "eks_cluster_name" {
  value       = aws_eks_cluster.main.name
  description = "Use with: aws eks update-kubeconfig --name <this> --region <region>"
}

output "eks_cluster_endpoint" {
  value = aws_eks_cluster.main.endpoint
}

output "eks_cluster_certificate_authority_data" {
  value     = aws_eks_cluster.main.certificate_authority[0].data
  sensitive = true
}
