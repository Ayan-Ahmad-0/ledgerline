import pytest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.validate.errors import MathValidationError
from app.validate.math import (
    LineItem,
    validate_line_item_math,
    route_to_dead_letter,
)
from app.extract.stitch import (
    XInterval,
    TableCell,
    TableRow,
    PageTable,
    compute_header_fingerprint,
    stitch_multi_page_tables,
)
from app.db.session import DeadLetter


# ===========================================================================
# 1. Multi-Page Table Stitching & Header Dropping Tests
# ===========================================================================

def test_x_interval_overlap():
    i1 = XInterval(10.0, 50.0)  # width 40
    i2 = XInterval(30.0, 70.0)  # width 40, overlap 20
    assert i1.overlap(i2) == 20.0
    assert i1.overlap_ratio(i2) == 0.5

    i3 = XInterval(60.0, 100.0)
    assert i1.overlap(i3) == 0.0


def test_header_fingerprint_generation():
    tokens1 = ["item", "description", "qty", "unit_price", "amount"]
    tokens2 = ["item", "description", "qty", "unit_price", "amount"]
    tokens_scrambled = ["amount", "qty", "item", "description", "unit_price"]

    fp1 = compute_header_fingerprint(tokens1)
    fp2 = compute_header_fingerprint(tokens2)
    fp3 = compute_header_fingerprint(tokens_scrambled)

    assert fp1 == fp2
    assert fp1 == fp3  # Sorted normalization makes it robust


def test_multi_page_table_stitching_drops_repeated_headers():
    # Page 1: Header + 2 data rows
    p1_header = TableRow(
        cells=[
            TableCell("Item", 10.0, 50.0, 50.0, 65.0),
            TableCell("Description", 55.0, 50.0, 200.0, 65.0),
            TableCell("Qty", 205.0, 50.0, 250.0, 65.0),
            TableCell("Total", 255.0, 50.0, 320.0, 65.0),
        ],
        page_number=1,
    )
    p1_row1 = TableRow(
        cells=[
            TableCell("1", 11.0, 70.0, 48.0, 85.0),
            TableCell("Consulting Services", 56.0, 70.0, 198.0, 85.0),
            TableCell("2", 206.0, 70.0, 248.0, 85.0),
            TableCell("200.00", 256.0, 70.0, 318.0, 85.0),
        ],
        page_number=1,
    )
    p1_table = PageTable(page_number=1, rows=[p1_header, p1_row1])

    # Page 2: Repeated Header + 1 data row
    p2_header = TableRow(
        cells=[
            TableCell("Item", 10.0, 50.0, 50.0, 65.0),
            TableCell("Description", 55.0, 50.0, 200.0, 65.0),
            TableCell("Qty", 205.0, 50.0, 250.0, 65.0),
            TableCell("Total", 255.0, 50.0, 320.0, 65.0),
        ],
        page_number=2,
    )
    p2_row2 = TableRow(
        cells=[
            TableCell("2", 12.0, 70.0, 49.0, 85.0),
            TableCell("Hosting Plan", 54.0, 70.0, 199.0, 85.0),
            TableCell("1", 204.0, 70.0, 249.0, 85.0),
            TableCell("50.00", 254.0, 70.0, 319.0, 85.0),
        ],
        page_number=2,
    )
    p2_table = PageTable(page_number=2, rows=[p2_header, p2_row2])

    stitched = stitch_multi_page_tables([p1_table, p2_table])

    assert len(stitched.columns) == 4
    assert stitched.dropped_headers_count == 1  # Successfully identified and dropped Page 2 header!
    assert len(stitched.rows) == 2             # Only the 2 data rows remain

    # Verify column index alignment carries forward
    # Row 1 (Page 1)
    assert stitched.rows[0].cells[0].col_idx == 0
    assert stitched.rows[0].cells[1].col_idx == 1
    assert stitched.rows[0].cells[2].col_idx == 2
    assert stitched.rows[0].cells[3].col_idx == 3

    # Row 2 (Page 2)
    assert stitched.rows[1].cells[0].col_idx == 0
    assert stitched.rows[1].cells[1].col_idx == 1
    assert stitched.rows[1].cells[2].col_idx == 2
    assert stitched.rows[1].cells[3].col_idx == 3


# ===========================================================================
# 2. Math Validation & Dead Letter Routing Tests
# ===========================================================================

def test_math_validation_success():
    items = [
        LineItem(description="Widget A", quantity=Decimal("3"), unit_price=Decimal("10.00")),   # 30.00
        LineItem(description="Widget B", quantity=Decimal("2"), unit_price=Decimal("15.50")),   # 31.00
        LineItem(description="Widget C", quantity=Decimal("0.5"), unit_price=Decimal("20.00")), # 10.00
    ]
    # Total sum = 71.00
    res = validate_line_item_math(items, stated_subtotal=Decimal("71.00"))
    assert res.is_valid
    assert res.calculated_sum == Decimal("71.00")
    assert res.difference == Decimal("0.00")

    # Within 1.00 currency unit tolerance (e.g. 71.50 stated subtotal)
    res_tol = validate_line_item_math(items, stated_subtotal=Decimal("71.50"), tolerance=Decimal("1.00"))
    assert res_tol.is_valid
    assert res_tol.difference == Decimal("0.50")


def test_math_validation_failure_raises_typed_error():
    items = [
        LineItem(description="Item 1", quantity=Decimal("2"), unit_price=Decimal("50.00")), # 100.00
    ]
    # Stated subtotal is 200.00, diff is 100.00 > 1.00 tolerance
    with pytest.raises(MathValidationError) as exc_info:
        validate_line_item_math(items, stated_subtotal=Decimal("200.00"), tolerance=Decimal("1.00"))

    err = exc_info.value
    assert err.calculated_sum == Decimal("100.00")
    assert err.stated_subtotal == Decimal("200.00")
    assert err.difference == Decimal("100.00")
    assert err.details["error_code"] == "MATH_VALIDATION_ERROR"


def test_route_to_dead_letter_creates_record():
    async def _run():
        session = AsyncMock()
        session.add = MagicMock()
        doc_id = uuid.uuid4()
        f_hash = "abc123sha256"

        error_details = {
            "error_code": "MATH_VALIDATION_ERROR",
            "calculated": "100.00",
            "stated": "150.00",
        }

        entry = await route_to_dead_letter(
            session=session,
            document_id=doc_id,
            file_hash=f_hash,
            failure_reason="MATH_VALIDATION_ERROR",
            error_details=error_details,
        )

        assert entry.document_id == doc_id
        assert entry.file_hash == f_hash
        assert entry.failure_reason == "MATH_VALIDATION_ERROR"
        assert entry.error_details == error_details
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    import asyncio
    asyncio.run(_run())
