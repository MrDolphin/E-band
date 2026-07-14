"""Pure display transforms and Tk panes for FMCW radar frames."""

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Optional
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

from .config import RadarConfig
from .models import RadarDiagnostics, RadarTarget


def radar_xy(
    range_m: float,
    azimuth_deg: Optional[float],
    radius_px: float,
    max_range_m: float,
) -> tuple[float, float]:
    """Map polar radar coordinates to canvas offsets from the sensor origin."""
    angle_rad = math.radians(azimuth_deg if azimuth_deg is not None else 0.0)
    distance_px = radius_px * range_m / max_range_m
    return distance_px * math.sin(angle_rad), -distance_px * math.cos(angle_rad)


def downsample_heatmap(
    values: np.ndarray,
    *,
    rows: int = 32,
    columns: int = 64,
) -> np.ndarray:
    """Peak-pool a heatmap into a bounded, still-visible cell grid."""
    source = np.asarray(values, dtype=float)
    if source.ndim != 2:
        raise ValueError("heatmap must be two-dimensional")
    if source.size == 0 or rows <= 0 or columns <= 0:
        raise ValueError("heatmap and output dimensions must be nonempty")

    def groups(length: int, count: int) -> tuple[np.ndarray, ...]:
        return tuple(
            np.arange(
                math.floor(index * length / count),
                max(
                    math.floor(index * length / count) + 1,
                    math.ceil((index + 1) * length / count),
                ),
            )
            for index in range(count)
        )

    row_groups = groups(source.shape[0], rows)
    column_groups = groups(source.shape[1], columns)
    return np.asarray(
        [
            [float(np.max(source[np.ix_(row_group, column_group)])) for column_group in column_groups]
            for row_group in row_groups
        ]
    )


def axis_ticks(minimum: float, maximum: float, count: int) -> tuple[float, ...]:
    """Return inclusive, evenly spaced axis tick values."""
    if count < 2:
        raise ValueError("tick count must be at least two")
    return tuple(float(value) for value in np.linspace(minimum, maximum, count))


def orient_heatmap_for_canvas(values: np.ndarray) -> np.ndarray:
    """Put ascending velocity rows onto a Canvas whose y axis points down."""
    return np.flipud(np.asarray(values))


def heatmap_plot_bounds(width: int, height: int) -> tuple[int, int, int, int]:
    """Fit labeled heatmap bounds inside the current Canvas allocation."""
    right = max(72, width - 8)
    left = min(72, right - 64)
    bottom = max(52, height - 34)
    top = min(20, bottom - 32)
    return left, top, min(right, width), min(bottom, height)


def range_doppler_y_axis_layout(
    plot_left: int,
    plot_top: int,
    plot_bottom: int,
) -> tuple[tuple[float, float, str, int], tuple[float, float, str, int]]:
    """Reserve separate horizontal bands for the rotated title and tick text."""
    middle_y = (plot_top + plot_bottom) / 2.0
    return (14.0, middle_y, "center", 90), (
        float(plot_left - 7),
        middle_y,
        "e",
        0,
    )


def dashboard_column_options() -> dict[str, object]:
    """Return identical Tk grid options for the two dashboard columns."""
    return {"weight": 1, "uniform": "radar-plots"}


def equal_dashboard_widths(total_width: int) -> tuple[int, int]:
    """Split available width into equal integer columns for layout checks."""
    left = total_width // 2
    return left, total_width - left


def semicircle_label_layout(
    origin_x: float,
    origin_y: float,
    radius: float,
    max_range_m: float,
) -> tuple[dict[int, tuple[float, float, str]], tuple[float, float, str]]:
    """Place range labels on a slight bearing, leaving boresight tick clear."""
    bearing = math.radians(12.0)
    rings = {
        ring_m: (
            origin_x + ring_radius * math.sin(bearing),
            origin_y - ring_radius * math.cos(bearing),
            "sw",
        )
        for ring_m in (10, 20, 30, 40, 50)
        if (ring_radius := radius * ring_m / max_range_m) <= radius + 0.5
    }
    zero_tick = (origin_x, origin_y - radius - 12.0, "center")
    return rings, zero_tick


def format_target(target: RadarTarget) -> str:
    """Format one target row, explicitly describing unavailable azimuth."""
    angle = (
        f"{target.azimuth_deg:+.1f}°"
        if target.azimuth_deg is not None
        else "单RX：方位角未测量"
    )
    return " | ".join(
        (
            target.target_id,
            f"{target.range_m:.2f} m",
            f"{target.radial_velocity_mps:+.2f} m/s",
            angle,
            f"{target.snr_db:.1f} dB",
            f"{target.confidence:.0%}",
        )
    )


def format_diagnostics(diagnostics: RadarDiagnostics) -> str:
    """Format all runtime diagnostics as concise Simplified Chinese rows."""
    return "\n".join(
        (
            f"帧: {diagnostics.frame_index}",
            f"源: {diagnostics.source}",
            f"同步: {'正常' if diagnostics.sync_ok else '失败'} ({diagnostics.sync_score:.3f})",
            f"相位一致性: {diagnostics.phase_consistency:.3f}",
            f"RMS: {diagnostics.rms:.2f} dBFS",
            f"峰值: {diagnostics.peak:.2f} dBFS",
            f"削顶: {'是' if diagnostics.clipping else '否'} ({diagnostics.clip_ratio:.3%})",
            f"噪声底: {diagnostics.noise_floor_db:.2f} dB",
            f"处理时间: {diagnostics.processing_time_ms:.2f} ms",
            f"超时: {diagnostics.overruns}",
        )
    )


def format_derived_config(config: RadarConfig) -> str:
    """Format physical values, deriving every value from ``RadarConfig``."""
    cpi_ms = config.cpi_duration_s * 1000.0
    return (
        f"每chirp {config.active_samples} 点 | 距离分辨率 {config.range_resolution_m:.2f} m | "
        f"最大无模糊速度 ±{config.max_unambiguous_velocity_mps:.2f} m/s | "
        f"速度分辨率 {config.velocity_resolution_mps:.2f} m/s | CPI {cpi_ms:.3f} ms"
    )


def radar_config_from_values(values: Mapping[str, str]) -> RadarConfig:
    """Validate display strings and construct the sole physical configuration."""
    converted = {
        "carrier_hz": float(values["carrier_ghz"]) * 1e9,
        "sample_rate_hz": float(values["sample_rate_msps"]) * 1e6,
        "bandwidth_hz": float(values["bandwidth_mhz"]) * 1e6,
        "active_time_s": float(values["active_us"]) * 1e-6,
        "idle_time_s": float(values["idle_us"]) * 1e-6,
        "chirp_count": int(values["chirp_count"]),
        "cfar_threshold_db": float(values["cfar_threshold_db"]),
        "max_display_range_m": float(values["max_display_range_m"]),
    }
    for name in (
        "carrier_hz",
        "sample_rate_hz",
        "bandwidth_hz",
        "active_time_s",
        "max_display_range_m",
    ):
        if not math.isfinite(converted[name]) or converted[name] <= 0.0:
            raise ValueError(f"{name} must be positive and finite")
    if not math.isfinite(converted["idle_time_s"]) or converted["idle_time_s"] < 0.0:
        raise ValueError("idle_time_s must be nonnegative and finite")
    if converted["chirp_count"] <= 0:
        raise ValueError("chirp_count must be positive")
    if not math.isfinite(converted["cfar_threshold_db"]):
        raise ValueError("cfar_threshold_db must be finite")
    return RadarConfig(**converted)


def validate_runtime_inputs(
    tx_gain: str,
    rx_gain: str,
    target_range: str,
    target_velocity: str,
    target_snr: str,
) -> tuple[float, float, float, float, float]:
    """Validate non-configuration GUI values before starting a runtime."""
    converted = tuple(
        float(value)
        for value in (tx_gain, rx_gain, target_range, target_velocity, target_snr)
    )
    if not all(math.isfinite(value) for value in converted):
        raise ValueError("gain and synthetic target values must be finite")
    if converted[2] < 0.0:
        raise ValueError("synthetic target range must be nonnegative")
    return converted


def poll_radar(
    controller: object,
    dashboard: object,
    last_frame_index: Optional[int],
    source_label: str,
) -> tuple[Optional[int], object]:
    """Render one runtime-adjusted latest frame and return one status snapshot."""
    status = controller.status()
    frame = controller.latest_frame()
    if frame is None or frame.frame_index == last_frame_index:
        return last_frame_index, status
    diagnostics = replace(
        frame.diagnostics,
        source=source_label,
        overruns=status.overruns,
    )
    adjusted_frame = replace(frame, diagnostics=diagnostics)
    dashboard.render(adjusted_frame)
    return frame.frame_index, status


class SemicircleRadarPane(ttk.LabelFrame):
    """Upper-semicircle plan-position display for measured targets."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, text="半圆雷达图")
        self.canvas = tk.Canvas(self, background="#07121f", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

    def render(self, frame: object) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        height = max(canvas.winfo_height(), 260)
        origin_x, origin_y = width / 2.0, height - 28.0
        radius = min(width / 2.0 - 42.0, height - 58.0)
        max_range = frame.config_snapshot.max_display_range_m
        grid = "#2d5974"
        ring_labels, zero_tick_label = semicircle_label_layout(
            origin_x, origin_y, radius, max_range
        )
        for ring_m, (label_x, label_y, label_anchor) in ring_labels.items():
            ring_radius = radius * ring_m / max_range
            canvas.create_arc(
                origin_x - ring_radius,
                origin_y - ring_radius,
                origin_x + ring_radius,
                origin_y + ring_radius,
                start=0,
                extent=180,
                outline=grid,
            )
            canvas.create_text(
                label_x,
                label_y,
                text=f"{ring_m} m",
                anchor=label_anchor,
                fill="#9bc7dd",
            )
        for angle in axis_ticks(-90.0, 90.0, 7):
            x, y = radar_xy(max_range, angle, radius, max_range)
            canvas.create_line(origin_x, origin_y, origin_x + x, origin_y + y, fill=grid)
            label_x, label_y, label_anchor = (
                zero_tick_label
                if angle == 0.0
                else (origin_x + x, origin_y + y - 8, "center")
            )
            canvas.create_text(
                label_x,
                label_y,
                text=f"{angle:+.0f}°" if angle else "0°",
                anchor=label_anchor,
                fill="#b9d9e8",
            )
        canvas.create_text(width / 2.0, 10, text="方位角", fill="#dcecf4")
        canvas.create_text(12, height / 2.0, text="径向距离 (m)", anchor="w", fill="#dcecf4")
        canvas.create_oval(origin_x - 4, origin_y - 4, origin_x + 4, origin_y + 4, fill="#f4d35e")
        for target in frame.targets:
            x, y = radar_xy(target.range_m, target.azimuth_deg, radius, max_range)
            target_x, target_y = origin_x + x, origin_y + y
            canvas.create_oval(
                target_x - 5,
                target_y - 5,
                target_x + 5,
                target_y + 5,
                fill="#ff5d73",
                outline="white",
            )
            canvas.create_text(
                target_x + 8,
                target_y - 7,
                text=target.target_id,
                anchor="sw",
                fill="white",
            )


class RangeDopplerPane(ttk.LabelFrame):
    """Canvas bitmap range-Doppler heatmap with explicit physical axes."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, text="距离-速度图")
        self.canvas = tk.Canvas(self, background="#081018", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._last_frame: Optional[object] = None
        self.canvas.bind("<Configure>", self._on_configure)

    def _on_configure(self, _event: tk.Event) -> None:
        if self._last_frame is not None:
            self.render(self._last_frame)

    def render(self, frame: object) -> None:
        self._last_frame = frame
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 80)
        height = max(canvas.winfo_height(), 60)
        left, top, right, bottom = heatmap_plot_bounds(width, height)
        range_mask = frame.range_axis_m <= frame.config_snapshot.max_display_range_m
        values = frame.range_doppler_db[:, range_mask]
        if values.shape[1] == 0:
            values = frame.range_doppler_db[:, :1]
        cells = orient_heatmap_for_canvas(
            downsample_heatmap(values, rows=32, columns=64)
        )
        finite = cells[np.isfinite(cells)]
        floor = float(np.percentile(finite, 5)) if finite.size else -120.0
        ceiling = float(np.percentile(finite, 99)) if finite.size else 0.0
        normalized = np.clip((cells - floor) / max(ceiling - floor, 1e-9), 0.0, 1.0)
        rgb = np.empty((*normalized.shape, 3), dtype=np.uint8)
        rgb[..., 0] = np.clip(510.0 * normalized - 255.0, 0.0, 255.0)
        rgb[..., 1] = np.clip(510.0 * np.minimum(normalized, 1.0 - normalized), 0.0, 255.0)
        rgb[..., 2] = np.clip(255.0 - 510.0 * normalized, 0.0, 255.0)
        image = Image.fromarray(rgb, mode="RGB").resize(
            (max(1, right - left), max(1, bottom - top)), Image.Resampling.NEAREST
        )
        self._photo = ImageTk.PhotoImage(image)
        canvas.create_image(left, top, anchor="nw", image=self._photo)
        canvas.create_rectangle(left, top, right, bottom, outline="#d5e8f0")
        for value in axis_ticks(0.0, frame.config_snapshot.max_display_range_m, 6):
            x = left + (right - left) * value / frame.config_snapshot.max_display_range_m
            canvas.create_line(x, bottom, x, bottom + 4, fill="white")
            canvas.create_text(x, bottom + 6, text=f"{value:.0f}", anchor="n", fill="white")
        velocity_min = float(frame.velocity_axis_mps[0])
        velocity_max = float(frame.velocity_axis_mps[-1])
        title_layout, _middle_tick_layout = range_doppler_y_axis_layout(
            left, top, bottom
        )
        for value in axis_ticks(velocity_min, velocity_max, 5):
            y = bottom - (bottom - top) * (value - velocity_min) / max(velocity_max - velocity_min, 1e-9)
            canvas.create_line(left - 4, y, left, y, fill="white")
            canvas.create_text(left - 7, y, text=f"{value:.1f}", anchor="e", fill="white")
        canvas.create_text((left + right) / 2.0, height - 8, text="距离 (m)", fill="white")
        canvas.create_text(
            title_layout[0],
            title_layout[1],
            text="径向速度 (m/s)",
            anchor=title_layout[2],
            angle=title_layout[3],
            fill="white",
        )


class TargetTablePane(ttk.LabelFrame):
    """Detected target table including explicit single-RX angle status."""

    _COLUMNS = ("id", "range", "velocity", "angle", "snr", "confidence")

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, text="目标列表")
        self.table = ttk.Treeview(self, columns=self._COLUMNS, show="headings", height=7)
        headings = ("ID", "距离", "径向速度", "角度状态", "SNR", "置信度")
        widths = (45, 65, 75, 115, 55, 60)
        for column, heading, width in zip(self._COLUMNS, headings, widths):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, anchor=tk.CENTER)
        self.table.pack(fill=tk.BOTH, expand=True)

    def render(self, frame: object) -> None:
        self.table.delete(*self.table.get_children())
        for target in frame.targets:
            angle = f"{target.azimuth_deg:+.1f}°" if target.azimuth_deg is not None else "方位角未测量"
            self.table.insert(
                "",
                tk.END,
                values=(
                    target.target_id,
                    f"{target.range_m:.2f} m",
                    f"{target.radial_velocity_mps:+.2f} m/s",
                    angle,
                    f"{target.snr_db:.1f} dB",
                    f"{target.confidence:.0%}",
                ),
            )


class RadarDiagnosticsPane(ttk.LabelFrame):
    """Complete per-frame diagnostic snapshot."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, text="雷达诊断")
        self.value = tk.StringVar(value="等待雷达帧")
        ttk.Label(self, textvariable=self.value, justify=tk.LEFT).pack(
            fill=tk.BOTH, expand=True, padx=8, pady=8, anchor="nw"
        )

    def render(self, frame: object) -> None:
        self.value.set(format_diagnostics(frame.diagnostics))


class RadarDashboard(ttk.Frame):
    """Render every radar view atomically from one ``RadarFrame``."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        column_options = dashboard_column_options()
        self.columnconfigure(0, **column_options)
        self.columnconfigure(1, **column_options)
        self.rowconfigure(0, weight=2)
        self.rowconfigure(1, weight=1)
        self.radar = SemicircleRadarPane(self)
        self.heatmap = RangeDopplerPane(self)
        self.targets = TargetTablePane(self)
        self.diagnostics = RadarDiagnosticsPane(self)
        self.radar.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 5))
        self.heatmap.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=(0, 5))
        self.targets.grid(row=1, column=0, sticky="nsew", padx=(0, 5), pady=(5, 0))
        self.diagnostics.grid(row=1, column=1, sticky="nsew", padx=(5, 0), pady=(5, 0))

    def render(self, frame: object) -> None:
        for pane in (self.radar, self.heatmap, self.targets, self.diagnostics):
            pane.render(frame)
