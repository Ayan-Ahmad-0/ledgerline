import uuid
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import RunLedger
from app.core.logging import logger


async def start_pipeline_run(
    session: AsyncSession,
    watermark: Optional[str] = None
) -> RunLedger:
    """Initialize a new pipeline run entry in run_ledger."""
    run = RunLedger(
        run_id=uuid.uuid4(),
        started_at=datetime.now(timezone.utc),
        status="running",
        watermark=watermark,
        documents_processed=0,
        documents_parked=0,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    logger.info(f"Pipeline run started: {run.run_id} (watermark: {watermark})")
    return run


async def record_canary_evaluation(
    session: AsyncSession,
    run_id: uuid.UUID,
    canary_f1: float,
    baseline_f1: float
) -> None:
    """Record canary evaluation metrics on the run ledger."""
    stmt = select(RunLedger).where(RunLedger.run_id == run_id)
    res = await session.execute(stmt)
    run = res.scalar_one_or_none()

    if run:
        run.canary_f1_score = canary_f1
        run.baseline_f1_score = baseline_f1
        await session.commit()


async def complete_pipeline_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    status: str,
    documents_processed: int,
    documents_parked: int,
    error_summary: Optional[dict[str, Any]] = None
) -> Optional[RunLedger]:
    """Finalize pipeline run status and document counts."""
    stmt = select(RunLedger).where(RunLedger.run_id == run_id)
    res = await session.execute(stmt)
    run = res.scalar_one_or_none()

    if run:
        run.status = status
        run.completed_at = datetime.now(timezone.utc)
        run.documents_processed = documents_processed
        run.documents_parked = documents_parked
        run.error_summary = error_summary
        await session.commit()
        await session.refresh(run)
        logger.info(f"Pipeline run {run_id} finished with status '{status}'")

    return run


async def get_rolling_baseline_f1(
    session: AsyncSession,
    window: int = 5,
    default_baseline: float = 0.95
) -> float:
    """
    Compute rolling average canary F1 score across recent successful runs.
    Defaults to 0.95 (95.0%) if no previous runs exist.
    """
    stmt = (
        select(RunLedger.canary_f1_score)
        .where(RunLedger.status == "success")
        .where(RunLedger.canary_f1_score.isnot(None))
        .order_by(desc(RunLedger.completed_at))
        .limit(window)
    )
    res = await session.execute(stmt)
    scores = [s for s in res.scalars().all() if s is not None]

    if not scores:
        return default_baseline

    return float(sum(scores) / len(scores))
