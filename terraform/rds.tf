###############################################################################
# RDS PostgreSQL — Multi-AZ, private, KMS-encrypted, PI, CW logs, backups.
###############################################################################

resource "aws_secretsmanager_secret" "db_pass" {
  name                    = "${var.project_name}-db-password"
  kms_key_id              = aws_kms_key.rds.arn
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_pass_val" {
  secret_id     = aws_secretsmanager_secret.db_pass.id
  secret_string = var.db_password
}

# Parameter group — force TLS, no plaintext connections.
resource "aws_db_parameter_group" "postgres" {
  name   = "${var.project_name}-pg15"
  family = "postgres15"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name         = "log_min_duration_statement"
    value        = "500"
    apply_method = "pending-reboot"
  }
  parameter {
    name         = "log_connections"
    value        = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name         = "log_disconnections"
    value        = "1"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "postgres" {
  identifier     = "${var.project_name}-db"
  engine         = "postgres"
  engine_version = "15"

  instance_class = "db.t4g.small"

  allocated_storage     = 50
  max_allocated_storage = 500
  storage_type          = "gp3"

  db_name  = replace("${var.project_name}_db", "-", "_")
  username = "postgres_admin"
  password = aws_secretsmanager_secret_version.db_pass_val.secret_string

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.rds_sg.id]

  parameter_group_name = aws_db_parameter_group.postgres.name
  ca_cert_identifier   = "rds-ca-rsa2048-g1"

  publicly_accessible = false
  multi_az            = true

  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn

  deletion_protection = true
  skip_final_snapshot = false

  final_snapshot_identifier = "${var.project_name}-final-snapshot-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.rds.arn
  performance_insights_retention_period = 7

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  auto_minor_version_upgrade = true
  maintenance_window         = "Sun:04:00-Sun:05:00"
  backup_window              = "02:00-03:00"
  backup_retention_period    = 14
  copy_tags_to_snapshot      = true

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  lifecycle {
    # final_snapshot_identifier embeds a timestamp() — would force replacement
    # on every apply otherwise.
    ignore_changes = [final_snapshot_identifier]
  }
}

resource "aws_iam_role" "rds_monitoring" {
  name = "${var.project_name}-rds-enhanced-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
