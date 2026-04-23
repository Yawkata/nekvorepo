# ---------------------------------------------------------------------------
# AWS Load Balancer Controller (LBC)
#
# What it does: watches Kubernetes Ingress and Service objects and creates
# matching AWS Application Load Balancers (for Ingress) and Network Load
# Balancers (for Service type=LoadBalancer with the right annotations).
# It also manages listeners, target groups, SGs, and health checks.
#
# Why we need it: our site is public. Traffic enters AWS at an ALB, which
# terminates TLS using our ACM cert and routes by host/path to the backend
# services inside EKS. Without LBC, we would have to provision each ALB by
# hand in Terraform — brittle and coupled to every future service change.
#
# How it's configured here:
#   - IAM role with the AWS-published policy (fetched from GitHub at the
#     pinned chart version to keep them in lock-step).
#   - Pod Identity association binding that role to the SA it runs as.
#   - Helm chart from the official eks/ repo. Version pinned. Replica count
#     2 with HA anti-affinity. Uses IMDSv2 session (Pod Identity disables
#     the legacy v4 credential path entirely).
#
# Notes:
#   - v2.8.2 chart ≈ controller v2.8.2. Upgrade policy: change both strings
#     together; consult AWS release notes before jumping minors.
#   - The controller needs subnets tagged kubernetes.io/role/elb=1
#     (public) or /internal-elb=1 (private). Those tags were added in
#     micro-task B on vpc.tf.
# ---------------------------------------------------------------------------

locals {
  lbc_chart_version      = "1.8.2" # chart version; controller app version 2.8.2
  lbc_controller_version = "v2.8.2"
}

# AWS publishes the minimum IAM policy as JSON in the controller repo.
# Pinning to the controller tag ensures the policy matches the code that
# consumes it — one version source of truth.
data "http" "lbc_iam_policy" {
  url = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/${local.lbc_controller_version}/docs/install/iam_policy.json"

  request_headers = {
    Accept = "application/json"
  }

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Failed to fetch AWS LBC IAM policy (HTTP ${self.status_code}). Check the controller version tag."
    }
  }
}

resource "aws_iam_policy" "lbc" {
  name        = "${local.cluster_name}-aws-lbc"
  description = "AWS Load Balancer Controller permissions (pinned to ${local.lbc_controller_version})"
  policy      = data.http.lbc_iam_policy.response_body
}

resource "aws_iam_role" "lbc" {
  name               = "${local.cluster_name}-aws-lbc"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Component   = "aws-load-balancer-controller"
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy_attachment" "lbc" {
  role       = aws_iam_role.lbc.name
  policy_arn = aws_iam_policy.lbc.arn
}

resource "aws_eks_pod_identity_association" "lbc" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.lbc.arn
}

resource "helm_release" "lbc" {
  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = local.lbc_chart_version
  namespace  = "kube-system"

  # Wait for rollout — makes failures appear during `terraform apply`
  # instead of silently leaving a broken controller behind.
  wait    = true
  timeout = 600

  values = [yamlencode({
    clusterName = aws_eks_cluster.main.name

    # Two replicas with topology spread so a single-node failure doesn't
    # take down ingress reconciliation.
    replicaCount = 2
    topologySpreadConstraints = [{
      maxSkew           = 1
      topologyKey       = "kubernetes.io/hostname"
      whenUnsatisfiable = "ScheduleAnyway"
      labelSelector = {
        matchLabels = {
          "app.kubernetes.io/name" = "aws-load-balancer-controller"
        }
      }
    }]

    serviceAccount = {
      create = true
      name   = "aws-load-balancer-controller"
      # No eks.amazonaws.com/role-arn annotation needed — Pod Identity is
      # configured at the AWS API level, not via SA annotations.
    }

    # Resource requests/limits — controller is lightweight.
    resources = {
      requests = { cpu = "100m", memory = "128Mi" }
      limits   = { memory = "256Mi" }
    }

    # Hardened pod security.
    podSecurityContext = { fsGroup = 65534 }
    securityContext = {
      allowPrivilegeEscalation = false
      readOnlyRootFilesystem   = true
      runAsNonRoot             = true
      capabilities             = { drop = ["ALL"] }
    }

    # Enable the ingress class "alb" (default) and shield Service type=LB.
    enableServiceMutatorWebhook = false
  })]

  depends_on = [
    aws_eks_pod_identity_association.lbc,
    aws_eks_addon.pod_identity_agent,
    aws_eks_node_group.system,
  ]
}
