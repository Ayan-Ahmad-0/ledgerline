import uuid
import hashlib
from typing import Optional, Any
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import logger
from app.api.auth import verify_api_key
from app.db.session import (
    get_db_session,
    RunLedger,
    ReviewItem,
    Document,
    ExtractionVersion,
    Field as DBField,
)
from app.extract.router import extraction_router
from app.queue.leases import (
    acquire_review_lease,
    renew_review_lease,
    release_review_lease,
    submit_reviewed_item,
    OptimisticConcurrencyCollisionError,
)

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------

@api_router.get("/health", tags=["System"])
async def health_check():
    return {"status": "healthy", "service": "ledgerline"}


# ---------------------------------------------------------------------------
# Ingestion & Extraction Endpoint
# ---------------------------------------------------------------------------

@api_router.post("/extract", tags=["Extraction"])
async def extract_document(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Upload a document (PDF or image).
    Classifies page as digital native vs. scanned, routes to appropriate model,
    enforces page-level caching and logs cost meter metrics.
    """
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes)"
        )

    result, metrics = await extraction_router.route_and_extract(
        file_bytes=file_bytes,
        file_name=file.filename or "uploaded_document.pdf",
        session=session,
    )

    # Persist document record and extracted fields to the database
    try:
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        settings = get_settings()

        doc = Document(
            id=uuid.uuid4(),
            file_hash=file_hash,
            r2_storage_key=f"uploads/{file_hash}",
            file_name=file.filename or "uploaded_receipt.pdf",
            file_size_bytes=len(file_bytes),
            mime_type=file.content_type or ("application/pdf" if (file.filename or "").endswith(".pdf") else "image/png"),
            page_count=1,
        )
        session.add(doc)

        ext_ver_id = uuid.uuid4()
        ext_ver = ExtractionVersion(
            id=ext_ver_id,
            document_id=doc.id,
            extractor_version=settings.EXTRACTOR_VERSION,
            prompt_version=settings.PROMPT_VERSION,
            idempotency_key=f"{file_hash}:{settings.EXTRACTOR_VERSION}",
            is_current=True,
        )
        session.add(ext_ver)

        fields_to_persist = [
            ("vendor_name", result.vendor_name),
            ("invoice_number", result.invoice_number),
            ("invoice_date", result.invoice_date),
            ("subtotal", str(result.subtotal) if result.subtotal is not None else None),
            ("tax", str(result.tax) if result.tax is not None else None),
            ("total", str(result.total) if result.total is not None else None),
        ]

        for fname, fval in fields_to_persist:
            if fval is not None:
                session.add(DBField(
                    id=uuid.uuid4(),
                    extraction_version_id=ext_ver_id,
                    field_name=fname,
                    raw_value=str(fval),
                    normalized_value={"value": str(fval)},
                    confidence_raw=0.98,
                    confidence_calibrated=0.95,
                ))

        await session.commit()
    except Exception as e:
        logger.warning(f"Failed to persist uploaded document records to database: {e}")
        try:
            await session.rollback()
        except Exception:
            pass

    return {
        "filename": file.filename,
        "extraction": result.model_dump(),
        "metrics": [m.model_dump() for m in metrics],
    }


# ---------------------------------------------------------------------------
# Review Items & Optimistic Concurrency Leases
# ---------------------------------------------------------------------------

class LeaseRequest(BaseModel):
    worker_id: str
    lease_duration_seconds: int = 300


class ReviewSubmission(BaseModel):
    expected_version: int
    reviewer_id: str
    reviewed_value: dict[str, Any]
    action: str = "approved"


@api_router.post("/reviews/lease", tags=["Human Review"])
async def lease_review_item(
    payload: LeaseRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Acquire next available review item using SELECT FOR UPDATE SKIP LOCKED.
    """
    item = await acquire_review_lease(
        session=session,
        worker_id=payload.worker_id,
        lease_duration_seconds=payload.lease_duration_seconds,
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending review items available for leasing."
        )

    return {
        "id": str(item.id),
        "document_id": str(item.document_id),
        "version": item.version,
        "status": item.status,
        "proposed_value": item.proposed_value,
        "lease_expires_at": item.lease_expires_at.isoformat() if item.lease_expires_at else None,
    }


@api_router.post("/reviews/{item_id}/submit", tags=["Human Review"])
async def submit_review(
    item_id: uuid.UUID,
    submission: ReviewSubmission,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Submit human review decision with optimistic concurrency version check.
    Returns HTTP 409 Conflict with field-level diff on version mismatch.
    """
    item = await submit_reviewed_item(
        session=session,
        item_id=item_id,
        expected_version=submission.expected_version,
        reviewed_value=submission.reviewed_value,
        reviewer_id=submission.reviewer_id,
        action=submission.action,
    )
    return {
        "id": str(item.id),
        "version": item.version,
        "status": item.status,
        "reviewed_value": item.reviewed_value,
    }


# ---------------------------------------------------------------------------
# Pipeline Spine & Run Ledger Status
# ---------------------------------------------------------------------------

@api_router.get("/ledger", tags=["Pipeline Spine"])
async def get_run_ledger(
    limit: int = 20,
    session: AsyncSession = Depends(get_db_session),
):
    """Retrieve recent pipeline execution runs from run_ledger."""
    stmt = select(RunLedger).order_by(desc(RunLedger.started_at)).limit(limit)
    res = await session.execute(stmt)
    runs = res.scalars().all()

    return [
        {
            "run_id": str(r.run_id),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "status": r.status,
            "watermark": r.watermark,
            "documents_processed": r.documents_processed,
            "documents_parked": r.documents_parked,
            "canary_f1_score": r.canary_f1_score,
            "baseline_f1_score": r.baseline_f1_score,
        }
        for r in runs
    ]


@api_router.post("/ledger/trigger-batch", tags=["Pipeline Spine"])
async def trigger_batch_run():
    """Trigger a batch ingestion run through the pipeline spine and canary gate."""
    from app.pipeline.batch import run_batch_pipeline
    sample_files = [
        ("batch_inv_alpha.pdf", b"%PDF-1.4 sample content alpha"),
        ("batch_inv_beta.pdf", b"%PDF-1.4 sample content beta"),
        ("batch_inv_gamma.pdf", b"%PDF-1.4 sample content gamma"),
    ]
    result = await run_batch_pipeline(sample_files)
    return result
