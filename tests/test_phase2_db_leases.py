import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException

from app.queue.leases import (
    compute_field_diff,
    OptimisticConcurrencyCollisionError,
    submit_reviewed_item,
    renew_review_lease,
    release_review_lease,
)
from app.queue.reaper import reap_expired_leases
from app.db.session import (
    Document,
    Page,
    ExtractionVersion,
    Field,
    FieldFeature,
    ReviewItem,
    RunLedger,
    DeadLetter,
    CostMeter,
    BackfillProgress,
)


def test_compute_field_diff():
    original = {"vendor": "Acme Corp", "total": "100.00", "currency": "USD"}
    updated = {"vendor": "Acme Corp", "total": "105.00", "notes": "manual fix"}

    diff = compute_field_diff(original, updated)

    assert "total" in diff
    assert diff["total"]["server_value"] == "100.00"
    assert diff["total"]["submitted_value"] == "105.00"

    assert "currency" in diff
    assert diff["currency"]["server_value"] == "USD"
    assert diff["currency"]["submitted_value"] is None

    assert "notes" in diff
    assert diff["notes"]["server_value"] is None
    assert diff["notes"]["submitted_value"] == "manual fix"

    assert "vendor" not in diff


def test_compute_field_diff_empty_and_identical():
    assert compute_field_diff(None, None) == {}
    assert compute_field_diff({"a": 1}, {"a": 1}) == {}
    assert compute_field_diff({}, {"new_field": "val"}) == {
        "new_field": {"server_value": None, "submitted_value": "val"}
    }


def test_optimistic_concurrency_collision_error():
    diff = {"total": {"server_value": "100.00", "submitted_value": "105.00"}}
    err = OptimisticConcurrencyCollisionError(
        current_version=2,
        attempted_version=1,
        diff=diff
    )

    assert err.status_code == 409
    assert err.detail["error"] == "OPTIMISTIC_LOCK_COLLISION"
    assert err.detail["current_version"] == 2
    assert err.detail["attempted_version"] == 1
    assert err.detail["field_diff"] == diff


def test_schema_model_attributes():
    # Document
    assert hasattr(Document, "file_hash")
    assert hasattr(Document, "r2_storage_key")
    assert hasattr(Document, "page_count")

    # ExtractionVersion (Time-travel / as_of support)
    assert hasattr(ExtractionVersion, "valid_from")
    assert hasattr(ExtractionVersion, "valid_to")
    assert hasattr(ExtractionVersion, "is_current")
    assert hasattr(ExtractionVersion, "idempotency_key")

    # ReviewItem (Optimistic locking & leases)
    assert hasattr(ReviewItem, "version")
    assert hasattr(ReviewItem, "status")
    assert hasattr(ReviewItem, "leased_by")
    assert hasattr(ReviewItem, "lease_expires_at")

    # RunLedger & DeadLetter (Pipeline spine)
    assert hasattr(RunLedger, "watermark")
    assert hasattr(RunLedger, "canary_f1_score")
    assert hasattr(DeadLetter, "failure_reason")
    assert hasattr(DeadLetter, "error_details")

    # CostMeter
    assert hasattr(CostMeter, "routing_path")
    assert hasattr(CostMeter, "estimated_cost_usd")

    # BackfillProgress
    assert hasattr(BackfillProgress, "last_processed_id")
    assert hasattr(BackfillProgress, "is_completed")


def test_submit_reviewed_item_collision_raises_409():
    async def _run():
        session = AsyncMock()
        mock_item = MagicMock(spec=ReviewItem)
        mock_item.id = uuid.uuid4()
        mock_item.version = 3
        mock_item.reviewed_value = {"total": "100.00"}
        mock_item.proposed_value = {"total": "90.00"}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_item
        session.execute.return_value = mock_result

        # Attempt submit with version 2 (stale)
        with pytest.raises(OptimisticConcurrencyCollisionError) as exc_info:
            await submit_reviewed_item(
                session=session,
                item_id=mock_item.id,
                expected_version=2,
                reviewed_value={"total": "110.00"},
                reviewer_id="reviewer-1",
            )

        err = exc_info.value
        assert err.status_code == 409
        assert err.detail["current_version"] == 3
        assert err.detail["attempted_version"] == 2
        assert "total" in err.detail["field_diff"]

    import asyncio
    asyncio.run(_run())


def test_submit_reviewed_item_success_increments_version():
    async def _run():
        session = AsyncMock()
        mock_item = MagicMock(spec=ReviewItem)
        mock_item.id = uuid.uuid4()
        mock_item.version = 1
        mock_item.status = "leased"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_item
        session.execute.return_value = mock_result

        res = await submit_reviewed_item(
            session=session,
            item_id=mock_item.id,
            expected_version=1,
            reviewed_value={"total": "120.00"},
            reviewer_id="reviewer-1",
        )

        assert mock_item.version == 2
        assert mock_item.status == "approved"
        assert mock_item.reviewed_by == "reviewer-1"
        session.commit.assert_awaited_once()

    import asyncio
    asyncio.run(_run())


def test_reap_expired_leases():
    async def _run():
        session = AsyncMock()

        mock_item1 = MagicMock(spec=ReviewItem)
        mock_item1.id = uuid.uuid4()
        mock_item1.status = "leased"

        mock_item2 = MagicMock(spec=ReviewItem)
        mock_item2.id = uuid.uuid4()
        mock_item2.status = "leased"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_item1, mock_item2]
        session.execute.return_value = mock_result

        count = await reap_expired_leases(session, batch_size=100)

        assert count == 2
        assert mock_item1.status == "pending"
        assert mock_item1.leased_by is None
        assert mock_item2.status == "pending"
        assert mock_item2.leased_by is None
        session.commit.assert_awaited_once()

    import asyncio
    asyncio.run(_run())

