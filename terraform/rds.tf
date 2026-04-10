# Create a secret for the DB password
resource "aws_secretsmanager_secret" "db_pass" {
  name                    = "${var.project_name}-db-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db_pass_val" {
  secret_id     = aws_secretsmanager_secret.db_pass.id
  secret_string = var.db_password # Still passed via CLI/Env, but stored securely
}

resource "aws_db_instance" "postgres" {
  identifier     = "${var.project_name}-db"
  engine         = "postgres"
  engine_version = "15"

  # DEV: t4g.micro is Free Tier eligible. 
  # PROD: instance_class = "db.t4g.small"
  instance_class = "db.t4g.micro"

  allocated_storage     = 20
  max_allocated_storage = 100 # Storage autoscaling

  db_name  = replace("${var.project_name}_db", "-", "_")
  username = "postgres_admin"
  password = aws_secretsmanager_secret_version.db_pass_val.secret_string

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.rds_sg.id]

  ca_cert_identifier = "rds-ca-rsa2048-g1"

  # DEV: true allows you to connect directly from your PC without the Bastion tunnel
  # PROD: publicly_accessible = false
  publicly_accessible = true

  # DEV: false (Single-AZ is much cheaper)
  # PROD: multi_az = true
  multi_az = false

  storage_encrypted = true

  # DEV: false for easy "terraform destroy". 
  # PROD: deletion_protection = true
  deletion_protection = false
  skip_final_snapshot = true # DEV: true to save time/cost on destroy

  final_snapshot_identifier = "${var.project_name}-final-snapshot"

  # DEV: Disable to save on storage and processing
  # PROD: performance_insights_enabled = true
  performance_insights_enabled = false
  # performance_insights_retention_period = 7
  # enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]

  # UPDATES
  auto_minor_version_upgrade = true
  maintenance_window         = "Sun:04:00-Sun:05:00"
  backup_window              = "02:00-03:00"
  backup_retention_period    = 7 # Keep 7 days of automated backups
}