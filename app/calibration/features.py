from typing import Optional, Any
from dataclasses import dataclass
import numpy as np


@dataclass
class CalibrationSample:
    features: list[float]  # [token_prob, ocr_conf, layout_pos, math_pass, two_pass]
    raw_confidence: float
    true_label: int        # 1 if prediction matches ground truth, 0 otherwise
    layout_id: str
    vendor_id: str


def build_feature_vector(
    token_probability: float,
    ocr_char_confidence: Optional[float] = None,
    relative_y_position: float = 0.5,
    math_validation_passed: bool = True,
    two_pass_agreement: bool = True,
) -> list[float]:
    """
    Construct multi-signal feature vector for calibration:
    1. token_probability (raw LLM confidence)
    2. ocr_char_confidence (1.0 for digital native)
    3. relative_y_position (layout geometry signal)
    4. math_validation_passed (1.0 or 0.0)
    5. two_pass_agreement (1.0 or 0.0)
    """
    ocr_conf = ocr_char_confidence if ocr_char_confidence is not None else 1.0
    math_sig = 1.0 if math_validation_passed else 0.0
    pass_sig = 1.0 if two_pass_agreement else 0.0

    return [
        float(np.clip(token_probability, 0.0, 1.0)),
        float(np.clip(ocr_conf, 0.0, 1.0)),
        float(np.clip(relative_y_position, 0.0, 1.0)),
        math_sig,
        pass_sig,
    ]
