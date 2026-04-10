resource "aws_iam_user" "local_dev" {
  name = "${var.project_name}-local-dev-user"
}

resource "aws_iam_access_key" "dev_key" {
  user = aws_iam_user.local_dev.name
}

# Policy allowing the local app to talk to S3 and Cognito
resource "aws_iam_user_policy" "local_dev_policy" {
  name = "LocalDevAccess"
  user = aws_iam_user.local_dev.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject",
          "cognito-idp:AdminCreateUser", "cognito-idp:AdminGetUser",
          "cognito-idp:AdminUpdateUserAttributes", "cognito-idp:AdminSetUserPassword",
          "cognito-idp:AdminInitiateAuth",
          "ses:SendEmail",
          "ssm:GetParameter"
        ]
        Resource = "*" # DEV: Broad for testing. PROD: Will be restricted.
      }
    ]
  })
}