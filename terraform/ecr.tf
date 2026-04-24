###############################################################################
# ECR — one repository per deployable image.
# Immutable tags (prevents tag-squatting), scan-on-push, lifecycle cleanup.
###############################################################################

locals {
  ecr_repos = toset([
    "frontend",
    "identity-service",
    "repo-service",
    "workflow-service",
    "database-migrations",
  ])
}

resource "aws_ecr_repository" "images" {
  for_each = local.ecr_repos

  name                 = "${var.project_name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # DEV: allow terraform destroy to remove non-empty repos.

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "images" {
  for_each   = aws_ecr_repository.images
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 20 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 20
        }
        action = { type = "expire" }
      },
    ]
  })
}
