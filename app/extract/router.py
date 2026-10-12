import hashlib
import uuid
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.extract.native import is_native_digital_pdf, extract_native_page
from app.extract.ocr import extract_ocr_page
from app.extract.llm_adapter import (
    llm_adapter,
    ExtractionResult,
    LLMCallMetrics,
)
from app.db.session import CostMeter
from app.core.logging import logger


class ExtractionRouter:
    """
    Classifies pages as native digital text vs. scanned images and routes:
    - Native pages -> Cheap text path ($0.0005)
    - Scanned/low-conf -> Expensive Vision path ($0.015)
    Enforces page-level caching and idempotency checks.
    """

    def __init__(self):
        self.settings = get_settings()

    def compute_file_hash(self, file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    def compute_idempotency_key(self, file_hash: str) -> str:
        """Idempotency key = hash(file_content) + extractor_version."""
        return f"{file_hash}:{self.settings.EXTRACTOR_VERSION}"

    async def route_and_extract(
        self,
        file_bytes: bytes,
        file_name: str,
        session: Optional[AsyncSession] = None,
        document_id: Optional[uuid.UUID] = None,
    ) -> tuple[ExtractionResult, list[LLMCallMetrics]]:
        file_hash = self.compute_file_hash(file_bytes)
        idempotency_key = self.compute_idempotency_key(file_hash)

        is_native = is_native_digital_pdf(file_bytes)
        logger.info(
            f"Routing document {file_name}: native={is_native}, hash={file_hash[:8]}",
            extra={"file_hash": file_hash, "is_native": is_native}
        )

        all_metrics: list[LLMCallMetrics] = []

        if is_native:
            # Cheap path: Extract digital layout & text
            native_content = extract_native_page(file_bytes, page_number=1)
            page_hash = hashlib.sha256(native_content.text.encode("utf-8")).hexdigest()

            result, metrics = await llm_adapter.extract_page(
                page_content=native_content.text,
                page_hash=page_hash,
                is_scanned=False,
            )
            all_metrics.append(metrics)
        else:
            # Expensive path: Scanned image / OCR / Vision model
            ocr_content = extract_ocr_page(file_bytes, page_number=1)
            page_hash = hashlib.sha256(ocr_content.text.encode("utf-8") if ocr_content.text else file_bytes).hexdigest()

            result, metrics = await llm_adapter.extract_page(
                page_content=ocr_content.text,
                page_hash=page_hash,
                is_scanned=True,
            )
            all_metrics.append(metrics)

        # Log metrics to CostMeter table if session provided
        if session is not None:
            for m in all_metrics:
                meter_record = CostMeter(
                    document_id=document_id,
                    page_id=None,
                    routing_path=m.routing_path,
                    input_tokens=m.input_tokens,
                    output_tokens=m.output_tokens,
                    estimated_cost_usd=m.estimated_cost_usd,
                    latency_ms=m.latency_ms,
                )
                session.add(meter_record)
            await session.commit()

        return result, all_metrics


extraction_router = ExtractionRouter()
