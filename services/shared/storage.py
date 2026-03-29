import os
import boto3
from botocore.config import Config

# 2026 Best Practice: Create the client ONCE at the module level.
# This reuses the underlying urllib3 connection pools across requests.
_s3_client = boto3.client(
    "s3", 
    config=Config(
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        retries={'max_attempts': 3, 'mode': 'standard'}
    )
)

class StorageManager:
    # Local: /mnt/efs/drafts (bind mount) | EKS: /mnt/efs/drafts (EFS)
    DRAFT_BASE = os.getenv("DRAFT_STORAGE_PATH", "/mnt/efs/drafts")
    S3_BUCKET = os.getenv("S3_REPO_BUCKET")

    def __init__(self):
        # Assign the global client to the instance
        self.s3 = _s3_client

    def get_draft_path(self, user_id: str, repo_id: str, file_path: str = "") -> str:
        return os.path.join(self.DRAFT_BASE, user_id, repo_id, file_path)

    def blob_exists(self, content_hash: str) -> bool:
        """Return True if an object with this SHA-256 key already exists in S3."""
        from botocore.exceptions import ClientError
        try:
            self.s3.head_object(Bucket=self.S3_BUCKET, Key=content_hash)
            return True
        except ClientError:
            return False

    def upload_blob(self, data: bytes, content_hash: str, content_type: str) -> None:
        """Upload bytes to S3 keyed by SHA-256 hex. No-op if the key already exists."""
        if not self.blob_exists(content_hash):
            self.s3.put_object(
                Bucket=self.S3_BUCKET,
                Key=content_hash,
                Body=data,
                ContentType=content_type,
            )

    def generate_presigned_url(self, content_hash: str, expires_in: int = 3600):
        # Used for "View Mode" to fetch directly from S3
        return self.s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.S3_BUCKET, 'Key': content_hash},
            ExpiresIn=expires_in
        )