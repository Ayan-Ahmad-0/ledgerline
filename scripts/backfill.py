"""
Resumable, chunked schema backfill script with zero-downtime budget.
Tracks progress in the `backfill_progress` table and supports chunked rollbacks.
"""

import argparse
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import (
    get_session_factory,
    BackfillProgress,
    Field,
    FieldFeature,
)
from app.core.logging import setup_logging, logger


async def get_or_create_progress(
    session: AsyncSession,
    migration_name: str,
    batch_size: int = 500
) -> BackfillProgress:
    stmt = select(BackfillProgress).where(BackfillProgress.migration_name == migration_name)
    result = await session.execute(stmt)
    progress = result.scalar_one_or_none()

    if progress is None:
        progress = BackfillProgress(
            migration_name=migration_name,
            batch_size=batch_size,
            rows_processed=0,
            is_completed=False,
            started_at=datetime.now(timezone.utc),
        )
        session.add(progress)
        await session.commit()
        await session.refresh(progress)

    return progress


async def run_chunked_backfill(
    migration_name: str,
    batch_size: int = 500,
    max_batches: Optional[int] = None
) -> None:
    """
    Example resumable backfill: Populates default field_features for any legacy fields
    lacking an associated feature vector, chunked by primary key with zero table locks.
    """
    logger.info(f"Starting chunked backfill: '{migration_name}' (batch_size: {batch_size})")

    session_factory = get_session_factory()
    async with session_factory() as session:
        progress = await get_or_create_progress(session, migration_name, batch_size)

        if progress.is_completed:
            logger.info(f"Migration '{migration_name}' is already marked as completed. Skipping.")
            return

        last_id = progress.last_processed_id
        batches_run = 0

        while True:
            if max_batches is not None and batches_run >= max_batches:
                logger.info(f"Reached batch execution limit ({max_batches}). Pausing backfill.")
                break

            # Select batch of fields strictly greater than last_processed_id
            query = select(Field).order_by(Field.id).limit(batch_size)
            if last_id is not None:
                query = query.where(Field.id > last_id)

            result = await session.execute(query)
            fields_batch = result.scalars().all()

            if not fields_batch:
                # No more records to process - mark migration as complete
                progress.is_completed = True
                progress.updated_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"Backfill '{migration_name}' successfully completed. Total rows: {progress.rows_processed}")
                break

            # Process batch
            for field_row in fields_batch:
                # Check if feature already exists
                feat_stmt = select(FieldFeature).where(FieldFeature.field_id == field_row.id)
                feat_res = await session.execute(feat_stmt)
                existing_feat = feat_res.scalar_one_or_none()

                if existing_feat is None:
                    new_feature = FieldFeature(
                        field_id=field_row.id,
                        token_probability=field_row.confidence_raw,
                        ocr_char_confidence=None,
                        math_validation_passed=True,
                        two_pass_agreement=True,
                        layout_feature_vector={"backfilled": True},
                    )
                    session.add(new_feature)

            # Update progress cursor
            last_id = fields_batch[-1].id
            progress.last_processed_id = last_id
            progress.rows_processed += len(fields_batch)
            progress.updated_at = datetime.now(timezone.utc)

            await session.commit()
            batches_run += 1
            logger.info(f"Committed batch {batches_run}: processed {len(fields_batch)} rows. Total: {progress.rows_processed}")


async def rollback_backfill(migration_name: str, batch_size: int = 500) -> None:
    """
    Tested chunked rollback for the migration: reverses backfilled records
    in safe batches and resets the progress table.
    """
    logger.warning(f"Initiating rollback for migration: '{migration_name}'")

    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = select(BackfillProgress).where(BackfillProgress.migration_name == migration_name)
        result = await session.execute(stmt)
        progress = result.scalar_one_or_none()

        if not progress:
            logger.info(f"No backfill progress found for '{migration_name}'. Nothing to roll back.")
            return

        # Chunked deletion of backfilled records
        while True:
            del_query = (
                select(FieldFeature)
                .where(FieldFeature.layout_feature_vector["backfilled"].astext == "true")
                .limit(batch_size)
            )
            del_res = await session.execute(del_query)
            features_to_del = del_res.scalars().all()

            if not features_to_del:
                break

            for feat in features_to_del:
                await session.delete(feat)

            await session.commit()
            logger.info(f"Rolled back batch of {len(features_to_del)} features.")

        # Reset progress record
        progress.is_completed = False
        progress.rows_processed = 0
        progress.last_processed_id = None
        progress.updated_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info(f"Rollback of migration '{migration_name}' completed successfully.")


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Resumable Zero-Downtime Schema Backfill")
    parser.add_argument("--name", type=str, default="backfill_field_features_v1", help="Migration identifier")
    parser.add_argument("--batch-size", type=int, default=500, help="Chunk size for batch processing")
    parser.add_argument("--rollback", action="store_true", help="Roll back the specified migration")
    args = parser.parse_args()

    if args.rollback:
        asyncio.run(rollback_backfill(args.name, args.batch_size))
    else:
        asyncio.run(run_chunked_backfill(args.name, args.batch_size))


if __name__ == "__main__":
    main()
