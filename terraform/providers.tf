terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # Helm provider installs in-cluster controllers (ALB controller,
    # ExternalDNS, ESO). The kubernetes provider is its dependency and
    # is useful for occasional CRD/namespace management.
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
    # HTTP data source fetches the AWS Load Balancer Controller IAM policy
    # JSON from the official source, pinned to a chart version below.
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Route53 DNSSEC key-signing keys MUST live in us-east-1 regardless of the
# cluster region. CloudFront + ACM for CloudFront also require us-east-1.
# We declare an aliased provider so the constraint is explicit; today it is
# the same region as the default provider, but this keeps us portable.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# ---------------------------------------------------------------------------
# Kubernetes + Helm provider authentication.
#
# Both providers hit the EKS API server. They authenticate by shelling out
# to `aws eks get-token` (the runner already has AWS CLI + valid OIDC
# creds). This is the 2026 recommended pattern — avoids baking a static
# token into TF state.
#
# Day-0 note: on the very first apply (no cluster yet) the providers
# defer configuration until the cluster resource exists. This works as
# long as the graph has aws_eks_cluster.main on the critical path before
# any helm_release / kubernetes_* resource — which it does via implicit
# dependencies on the cluster endpoint/CA below.
# ---------------------------------------------------------------------------

provider "kubernetes" {
  host                   = aws_eks_cluster.main.endpoint
  cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", aws_eks_cluster.main.name, "--region", var.aws_region]
  }
}

provider "helm" {
  kubernetes {
    host                   = aws_eks_cluster.main.endpoint
    cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", aws_eks_cluster.main.name, "--region", var.aws_region]
    }
  }
}