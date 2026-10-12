import numpy as np
from typing import Optional
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

from app.calibration.features import CalibrationSample
from app.calibration.ece import compute_ece_equal_mass


class PlattScalingCalibrator:
    """Platt scaling baseline: Logistic regression on raw confidence scores."""

    def __init__(self):
        self.model = LogisticRegression(solver="lbfgs")

    def fit(self, confidences: np.ndarray, labels: np.ndarray) -> "PlattScalingCalibrator":
        X = confidences.reshape(-1, 1)
        self.model.fit(X, labels)
        return self

    def predict_proba(self, confidences: np.ndarray) -> np.ndarray:
        X = confidences.reshape(-1, 1)
        # Probability of class 1
        return self.model.predict_proba(X)[:, 1]


class IsotonicCalibrator:
    """Non-parametric Isotonic Regression calibrator."""

    def __init__(self):
        self.model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")

    def fit(self, confidences: np.ndarray, labels: np.ndarray) -> "IsotonicCalibrator":
        self.model.fit(confidences, labels)
        return self

    def predict(self, confidences: np.ndarray) -> np.ndarray:
        return self.model.predict(confidences)


class MultiSignalCalibrator:
    """
    Combines multi-signal feature vector (token prob, OCR conf, layout, math check, two-pass)
    with Platt scaling + Isotonic regression calibration.
    """

    def __init__(self):
        self.feature_model = LogisticRegression(solver="lbfgs", max_iter=200)
        self.isotonic_calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")

    def fit(self, samples: list[CalibrationSample]) -> "MultiSignalCalibrator":
        X = np.array([s.features for s in samples], dtype=np.float64)
        y = np.array([s.true_label for s in samples], dtype=np.int32)

        # Step 1: Fit multi-feature classifier
        self.feature_model.fit(X, y)
        uncalibrated_probs = self.feature_model.predict_proba(X)[:, 1]

        # Step 2: Fit isotonic regression calibrator on predicted probabilities
        self.isotonic_calibrator.fit(uncalibrated_probs, y)
        return self

    def calibrate(self, features: list[float]) -> float:
        X = np.array([features], dtype=np.float64)
        raw_prob = self.feature_model.predict_proba(X)[0, 1]
        calibrated = self.isotonic_calibrator.predict([raw_prob])[0]
        return float(np.clip(calibrated, 0.0, 1.0))


def run_holdout_calibration(
    samples: list[CalibrationSample],
    heldout_layout: str = "layout_8",
    heldout_vendor: str = "vendor_heldout",
    num_bins: int = 10,
    target_ece: float = 0.10,
) -> dict:
    """
    Directive #2:
    Fit calibrator while completely holding out one specific layout and one vendor.
    Measure Expected Calibration Error (ECE) out-of-distribution using 10 equal-mass bins.
    Asserts ECE <= 0.10.
    """
    train_samples = [
        s for s in samples
        if s.layout_id != heldout_layout and s.vendor_id != heldout_vendor
    ]
    test_samples = [
        s for s in samples
        if s.layout_id == heldout_layout or s.vendor_id == heldout_vendor
    ]

    if not train_samples or not test_samples:
        raise ValueError(
            f"Holdout split failed: train={len(train_samples)}, test={len(test_samples)}. "
            f"Ensure sample dataset includes layout '{heldout_layout}' and vendor '{heldout_vendor}'."
        )

    # 1. Fit Multi-Signal + Isotonic Calibrator
    calibrator = MultiSignalCalibrator()
    calibrator.fit(train_samples)

    # 2. Evaluate on out-of-distribution held-out samples
    test_raw_conf = np.array([s.raw_confidence for s in test_samples])
    test_labels = np.array([s.true_label for s in test_samples])

    test_calibrated_conf = np.array([
        calibrator.calibrate(s.features) for s in test_samples
    ])

    # 3. Calculate ECE with 10 equal-mass bins
    raw_ece = compute_ece_equal_mass(test_raw_conf, test_labels, num_bins=num_bins)
    calibrated_ece = compute_ece_equal_mass(test_calibrated_conf, test_labels, num_bins=num_bins)

    meets_target = calibrated_ece <= target_ece

    return {
        "heldout_layout": heldout_layout,
        "heldout_vendor": heldout_vendor,
        "train_samples_count": len(train_samples),
        "test_samples_count": len(test_samples),
        "raw_ece": raw_ece,
        "calibrated_ece": calibrated_ece,
        "target_ece": target_ece,
        "meets_target": meets_target,
    }
