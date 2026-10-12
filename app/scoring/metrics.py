from dataclasses import dataclass, field
from typing import Any, Optional
from decimal import Decimal

from app.scoring.comparators import (
    parse_money,
    compare_money,
    parse_date,
    compare_dates,
    compare_strings,
    NormalizedMoney,
    NormalizedDate,
)


@dataclass
class FieldScoreResult:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def f1_score(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r) / (p + r) if (p + r) > 0 else 0.0


@dataclass
class EvaluationReport:
    dataset_name: str
    field_scores: dict[str, FieldScoreResult] = field(default_factory=dict)

    @property
    def overall_f1(self) -> float:
        if not self.field_scores:
            return 0.0
        scores = [f.f1_score for f in self.field_scores.values()]
        return sum(scores) / len(scores)


class TypedScorer:
    """
    Evaluator enforcing typed comparisons per field type:
    - Money fields: subtotal, tax, total, unit_price
    - Date fields: invoice_date, due_date
    - Vendor fields: vendor_name
    - Generic text: invoice_number, description
    """

    CORD_ALLOWED_FIELDS = {"line_items", "subtotal", "tax", "total"}

    @classmethod
    def evaluate_field(
        cls,
        field_name: str,
        ground_truth: Any,
        prediction: Any,
        dataset_name: Optional[str] = None
    ) -> bool:
        """
        Evaluate a single field match using typed comparators.
        CORD rule: Never evaluate vendor or dates on CORD.
        """
        if dataset_name == "CORD" and field_name not in cls.CORD_ALLOWED_FIELDS:
            raise ValueError(f"CORD dataset does not label '{field_name}'. Only {cls.CORD_ALLOWED_FIELDS} are allowed.")

        if ground_truth is None and prediction is None:
            return True
        if ground_truth is None or prediction is None:
            return False

        # Money fields
        if field_name in ("subtotal", "tax", "total", "unit_price", "amount"):
            gt_money = parse_money(str(ground_truth))
            pred_money = parse_money(str(prediction))
            if gt_money is None or pred_money is None:
                return False
            return compare_money(gt_money, pred_money)

        # Date fields
        if "date" in field_name:
            gt_date = parse_date(str(ground_truth))
            pred_date = parse_date(str(prediction))
            if gt_date is None or pred_date is None:
                return False
            return compare_dates(gt_date, pred_date)

        # Vendor name field
        if field_name == "vendor_name":
            return compare_strings(str(ground_truth), str(prediction), vendor_mode=True)

        # Generic string field
        return compare_strings(str(ground_truth), str(prediction), vendor_mode=False)
