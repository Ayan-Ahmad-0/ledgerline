"""
Break-It Branches & Fault Injection Suite.
Verifies that 1-line deliberate faults in core hard-part modules
(stitching, calibration, idempotency, canary gate) turn the corresponding assertions RED (fail).
"""

import os
import pytest
from unittest.mock import patch

from app.extract.stitch import TableRow, TableCell, PageTable, stitch_multi_page_tables
from app.calibration.features import CalibrationSample, build_feature_vector
from app.calibration.fit import run_holdout_calibration
from app.extract.llm_adapter import llm_adapter, clear_page_cache, reset_network_call_count, get_network_call_count
from app.pipeline.canary import evaluate_canary_slice, CanaryGateFailureError


def test_breakit_stitching_fault_detects_failure():
    """When FAULT_MULTI_PAGE_STITCH=1, header dropping is broken (fails to drop header)."""
    p1_header = TableRow(cells=[TableCell("Item", 10.0, 50.0, 50.0, 65.0), TableCell("Amount", 55.0, 50.0, 200.0, 65.0)], page_number=1)
    p1_row = TableRow(cells=[TableCell("1", 11.0, 70.0, 48.0, 85.0), TableCell("100.00", 56.0, 70.0, 198.0, 85.0)], page_number=1)
    p2_header = TableRow(cells=[TableCell("Item", 10.0, 50.0, 50.0, 65.0), TableCell("Amount", 55.0, 50.0, 200.0, 65.0)], page_number=2)
    p2_row = TableRow(cells=[TableCell("2", 12.0, 70.0, 49.0, 85.0), TableCell("200.00", 54.0, 70.0, 199.0, 85.0)], page_number=2)

    with patch.dict(os.environ, {"FAULT_MULTI_PAGE_STITCH": "1"}):
        stitched = stitch_multi_page_tables([PageTable(1, [p1_header, p1_row]), PageTable(2, [p2_header, p2_row])])
        # Fault active: Repeated header was NOT dropped
        assert stitched.dropped_headers_count == 0  # PROVES FAULT BREAKS EXPECTED BEHAVIOR


def test_breakit_calibration_fault_detects_failure():
    """When FAULT_CALIBRATION=1, holdout calibration ECE exceeds 0.10 target."""
    samples = []
    for i in range(60):
        is_correct = int(i % 5 != 0)
        samples.append(
            CalibrationSample(
                features=build_feature_vector(0.9 if is_correct else 0.4, 0.9, 0.5, is_correct, is_correct),
                raw_confidence=0.9 if is_correct else 0.4,
                true_label=is_correct,
                layout_id="layout_1" if i < 45 else "layout_8",
                vendor_id="vendor_1" if i < 45 else "vendor_heldout",
            )
        )

    with patch.dict(os.environ, {"FAULT_CALIBRATION": "1"}):
        res = run_holdout_calibration(samples, heldout_layout="layout_8", heldout_vendor="vendor_heldout")
        # Fault active: ECE is distorted to 0.45 > 0.10 target
        assert res["meets_target"] is False
        assert res["calibrated_ece"] > 0.10


def test_breakit_idempotency_fault_detects_failure():
    """When FAULT_IDEMPOTENCY=1, page-level cache is bypassed (violates zero-call constraint)."""
    async def _run():
        clear_page_cache()
        reset_network_call_count()

        content = "Invoice 123"
        page_hash = "fault_hash"

        # First call
        await llm_adapter.extract_page(content, page_hash)
        assert get_network_call_count() == 1

        # Second call with fault active
        with patch.dict(os.environ, {"FAULT_IDEMPOTENCY": "1"}):
            await llm_adapter.extract_page(content, page_hash)
            # Fault active: network call count increased to 2 instead of remaining 1
            assert get_network_call_count() == 2

    import asyncio
    asyncio.run(_run())


def test_breakit_canary_fault_detects_failure():
    """When FAULT_CANARY_GATE=1, canary quality gate silently passes degraded runs."""
    degraded_pairs = [
        {"ground_truth": {"total": "100.00"}, "prediction": {"total": "999.00"}}
        for _ in range(50)
    ]

    with patch.dict(os.environ, {"FAULT_CANARY_GATE": "1"}):
        res = evaluate_canary_slice(degraded_pairs, baseline_f1=0.95, max_drop_points=2.0, raise_on_failure=False)
        # Fault active: passed is True despite severe degradation
        assert res.passed is True
