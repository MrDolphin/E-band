import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze E310 int16 I/Q capture.")
    parser.add_argument("path", type=Path, help="rx_capture_*.iq file")
    parser.add_argument("--sample-rate", type=float, default=3_840_000.0)
    parser.add_argument(
        "--adc-full-scale",
        type=float,
        default=2048.0,
        help="ADC signed full-scale value; E310 RX is 12-bit by default",
    )
    args = parser.parse_args()

    raw = np.fromfile(args.path, dtype="<i2")
    if raw.size < 4:
        raise SystemExit("Capture is empty or too short.")
    raw = raw[: raw.size - raw.size % 2].reshape(-1, 2)
    iq = raw[:, 0].astype(np.float64) / args.adc_full_scale
    qq = raw[:, 1].astype(np.float64) / args.adc_full_scale
    signal = iq + 1j * qq
    window_size = 4096
    complete_window_samples = signal.size - signal.size % window_size
    window_rms = np.sqrt(
        np.mean(
            np.abs(signal[:complete_window_samples].reshape(-1, window_size)) ** 2,
            axis=1,
        )
    )
    strongest_window_index = int(np.argmax(window_rms))
    strongest_start = strongest_window_index * window_size
    strongest = signal[strongest_start : strongest_start + window_size]

    rms_i = np.sqrt(np.mean(iq * iq))
    rms_q = np.sqrt(np.mean(qq * qq))
    rms = np.sqrt(np.mean(np.abs(signal) ** 2))
    peak = np.max(np.abs(signal))
    imbalance_db = 10.0 * np.log10(
        max(np.mean(iq * iq), 1e-20) / max(np.mean(qq * qq), 1e-20)
    )
    iq_corr = np.mean(iq * qq) / max(rms_i * rms_q, 1e-20)
    fourth = signal**4
    coarse_frequency = (
        np.angle(np.sum(fourth[1:] * np.conj(fourth[:-1])))
        * args.sample_rate
        / (8.0 * np.pi)
    )

    print(f"Samples: {signal.size}")
    print(f"Duration: {signal.size / args.sample_rate:.6f} s")
    print(f"Mean I/Q: {np.mean(iq):+.8f} / {np.mean(qq):+.8f}")
    print(f"RMS I/Q: {rms_i:.8f} / {rms_q:.8f}")
    print(f"Total RMS: {rms:.8f}")
    print(f"Peak: {peak:.8f}")
    print(
        "Clipping: "
        f"{np.mean((np.abs(raw[:, 0]) >= 2047) | (np.abs(raw[:, 1]) >= 2047)) * 100:.6f}%"
    )
    print(f"Zero samples: {np.mean(np.all(raw == 0, axis=1)) * 100:.6f}%")
    print(f"I/Q imbalance: {imbalance_db:+.3f} dB")
    print(f"I/Q correlation: {iq_corr:+.6f}")
    print(f"Coarse QPSK frequency offset: {coarse_frequency:+.1f} Hz")
    print(
        f"Strongest 4096-sample window: index={strongest_window_index}, "
        f"time={strongest_start / args.sample_rate * 1e3:.3f} ms, "
        f"RMS={window_rms[strongest_window_index]:.8f}, "
        f"peak={np.max(np.abs(strongest)):.8f}"
    )
    print(
        "Window RMS percentiles: "
        + ", ".join(
            f"{percentile:g}%={value:.8f}"
            for percentile, value in zip(
                (0, 25, 50, 75, 90, 99, 100),
                np.percentile(window_rms, (0, 25, 50, 75, 90, 99, 100)),
            )
        )
    )

    plot_samples = min(signal.size, 200_000)
    spectrum_samples = min(signal.size, 1 << 18)
    window = np.hanning(spectrum_samples)
    spectrum = np.fft.fftshift(np.fft.fft(signal[:spectrum_samples] * window))
    frequency = np.fft.fftshift(
        np.fft.fftfreq(spectrum_samples, d=1.0 / args.sample_rate)
    )
    spectrum_db = 20.0 * np.log10(
        np.maximum(np.abs(spectrum) / max(np.max(np.abs(spectrum)), 1e-20), 1e-12)
    )

    figure, axes = plt.subplots(3, 1, figsize=(11, 10), constrained_layout=True)
    axes[0].plot(
        np.arange(min(4000, signal.size)) / args.sample_rate * 1e6,
        iq[:4000],
        label="I",
        linewidth=0.8,
    )
    axes[0].plot(
        np.arange(min(4000, signal.size)) / args.sample_rate * 1e6,
        qq[:4000],
        label="Q",
        linewidth=0.8,
    )
    axes[0].set_title("Time domain")
    axes[0].set_xlabel("Time (us)")
    axes[0].grid(True)
    axes[0].legend()

    axes[1].plot(frequency / 1e6, spectrum_db, linewidth=0.8)
    axes[1].set_title("Baseband spectrum")
    axes[1].set_xlabel("Frequency (MHz)")
    axes[1].set_ylabel("Relative level (dB)")
    axes[1].set_ylim(-100, 5)
    axes[1].grid(True)

    stride = max(1, plot_samples // 30_000)
    axes[2].scatter(
        iq[:plot_samples:stride],
        qq[:plot_samples:stride],
        s=1,
        alpha=0.25,
    )
    axes[2].set_title("I/Q constellation")
    axes[2].set_xlabel("I")
    axes[2].set_ylabel("Q")
    axes[2].axis("equal")
    axes[2].grid(True)

    output = args.path.with_suffix(".analysis.png")
    figure.savefig(output, dpi=160)
    print(f"Plot: {output}")

    strongest_spectrum = np.fft.fftshift(
        np.fft.fft(strongest * np.hanning(strongest.size))
    )
    strongest_frequency = np.fft.fftshift(
        np.fft.fftfreq(strongest.size, d=1.0 / args.sample_rate)
    )
    strongest_spectrum_db = 20.0 * np.log10(
        np.maximum(
            np.abs(strongest_spectrum)
            / max(np.max(np.abs(strongest_spectrum)), 1e-20),
            1e-12,
        )
    )
    active_figure, active_axes = plt.subplots(
        3, 1, figsize=(11, 10), constrained_layout=True
    )
    active_time = np.arange(strongest.size) / args.sample_rate * 1e6
    active_axes[0].plot(active_time, strongest.real, label="I", linewidth=0.8)
    active_axes[0].plot(active_time, strongest.imag, label="Q", linewidth=0.8)
    active_axes[0].set_title(
        f"Strongest window #{strongest_window_index} time domain"
    )
    active_axes[0].set_xlabel("Time (us)")
    active_axes[0].grid(True)
    active_axes[0].legend()
    active_axes[1].plot(
        strongest_frequency / 1e6, strongest_spectrum_db, linewidth=0.8
    )
    active_axes[1].set_title("Strongest-window baseband spectrum")
    active_axes[1].set_xlabel("Frequency (MHz)")
    active_axes[1].set_ylabel("Relative level (dB)")
    active_axes[1].set_ylim(-100, 5)
    active_axes[1].grid(True)
    active_axes[2].scatter(
        strongest.real,
        strongest.imag,
        s=3,
        alpha=0.35,
    )
    active_axes[2].set_title("Strongest-window raw I/Q")
    active_axes[2].set_xlabel("I")
    active_axes[2].set_ylabel("Q")
    active_axes[2].axis("equal")
    active_axes[2].grid(True)
    active_output = args.path.with_suffix(".active.png")
    active_figure.savefig(active_output, dpi=160)
    print(f"Strongest-window plot: {active_output}")


if __name__ == "__main__":
    main()
