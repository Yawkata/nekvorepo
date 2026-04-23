# ---------------------------------------------------------------------------
# EKS Pod Identity associations — how pods get AWS permissions in 2026.
#
# Concept in one paragraph:
#   A Kubernetes ServiceAccount (SA) is the identity a pod runs as inside
#   the cluster. To call AWS APIs (e.g. SES SendEmail) the pod needs AWS
#   credentials. A Pod Identity *association* binds "cluster X, namespace Y,
#   serviceaccount Z" to an IAM role. When a pod whose SA matches (Y, Z) asks
#   for credentials, the pod-identity-agent (installed as an EKS add-on in
#   micro-task B) returns short-lived STS credentials for the associated role.
#   No OIDC provider, no trust-policy gymnastics, no ConfigMaps.
#
# Why Pod Identity instead of IRSA:
#   - IRSA requires an OIDC provider on the cluster and a trust policy on
#     each role that encodes the cluster OIDC URL + SA namespace/name —
#     brittle to cluster recreation and to cross-cluster role reuse.
#   - Pod Identity moves that mapping into a first-class AWS API resource
#     (aws_eks_pod_identity_association). Auditable, scriptable, reusable
#     across clusters, supports role chaining.
#   - GA since Nov 2023; AWS SDKs auto-detect Pod Identity via a credential
#     provider that was added in 2024. No app code changes needed.
#
# What changes for the existing IAM roles (iam.tf):
#   - Trust principal: ec2.amazonaws.com → pods.eks.amazonaws.com
#   - Required actions: sts:AssumeRole AND sts:TagSession. Pod Identity tags
#     the session with cluster/namespace/SA metadata so CloudTrail shows
#     which pod made which AWS call. Missing sts:TagSession = silent auth
#     failure at runtime.
#
# Namespace/SA convention used below:
#   Every app runs in namespace "chrono-vcs". Each service has a dedicated
#   SA named after it. These names are the contract that k8s manifests in
#   micro-task E must honor — if a Deployment uses a different SA, the pod
#   gets no AWS credentials.
# ---------------------------------------------------------------------------

locals {
  app_namespace = "chrono-vcs"

  app_service_accounts = {
    identity-service = aws_iam_role.identity_service_role.arn
    repo-service     = aws_iam_role.repo_service_role.arn
    workflow-service = aws_iam_role.workflow_service_role.arn
  }
}

resource "aws_eks_pod_identity_association" "app" {
  for_each = local.app_service_accounts

  cluster_name    = aws_eks_cluster.main.name
  namespace       = local.app_namespace
  service_account = each.key
  role_arn        = each.value

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = each.key
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# CSI driver IAM — separate from app services.
#
# The EBS and EFS CSI drivers run as controller pods (in kube-system) and
# call AWS APIs to create/attach/mount volumes on behalf of PVCs. They need
# their own IAM roles, bound to their own ServiceAccounts, via Pod Identity.
#
# Using AWS-managed policies (AmazonEBSCSIDriverPolicy, AmazonEFSCSIDriverPolicy)
# is the 2026 baseline — AWS updates them when new API calls are needed.
# ---------------------------------------------------------------------------

# Shared trust policy for any role assumed by Pod Identity.
data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

# --- EBS CSI ---------------------------------------------------------------

resource "aws_iam_role" "ebs_csi_driver" {
  name               = "${local.cluster_name}-ebs-csi-driver"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Component   = "ebs-csi-driver"
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy_attachment" "ebs_csi_driver" {
  role       = aws_iam_role.ebs_csi_driver.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_eks_pod_identity_association" "ebs_csi_driver" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "kube-system"
  service_account = "ebs-csi-controller-sa"
  role_arn        = aws_iam_role.ebs_csi_driver.arn
}

# --- EFS CSI ---------------------------------------------------------------

resource "aws_iam_role" "efs_csi_driver" {
  name               = "${local.cluster_name}-efs-csi-driver"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Component   = "efs-csi-driver"
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy_attachment" "efs_csi_driver" {
  role       = aws_iam_role.efs_csi_driver.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
}

resource "aws_eks_pod_identity_association" "efs_csi_driver" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "kube-system"
  service_account = "efs-csi-controller-sa"
  role_arn        = aws_iam_role.efs_csi_driver.arn
}

# ---------------------------------------------------------------------------
# Outputs — k8s manifests in micro-task E consume these.
# ---------------------------------------------------------------------------

output "app_namespace" {
  value       = local.app_namespace
  description = "Namespace where all chrono-vcs app pods run. Must match Deployment.metadata.namespace in k8s manifests."
}

output "app_service_account_names" {
  value       = keys(local.app_service_accounts)
  description = "ServiceAccount names per app. Must match Deployment.spec.template.spec.serviceAccountName in k8s manifests."
}

output "efs_access_point_id" {
  value       = aws_efs_access_point.main.id
  description = "Referenced by the k8s PersistentVolume volumeHandle for repo-service drafts."
}

output "efs_file_system_id" {
  value       = aws_efs_file_system.draft_storage.id
  description = "Referenced in the EFS CSI PersistentVolume volumeHandle (format: <fs-id>::<ap-id>)."
}
