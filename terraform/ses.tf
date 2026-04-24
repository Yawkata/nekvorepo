###############################################################################
# SES — Transactional email for invite and membership lifecycle notifications
#
# Phase 9: invites, role changes, member removal.
#
# DEV:  Email identity verification (sender address only).
#       AWS will send a confirmation email to var.ses_sender_email; click the
#       link before the service can send.
# PROD: Swap to aws_sesv2_email_identity with domain + DKIM (see commented
#       block below) and move out of the SES sandbox via a production access
#       request in the AWS Console.
###############################################################################

resource "aws_sesv2_email_identity" "sender" {
  email_identity = var.ses_sender_email

  tags = {
    Project     = var.project_name
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# SES Configuration Set — enables per-send tracking and future suppression
# list management (2026 best practice: always attach a configuration set).
# ---------------------------------------------------------------------------

resource "aws_sesv2_configuration_set" "main" {
  configuration_set_name = "${var.project_name}-email-config"

  sending_options {
    sending_enabled = true
  }

  suppression_options {
    # Automatically suppress addresses that hard-bounce or mark as spam.
    suppressed_reasons = ["BOUNCE", "COMPLAINT"]
  }

  tags = {
    Project     = var.project_name
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# PRODUCTION UPGRADE PATH (commented out — enable when leaving SES sandbox)
# ---------------------------------------------------------------------------
# Replace aws_sesv2_email_identity above with:
#
# resource "aws_sesv2_email_identity" "sender_domain" {
#   email_identity = var.ses_sender_domain          # e.g. "mail.example.com"
#
#   dkim_signing_attributes {
#     next_signing_key_length = "RSA_2048_BIT"
#   }
# }
#
# resource "aws_route53_record" "ses_dkim" {
#   count   = 3
#   zone_id = var.route53_zone_id
#   name    = "${aws_sesv2_email_identity.sender_domain.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.ses_sender_domain}"
#   type    = "CNAME"
#   ttl     = 300
#   records = ["${aws_sesv2_email_identity.sender_domain.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
# }