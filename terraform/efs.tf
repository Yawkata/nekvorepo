resource "aws_efs_file_system" "draft_storage" {
  creation_token = "${var.project_name}-drafts"
  encrypted      = true
  
  tags = { Name = "${var.project_name}-efs" }
}

# Access Point: Forces the UID/GID to 1000 so permissions match Docker & EKS
resource "aws_efs_access_point" "main" {
  file_system_id = aws_efs_file_system.draft_storage.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/drafts"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "755"
    }
  }
}

# Mount Targets: The "Network Plugs" for the VPC
resource "aws_efs_mount_target" "target" {
  count           = 3
  file_system_id  = aws_efs_file_system.draft_storage.id
  subnet_id       = module.vpc.private_subnets[count.index]
  security_groups = [aws_security_group.efs_sg.id]
}