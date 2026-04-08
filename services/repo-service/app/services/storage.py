"""
S3 blob storage manager — repo-service only.

Handles content-addressed blob operations: existence checks, uploads,
downloads, and presigned URL generation.  All objects are keyed by their
SHA-256 hex digest (content-addressed storage).

EFS draft path logic lives in EFSService, not here.
"""
import os
import boto3
from botocore.config import Config

# Module-level singleton — credential resolution + connection pool init is
# expensive.  Re-using the client across requests is the standard boto3 pattern
# and is safe for concurrent read operations.
_s3_client = boto3.client(
    "s3",
    config=Config(
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        retries={"max_attempts": 3, "mode": "standard"},
    ),
)


class StorageManager:
    S3_BUCKET = os.getenv("S3_REPO_BUCKET")

    def __init__(self):
        self.s3 = _s3_client

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

    def download_blob(self, content_hash: str) -> bytes:
        """Download blob bytes from S3 by content hash (= S3 object key)."""
        response = self.s3.get_object(Bucket=self.S3_BUCKET, Key=content_hash)
        return response["Body"].read()

    def generate_presigned_url(self, content_hash: str, expires_in: int = 3600) -> str:
        """Generate a presigned GET URL for a blob. Used by view-mode endpoints."""
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.S3_BUCKET, "Key": content_hash},
            ExpiresIn=expires_in,
        )
