"""NumPy-only 2D CA-CFAR and deterministic radar target extraction."""

from collections import deque
from typing import Optional

import numpy as np

from .config import RadarConfig
from .models import RadarTarget


DOPPLER_TRAINING_CELLS = 4
DOPPLER_GUARD_CELLS = 1
RANGE_TRAINING_CELLS = 8
RANGE_GUARD_CELLS = 2

# Near DC, one-sided training still has 88 cells with the default geometry.
# Requiring at least 32 prevents unstable estimates on unusually small maps.
MIN_CFAR_TRAINING_CELLS = 32


def _integral_image(values: np.ndarray) -> np.ndarray:
    integral = np.zeros((values.shape[0] + 1, values.shape[1] + 1), dtype=float)
    integral[1:, 1:] = np.cumsum(np.cumsum(values, axis=0), axis=1)
    return integral


def _rectangle_sum(
    integral: np.ndarray,
    row_low: np.ndarray,
    row_high: np.ndarray,
    column_low: np.ndarray,
    column_high: np.ndarray,
) -> np.ndarray:
    return (
        integral[row_high + 1, column_high + 1]
        - integral[row_low, column_high + 1]
        - integral[row_high + 1, column_low]
        + integral[row_low, column_low]
    )


def _validate_cfar_inputs(
    power: np.ndarray,
    doppler_training: int,
    doppler_guard: int,
    range_training: int,
    range_guard: int,
    minimum_training_cells: Optional[int] = None,
) -> np.ndarray:
    values = np.asarray(power, dtype=float)
    if values.ndim != 2:
        raise ValueError("power must be a two-dimensional Doppler-range map")
    if np.any(~np.isfinite(values)) or np.any(values < 0.0):
        raise ValueError("power must contain finite nonnegative values")
    parameters = [doppler_training, doppler_guard, range_training, range_guard]
    if minimum_training_cells is not None:
        parameters.append(minimum_training_cells)
    if any(
        not isinstance(value, int) or isinstance(value, bool) for value in parameters
    ):
        raise ValueError("CFAR cell counts must be integers")
    positive_counts = [doppler_training, range_training]
    if minimum_training_cells is not None:
        positive_counts.append(minimum_training_cells)
    if min(positive_counts) <= 0:
        raise ValueError("CFAR training counts must be positive")
    if min(doppler_guard, range_guard) < 0:
        raise ValueError("CFAR guard counts must be nonnegative")
    return values


def cfar_training_stats(
    power: np.ndarray,
    *,
    doppler_training: int = DOPPLER_TRAINING_CELLS,
    doppler_guard: int = DOPPLER_GUARD_CELLS,
    range_training: int = RANGE_TRAINING_CELLS,
    range_guard: int = RANGE_GUARD_CELLS,
) -> tuple[np.ndarray, np.ndarray]:
    """Return integral-image CA-CFAR training sums and cell counts."""
    values = _validate_cfar_inputs(
        power,
        doppler_training,
        doppler_guard,
        range_training,
        range_guard,
    )

    row_count, column_count = values.shape
    training_sums = np.zeros(values.shape, dtype=float)
    training_counts = np.zeros(values.shape, dtype=np.int64)
    doppler_margin = doppler_training + doppler_guard
    range_margin = range_training + range_guard
    valid_rows = np.arange(doppler_margin, row_count - doppler_margin)
    if valid_rows.size == 0:
        return training_sums, training_counts

    integral = _integral_image(values)
    rows = valid_rows[:, None]
    outer_row_low = rows - doppler_margin
    outer_row_high = rows + doppler_margin

    near_columns = np.arange(1, min(range_margin, column_count))
    if near_columns.size:
        columns = near_columns[None, :]
        requested_low = columns + range_guard + 1
        training_low = np.minimum(requested_low, column_count)
        training_high = np.minimum(
            requested_low + range_training - 1, column_count - 1
        )
        valid_training = training_low <= training_high
        training_sum = _rectangle_sum(
            integral,
            outer_row_low,
            outer_row_high,
            training_low,
            training_high,
        )
        training_count = (
            (2 * doppler_margin + 1)
            * np.maximum(training_high - training_low + 1, 0)
        )
        training_sum = np.where(valid_training, training_sum, 0.0)
        training_count = np.where(valid_training, training_count, 0)
        training_sums[np.ix_(valid_rows, near_columns)] = training_sum
        training_counts[np.ix_(valid_rows, near_columns)] = training_count

    interior_columns = np.arange(range_margin, column_count - range_margin)
    if interior_columns.size:
        columns = interior_columns[None, :]
        outer_sum = _rectangle_sum(
            integral,
            outer_row_low,
            outer_row_high,
            columns - range_margin,
            columns + range_margin,
        )
        guard_sum = _rectangle_sum(
            integral,
            rows - doppler_guard,
            rows + doppler_guard,
            columns - range_guard,
            columns + range_guard,
        )
        outer_count = (2 * doppler_margin + 1) * (2 * range_margin + 1)
        guard_count = (2 * doppler_guard + 1) * (2 * range_guard + 1)
        training_count = outer_count - guard_count
        training_sums[np.ix_(valid_rows, interior_columns)] = outer_sum - guard_sum
        training_counts[np.ix_(valid_rows, interior_columns)] = training_count

    return training_sums, training_counts


def ca_cfar_2d(
    power: np.ndarray,
    config: RadarConfig,
    *,
    doppler_training: int = DOPPLER_TRAINING_CELLS,
    doppler_guard: int = DOPPLER_GUARD_CELLS,
    range_training: int = RANGE_TRAINING_CELLS,
    range_guard: int = RANGE_GUARD_CELLS,
    minimum_training_cells: int = MIN_CFAR_TRAINING_CELLS,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a CA-CFAR mask and local noise power for a Doppler-range map.

    Doppler cells require the complete two-sided window. DC range bin zero is
    protected. CUTs in bins one through the near-range margin train only on
    farther-range cells beginning after their guard, clipped to the matrix.
    Far-range and Doppler edges remain excluded. A noise estimate is accepted
    only when at least ``minimum_training_cells`` contribute.
    """
    values = _validate_cfar_inputs(
        power,
        doppler_training,
        doppler_guard,
        range_training,
        range_guard,
        minimum_training_cells,
    )
    training_sums, training_counts = cfar_training_stats(
        values,
        doppler_training=doppler_training,
        doppler_guard=doppler_guard,
        range_training=range_training,
        range_guard=range_guard,
    )
    noise_power = np.full(values.shape, np.nan, dtype=float)
    valid = training_counts >= minimum_training_cells
    noise_power[valid] = training_sums[valid] / training_counts[valid]

    threshold_scale = 10.0 ** (config.cfar_threshold_db / 10.0)
    threshold = noise_power * threshold_scale
    detections = np.isfinite(threshold) & (values > threshold)
    return detections, noise_power


def local_peak_mask(power: np.ndarray, detections: np.ndarray) -> np.ndarray:
    """Keep detected cells that are not weaker than any raw-power 8-neighbor."""
    values = np.asarray(power, dtype=float)
    mask = np.asarray(detections, dtype=bool)
    if values.shape != mask.shape or values.ndim != 2:
        raise ValueError("power and detections must be same-shaped 2D arrays")

    neighbor_max = np.full(values.shape, -np.inf, dtype=float)
    row_count, column_count = values.shape
    for row_shift in (-1, 0, 1):
        for column_shift in (-1, 0, 1):
            if row_shift == 0 and column_shift == 0:
                continue
            source_rows = slice(max(0, -row_shift), min(row_count, row_count - row_shift))
            source_columns = slice(
                max(0, -column_shift), min(column_count, column_count - column_shift)
            )
            target_rows = slice(max(0, row_shift), min(row_count, row_count + row_shift))
            target_columns = slice(
                max(0, column_shift), min(column_count, column_count + column_shift)
            )
            neighbor_max[target_rows, target_columns] = np.maximum(
                neighbor_max[target_rows, target_columns],
                values[source_rows, source_columns],
            )
    return mask & (values >= neighbor_max)


def cluster_detections(
    power: np.ndarray, detections: np.ndarray
) -> tuple[tuple[int, int], ...]:
    """Return one strongest local peak from every 8-connected detection island."""
    values = np.asarray(power, dtype=float)
    remaining = np.asarray(detections, dtype=bool).copy()
    if values.shape != remaining.shape or values.ndim != 2:
        raise ValueError("power and detections must be same-shaped 2D arrays")
    peaks = local_peak_mask(values, remaining)
    selected: list[tuple[int, int]] = []
    row_count, column_count = remaining.shape

    while np.any(remaining):
        seed_row, seed_column = np.argwhere(remaining)[0]
        queue = deque([(int(seed_row), int(seed_column))])
        remaining[seed_row, seed_column] = False
        component: list[tuple[int, int]] = []
        while queue:
            row, column = queue.popleft()
            component.append((row, column))
            for neighbor_row in range(max(0, row - 1), min(row_count, row + 2)):
                for neighbor_column in range(
                    max(0, column - 1), min(column_count, column + 2)
                ):
                    if remaining[neighbor_row, neighbor_column]:
                        remaining[neighbor_row, neighbor_column] = False
                        queue.append((neighbor_row, neighbor_column))

        candidates = [cell for cell in component if peaks[cell]]
        if candidates:
            selected.append(
                min(candidates, key=lambda cell: (-values[cell], cell[0], cell[1]))
            )

    return tuple(selected)


def detect_targets(
    power: np.ndarray,
    range_axis_m: np.ndarray,
    velocity_axis_mps: np.ndarray,
    config: RadarConfig,
    *,
    timestamp: float,
    diagnostics: Optional[object] = None,
    sync_score: Optional[float] = None,
    adjacent_chirp_correlation: Optional[float] = None,
    phase_consistency: Optional[float] = None,
    velocity_trust_threshold: float = 0.5,
) -> tuple[RadarTarget, ...]:
    """Convert clustered CFAR cells into frame-local measured targets."""
    values = np.asarray(power, dtype=float)
    ranges = np.asarray(range_axis_m, dtype=float)
    velocities = np.asarray(velocity_axis_mps, dtype=float)
    if values.shape != (velocities.size, ranges.size):
        raise ValueError("power shape must match velocity and range axes")

    detections, noise_power = ca_cfar_2d(values, config)
    detections &= ranges[None, :] <= config.max_display_range_m
    cells = cluster_detections(values, detections)
    if diagnostics is not None:
        if sync_score is None:
            sync_score = float(getattr(diagnostics, "sync_score"))
        if phase_consistency is None:
            phase_consistency = float(getattr(diagnostics, "phase_consistency"))
    sync_quality = float(np.clip(1.0 if sync_score is None else sync_score, 0.0, 1.0))
    chirp_quality = float(
        np.clip(
            1.0 if adjacent_chirp_correlation is None else adjacent_chirp_correlation,
            0.0,
            1.0,
        )
    )
    phase_quality = float(
        np.clip(1.0 if phase_consistency is None else phase_consistency, 0.0, 1.0)
    )
    velocity_trusted = (
        sync_quality >= velocity_trust_threshold
        and chirp_quality >= velocity_trust_threshold
        and phase_quality >= velocity_trust_threshold
    )
    threshold_scale = 10.0 ** (config.cfar_threshold_db / 10.0)

    measurements = []
    for doppler_bin, range_bin in cells:
        local_noise = max(
            float(noise_power[doppler_bin, range_bin]), np.finfo(float).tiny
        )
        cell_power = max(float(values[doppler_bin, range_bin]), np.finfo(float).tiny)
        snr_db = float(10.0 * np.log10(cell_power / local_noise))
        margin_db = float(10.0 * np.log10(cell_power / (local_noise * threshold_scale)))
        margin_quality = 1.0 - np.exp(-max(margin_db, 0.0) / 6.0)
        confidence = float(
            np.clip(
                0.6 * margin_quality + 0.2 * sync_quality + 0.2 * phase_quality,
                0.0,
                1.0,
            )
        )
        measurements.append(
            {
                "range_m": float(ranges[range_bin]),
                "radial_velocity_mps": float(velocities[doppler_bin]),
                "azimuth_deg": None,
                "snr_db": snr_db,
                "power_db": float(10.0 * np.log10(cell_power)),
                "range_bin": int(range_bin),
                "doppler_bin": int(doppler_bin),
                "confidence": confidence,
                "velocity_confidence": (
                    float(min(sync_quality, chirp_quality, phase_quality))
                    if velocity_trusted
                    else 0.0
                ),
                "velocity_trusted": velocity_trusted,
                "timestamp": float(timestamp),
            }
        )

    measurements.sort(
        key=lambda item: (
            -item["snr_db"],
            item["range_bin"],
            item["doppler_bin"],
        )
    )
    return tuple(
        RadarTarget(target_id=f"T{index:02d}", **measurement)
        for index, measurement in enumerate(measurements, start=1)
    )
