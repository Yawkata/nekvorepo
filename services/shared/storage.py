import os
import boto3
from botocore.config import Config

class StorageManager:
    # Local: /mnt/efs/drafts (bind mount) | EKS: /mnt/efs/drafts (EFS)
    DRAFT_BASE = os.getenv("DRAFT_STORAGE_PATH", "/mnt/efs/drafts")
    S3_BUCKET = os.getenv("S3_REPO_BUCKET")

    def __init__(self):
        # Local: Uses ~/.aws/credentials | EKS: Uses IRSA (IAM Roles for Service Accounts)
        self.s3 = boto3.client("s3", config=Config(region_name=os.getenv("AWS_REGION")))

    def get_draft_path(self, user_id: str, repo_id: str, file_path: str = "") -> str:
        return os.path.join(self.DRAFT_BASE, user_id, repo_id, file_path)

    def generate_presigned_url(self, content_hash: str, expires_in: int = 3600):
        # Used for "View Mode" to fetch directly from S3
        return self.s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.S3_BUCKET, 'Key': content_hash},
            ExpiresIn=expires_in
        )