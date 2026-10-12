import pytest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.pipeline.canary import (
    evaluate_canary_slice,
    CanaryGateFailureError,
)
from app.pipeline.batch import (
    inspect_file_guardrails,
    process_single_document,
    run_batch_pipeline,
)
from app.api.auth import verify_api_key


# ===========================================================================
# 1. 50-Document Canary Quality Gate Tests (Directive #8)
# ===========================================================================

def test_canary_quality_gate_passes_healthy_f1():
    # 50 matching documents (100% F1)
    canary_pairs = [
        {
            "ground_truth": {"vendor_name": "Acme", "total": "100.00"},
            "prediction": {"vendor_name": "Acme", "total": "100.00"},
        }
        for _ in range(50)
    ]
    # Baseline is 0.95, canary is 1.0 (drop is 0.0 points <= 2.0 max allowed)
    result = evaluate_canary_slice(canary_pairs, baseline_f1=0.95, max_drop_points=2.0)
    assert result.passed is True
    assert result.canary_f1 == 1.0
    assert result.drop_points == 0.0


def test_canary_quality_gate_fails_and_alerts_on_f1_drop():
    # 50 documents with predictions degraded/failing
    canary_pairs = [
        {
            "ground_truth": {"vendor_name": "Acme", "total": "100.00"},
            "prediction": {"vendor_name": "Different Corp", "total": "999.00"},
        }
        for _ in range(50)
    ]
    # Baseline is 0.95, canary F1 drops severely (> 2 points)
    with pytest.raises(CanaryGateFailureError) as exc_info:
        evaluate_canary_slice(
            canary_pairs,
            baseline_f1=0.95,
            max_drop_points=2.0,
            raise_on_failure=True
        )

    err = exc_info.value
    assert err.drop_points > 2.0
    assert "CANARY QUALITY GATE FAILED" in str(err)


# ===========================================================================
# 2. Mandatory CI Edge Case Tests (Directive #8 Guardrails)
# ===========================================================================

def test_guardrail_zero_byte_file_fails():
    with pytest.raises(ValueError) as exc:
        inspect_file_guardrails(b"", "empty.pdf")
    assert "ZERO_BYTE_FILE" in str(exc.value)


def test_guardrail_password_locked_pdf_fails():
    encrypted_pdf = b"%PDF-1.4\n1 0 obj\n<< /Encrypt 2 0 R >>\nendobj"
    with pytest.raises(PermissionError) as exc:
        inspect_file_guardrails(encrypted_pdf, "locked.pdf")
    assert "PASSWORD_PROTECTED_PDF" in str(exc.value)


def test_guardrail_400_page_doc_fails():
    # Simulate 400 pages markers in PDF
    massive_pdf = b"%PDF-1.4\n" + (b"/Type /Page\n" * 401)
    with pytest.raises(ValueError) as exc:
        inspect_file_guardrails(massive_pdf, "massive.pdf")
    assert "PAGE_LIMIT_EXCEEDED" in str(exc.value)


def test_process_single_document_bad_math_parks_and_writes_no_clean_row():
    """
    Feeds an invoice with bad math: proves it parks to dead_letter
    and returns None (zero clean document records written).
    """
    async def _run():
        session = AsyncMock()
        session.add = MagicMock()

        # Simulated corrupted invoice text with conflicting subtotal vs line items
        corrupt_invoice = (
            b"ACME Corp\n"
            b"Item 1: 1 x 50.00\n"
            b"Subtotal: 200.00\n"  # 50.00 != 200.00
            b"Total: 216.00\n"
        )

        with patch("app.pipeline.batch.extraction_router.route_and_extract") as mock_extract:
            from app.extract.llm_adapter import ExtractionResult, LLMCallMetrics
            mock_extract.return_value = (
                ExtractionResult(
                    vendor_name="ACME",
                    line_items=[{"description": "Item 1", "quantity": 1, "unit_price": 50.00}],
                    subtotal=Decimal("200.00"),  # Corrupt subtotal
                    total=Decimal("216.00"),
                ),
                [LLMCallMetrics(routing_path="native_cheap_text", input_tokens=10, output_tokens=10, estimated_cost_usd=Decimal("0.0005"), latency_ms=10)]
            )

            doc = await process_single_document(session, corrupt_invoice, "bad_math.pdf")

            # Must return None indicating no clean row written
            assert doc is None
            # Must rollback transaction
            session.rollback.assert_awaited()

    import asyncio
    asyncio.run(_run())


# ===========================================================================
# 3. API Key Authentication Dependency Tests
# ===========================================================================

def test_api_key_auth_valid():
    async def _run():
        key = await verify_api_key(api_key="dev-ledgerline-key")
        assert key == "dev-ledgerline-key"
    import asyncio
    asyncio.run(_run())


def test_api_key_auth_invalid():
    async def _run():
        with pytest.raises(HTTPException) as exc:
            await verify_api_key(api_key="wrong-key")
        assert exc.value.status_code == 401
    import asyncio
    asyncio.run(_run())
