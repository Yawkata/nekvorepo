###############################################################################
# Public DNS, TLS, and DNSSEC for the apex domain.
#
#   * Route 53 hosted zone — authoritative for chronovcs.com.
#   * ACM certificate      — DNS-validated, covers apex + www.
#   * DNSSEC               — asymmetric KMS key (ECC_NIST_P256, SIGN_VERIFY,
#                            MUST live in us-east-1) used as the KSK; Route 53
#                            derives the ZSK and signs the zone.
#   * Alias records        — apex + www → the ALB the AWS LB Controller created
#                            for the `chrono` Ingress.
#
# DNSSEC chain-of-trust is only complete once you paste the DS record
# (output `dnssec_ds_record`) into your registrar. Terraform can't do that.
###############################################################################

resource "aws_route53_zone" "primary" {
  name          = var.domain_name
  comment       = "Public zone for ${var.project_name}"
  force_destroy = false
}

# ─────────────────────────────────────────────────────────────────────────────
# DNSSEC — KSK backed by a customer-managed KMS key. Route 53 requires the key
# to be in us-east-1, ECC_NIST_P256, and with a policy that lets the service
# sign and read the public key.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_kms_key" "dnssec" {
  description              = "Route 53 DNSSEC KSK for ${var.domain_name}"
  customer_master_key_spec = "ECC_NIST_P256"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 30
  enable_key_rotation      = false # Asymmetric sign/verify keys cannot rotate.

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowRoute53DNSSECService"
        Effect    = "Allow"
        Principal = { Service = "dnssec-route53.amazonaws.com" }
        Action = [
          "kms:DescribeKey",
          "kms:GetPublicKey",
          "kms:Sign",
          "kms:Verify",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:route53:::hostedzone/*"
          }
        }
      },
      {
        Sid       = "AllowRoute53DNSSECGrants"
        Effect    = "Allow"
        Principal = { Service = "dnssec-route53.amazonaws.com" }
        Action    = "kms:CreateGrant"
        Resource  = "*"
        Condition = {
          Bool = {
            "kms:GrantIsForAWSResource" = "true"
          }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "dnssec" {
  name          = "alias/${var.project_name}-route53-dnssec"
  target_key_id = aws_kms_key.dnssec.key_id
}

resource "aws_route53_key_signing_key" "primary" {
  hosted_zone_id             = aws_route53_zone.primary.id
  key_management_service_arn = aws_kms_key.dnssec.arn
  name                       = "${replace(var.project_name, "-", "_")}_ksk"
}

resource "aws_route53_hosted_zone_dnssec" "primary" {
  hosted_zone_id = aws_route53_key_signing_key.primary.hosted_zone_id
  depends_on     = [aws_route53_key_signing_key.primary]
}

# ─────────────────────────────────────────────────────────────────────────────
# ACM certificate — DNS-validated, apex + www. The AWS LB Controller auto-
# discovers certificates in ACM whose SANs match an Ingress host rule, so we
# don't need to inject the ARN into the Ingress.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_acm_certificate" "primary" {
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.primary.domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = aws_route53_zone.primary.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "primary" {
  certificate_arn         = aws_acm_certificate.primary.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# ─────────────────────────────────────────────────────────────────────────────
# Alias records → ALB provisioned by the AWS LB Controller for the `chrono`
# Ingress. We look it up by the controller's well-known tags rather than
# hard-coding the DNS name, so ALB replacements are picked up on next apply.
#
# This data source fails if the ALB doesn't exist yet. First-time bring-up
# order: terraform apply (hosted zone + cert), deploy k8s Ingress, then a
# second terraform apply to create these aliases.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_lb" "ingress" {
  count = var.create_alb_alias_records ? 1 : 0

  tags = {
    "elbv2.k8s.aws/cluster"    = module.eks.cluster_name
    "ingress.k8s.aws/stack"    = "${local.app_namespace}/chrono"
    "ingress.k8s.aws/resource" = "LoadBalancer"
  }
}

resource "aws_route53_record" "apex" {
  count = var.create_alb_alias_records ? 1 : 0

  zone_id = aws_route53_zone.primary.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = data.aws_lb.ingress[0].dns_name
    zone_id                = data.aws_lb.ingress[0].zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "www" {
  count = var.create_alb_alias_records ? 1 : 0

  zone_id = aws_route53_zone.primary.zone_id
  name    = "www.${var.domain_name}"
  type    = "A"

  alias {
    name                   = data.aws_lb.ingress[0].dns_name
    zone_id                = data.aws_lb.ingress[0].zone_id
    evaluate_target_health = true
  }
}
