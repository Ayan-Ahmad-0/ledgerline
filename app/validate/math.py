import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Any
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.validate.errors import MathValidationError
from app.db.session import DeadLetter
from app.core.logging import logger


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Optional[Decimal] = None

    @property
    def calculated_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class MathValidationResult(BaseModel):
    is_valid: bool
    calculated_sum: Decimal
    stated_subtotal: Decimal
    difference: Decimal
    tolerance: Decimal
    line_item_count: int


def validate_line_item_math(
    line_items: list[LineItem],
    stated_subtotal: Decimal,
    tolerance: Decimal = Decimal("1.00"),
    raise_on_error: bool = True
) -> MathValidationResult:
    """
    Validate that SUM(quantity * unit_price) == subtotal within allowed tolerance (1 currency unit).
    If validation fails and raise_on_error is True, raises typed MathValidationError.
    """
    if not line_items:
        diff = abs(stated_subtotal)
        if diff > tolerance:
            if raise_on_error:
                raise MathValidationError(
                    calculated_sum=Decimal("0.00"),
                    stated_subtotal=stated_subtotal,
                    difference=diff,
                    tolerance=tolerance,
                    line_items=[]
                )
            return MathValidationResult(
                is_valid=False,
                calculated_sum=Decimal("0.00"),
                stated_subtotal=stated_subtotal,
                difference=diff,
                tolerance=tolerance,
                line_item_count=0
            )

    # Compute sum of (quantity * unit_price)
    calculated_sum = sum(
        (item.quantity * item.unit_price) for item in line_items
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    diff = abs(calculated_sum - stated_subtotal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    is_valid = diff <= tolerance

    if not is_valid:
        line_items_data = [
            {
                "description": item.description,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "calculated_total": str(item.calculated_total),
                "stated_line_total": str(item.line_total) if item.line_total is not None else None,
            }
            for item in line_items
        ]
        logger.warning(
            f"Math validation failed: calculated {calculated_sum} vs stated {stated_subtotal} (diff: {diff})",
            extra={"difference": str(diff), "tolerance": str(tolerance)}
        )
        if raise_on_error:
            raise MathValidationError(
                calculated_sum=calculated_sum,
                stated_subtotal=stated_subtotal,
                difference=diff,
                tolerance=tolerance,
                line_items=line_items_data,
            )

    return MathValidationResult(
        is_valid=is_valid,
        calculated_sum=calculated_sum,
        stated_subtotal=stated_subtotal,
        difference=diff,
        tolerance=tolerance,
        line_item_count=len(line_items)
    )


async def route_to_dead_letter(
    session: AsyncSession,
    document_id: Optional[uuid.UUID],
    file_hash: Optional[str],
    failure_reason: str,
    error_details: dict[str, Any]
) -> DeadLetter:
    """
    Park a rejected document into the dead_letter table.
    Ensures zero rows are written to clean tables on validation failure.
    """
    dead_letter_entry = DeadLetter(
        document_id=document_id,
        file_hash=file_hash,
        failure_reason=failure_reason,
        error_details=error_details,
        resolved=False
    )
    session.add(dead_letter_entry)
    await session.commit()
    await session.refresh(dead_letter_entry)

    logger.info(
        f"Document parked to dead_letter: {failure_reason}",
        extra={
            "document_id": str(document_id) if document_id else None,
            "file_hash": file_hash,
            "failure_reason": failure_reason,
            "dead_letter_id": str(dead_letter_entry.id)
        }
    )
    return dead_letter_entry
