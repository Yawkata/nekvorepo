# k8s/ — Application manifests for chrono-vcs on EKS

Kustomize layout. `base/` is environment-agnostic; overlays encode per-env
differences (image tags, replica counts, resource tuning, hostnames).

```
k8s/
├── base/                  # canonical manifests
└── overlays/
    ├── staging/           # staging tuning + image tags
    └── prod/              # prod tuning + HA + image tags
```

## Rendering locally

```powershell
# Render staging overlay without applying
kubectl kustomize k8s/overlays/staging

# Apply
kubectl apply -k k8s/overlays/staging
```

## Image strategy

- CI builds per-service images tagged `<git-sha>` and pushes to ECR.
- `kustomize edit set image` rewrites the `newTag` in the overlay before apply.
- Tags are immutable (ECR policy); a redeploy = a new SHA, never an overwrite.

## Secret strategy (MVP)

Plain Kubernetes Secrets, created/updated by the `k8s-deploy` workflow
directly from GitHub Actions secrets and Terraform outputs. Pods consume
via `envFrom: secretRef`. EKS envelope-encryption ([eks.tf:131](../terraform/eks.tf:131))
keeps them encrypted at rest in etcd.

Secrets the workflow creates into namespace `chrono-vcs`:

| k8s Secret name       | Source                                                   |
|-----------------------|----------------------------------------------------------|
| `db-app-credentials`  | TF outputs (RDS) + `TF_VAR_DB_PASSWORD` GH secret        |
| `identity-cognito`    | TF outputs (Cognito pool + client id/secret)             |
| `identity-misc`       | `JWT_SIGNING_KEY` GH secret                              |

To rotate: change the source value, re-run the workflow. To add rotation
automation later, re-introduce ESO + Secrets Manager; the ServiceAccounts
and IAM scaffolding already fit that upgrade path.
