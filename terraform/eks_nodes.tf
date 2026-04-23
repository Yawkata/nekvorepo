# ---------------------------------------------------------------------------
# EKS managed node group — the worker EC2 instances that run our pods.
#
# Why a managed node group (MNG) instead of self-managed or Fargate?
#   - MNG handles OS patching, k8s version upgrades, and graceful drain for us.
#   - Karpenter is better for dynamic app scaling (later micro-task), but you
#     still need a small MNG to host cluster-critical pods (Karpenter itself,
#     CoreDNS, AWS Load Balancer Controller) — you cannot bootstrap Karpenter
#     with Karpenter. Hence this MNG is called "system" and is intentionally small.
#   - Fargate has cold-start latency and no EFS support. Not suitable.
#
# Why Graviton (ARM64) t4g.medium?
#   - 20-40% cheaper than x86 for the same vCPU/memory, 60% better perf/watt.
#     2026 industry standard for stateless services.
#   - Caveat: your Docker images MUST be built for linux/arm64 (or multi-arch).
#     CI must use docker buildx with --platform linux/arm64. If images are
#     amd64-only, pods will CrashLoopBackOff with "exec format error".
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Node IAM role.
#
# Three AWS-managed policies are the minimum for MNG workers:
#   - AmazonEKSWorkerNodePolicy: lets the kubelet talk to the EKS API.
#   - AmazonEKS_CNI_Policy: lets the VPC CNI assign ENIs/IPs to pods.
#     NOTE: in production this should move off the node role and onto the
#     vpc-cni add-on via Pod Identity (micro-task C handles that).
#   - AmazonEC2ContainerRegistryReadOnly: lets the kubelet pull images from ECR.
#
# Pod-level permissions do NOT live on this role — we use EKS Pod Identity
# in micro-task C. This role is strictly for the kubelet/node lifecycle.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "eks_nodes" {
  name = "${local.cluster_name}-nodes-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy_attachment" "nodes_worker" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "nodes_cni" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "nodes_ecr" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# ---------------------------------------------------------------------------
# Launch template — how each node boots.
#
# Managed node groups can run without a custom launch template, but then
# you cannot enforce IMDSv2, EBS encryption, or custom AMI hardening.
# 2026 best practice: always bring your own launch template.
#
# Settings explained:
#   - metadata_options.http_tokens = "required": forces IMDSv2. Prevents the
#     classic SSRF-to-credential-theft attack (e.g. the 2019 Capital One
#     breach, which was an IMDSv1 exploit).
#   - http_put_response_hop_limit = 2: pods run in a container network
#     namespace one hop away from the node; the kubelet and controllers
#     still need IMDS. Hop limit 2 allows the kubelet in, blocks pod SSRF.
#   - block_device_mappings: 50 GiB gp3 root volume, encrypted by default.
#     gp3 is cheaper and faster than gp2; encryption is free.
# ---------------------------------------------------------------------------

resource "aws_launch_template" "eks_nodes" {
  name_prefix = "${local.cluster_name}-node-"

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2
    http_endpoint               = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 50
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "${local.cluster_name}-node"
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ---------------------------------------------------------------------------
# The node group.
#
# capacity_type = "ON_DEMAND" for system nodes — these host cluster-critical
# controllers that must not be interrupted by Spot reclamation. Later,
# KEDA-driven SQS consumers will go on Karpenter-managed Spot nodes.
# ---------------------------------------------------------------------------

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "system"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = module.vpc.private_subnets

  ami_type       = "AL2023_ARM_64_STANDARD" # Amazon Linux 2023 on Graviton
  instance_types = ["t4g.medium"]
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = 2
    min_size     = 2
    max_size     = 4
  }

  # When a node template changes, roll nodes one-at-a-time with pod draining.
  update_config {
    max_unavailable = 1
  }

  launch_template {
    id      = aws_launch_template.eks_nodes.id
    version = aws_launch_template.eks_nodes.latest_version
  }

  # Labels let later workloads target or avoid these nodes explicitly.
  labels = {
    "node-role" = "system"
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [
    aws_iam_role_policy_attachment.nodes_worker,
    aws_iam_role_policy_attachment.nodes_cni,
    aws_iam_role_policy_attachment.nodes_ecr,
  ]

  # Node group scale changes should not churn the resource.
  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }
}

# ---------------------------------------------------------------------------
# Let EKS nodes reach EFS on port 2049.
#
# The existing efs_sg permits the "backend_sg" only. Once pods run on EKS,
# the mounting kubelet uses the EKS-managed cluster security group for
# outbound NFS. Allow it in here.
# ---------------------------------------------------------------------------

resource "aws_security_group_rule" "efs_from_eks" {
  type                     = "ingress"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  security_group_id        = aws_security_group.efs_sg.id
  source_security_group_id = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  description              = "Allow NFS from EKS pods via cluster SG"
}
