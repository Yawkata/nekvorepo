# ---------------------------------------------------------------------------
# ExternalDNS
#
# What it does: watches Ingress (and optionally Service) objects for
# hostnames and synchronizes matching A/AAAA/CNAME records into Route 53.
# One-way: cluster → DNS. When you `kubectl delete ingress`, ExternalDNS
# removes the record too — which is why the pre-destroy cleanup job works.
#
# Why we need it: without it, every time you add a new hostname
# (e.g. api.chronovcs.com) someone has to click through the Route 53
# console. That's a GitOps anti-pattern and a source of drift.
#
# How it's configured here:
#   - IAM role scoped to exactly our hosted zone (ChangeResourceRecordSets),
#     plus cluster-wide Route53 List/Get actions (needed for zone discovery).
#   - Ownership via the TXT registry — ExternalDNS writes a companion TXT
#     record per managed entry so it never touches records it didn't create.
#   - txtOwnerId = cluster name. If you ever run two clusters pointing at
#     the same zone, each only manages its own records.
#   - Policy = "sync" → ExternalDNS both creates AND deletes records.
#     Required for the pre-destroy cleanup path; "upsert-only" would leak.
#
# Gated behind var.domain_name: with no hosted zone, ExternalDNS has
# nothing to sync and Helm install would just idle. Saves resources.
# ---------------------------------------------------------------------------

locals {
  externaldns_enabled       = local.dns_enabled
  externaldns_chart_version = "1.15.0" # Oct 2024, app 0.15.1 — stable
}

data "aws_iam_policy_document" "externaldns" {
  count = local.externaldns_enabled ? 1 : 0

  statement {
    sid       = "ChangeRecordSetsInOurZone"
    effect    = "Allow"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${aws_route53_zone.primary[0].zone_id}"]
  }

  # Discovery APIs must be unscoped — ListHostedZones has no resource filter.
  statement {
    sid    = "ListZonesAndRecords"
    effect = "Allow"
    actions = [
      "route53:ListHostedZones",
      "route53:ListHostedZonesByName",
      "route53:ListResourceRecordSets",
      "route53:ListTagsForResource",
      "route53:GetChange",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "externaldns" {
  count              = local.externaldns_enabled ? 1 : 0
  name               = "${local.cluster_name}-externaldns"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Component   = "external-dns"
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy" "externaldns" {
  count  = local.externaldns_enabled ? 1 : 0
  name   = "route53-sync"
  role   = aws_iam_role.externaldns[0].id
  policy = data.aws_iam_policy_document.externaldns[0].json
}

resource "aws_eks_pod_identity_association" "externaldns" {
  count           = local.externaldns_enabled ? 1 : 0
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "kube-system"
  service_account = "external-dns"
  role_arn        = aws_iam_role.externaldns[0].arn
}

resource "helm_release" "externaldns" {
  count      = local.externaldns_enabled ? 1 : 0
  name       = "external-dns"
  repository = "https://kubernetes-sigs.github.io/external-dns/"
  chart      = "external-dns"
  version    = local.externaldns_chart_version
  namespace  = "kube-system"

  wait    = true
  timeout = 300

  values = [yamlencode({
    provider = { name = "aws" }

    serviceAccount = {
      create = true
      name   = "external-dns"
    }

    # Lock ExternalDNS to the zone we own; never touch records in other zones.
    domainFilters = [var.domain_name]
    zoneIdFilters = [aws_route53_zone.primary[0].zone_id]

    policy     = "sync"             # create + delete
    registry   = "txt"              # ownership tracking via TXT records
    txtOwnerId = local.cluster_name # cluster-unique prefix

    # Watch Ingress only — we don't use Service type=LoadBalancer as a
    # DNS source; all ingress traffic enters through ALBs.
    sources = ["ingress"]

    # Don't sync if there's nothing to sync (shrinks rate-limit pressure).
    interval = "1m"

    resources = {
      requests = { cpu = "50m", memory = "64Mi" }
      limits   = { memory = "128Mi" }
    }

    securityContext = {
      allowPrivilegeEscalation = false
      readOnlyRootFilesystem   = true
      runAsNonRoot             = true
      runAsUser                = 65534
      capabilities             = { drop = ["ALL"] }
    }
  })]

  depends_on = [
    aws_eks_pod_identity_association.externaldns,
    aws_eks_addon.pod_identity_agent,
    aws_eks_node_group.system,
  ]
}
