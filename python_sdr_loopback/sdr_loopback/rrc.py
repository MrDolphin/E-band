from __future__ import annotations

import numpy as np


def root_raised_cosine_taps(samples_per_symbol: int, span_symbols: int, alpha: float) -> np.ndarray:
    if samples_per_symbol <= 0:
        raise ValueError("samples_per_symbol must be positive")
    if span_symbols <= 0:
        raise ValueError("span_symbols must be positive")
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")

    half = span_symbols * samples_per_symbol // 2
    t = np.arange(-half, half + 1, dtype=np.float64) / samples_per_symbol
    taps = np.empty_like(t)

    for i, x in enumerate(t):
        if abs(x) < 1e-12:
            taps[i] = 1.0 - alpha + 4.0 * alpha / np.pi
        elif abs(abs(4.0 * alpha * x) - 1.0) < 1e-12:
            taps[i] = (
                alpha
                / np.sqrt(2.0)
                * ((1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * alpha))
                   + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * alpha)))
            )
        else:
            numerator = (
                np.sin(np.pi * x * (1.0 - alpha))
                + 4.0 * alpha * x * np.cos(np.pi * x * (1.0 + alpha))
            )
            denominator = np.pi * x * (1.0 - (4.0 * alpha * x) ** 2)
            taps[i] = numerator / denominator

    taps /= np.sqrt(np.sum(taps * taps))
    return taps
