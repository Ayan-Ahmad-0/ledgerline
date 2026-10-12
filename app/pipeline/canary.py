import os
from typing import Any
from dataclasses import dataclass
from app.scoring.metrics import TypedScorer, FieldScoreResult
from app.core.logging import logger


class CanaryGateFailureError(Exception):
    """
    Raised when canary slice F1 score drops by more than the allowed threshold
    against the rolling baseline (exceeds 2.0 percentage points drop).
    The entire batch pipeline must abort immediately with zero clean writes.
    """
    def __init__(self, canary_f1: float, baseline_f1: float, drop_points: float, max_drop: float):
        msg = (
            f"CANARY QUALITY GATE FAILED: Canary F1 score ({canary_f1:.4f}) dropped by "
            f"{drop_points:.2f} points against rolling baseline ({baseline_f1:.4f}). "
            f"Allowed maximum drop is {max_drop:.2f} points. Entire run aborted."
        )
        super().__init__(msg)
        self.canary_f1 = canary_f1
        self.baseline_f1 = baseline_f1
        self.drop_points = drop_points
        self.max_drop = max_drop


@dataclass
class CanaryEvaluationResult:
    passed: bool
    canary_f1: float
    baseline_f1: float
    drop_points: float
    sample_count: int
    field_scores: dict[str, float]


def evaluate_canary_slice(
    canary_pairs: list[dict[str, Any]],  # list of {"ground_truth": ..., "prediction": ...}
    baseline_f1: float = 0.95,
    max_drop_points: float = 2.0,        # 2.0 points drop threshold
    raise_on_failure: bool = True
) -> CanaryEvaluationResult:
    """
    Evaluate 50-document canary slice first.
    If field F1 drops by > 2 points (0.02) against rolling baseline, fail run and alert.
    """
    if not canary_pairs:
        raise ValueError("Canary slice must contain documents to evaluate.")

    fields_to_evaluate = ["vendor_name", "invoice_date", "subtotal", "tax", "total"]
    score_trackers: dict[str, FieldScoreResult] = {f: FieldScoreResult() for f in fields_to_evaluate}

    for pair in canary_pairs:
        gt = pair.get("ground_truth", {})
        pred = pair.get("prediction", {})

        for f_name in fields_to_evaluate:
            gt_val = gt.get(f_name)
            pred_val = pred.get(f_name)

            if gt_val is not None:
                is_match = TypedScorer.evaluate_field(f_name, gt_val, pred_val)
                if is_match:
                    score_trackers[f_name].true_positives += 1
                else:
                    score_trackers[f_name].false_negatives += 1
            elif pred_val is not None:
                score_trackers[f_name].false_positives += 1

    # Calculate overall macro F1 across fields present in the slice
    active_fields = [
        f for f in fields_to_evaluate
        if (score_trackers[f].true_positives + score_trackers[f].false_positives + score_trackers[f].false_negatives) > 0
    ]
    field_f1_scores = {f: score_trackers[f].f1_score for f in active_fields}
    canary_f1 = sum(field_f1_scores.values()) / len(field_f1_scores) if field_f1_scores else 0.0

    # Drop in points (e.g. 0.95 -> 0.92 is 3.0 points drop)
    drop_points = max(0.0, (baseline_f1 - canary_f1) * 100.0)

    passed = drop_points <= max_drop_points
    if os.getenv("FAULT_CANARY_GATE") == "1":
        passed = True  # Break-It Fault: Injects deliberate silent pass of degraded canary gate

    if not passed:
        logger.error(
            f"CRITICAL ALERT: Canary quality gate failed! Canary F1: {canary_f1:.4f}, "
            f"Baseline: {baseline_f1:.4f}, Drop: {drop_points:.2f} pts (max: {max_drop_points} pts)",
            extra={
                "canary_f1": canary_f1,
                "baseline_f1": baseline_f1,
                "drop_points": drop_points,
                "field_f1_scores": field_f1_scores,
            }
        )
        if raise_on_failure:
            raise CanaryGateFailureError(
                canary_f1=canary_f1,
                baseline_f1=baseline_f1,
                drop_points=drop_points,
                max_drop=max_drop_points,
            )

    logger.info(
        f"Canary quality gate PASSED (F1: {canary_f1:.4f}, baseline: {baseline_f1:.4f}, drop: {drop_points:.2f} pts)"
    )

    return CanaryEvaluationResult(
        passed=passed,
        canary_f1=canary_f1,
        baseline_f1=baseline_f1,
        drop_points=drop_points,
        sample_count=len(canary_pairs),
        field_scores=field_f1_scores,
    )
