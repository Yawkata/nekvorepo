# ---------------------------------------------------------------------------
# DNS foundation: Route 53 public hosted zone + DNSSEC signing.
#
# Everything in this file is gated behind var.domain_name. Until you set it,
# no DNS resources are created, so you can run `terraform plan/apply` for
# unrelated changes safely.
# ---------------------------------------------------------------------------

locals {
  dns_enabled = var.domain_name != ""
}

# The public hosted zone holds every DNS record for your domain (A records
# pointing at the ALB, TXT records for SES/ACM validation, etc.). Once you
# register the domain at a registrar, you paste this zone's four NS records
# into the registrar — that delegation is what makes the internet ask AWS
# for your DNS answers.
resource "aws_route53_zone" "primary" {
  count = local.dns_enabled ? 1 : 0

  name    = var.domain_name
  comment = "Primary public zone for ${var.project_name}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# DNSSEC — cryptographic signing of DNS answers.
#
# Without DNSSEC, an on-path attacker can forge DNS replies and silently
# redirect users to a hostile server before TLS even starts. DNSSEC lets
# resolvers verify that every answer came from the real zone owner.
#
# Route 53 signs the zone with a Key-Signing Key (KSK) backed by a customer-
# managed KMS key. Constraints AWS imposes:
#   - KMS key MUST be in us-east-1.
#   - Key spec MUST be ECC_NIST_P256 with SIGN_VERIFY usage.
#   - The key policy MUST allow dnssec-route53.amazonaws.com to Sign and
#     GetPublicKey (scoped by the aws:SourceAccount condition so no other
#     account can hijack it).
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "dnssec" {
  count    = local.dns_enabled ? 1 : 0
  provider = aws.us_east_1

  description              = "Route53 DNSSEC KSK for ${var.domain_name}"
  customer_master_key_spec = "ECC_NIST_P256"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 7
  enable_key_rotation      = false # KMS does not rotate asymmetric keys.

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
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
            "aws:SourceArn" = "arn:aws:route53:::hostedzone/*"
          }
        }
      },
      {
        Sid       = "AllowRoute53DNSSECGrants"
        Effect    = "Allow"
        Principal = { Service = "dnssec-route53.amazonaws.com" }
        Action    = ["kms:CreateGrant"]
        Resource  = "*"
        Condition = {
          Bool = { "kms:GrantIsForAWSResource" = true }
        }
      },
    ]
  })

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_route53_key_signing_key" "primary" {
  count = local.dns_enabled ? 1 : 0

  hosted_zone_id             = aws_route53_zone.primary[0].id
  key_management_service_arn = aws_kms_key.dnssec[0].arn
  name                       = replace("${var.project_name}-ksk", "-", "_")
}

resource "aws_route53_hosted_zone_dnssec" "primary" {
  count = local.dns_enabled ? 1 : 0

  hosted_zone_id = aws_route53_key_signing_key.primary[0].hosted_zone_id
  # Must wait for the KSK resource record to propagate before enabling.
  depends_on = [aws_route53_key_signing_key.primary]
}

# ---------------------------------------------------------------------------
# Outputs you will need at the registrar.
# ---------------------------------------------------------------------------

output "route53_zone_id" {
  description = "Hosted zone ID. Referenced by ACM validation, ExternalDNS, etc."
  value       = local.dns_enabled ? aws_route53_zone.primary[0].zone_id : null
}

output "route53_name_servers" {
  description = "Paste these four NS records into your domain registrar to delegate the zone to AWS."
  value       = local.dns_enabled ? aws_route53_zone.primary[0].name_servers : null
}

output "route53_dnssec_ds_record" {
  description = "After delegation, paste this DS record at the registrar to activate DNSSEC end-to-end. Format: 'key_tag algorithm digest_type digest'."
  value = local.dns_enabled ? format(
    "%s %s %s %s",
    aws_route53_key_signing_key.primary[0].key_tag,
    aws_route53_key_signing_key.primary[0].signing_algorithm_type,
    aws_route53_key_signing_key.primary[0].digest_algorithm_type,
    aws_route53_key_signing_key.primary[0].digest_value,
  ) : null
}
