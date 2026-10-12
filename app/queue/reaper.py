import asyncio
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory, ReviewItem
from app.core.logging import logger


async def reap_expired_leases(session: AsyncSession, batch_size: int = 100) -> int:
    """
    Find and release all review items whose lease has expired.
    Resets status from 'leased' to 'pending' and clears lease metadata.
    """
    now = datetime.now(timezone.utc)

    # Fetch expired leases with SKIP LOCKED to avoid blocking active transactions
    stmt = (
        select(ReviewItem)
        .where(
            and_(
                ReviewItem.status == "leased",
                ReviewItem.lease_expires_at < now
            )
        )
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    )

    result = await session.execute(stmt)
    expired_items = result.scalars().all()

    if not expired_items:
        return 0

    reaped_count = len(expired_items)
    reaped_ids = []

    for item in expired_items:
        reaped_ids.append(str(item.id))
        item.status = "pending"
        item.leased_by = None
        item.leased_at = None
        item.lease_expires_at = None

    await session.commit()

    logger.info(
        f"Reaper released {reaped_count} expired leases",
        extra={"reaped_count": reaped_count, "reaped_ids": reaped_ids}
    )
    return reaped_count


async def run_reaper_daemon(interval_seconds: int = 30, stop_event: Optional[asyncio.Event] = None) -> None:
    """
    Daemon loop that runs periodically to reap expired leases.
    """
    logger.info(f"Starting lease reaper daemon (interval: {interval_seconds}s)")
    while stop_event is None or not stop_event.is_set():
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                total_reaped = 0
                while True:
                    count = await reap_expired_leases(session, batch_size=100)
                    total_reaped += count
                    if count < 100:
                        break
                if total_reaped > 0:
                    logger.info(f"Reaper cycle completed. Reaped total: {total_reaped}")
        except Exception as e:
            logger.error(f"Error in lease reaper cycle: {str(e)}", exc_info=True)

        if stop_event:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(interval_seconds)
