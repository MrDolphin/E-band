from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback import ModemConfig, QpskLoopbackModem


def db(value: float) -> float:
    return 10.0 * np.log10(max(value, 1e-24))


def db20(value: float) -> float:
    return 20.0 * np.log10(max(value, 1e-12))


def occupied_bandwidth_hz(samples: np.ndarray, sample_rate: float, fraction: float = 0.99) -> float:
    centered = samples - np.mean(samples)
    window = np.hanning(len(centered))
    spectrum = np.fft.fftshift(np.fft.fft(centered * window))
    power = np.abs(spectrum) ** 2
    freq = np.fft.fftshift(np.fft.fftfreq(len(centered), d=1.0 / sample_rate))
    total = float(np.sum(power))
    if total <= 0.0:
        return 0.0

    order = np.argsort(freq)
    sorted_freq = freq[order]
    sorted_power = power[order]
    cdf = np.cumsum(sorted_power) / total
    lower = sorted_freq[np.searchsorted(cdf, (1.0 - fraction) / 2.0)]
    upper = sorted_freq[np.searchsorted(cdf, 1.0 - (1.0 - fraction) / 2.0)]
    return float(upper - lower)


def threshold_bandwidth_hz(samples: np.ndarray, sample_rate: float, threshold_db: float = -20.0) -> float:
    centered = samples - np.mean(samples)
    window = np.hanning(len(centered))
    spectrum = np.fft.fftshift(np.fft.fft(centered * window))
    freq = np.fft.fftshift(np.fft.fftfreq(len(centered), d=1.0 / sample_rate))
    magnitude_db = 20.0 * np.log10(np.maximum(np.abs(spectrum), 1e-12))
    magnitude_db -= float(np.max(magnitude_db))
    mask = magnitude_db >= threshold_db
    if not np.any(mask):
        return 0.0
    return float(freq[mask][-1] - freq[mask][0])


def constellation_metrics(points: np.ndarray) -> dict[str, float]:
    if len(points) == 0:
        return {
            "constellation_points": 0,
            "evm_rms_percent": float("nan"),
            "evm_db": float("nan"),
            "snr_est_db": float("nan"),
        }

    # Estimate cluster centers from received quadrants, then measure residual spread.
    quadrants = np.column_stack((points.real >= 0.0, points.imag >= 0.0))
    centers = []
    residual_power = 0.0
    signal_power = 0.0
    used = 0
    for i_positive in (False, True):
        for q_positive in (False, True):
            mask = (quadrants[:, 0] == i_positive) & (quadrants[:, 1] == q_positive)
            if not np.any(mask):
                continue
            cluster = points[mask]
            center = np.mean(cluster)
            centers.append(center)
            residual_power += float(np.sum(np.abs(cluster - center) ** 2))
            signal_power += float(len(cluster) * np.abs(center) ** 2)
            used += len(cluster)

    if used == 0 or signal_power <= 0.0:
        evm_rms = float("nan")
    else:
        evm_rms = float(np.sqrt(residual_power / signal_power))

    return {
        "constellation_points": int(used),
        "evm_rms_percent": evm_rms * 100.0,
        "evm_db": db20(evm_rms),
        "snr_est_db": -db20(evm_rms),
        "cluster_center_count": len(centers),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a saved Python SDR RF loopback IQ capture.")
    parser.add_argument("capture", type=Path)
    parser.add_argument("--adc-full-scale", type=float, default=2048.0)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    data = np.load(args.capture)
    rx = data["rx"]
    sample_rate = float(data["sample_rate"])
    symbol_rate = float(data["symbol_rate"])
    packet_count = int(data["packet_count"])
    expected_payload = b"hello rf"

    modem = QpskLoopbackModem(
        ModemConfig(
            sample_rate=sample_rate,
            symbol_rate=symbol_rate,
            tx_amplitude=float(data["tx_amplitude"]),
        )
    )
    packets = modem.receive_many(rx, packet_count)
    good_sequences = {
        packet.sequence
        for packet in packets
        if 1 <= packet.sequence <= packet_count and packet.payload == expected_payload
    }
    points = modem.constellation_points(rx, max_points=4000)
    magnitude = np.abs(rx)
    rms = float(np.sqrt(np.mean(magnitude * magnitude)))
    peak = float(np.max(magnitude))

    theoretical_rrc_bw = symbol_rate * (1.0 + modem.config.rrc_alpha)
    report = {
        "capture": str(args.capture),
        "packet_count": packet_count,
        "packets_decoded": len(packets),
        "packets_ok": len(good_sequences),
        "packet_error_rate": 1.0 - len(good_sequences) / max(packet_count, 1),
        "missing_sequences": [i for i in range(1, packet_count + 1) if i not in good_sequences],
        "sample_rate_hz": sample_rate,
        "symbol_rate_hz": symbol_rate,
        "rrc_alpha": modem.config.rrc_alpha,
        "theoretical_rrc_bandwidth_hz": theoretical_rrc_bw,
        "occupied_bw_99_hz": occupied_bandwidth_hz(rx, sample_rate, 0.99),
        "threshold_bw_minus_20db_hz": threshold_bandwidth_hz(rx, sample_rate, -20.0),
        "rx_rms_counts": rms,
        "rx_peak_counts": peak,
        "rx_rms_dbfs": db20(rms / args.adc_full_scale),
        "rx_peak_dbfs": db20(peak / args.adc_full_scale),
        **constellation_metrics(points),
    }

    for key, value in report.items():
        if isinstance(value, float):
            print(f"{key}={value:.6f}")
        else:
            print(f"{key}={value}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"json_report={args.json_out}")

    return 0 if report["packets_ok"] == packet_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
