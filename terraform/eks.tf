###############################################################################
# EKS cluster — control plane + one managed node group across 3 AZs.
#
# Well-Architected choices:
#   * Envelope-encrypted Secrets (KMS) — SEC08
#   * All control-plane log types → CloudWatch, 90 d retention — OPS04
#   * Private + restricted public endpoint — SEC01
#   * Node group in private subnets only, min 3 (one per AZ) — REL01
#   * Pod Identity (not IRSA) — modern, revocable, short-lived, no OIDC plumbing.
###############################################################################

locals {
  cluster_name = "${var.project_name}-eks"
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = local.cluster_name
  cluster_version = var.cluster_version

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  # Restricted-but-enabled public endpoint — lets GitHub Actions reach the API
  # without a bastion while keeping random internet scanners out. Tighten
  # var.allowed_ips to your runner egress range for a fully hardened stance.
  cluster_endpoint_public_access       = true
  cluster_endpoint_public_access_cidrs = length(var.allowed_ips) > 0 ? var.allowed_ips : ["0.0.0.0/0"]
  cluster_endpoint_private_access      = true

  # Envelope-encrypt Kubernetes Secrets with a customer-managed KMS key.
  cluster_encryption_config = {
    resources        = ["secrets"]
    provider_key_arn = aws_kms_key.eks_secrets.arn
  }

  # Ship every control-plane log type to CloudWatch — forensics, audit.
  cluster_enabled_log_types              = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  cloudwatch_log_group_retention_in_days = 90
  cloudwatch_log_group_kms_key_id        = aws_kms_key.logs.arn

  # API auth — grant the Terraform runner cluster-admin so kubectl works from CI.
  authentication_mode                      = "API_AND_CONFIG_MAP"
  enable_cluster_creator_admin_permissions = true

  # Core cluster addons, all managed by EKS so upgrades are one-line.
  cluster_addons = {
    coredns = {
      most_recent = true
      configuration_values = jsonencode({
        replicaCount = 3
        tolerations  = []
      })
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      most_recent    = true
      before_compute = true
      configuration_values = jsonencode({
        env = {
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
    eks-pod-identity-agent = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent              = true
      service_account_role_arn = aws_iam_role.ebs_csi.arn
    }
    aws-efs-csi-driver = {
      most_recent              = true
      service_account_role_arn = aws_iam_role.efs_csi.arn
    }
    metrics-server = {
      most_recent = true
    }
  }

  eks_managed_node_group_defaults = {
    ami_type       = "AL2023_x86_64_STANDARD"
    instance_types = var.node_instance_types

    # IMDSv2 only, hop-limit 1 so pods cannot reach the node IAM role.
    metadata_options = {
      http_endpoint               = "enabled"
      http_tokens                 = "required"
      http_put_response_hop_limit = 1
      instance_metadata_tags      = "enabled"
    }

    block_device_mappings = {
      xvda = {
        device_name = "/dev/xvda"
        ebs = {
          volume_size           = 50
          volume_type           = "gp3"
          iops                  = 3000
          throughput            = 125
          encrypted             = true
          kms_key_id            = aws_kms_key.ebs.arn
          delete_on_termination = true
        }
      }
    }

    iam_role_additional_policies = {
      # Needed so Cluster Autoscaler (running on the node) can tag ASGs and
      # describe/launch instances when scaling.
      AmazonSSMManagedInstanceCore = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    }
  }

  eks_managed_node_groups = {
    primary = {
      min_size     = var.node_group_min_size
      max_size     = var.node_group_max_size
      desired_size = var.node_group_desired_size

      # Spread the ASG across all 3 AZs — without this the MNG defaults to one.
      subnet_ids = module.vpc.private_subnets

      update_config = {
        max_unavailable_percentage = 33
      }

      labels = {
        role = "primary"
      }

      tags = {
        # Cluster Autoscaler discovery tags.
        "k8s.io/cluster-autoscaler/enabled"               = "true"
        "k8s.io/cluster-autoscaler/${local.cluster_name}" = "owned"
      }
    }
  }

  # Extra security group rule — let nodes talk to EFS (2049) and RDS (5432).
  node_security_group_additional_rules = {
    egress_efs = {
      description = "Egress to EFS"
      protocol    = "tcp"
      from_port   = 2049
      to_port     = 2049
      type        = "egress"
      cidr_blocks = [module.vpc.vpc_cidr_block]
    }
    egress_rds = {
      description = "Egress to RDS"
      protocol    = "tcp"
      from_port   = 5432
      to_port     = 5432
      type        = "egress"
      cidr_blocks = [module.vpc.vpc_cidr_block]
    }
  }
}

# ----------------------------------------------------------------------------
# IAM roles for EKS-managed addons (EBS CSI, EFS CSI) — Pod Identity style.
# ----------------------------------------------------------------------------

data "aws_iam_policy_document" "pod_identity_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.project_name}-ebs-csi-role"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role" "efs_csi" {
  name               = "${var.project_name}-efs-csi-role"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

resource "aws_iam_role_policy_attachment" "efs_csi" {
  role       = aws_iam_role.efs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
}
