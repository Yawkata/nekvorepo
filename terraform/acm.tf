# ---------------------------------------------------------------------------
# ACM — public TLS certificate for the ALB.
#
# ACM is AWS's free managed certificate authority for public TLS. The ALB
# terminates HTTPS using this cert, so pods only serve HTTP inside the VPC.
# The cert auto-renews as long as the DNS validation record stays in place.
#
# Scope: one cert covering the apex (chronovcs.com) AND a wildcard
# (*.chronovcs.com) so every service subdomain (api., app., etc.) shares it.
#
# Validation is DNS-based: ACM asks us to publish a CNAME in the zone; it
# polls, sees the record, and issues. We automate publishing via Route 53 —
# no human step required.
#
# Note: the cert lives in var.aws_region because the ALB lives there. If you
# later add CloudFront, you will need a SEPARATE cert in us-east-1 (CloudFront
# only accepts us-east-1 ACM certs). That is a later concern.
# ---------------------------------------------------------------------------

resource "aws_acm_certificate" "primary" {
  count = local.dns_enabled ? 1 : 0

  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  validation_method         = "DNS"

  # Required so Terraform creates a replacement cert BEFORE deleting the old
  # one on domain changes — prevents a TLS outage on the ALB.
  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ACM emits one validation record per unique name. The for_each keys them by
# domain name so each gets its own Route53 record.
resource "aws_route53_record" "acm_validation" {
  for_each = local.dns_enabled ? {
    for dvo in aws_acm_certificate.primary[0].domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  } : {}

  zone_id         = aws_route53_zone.primary[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

# Blocks until ACM reports ISSUED. Safe to apply now: even before you register
# the domain, the records exist in the zone; issuance just stays PENDING until
# you delegate NS at the registrar. At that point ACM finishes within minutes
# with zero further Terraform action needed.
resource "aws_acm_certificate_validation" "primary" {
  count = local.dns_enabled ? 1 : 0

  certificate_arn         = aws_acm_certificate.primary[0].arn
  validation_record_fqdns = [for r in aws_route53_record.acm_validation : r.fqdn]

  timeouts {
    create = "60m"
  }
}

output "acm_certificate_arn" {
  description = "ARN of the public TLS cert. Attach to the ALB listener via the ingress annotation alb.ingress.kubernetes.io/certificate-arn."
  value       = local.dns_enabled ? aws_acm_certificate.primary[0].arn : null
}
