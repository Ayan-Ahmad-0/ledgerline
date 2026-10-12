from typing import Optional, Any
from decimal import Decimal


class LedgerlineValidationError(Exception):
    """Base exception for all Ledgerline validation errors."""
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class MathValidationError(LedgerlineValidationError):
    """
    Raised when line item mathematical consistency checks fail:
    SUM(quantity * unit_price) != subtotal within allowed tolerance (1 currency unit).
    Document must be parked to dead_letter table with zero clean rows written.
    """
    def __init__(
        self,
        calculated_sum: Decimal,
        stated_subtotal: Decimal,
        difference: Decimal,
        tolerance: Decimal,
        line_items: list[dict[str, Any]],
        message: Optional[str] = None
    ):
        diff_str = str(difference)
        calc_str = str(calculated_sum)
        stated_str = str(stated_subtotal)
        msg = message or (
            f"Math validation failed: calculated sum {calc_str} differs from stated subtotal {stated_str} "
            f"by {diff_str} (exceeds allowed tolerance of {tolerance} currency unit)."
        )
        super().__init__(
            message=msg,
            details={
                "error_code": "MATH_VALIDATION_ERROR",
                "calculated_sum": calc_str,
                "stated_subtotal": stated_str,
                "difference": diff_str,
                "tolerance": str(tolerance),
                "line_items_count": len(line_items),
                "line_items": line_items,
            }
        )
        self.calculated_sum = calculated_sum
        self.stated_subtotal = stated_subtotal
        self.difference = difference
        self.tolerance = tolerance
        self.line_items = line_items


class TableStitchError(LedgerlineValidationError):
    """Raised when column interval clustering or page alignment fails."""
    pass


class DocumentParkedError(LedgerlineValidationError):
    """Raised when an unrecoverable document is routed and parked to dead_letter."""
    pass
