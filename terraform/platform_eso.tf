# ---------------------------------------------------------------------------
# External Secrets Operator (ESO)
#
# What it does: keeps Kubernetes Secret objects in sync with a remote
# source of truth — here, AWS Secrets Manager. You define an
# ExternalSecret CR in a namespace; ESO creates/updates the corresponding
# Secret resource, polling every refresh interval.
#
# Why we need it: the three backend services need DB passwords, Cognito
# client secrets, SES identities, etc. Storing those literally in git is
# non-starter. Manually `kubectl create secret` works once but drifts and
# can't rotate. ESO solves both: secret material lives only in Secrets
# Manager (already encrypted with KMS, audited in CloudTrail, rotatable),
# and pods consume them as normal Kubernetes Secrets via envFrom / volume.
#
# How it's configured here:
#   - IAM role scoped to secretsmanager:Get/Describe/List on `chrono-vcs/*`
#     secret names. Prefix-scoping prevents leakage across projects.
#   - KMS Decrypt allowed only when used by the Secrets Manager service
#     (the kms:ViaService condition) — stops cross-service key abuse.
#   - ClusterSecretStore referencing the `external-secrets` SA via Pod
#     Identity. App namespaces consume it via a simple `storeRef`.
#
# The app-level ExternalSecret CRs live in k8s/ manifests (micro-task E),
# not here.
# ---------------------------------------------------------------------------

locals {
  eso_chart_version = "0.10.7" # Oct 2024 stable
}

data "aws_iam_policy_document" "eso" {
  statement {
    sid    = "ReadProjectSecrets"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.project_name}/*",
    ]
  }

  # ListSecrets has no resource-level filter; allow it broadly but readonly.
  statement {
    sid       = "ListAllSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:ListSecrets"]
    resources = ["*"]
  }

  # KMS Decrypt permitted only when the request is routed through Secrets
  # Manager. Without the condition, a compromised ESO pod could decrypt
  # EBS volumes, S3 objects, or EKS secrets encrypted with the same key.
  statement {
    sid       = "DecryptViaSecretsManager"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eso" {
  name               = "${local.cluster_name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Component   = "external-secrets"
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy" "eso" {
  name   = "secretsmanager-read"
  role   = aws_iam_role.eso.id
  policy = data.aws_iam_policy_document.eso.json
}

resource "aws_eks_pod_identity_association" "eso" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.eso.arn
}

# ESO runs in its own namespace by convention — isolates its CRDs and
# RBAC from kube-system and the app namespace. We let Helm create the
# namespace (create_namespace=true) instead of using a separate
# kubernetes_namespace resource: fewer apiserver round-trips at plan
# time, which matters in CI where every extra call risks a timeout.
resource "helm_release" "eso" {
  name             = "external-secrets"
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  version          = local.eso_chart_version
  namespace        = "external-secrets"
  create_namespace = true

  wait    = true
  timeout = 300

  values = [yamlencode({
    installCRDs = true

    # Single replica for controller is OK in staging; flip to 2 for prod.
    replicaCount = 1

    serviceAccount = {
      create = true
      name   = "external-secrets"
    }

    resources = {
      requests = { cpu = "50m", memory = "64Mi" }
      limits   = { memory = "256Mi" }
    }

    securityContext = {
      allowPrivilegeEscalation = false
      readOnlyRootFilesystem   = true
      runAsNonRoot             = true
      capabilities             = { drop = ["ALL"] }
    }

    # Webhook + cert-controller share the same settings for simplicity.
    webhook = {
      replicaCount = 1
      resources = {
        requests = { cpu = "50m", memory = "64Mi" }
        limits   = { memory = "128Mi" }
      }
    }
    certController = {
      replicaCount = 1
      resources = {
        requests = { cpu = "50m", memory = "64Mi" }
        limits   = { memory = "128Mi" }
      }
    }
  })]

  depends_on = [
    aws_eks_pod_identity_association.eso,
    aws_eks_addon.pod_identity_agent,
    aws_eks_node_group.system,
  ]
}

# ---------------------------------------------------------------------------
# ClusterSecretStore CR is NOT created here.
#
# `kubernetes_manifest` performs live CRD schema discovery against the EKS
# apiserver at plan time, which is brittle in CI (private endpoint + runner
# not on the VPC = timeout). The CR is applied in micro-task E via kubectl
# / Kustomize against the `k8s/` manifests, after the Helm release has
# installed the CRDs. Terraform owns the IAM + Helm; the app pipeline owns
# the CRs that depend on them.
# ---------------------------------------------------------------------------
