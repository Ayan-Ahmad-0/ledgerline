import pytest
import numpy as np
from decimal import Decimal

from app.extract.llm_adapter import (
    llm_adapter,
    clear_page_cache,
    reset_network_call_count,
    get_network_call_count,
)
from app.extract.router import extraction_router
from app.calibration.features import build_feature_vector, CalibrationSample
from app.calibration.ece import compute_ece_equal_mass
from app.calibration.fit import (
    PlattScalingCalibrator,
    IsotonicCalibrator,
    MultiSignalCalibrator,
    run_holdout_calibration,
)


# ===========================================================================
# 1. Idempotency & Zero Network Call Caching Tests (Directive #5 & #7)
# ===========================================================================

def test_page_level_caching_makes_zero_network_calls_on_rerun():
    async def _run():
        clear_page_cache()
        reset_network_call_count()

        content = "ACME Supplies\nInvoice #: INV-001\nTotal: 100.00"
        page_hash = "hash_page_12345"

        # First run: Cache miss -> makes 1 network call
        res1, metrics1 = await llm_adapter.extract_page(
            page_content=content,
            page_hash=page_hash,
            is_scanned=False,
            extractor_version="v1.0.0",
            prompt_version="v1",
        )
        assert get_network_call_count() == 1
        assert not metrics1.is_cache_hit
        assert metrics1.routing_path == "native_cheap_text"
        assert res1.total == Decimal("100.00")

        # Second run (re-run of the same batch/page):
        # Must make EXACTLY ZERO additional network calls
        res2, metrics2 = await llm_adapter.extract_page(
            page_content=content,
            page_hash=page_hash,
            is_scanned=False,
            extractor_version="v1.0.0",
            prompt_version="v1",
        )

        assert get_network_call_count() == 1  # Exactly 0 new network calls!
        assert metrics2.is_cache_hit
        assert metrics2.routing_path == "cache_hit"
        assert metrics2.estimated_cost_usd == Decimal("0.000000")
        assert res2.total == res1.total

    import asyncio
    asyncio.run(_run())


def test_smart_router_classifies_paths():
    async def _run():
        clear_page_cache()
        reset_network_call_count()

        # 1. Native text file
        native_bytes = (
            b"%PDF-1.4\n/Type /Page\n/Font\nBT\n" +
            b"ACME Corp Invoice #INV-100 Total: 250.00 USD\nET\n" * 5
        )
        res_nat, metrics_nat = await extraction_router.route_and_extract(
            file_bytes=native_bytes,
            file_name="digital_invoice.pdf"
        )
        assert metrics_nat[0].routing_path == "native_cheap_text"
        assert metrics_nat[0].estimated_cost_usd == Decimal("0.000500")

        # 2. Scanned / image file
        scanned_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 50
        res_scan, metrics_scan = await extraction_router.route_and_extract(
            file_bytes=scanned_bytes,
            file_name="scanned_receipt.png"
        )
        assert metrics_scan[0].routing_path == "scanned_expensive_vision"
        assert metrics_scan[0].estimated_cost_usd == Decimal("0.015000")

    import asyncio
    asyncio.run(_run())


# ===========================================================================
# 2. Confidence Calibration & OOD Holdout Tests (Directive #2)
# ===========================================================================

def test_equal_mass_ece_perfect_calibration():
    # When predicted probabilities match exact empirical accuracy
    # Bins of equal size: 0.2 and 0.8 confidences
    confidences = [0.2] * 50 + [0.8] * 50
    # Exactly 20% 1s in first bin, 80% 1s in second bin
    labels = [1] * 10 + [0] * 40 + [1] * 40 + [0] * 10

    ece = compute_ece_equal_mass(confidences, labels, num_bins=2)
    assert abs(ece - 0.0) < 1e-4


def test_holdout_calibration_meets_ece_target():
    """
    Generate synthetic multi-layout dataset with raw overconfident probabilities.
    Assert that holdout calibration on held-out layout and vendor achieves ECE <= 0.10.
    """
    np.random.seed(42)
    samples: list[CalibrationSample] = []

    layouts = [f"layout_{i}" for i in range(1, 9)]
    vendors = [f"vendor_{v}" for v in ["acme", "globex", "initech", "heldout_corp"]]

    for i in range(1000):
        layout = layouts[i % len(layouts)]
        vendor = vendors[i % len(vendors)]

        # Simulate true difficulty & overconfident raw token prob
        true_quality = 0.85 if layout != "layout_8" else 0.75
        is_correct = int(np.random.rand() < true_quality)

        # Overconfident uncalibrated LLM confidence (e.g. mostly 0.95 - 0.99)
        raw_token_prob = float(np.clip(0.70 + 0.29 * is_correct + np.random.normal(0, 0.05), 0.5, 0.99))
        ocr_conf = float(np.clip(0.85 + 0.15 * is_correct + np.random.normal(0, 0.05), 0.4, 1.0))
        math_check = bool(is_correct if np.random.rand() < 0.9 else not is_correct)
        two_pass = bool(is_correct if np.random.rand() < 0.85 else not is_correct)

        feats = build_feature_vector(
            token_probability=raw_token_prob,
            ocr_char_confidence=ocr_conf,
            relative_y_position=float(np.random.rand()),
            math_validation_passed=math_check,
            two_pass_agreement=two_pass,
        )

        samples.append(
            CalibrationSample(
                features=feats,
                raw_confidence=raw_token_prob,
                true_label=is_correct,
                layout_id=layout,
                vendor_id=vendor,
            )
        )

    # Run holdout calibration holding out layout_8 and vendor_heldout_corp
    eval_result = run_holdout_calibration(
        samples=samples,
        heldout_layout="layout_8",
        heldout_vendor="vendor_heldout_corp",
        num_bins=10,
        target_ece=0.10,
    )

    assert eval_result["train_samples_count"] > 0
    assert eval_result["test_samples_count"] > 0
    # Assert calibrated ECE meets the <= 0.10 target
    assert eval_result["calibrated_ece"] <= 0.10
    assert eval_result["meets_target"] is True
