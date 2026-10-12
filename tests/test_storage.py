import hashlib
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError
from app.core.storage import R2StorageService


def test_storage_key_generation():
    service = R2StorageService()
    file_bytes = b"%PDF-1.4 test invoice content"
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    key = service.build_storage_key(file_hash, "invoice_001.pdf")

    assert key == f"raw/{file_hash[:2]}/{file_hash}/invoice_001.pdf"


def test_local_fallback_upload_and_download():
    # Test upload and download when R2 credentials are not set
    service = R2StorageService()
    content = b"PDF-raw-bytes-sample"

    key = service.upload_file(
        file_bytes=content,
        file_name="receipt.pdf",
        content_type="application/pdf",
    )

    assert key.startswith("raw/")
    assert key.endswith("/receipt.pdf")

    downloaded = service.download_file(key)
    assert downloaded == content

    # Test presigned URL fallback
    url = service.get_presigned_url(key)
    assert key in url

    # Test delete
    assert service.delete_file(key) is True
    with pytest.raises(FileNotFoundError):
        service.download_file(key)


def test_r2_boto3_client_interaction():
    service = R2StorageService()
    mock_s3 = MagicMock()
    service._s3_client = mock_s3

    with patch.object(R2StorageService, "is_configured", return_value=True):
        content = b"sample pdf content"
        key = service.upload_file(
            file_bytes=content,
            file_name="test.pdf",
            content_type="application/pdf",
            file_hash="abcdef1234567890",
        )

        assert key == "raw/ab/abcdef1234567890/test.pdf"
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Key"] == key
        assert call_kwargs["Body"] == content
        assert call_kwargs["ContentType"] == "application/pdf"
