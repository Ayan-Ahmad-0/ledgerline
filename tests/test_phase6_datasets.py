import pytest
from pathlib import Path
from decimal import Decimal

from scripts.make_invoices import (
    generate_synthetic_invoice_data,
    generate_synthetic_dataset,
    apply_visual_degradation,
)
from scripts.load_cord import (
    load_cord_samples,
    evaluate_cord_predictions,
    validate_cord_evaluation_fields,
    CordRecord,
)
from PIL import Image


# ===========================================================================
# 1. Synthetic Generator Tests
# ===========================================================================

def test_synthetic_invoice_data_math_consistency():
    for layout_idx in range(1, 9):
        layout_id = f"layout_{layout_idx}"
        data = generate_synthetic_invoice_data(
            invoice_idx=layout_idx,
            layout_id=layout_id,
            multi_page=(layout_idx % 2 == 0)
        )

        assert data["layout_id"] == layout_id
        assert data["vendor_name"]
        assert data["invoice_number"].startswith("INV-2024-")
        assert len(data["line_items"]) > 0

        # Verify exact ground truth math
        calc_subtotal = sum(item["quantity"] * item["unit_price"] for item in data["line_items"])
        assert abs(Decimal(str(calc_subtotal)) - Decimal(str(data["subtotal"]))) < Decimal("0.05")

        calc_total = Decimal(str(data["subtotal"])) + Decimal(str(data["tax"]))
        assert abs(calc_total - Decimal(str(data["total"]))) < Decimal("0.02")


def test_visual_degradation_pipeline():
    img = Image.new("RGB", (600, 800), (255, 255, 255))
    degraded = apply_visual_degradation(
        image=img,
        target_dpi=150,
        skew_degrees=2.0,
        add_shadow=True,
        add_noise=True
    )

    # Asserts 150 DPI A4 scaled width
    assert degraded.width == 1240
    assert degraded.height > 1000
    assert degraded.mode == "RGB"


def test_generate_synthetic_dataset_files(tmp_path):
    output_dir = str(tmp_path / "synthetic")
    records = generate_synthetic_dataset(output_dir=output_dir, count_per_layout=1, degrade=False)

    assert len(records) == 8  # 1 per layout across all 8 layouts
    for r in records:
        assert Path(r["pdf_path"]).exists()
        assert Path(r["json_path"]).exists()


# ===========================================================================
# 2. CORD Dataset Loader & Unblended Scoring Tests
# ===========================================================================

def test_cord_loader_and_scoring(tmp_path):
    data_dir = str(tmp_path / "cord")
    samples = load_cord_samples(data_dir=data_dir, limit=5)

    assert len(samples) == 5
    first = samples[0]
    assert first.receipt_id.startswith("cord_receipt_")
    assert len(first.line_items) >= 2
    assert first.subtotal > 0
    assert first.total > first.subtotal

    # Prediction matching ground truth
    preds = [
        {
            "subtotal": float(s.subtotal),
            "tax": float(s.tax),
            "total": float(s.total),
            "line_items": s.line_items,
        }
        for s in samples
    ]

    report = evaluate_cord_predictions(samples, preds)
    assert report.dataset_name == "CORD"
    assert report.field_scores["subtotal"].f1_score == 1.0
    assert report.field_scores["total"].f1_score == 1.0
    assert report.overall_f1 == 1.0


def test_cord_forbids_vendor_and_date_scoring():
    # Attempting to score vendor on CORD must raise ValueError
    with pytest.raises(ValueError) as exc_info:
        validate_cord_evaluation_fields(["subtotal", "vendor_name"])
    assert "is forbidden on CORD dataset" in str(exc_info.value)

    # Attempting to score date on CORD must raise ValueError
    with pytest.raises(ValueError):
        validate_cord_evaluation_fields(["total", "invoice_date"])
