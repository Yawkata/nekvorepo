###############################################################################
# In-cluster controllers — installed via Helm so terraform owns their lifecycle.
#
#   * AWS Load Balancer Controller — turns Ingress → ALB.
#   * Cluster Autoscaler           — scales the managed node group on pending pods.
#   * External Secrets Operator    — syncs SSM params → K8s Secrets.
#
# Pod Identity associations in pod_identity.tf handle AWS auth — no IRSA.
###############################################################################

resource "kubernetes_namespace" "chrono" {
  metadata {
    name = local.app_namespace
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "pod-security.kubernetes.io/enforce" = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
    }
  }

  depends_on = [module.eks]
}

resource "kubernetes_namespace" "external_secrets" {
  metadata {
    name = "external-secrets"
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  depends_on = [module.eks]
}

resource "helm_release" "aws_lb_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = "1.10.1"

  # Controller reads ALB/NLB status from the cluster — survives node rolls.
  set {
    name  = "clusterName"
    value = module.eks.cluster_name
  }
  set {
    name  = "serviceAccount.create"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }
  set {
    name  = "replicaCount"
    value = "2"
  }
  set {
    name  = "region"
    value = var.aws_region
  }
  set {
    name  = "vpcId"
    value = module.vpc.vpc_id
  }
  set {
    name  = "enableServiceMutatorWebhook"
    value = "false"
  }

  depends_on = [
    module.eks,
    aws_eks_pod_identity_association.lb_controller,
  ]
}

resource "helm_release" "cluster_autoscaler" {
  name       = "cluster-autoscaler"
  namespace  = "kube-system"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = "9.43.2"

  set {
    name  = "autoDiscovery.clusterName"
    value = module.eks.cluster_name
  }
  set {
    name  = "awsRegion"
    value = var.aws_region
  }
  set {
    name  = "rbac.serviceAccount.create"
    value = "true"
  }
  set {
    name  = "rbac.serviceAccount.name"
    value = "cluster-autoscaler"
  }
  set {
    name  = "extraArgs.balance-similar-node-groups"
    value = "true"
  }
  set {
    name  = "extraArgs.skip-nodes-with-system-pods"
    value = "false"
  }

  depends_on = [
    module.eks,
    aws_eks_pod_identity_association.cluster_autoscaler,
  ]
}

resource "helm_release" "external_secrets" {
  name       = "external-secrets"
  namespace  = kubernetes_namespace.external_secrets.metadata[0].name
  repository = "https://charts.external-secrets.io"
  chart      = "external-secrets"
  version    = "0.10.7"

  set {
    name  = "installCRDs"
    value = "true"
  }
  set {
    name  = "serviceAccount.create"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "external-secrets"
  }

  depends_on = [
    module.eks,
    aws_eks_pod_identity_association.external_secrets,
  ]
}

###############################################################################
# EFS CSI — StorageClass for dynamic PV provisioning against the draft FS.
###############################################################################

resource "kubernetes_storage_class_v1" "efs" {
  metadata {
    name = "efs-sc"
  }
  storage_provisioner = "efs.csi.aws.com"
  reclaim_policy      = "Retain"
  parameters = {
    provisioningMode = "efs-ap"
    fileSystemId     = aws_efs_file_system.draft_storage.id
    directoryPerms   = "700"
    uid              = "65532"
    gid              = "65532"
  }

  depends_on = [module.eks]
}

###############################################################################
# gp3 as default storage class — cheaper + faster than gp2, baseline 3000 IOPS.
###############################################################################

resource "kubernetes_annotations" "gp2_not_default" {
  api_version = "storage.k8s.io/v1"
  kind        = "StorageClass"
  metadata {
    name = "gp2"
  }
  annotations = {
    "storageclass.kubernetes.io/is-default-class" = "false"
  }
  force = true

  depends_on = [module.eks]
}

resource "kubernetes_storage_class_v1" "gp3" {
  metadata {
    name = "gp3"
    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }
  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Delete"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    type      = "gp3"
    encrypted = "true"
    kmsKeyId  = aws_kms_key.ebs.arn
  }

  depends_on = [module.eks]
}
