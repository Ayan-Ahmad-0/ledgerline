import numpy as np
from typing import Sequence, Tuple


def compute_ece_equal_mass(
    confidences: Sequence[float],
    labels: Sequence[int],
    num_bins: int = 10
) -> float:
    """
    Compute Expected Calibration Error (ECE) using equal-mass (quantile) bins.
    Each bin contains an equal number of samples (N / num_bins).

    ECE = sum_{b=1}^{M} (|B_b| / N) * |accuracy(B_b) - confidence(B_b)|
    """
    conf_arr = np.asarray(confidences, dtype=np.float64)
    label_arr = np.asarray(labels, dtype=np.float64)

    n_samples = len(conf_arr)
    if n_samples == 0:
        return 0.0

    # Sort samples by confidence
    sorted_indices = np.argsort(conf_arr)
    sorted_conf = conf_arr[sorted_indices]
    sorted_labels = label_arr[sorted_indices]

    # Partition into num_bins equal-mass chunks
    bin_chunks = np.array_split(np.arange(n_samples), num_bins)

    total_weighted_error = 0.0

    for chunk in bin_chunks:
        if len(chunk) == 0:
            continue

        bin_conf = sorted_conf[chunk]
        bin_labels = sorted_labels[chunk]

        avg_conf = float(np.mean(bin_conf))
        avg_acc = float(np.mean(bin_labels))
        bin_weight = len(chunk) / float(n_samples)

        bin_error = abs(avg_acc - avg_conf)
        total_weighted_error += bin_weight * bin_error

    return float(total_weighted_error)


def get_calibration_curve_equal_mass(
    confidences: Sequence[float],
    labels: Sequence[int],
    num_bins: int = 10
) -> Tuple[list[float], list[float]]:
    """Returns (bin_accuracies, bin_confidences) for plotting/auditing."""
    conf_arr = np.asarray(confidences, dtype=np.float64)
    label_arr = np.asarray(labels, dtype=np.float64)

    n_samples = len(conf_arr)
    if n_samples == 0:
        return [], []

    sorted_indices = np.argsort(conf_arr)
    sorted_conf = conf_arr[sorted_indices]
    sorted_labels = label_arr[sorted_indices]

    bin_chunks = np.array_split(np.arange(n_samples), num_bins)

    accuracies = []
    confs = []

    for chunk in bin_chunks:
        if len(chunk) == 0:
            continue
        accuracies.append(float(np.mean(sorted_labels[chunk])))
        confs.append(float(np.mean(sorted_conf[chunk])))

    return accuracies, confs
