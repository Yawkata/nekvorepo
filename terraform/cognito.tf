resource "aws_cognito_user_pool" "pool" {
  name = "${var.project_name}-user-pool"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  # Email verification setup
  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  user_pool_add_ons {
    advanced_security_mode = "ENFORCED"
  }

  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  deletion_protection = "ACTIVE"

  schema {
    attribute_data_type      = "String"
    developer_only_attribute = false
    mutable                  = true # Users should be able to change their name later
    name                     = "email"
    required                 = true # This is already set by alias_attributes, but good for clarity
  }

  schema {
    attribute_data_type      = "String"
    developer_only_attribute = false
    mutable                  = true
    name                     = "preferred_username" # This matches the "name" field you send from Python
    required                 = false                # Set to true if you want to force all users to provide it

    string_attribute_constraints {
      min_length = 1
      max_length = 2048
    }
  }
}

resource "aws_cognito_user_pool_client" "client" {
  name         = "gatekeeper-web-client"
  user_pool_id = aws_cognito_user_pool.pool.id

  generate_secret = true

  # ADMIN_NO_SRP allows your FastAPI backend to facilitate login easily
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_ADMIN_USER_PASSWORD_AUTH"
  ]

  read_attributes  = ["email", "name", "email_verified"]
  write_attributes = ["email", "name"]

  # Explicit TTLs per spec: access/id = 1 hour, refresh = 30 days
  access_token_validity  = 60 # minutes
  id_token_validity      = 60 # minutes
  refresh_token_validity = 30 # days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}