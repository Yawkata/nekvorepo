# ---------------------------------------------------------------------------
# ECR — private container registry for every image EKS will pull.
#
# One repo per deployable artifact. Why not one giant repo with many tags?
# Because ECR IAM policies, lifecycle rules, and vulnerability scan findings
# are scoped per-repository, and one-repo-per-service is the idiomatic layout.
#
# Production-grade defaults we apply below:
#
#   image_tag_mutability = IMMUTABLE
#       Once you push `identity-service:1.4.2`, that tag is frozen. Stops
#       the supply-chain failure mode where someone pushes a malicious image
#       over a tag that is already running in prod.
#
#   image_scanning_configuration.scan_on_push = true
#       ECR basic scanning runs Clair-style CVE detection on every push.
#       You can upgrade to Amazon Inspector "enhanced scanning" later —
#       that's a one-click account-level toggle and is a good-practice add.
#
#   encryption_configuration = AES256
#       Server-side encryption with ECR-managed keys. Free, zero-ops.
#       Upgrade to KMS-CMK later only if you need per-key audit / revocation.
#
#   lifecycle_policy
#       Keeps the last 30 tagged images and deletes untagged images after 7d.
#       Untagged = orphaned layers from failed pushes; they cost money and
#       serve no purpose.
#
# Tagged vs digest pulls: in k8s manifests we will pin images by DIGEST
# (e.g. identity-service@sha256:abc...) for guaranteed immutability across
# the cluster. Tags are for humans; digests are for machines.
# ---------------------------------------------------------------------------

locals {
  ecr_repos = toset([
    "identity-service",
    "repo-service",
    "workflow-service",
    "frontend",
    "database-migrations",
  ])
}

resource "aws_ecr_repository" "services" {
  for_each = local.ecr_repos

  name                 = "${var.project_name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  # Without this, `terraform destroy` fails the moment the repo has any
  # images. For staging where we rebuild from source, losing images on
  # destroy is the desired behavior. For prod, invert this and empty
  # repos out-of-band before destroy (or just never destroy).
  force_delete = var.environment != "prod"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = each.key
    ManagedBy   = "terraform"
  }
}

resource "aws_ecr_lifecycle_policy" "services" {
  for_each = aws_ecr_repository.services

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the 30 most recent tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      },
    ]
  })
}

output "ecr_repository_urls" {
  description = "Map of service name -> ECR repo URL. Used by CI to tag + push images and by k8s manifests as the image: prefix."
  value       = { for name, repo in aws_ecr_repository.services : name => repo.repository_url }
}
