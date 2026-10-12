"""
Cloudflare R2 Storage Client for raw invoice/receipt PDFs and images.
R2 is S3-compatible, accessed via boto3 with endpoint_url and 'auto' region.
Includes automatic fallback to local memory storage when R2 credentials are unset.
"""

import hashlib
import io
from typing import Optional
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.logging import logger


class R2StorageService:
    def __init__(self):
        self.settings = get_settings()
        self._s3_client = None
        self._local_mock_store: dict[str, bytes] = {}

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.R2_ACCESS_KEY_ID
            and self.settings.R2_SECRET_ACCESS_KEY
            and (self.settings.R2_ACCOUNT_ID or self.settings.R2_ENDPOINT_URL)
        )

    def _get_client(self):
        if not self.is_configured:
            return None

        if self._s3_client is None:
            endpoint = self.settings.R2_ENDPOINT_URL or (
                f"https://{self.settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            )
            self._s3_client = boto3.client(
                service_name="s3",
                endpoint_url=endpoint,
                aws_access_key_id=self.settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=self.settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
                config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
            )
        return self._s3_client

    def build_storage_key(self, file_hash: str, file_name: str) -> str:
        """Create structured storage path: raw/<prefix>/<hash>/<filename>"""
        prefix = file_hash[:2]
        return f"raw/{prefix}/{file_hash}/{file_name}"

    def upload_file(
        self,
        file_bytes: bytes,
        file_name: str,
        content_type: str,
        file_hash: Optional[str] = None
    ) -> str:
        """
        Upload raw document (PDF or image) to Cloudflare R2 bucket.
        Returns the persistent R2 storage_key.
        """
        if file_hash is None:
            file_hash = hashlib.sha256(file_bytes).hexdigest()

        storage_key = self.build_storage_key(file_hash, file_name)
        client = self._get_client()

        if client is not None:
            try:
                client.put_object(
                    Bucket=self.settings.R2_BUCKET_NAME,
                    Key=storage_key,
                    Body=file_bytes,
                    ContentType=content_type,
                    Metadata={
                        "sha256": file_hash,
                        "original_filename": file_name,
                    },
                )
                logger.info(
                    f"Uploaded file to R2: {storage_key}",
                    extra={"storage_key": storage_key, "file_size": len(file_bytes)}
                )
            except ClientError as e:
                logger.error(f"Failed to upload {storage_key} to R2: {str(e)}", exc_info=True)
                raise
        else:
            # Fallback to local memory storage for offline development/testing
            logger.warning(f"R2 credentials not set. Storing {storage_key} in local memory store.")
            self._local_mock_store[storage_key] = file_bytes

        return storage_key

    def download_file(self, storage_key: str) -> bytes:
        """Download raw document bytes from R2 or local fallback."""
        client = self._get_client()

        if client is not None:
            try:
                response = client.get_object(
                    Bucket=self.settings.R2_BUCKET_NAME,
                    Key=storage_key,
                )
                return response["Body"].read()
            except ClientError as e:
                logger.error(f"Failed to download {storage_key} from R2: {str(e)}", exc_info=True)
                raise
        else:
            if storage_key not in self._local_mock_store:
                raise FileNotFoundError(f"Storage key '{storage_key}' not found in local store.")
            return self._local_mock_store[storage_key]

    def get_presigned_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        """Generate a presigned GET URL for secure client access."""
        client = self._get_client()

        if client is not None:
            try:
                url = client.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={
                        "Bucket": self.settings.R2_BUCKET_NAME,
                        "Key": storage_key,
                    },
                    ExpiresIn=expires_in_seconds,
                )
                return url
            except ClientError as e:
                logger.error(f"Failed to generate presigned URL for {storage_key}: {str(e)}", exc_info=True)
                raise
        else:
            return f"https://mock-r2.local/{self.settings.R2_BUCKET_NAME}/{storage_key}"

    def delete_file(self, storage_key: str) -> bool:
        """Delete document from R2."""
        client = self._get_client()

        if client is not None:
            try:
                client.delete_object(
                    Bucket=self.settings.R2_BUCKET_NAME,
                    Key=storage_key,
                )
                return True
            except ClientError as e:
                logger.error(f"Failed to delete {storage_key} from R2: {str(e)}", exc_info=True)
                return False
        else:
            if storage_key in self._local_mock_store:
                del self._local_mock_store[storage_key]
                return True
            return False


# Global singleton instance
storage_service = R2StorageService()
