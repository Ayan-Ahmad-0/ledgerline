import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from fastapi import HTTPException, status
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import ReviewItem


class OptimisticConcurrencyCollisionError(HTTPException):
    def __init__(self, current_version: int, attempted_version: int, diff: dict[str, Any]):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "OPTIMISTIC_LOCK_COLLISION",
                "message": f"Review item was modified by another transaction. Version mismatch (current: {current_version}, attempted: {attempted_version}).",
                "current_version": current_version,
                "attempted_version": attempted_version,
                "field_diff": diff,
            }
        )


def compute_field_diff(original: Optional[dict[str, Any]], updated: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Calculate field-level difference between two dictionary states."""
    original = original or {}
    updated = updated or {}
    diff: dict[str, Any] = {}
    
    all_keys = set(original.keys()) | set(updated.keys())
    for key in all_keys:
        orig_val = original.get(key)
        new_val = updated.get(key)
        if orig_val != new_val:
            diff[key] = {
                "server_value": orig_val,
                "submitted_value": new_val,
            }
    return diff


async def acquire_review_lease(
    session: AsyncSession,
    worker_id: str,
    lease_duration_seconds: int = 300
) -> Optional[ReviewItem]:
    """
    Acquire a review item lease safely using SELECT FOR UPDATE SKIP LOCKED.
    Acquires either an unleased 'pending' item or an expired 'leased' item.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(ReviewItem)
        .where(
            or_(
                ReviewItem.status == "pending",
                and_(
                    ReviewItem.status == "leased",
                    ReviewItem.lease_expires_at < now
                )
            )
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )

    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if item is None:
        return None

    item.status = "leased"
    item.leased_by = worker_id
    item.leased_at = now
    item.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
    await session.commit()
    await session.refresh(item)
    return item


async def renew_review_lease(
    session: AsyncSession,
    item_id: uuid.UUID,
    worker_id: str,
    extend_seconds: int = 300
) -> ReviewItem:
    """Extend the lease on an actively leased item held by worker_id."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(ReviewItem)
        .where(ReviewItem.id == item_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    if item.leased_by != worker_id or item.status != "leased":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lease is not held by this worker")

    item.lease_expires_at = now + timedelta(seconds=extend_seconds)
    await session.commit()
    await session.refresh(item)
    return item


async def release_review_lease(
    session: AsyncSession,
    item_id: uuid.UUID,
    worker_id: str
) -> ReviewItem:
    """Release a lease prematurely back to 'pending' state."""
    stmt = (
        select(ReviewItem)
        .where(ReviewItem.id == item_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    if item.leased_by != worker_id and item.status == "leased":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lease is held by another worker")

    item.status = "pending"
    item.leased_by = None
    item.leased_at = None
    item.lease_expires_at = None
    await session.commit()
    await session.refresh(item)
    return item


async def submit_reviewed_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    expected_version: int,
    reviewed_value: dict[str, Any],
    reviewer_id: str,
    action: str = "approved"  # "approved" or "rejected"
) -> ReviewItem:
    """
    Submit a review update with optimistic concurrency control.
    If expected_version != item.version, raise HTTP 409 with field-level diff.
    """
    stmt = (
        select(ReviewItem)
        .where(ReviewItem.id == item_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    # Optimistic locking collision check
    if item.version != expected_version:
        base_state = item.reviewed_value or item.proposed_value or {}
        diff = compute_field_diff(base_state, reviewed_value)
        raise OptimisticConcurrencyCollisionError(
            current_version=item.version,
            attempted_version=expected_version,
            diff=diff
        )

    now = datetime.now(timezone.utc)
    item.reviewed_value = reviewed_value
    item.reviewed_by = reviewer_id
    item.reviewed_at = now
    item.status = action
    item.leased_by = None
    item.lease_expires_at = None
    item.version += 1  # Increment optimistic concurrency version

    await session.commit()
    await session.refresh(item)
    return item
