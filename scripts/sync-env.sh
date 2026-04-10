#!/bin/bash
# Pulls the latest Terraform outputs from AWS SSM and writes them into .env.
# Run this once after every terraform apply.
#
# Usage: ./scripts/sync-env.sh

set -eu

REGION="us-east-1"
PREFIX="/chrono/dev"
AWS_PROFILE="chrono-dev"
ENV_FILE="$(dirname "$0")/../.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env file not found at $ENV_FILE" >&2
  exit 1
fi

get_param() {
  aws ssm get-parameter \
    --name "$PREFIX/$1" \
    --with-decryption \
    --query "Parameter.Value" \
    --output text \
    --region "$REGION" \
    --profile "$AWS_PROFILE"
}

echo "Fetching outputs from SSM ($PREFIX)..."

COGNITO_POOL_ID=$(get_param cognito_pool_id)
COGNITO_CLIENT_ID=$(get_param cognito_client_id)
COGNITO_CLIENT_SECRET=$(get_param cognito_client_secret)
AWS_ACCESS_KEY_ID_VAL=$(get_param dev_access_key_id)
AWS_SECRET_ACCESS_KEY_VAL=$(get_param dev_secret_access_key)
S3_BUCKET=$(get_param s3_bucket_name)
RDS_ENDPOINT=$(get_param rds_endpoint)

set_env() {
  local key=$1 val=$2
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    # Replace existing line (cross-platform sed -i)
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

set_env "COGNITO_USER_POOL_ID"  "$COGNITO_POOL_ID"
set_env "COGNITO_CLIENT_ID"     "$COGNITO_CLIENT_ID"
set_env "COGNITO_CLIENT_SECRET" "$COGNITO_CLIENT_SECRET"
set_env "AWS_ACCESS_KEY_ID"     "$AWS_ACCESS_KEY_ID_VAL"
set_env "AWS_SECRET_ACCESS_KEY" "$AWS_SECRET_ACCESS_KEY_VAL"
set_env "S3_REPO_BUCKET"        "$S3_BUCKET"

echo ""
echo "Done. .env updated with latest Terraform outputs."
echo ""
echo "NOTE: DATABASE_URL was not updated automatically."
echo "      New RDS endpoint: $RDS_ENDPOINT"
echo "      Update DATABASE_URL in .env manually if the endpoint changed."
