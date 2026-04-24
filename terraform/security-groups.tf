###############################################################################
# Security groups — strict, source-SG-based (no CIDR-wide allows).
###############################################################################

# Legacy placeholder — predates EKS. An old ENI outside terraform still holds a
# reference, so AWS rejects deletion. Kept empty (no rules, no attachments) so
# subsequent applies are no-ops. Remove with `aws ec2 delete-security-group`
# once the lingering ENI is detached (check via `aws ec2 describe-network-
# interfaces --filters Name=group-id,Values=<sg-id>`).
resource "aws_security_group" "backend_sg" {
  name        = "${var.project_name}-backend-sg"
  description = "Legacy placeholder — pending manual cleanup."
  vpc_id      = module.vpc.vpc_id

  lifecycle {
    ignore_changes = [description, tags, tags_all]
  }
}

# RDS — only reachable from EKS nodes.
# SG description is immutable in AWS — changing it forces destroy+recreate,
# which fails while RDS/EFS still reference the SG. ignore_changes keeps the
# existing SG in place across edits to description/name.
resource "aws_security_group" "rds_sg" {
  name        = "${var.project_name}-rds-sg"
  description = "Postgres access from EKS nodes only."
  vpc_id      = module.vpc.vpc_id

  lifecycle {
    ignore_changes = [description, name]
  }
}

resource "aws_security_group_rule" "rds_ingress_from_nodes" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds_sg.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "Postgres from EKS node SG"
}

resource "aws_security_group_rule" "rds_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.rds_sg.id
}

# EFS — NFS from EKS nodes only.
resource "aws_security_group" "efs_sg" {
  name        = "${var.project_name}-efs-sg"
  description = "NFS access from EKS nodes only."
  vpc_id      = module.vpc.vpc_id

  lifecycle {
    ignore_changes = [description, name]
  }
}

resource "aws_security_group_rule" "efs_ingress_from_nodes" {
  type                     = "ingress"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  security_group_id        = aws_security_group.efs_sg.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "NFS from EKS node SG"
}
