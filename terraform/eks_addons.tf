# ---------------------------------------------------------------------------
# EKS add-ons — the foundational in-cluster controllers AWS manages for us.
#
# An "add-on" is a Kubernetes workload AWS installs, upgrades, and patches
# through the EKS API. The alternative is installing the same software
# manually via Helm, which means you own CVE tracking and upgrade timing.
# For CORE components we want AWS-managed; for app-level controllers
# (ALB controller, ExternalDNS, ESO) we'll use Helm in micro-task D.
#
# resolve_conflicts_on_update = "PRESERVE"
#   If you customized an add-on's config via kubectl, keep your changes
#   when EKS upgrades the add-on. Safer than OVERWRITE for production.
#
# most_recent = true
#   Always track the latest compatible version for the cluster's k8s minor.
#   Simpler than pinning; EKS guarantees compatibility.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# vpc-cni — the pod networking plugin.
#
# Gives every pod its own VPC IP (not an overlay). Consequence: pods can
# talk to RDS, EFS, etc. as if they were EC2 instances. Trade-off: pod
# density per node is capped by ENI/IP limits of the instance type.
# A t4g.medium supports ~17 pods. Prefix delegation (enabled by default
# now) multiplies that ~16x. You do not need to configure anything.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "vpc-cni"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  # Enable in-kernel NetworkPolicy enforcement. Without this flag the
  # policies in k8s/base/networkpolicies.yaml are accepted by the apiserver
  # but never programmed into eBPF, so default-deny silently allows all
  # traffic. 2026 baseline for any cluster claiming zero-trust networking.
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })
}

# ---------------------------------------------------------------------------
# kube-proxy — implements Kubernetes Service ClusterIPs.
#
# Every Service object (e.g. "identity-service on port 8000") gets a stable
# virtual IP. kube-proxy programs iptables/IPVS on each node so that a pod
# hitting that IP is load-balanced across the backing pods.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "kube-proxy"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
}

# ---------------------------------------------------------------------------
# coredns — cluster DNS.
#
# Resolves in-cluster names like `identity-service.default.svc.cluster.local`
# to the Service's ClusterIP. Also forwards external lookups (rds.amazonaws.com,
# etc.) to VPC DNS. Without it, pods can't find each other by name.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "coredns"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  # CoreDNS must land on a running node; the system MNG provides that.
  depends_on = [aws_eks_node_group.system]
}

# ---------------------------------------------------------------------------
# eks-pod-identity-agent — our 2026 replacement for IRSA.
#
# A pod that needs to call AWS APIs mounts a service account. Pod Identity
# Agent intercepts that SA's credential request and hands back STS creds
# for the mapped IAM role. Simpler than IRSA:
#   - No OIDC provider to configure on the cluster.
#   - No annotations on ServiceAccounts.
#   - Mapping is a plain AWS API resource (aws_eks_pod_identity_association),
#     not a trust-policy edit.
# Micro-task C wires each service's existing IAM role to its ServiceAccount.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "pod_identity_agent" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "eks-pod-identity-agent"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  depends_on = [aws_eks_node_group.system]
}

# ---------------------------------------------------------------------------
# aws-ebs-csi-driver — attach EBS volumes to pods (PersistentVolumes).
#
# Even if we only use EFS for app data, Kubernetes requires block storage
# for things like Prometheus's TSDB, buildkit caches, and temporary stateful
# workloads. Better to install it now than to discover it missing later.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "aws-ebs-csi-driver"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  # The EBS CSI controller needs IAM permission to CreateVolume /
  # AttachVolume. Pod Identity association is added in micro-task C.
  depends_on = [aws_eks_node_group.system]
}

# ---------------------------------------------------------------------------
# aws-efs-csi-driver — mount EFS access points as pod volumes.
#
# This is what makes `/mnt/efs/drafts/...` work inside repo-service pods.
# The driver talks to the EFS mount target using its IAM-gated API;
# IAM is again wired via Pod Identity in micro-task C.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "efs_csi" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "aws-efs-csi-driver"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  depends_on = [aws_eks_node_group.system]
}

# ---------------------------------------------------------------------------
# metrics-server — resource metrics for HPA.
#
# HorizontalPodAutoscaler reads CPU/memory from the metrics API. metrics-server
# scrapes kubelets and serves that API. Without it, HPAs stay stuck at
# "unknown" and never scale. Shipped as an EKS add-on since 2024.
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "metrics_server" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "metrics-server"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  depends_on = [aws_eks_node_group.system]
}
