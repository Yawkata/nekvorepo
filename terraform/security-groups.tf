###############################################################################
# Security groups — strict, source-SG-based (no CIDR-wide allows).
###############################################################################

# RDS — only reachable from EKS nodes.
resource "aws_security_group" "rds_sg" {
  name        = "${var.project_name}-rds-sg"
  description = "Postgres access from EKS nodes only."
  vpc_id      = module.vpc.vpc_id
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
