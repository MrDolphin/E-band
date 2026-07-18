from __future__ import annotations

import functools
import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
import io
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))


from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.assessment import assess_frame, append_position_candidate
from sdr_loopback.radar.controller import RadarController
from sdr_loopback.radar.models import SyntheticTarget
from sdr_loopback.radar.processor import FmcwProcessor
from sdr_loopback.radar.sources import (
    E310CpiSource,
    E310RadioConfig,
    IqReplaySource,
    SyntheticTargetSource,
)
from sdr_loopback.radar.storage import load_capture_config, save_frame
from sdr_loopback.radar.synchronizer import ChirpSyncError
from sdr_loopback.radar.ui import (
    RadarDashboard,
    format_derived_config,
    poll_radar,
    radar_config_from_values,
    validate_runtime_inputs,
)


STREAM_SCRIPT = SCRIPT_DIR / "run_low_latency_ts_stream.py"


@dataclass(frozen=True)
class Preset:
    label: str
    description: str
    args: tuple[str, ...]


PRESETS: tuple[Preset, ...] = (
    Preset(
        "稳定演示",
        "250k, 360p, 12fps, veryfast, 接收端小缓冲，优先流畅播放",
        ("--chunk-bytes", "16000"),
    ),
    Preset(
        "E波段稳定演示",
        "E波段实测最稳档：300k, 360p, 12fps, veryfast, 16 KB 分块，20 ms 发射稳定等待",
        (
            "--video-bitrate",
            "300k",
            "--video-bufsize",
            "600k",
            "--encoder-preset",
            "veryfast",
            "--scale-height",
            "360",
            "--fps",
            "12",
            "--gop",
            "12",
            "--chunk-bytes",
            "16000",
            "--tx-settle-sec",
            "0.02",
        ),
    ),
    Preset(
        "E波段画质演示",
        "E波段画质档：400k, 360p, 12fps, veryfast, 20 KB 分块，适合链路状态较好时展示",
        (
            "--video-bitrate",
            "400k",
            "--video-bufsize",
            "800k",
            "--encoder-preset",
            "veryfast",
            "--scale-height",
            "360",
            "--fps",
            "12",
            "--gop",
            "12",
            "--chunk-bytes",
            "20000",
            "--tx-settle-sec",
            "0.02",
        ),
    ),
    Preset(
        "快速测试",
        "稳定演示参数，但 20 个 chunk 后停止",
        ("--chunk-bytes", "16000", "--max-chunks", "20"),
    ),
    Preset(
        "低延迟测试",
        "更小块和 nobuffer，延迟更低但可能卡顿",
        ("--player-nobuffer", "--chunk-bytes", "12000"),
    ),
    Preset(
        "保守链路",
        "更低码率和帧率，用于较弱链路",
        ("--video-bitrate", "200k", "--video-bufsize", "400k", "--fps", "10", "--gop", "10", "--chunk-bytes", "15000"),
    ),
    Preset(
        "画质尝试",
        "尝试更高码率，可能卡顿",
        ("--video-bitrate", "300k", "--video-bufsize", "600k", "--fps", "12", "--gop", "12"),
    ),
    Preset("无播放调试", "不打开 ffplay，只跑 RF 流，20 个 chunk 后停止", ("--no-player", "--max-chunks", "20")),
)


def create_radar_runtime(
    source_mode: str,
    display_config: RadarConfig | None,
    replay_path: Path | None,
    synthetic_target: SyntheticTarget | None,
    radio_config: E310RadioConfig | None = None,
) -> tuple[RadarController, RadarConfig]:
    """Build a controller with the source-owned effective configuration."""
    if source_mode == "IQ回放":
        if replay_path is None or not replay_path.is_file():
            raise ValueError("请选择有效的IQ回放文件")
        effective_config = load_capture_config(replay_path)
        source = IqReplaySource((replay_path,), loop=True)
        sync_mode = "known"
    elif source_mode == "E310":
        if display_config is None or radio_config is None:
            raise ValueError("E310模式需要雷达和射频参数")
        effective_config = display_config
        source = E310CpiSource(effective_config, radio_config)
        sync_mode = "correlation"
    else:
        if display_config is None or synthetic_target is None:
            raise ValueError("仿真模式需要目标参数")
        effective_config = display_config
        source = SyntheticTargetSource(effective_config, (synthetic_target,))
        sync_mode = "known"
    controller = RadarController(
        source,
        FmcwProcessor(effective_config, sync_mode=sync_mode),
        effective_config.cpi_duration_s,
        recoverable_processing_errors=(ChirpSyncError,)
        if source_mode == "E310"
        else (),
    )
    return controller, effective_config


def shutdown_video_process(process: subprocess.Popen | None, timeout_s: float = 1.0) -> None:
    """Synchronously stop one video process within two bounded waits."""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
    except Exception:
        try:
            process.kill()
        except Exception:
            return
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            return
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            pass


class VideoPane(ttk.Frame):
    def __init__(self, parent: tk.Widget, title: str, width: int = 640, height: int = 360) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.photo: ImageTk.PhotoImage | None = None

        ttk.Label(self, text=title, anchor="center").grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.canvas = tk.Canvas(self, background="black", highlightthickness=1, highlightbackground="#333333")
        self.set_display_size(width, height)
        self.canvas.grid(row=1, column=0)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.image: Image.Image | None = None
        self.placeholder = "等待视频"
        self.redraw()

    def set_display_size(self, width: int, height: int) -> None:
        self.canvas.configure(width=max(1, int(width)), height=max(1, int(height)))

    def show_placeholder(self, text: str) -> None:
        self.placeholder = text
        self.image = None
        self.redraw()

    def show_image(self, image: Image.Image) -> None:
        self.image = image
        self.redraw()

    def redraw(self) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, width, height, fill="black", outline="")
        if self.image is None:
            self.canvas.create_text(width // 2, height // 2, text=self.placeholder, fill="#888888")
            return

        image = self.image.copy()
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        x = (width - image.width) // 2
        y = (height - image.height) // 2
        self.canvas.create_image(x, y, anchor="nw", image=self.photo)


class LinkStatsPane(ttk.LabelFrame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, text="链路质量 / 发送状态", padding=12)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.values: dict[str, tk.StringVar] = {}
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas)
        self.content.columnconfigure(1, weight=1)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window_id, width=event.width),
        )
        rows = (
            ("input", "输入文件"),
            ("output", "输出文件"),
            ("profile", "视频配置"),
            ("player_buffered", "播放缓冲"),
            ("chunk_effective", "有效块大小"),
            ("ts_aligned", "TS 包对齐"),
            ("raw_bitrate", "QPSK 原始速率"),
            ("payload_bitrate", "有效载荷估计"),
            ("chunk_bytes", "当前块大小"),
            ("output_bytes", "已恢复数据"),
            ("lo_hz", "中心频率 LO"),
            ("sample_rate", "采样率"),
            ("symbol_rate", "符号率"),
            ("rf_bandwidth", "模拟带宽"),
            ("tx_gain", "TX 增益"),
            ("rx_gain", "RX 增益"),
            ("rx_samples", "RX 样点数"),
            ("rx_rms", "接收电平 RMS"),
            ("rx_peak", "接收峰值"),
            ("rx_clip", "削顶比例"),
            ("packets_decoded", "已解包分组"),
            ("chunks_in_chunk", "块内有效分片"),
            ("missing_chunks", "缺失分片"),
            ("chunk_elapsed", "单块耗时"),
            ("capture_attempts", "采集尝试"),
            ("context_recreates", "IIO 重建"),
            ("chunks", "块成功/失败"),
            ("goodput", "当前吞吐"),
            ("stream_elapsed", "链路运行时间"),
            ("stream_ok", "运行结果"),
            ("receiver", "接收播放"),
            ("delay", "对照说明"),
        )
        for row, (key, label) in enumerate(rows):
            ttk.Label(self.content, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 14), pady=3)
            variable = tk.StringVar(value="-")
            self.values[key] = variable
            ttk.Label(self.content, textvariable=variable, font=("Segoe UI", 10, "bold"), wraplength=520).grid(
                row=row, column=1, sticky="w", pady=3
            )

    def set_value(self, key: str, value: str) -> None:
        if key in self.values:
            self.values[key].set(value)

    def reset(self) -> None:
        for variable in self.values.values():
            variable.set("-")


class SdrVideoGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ANTSDR E310 实时视频回环演示")
        self.geometry("1080x760")
        self.minsize(900, 620)

        self.process: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.output_queue: queue.Queue[object] = queue.Queue()
        self.preview_process: subprocess.Popen | None = None
        self.preview_thread: threading.Thread | None = None
        self.preview_after_id: str | None = None
        self.receiver_output_path: Path | None = None
        self.radar_controller: RadarController | None = None
        self._latest_radar_frame: object | None = None
        self._last_radar_frame_index: int | None = None
        self._radar_calibration_deadline: float | None = None
        self.radar_source_label = "仿真"

        self.input_var = tk.StringVar(value=str(PROJECT_DIR / "small.mp4"))
        self.work_dir_var = tk.StringVar(value=str(PROJECT_DIR / "artifacts" / "gui_stream_demo"))
        self.preset_var = tk.StringVar(value=PRESETS[0].label)
        self.extra_args_var = tk.StringVar(value="")
        self.source_preview_var = tk.BooleanVar(value=True)
        self.source_preview_delay_var = tk.DoubleVar(value=6.0)
        self.auto_player_size_var = tk.BooleanVar(value=True)
        self.player_width_var = tk.IntVar(value=640)
        self.player_height_var = tk.IntVar(value=360)

        self.status_var = tk.StringVar(value="就绪")
        self.chunk_var = tk.StringVar(value="-")
        self.goodput_var = tk.StringVar(value="-")
        self.ok_var = tk.StringVar(value="-")
        self.context_var = tk.StringVar(value="-")
        self.elapsed_var = tk.StringVar(value="-")
        self.source_status_var = tk.StringVar(value="未启动")
        self.receiver_status_var = tk.StringVar(value="未启动")
        self.delay_status_var = tk.StringVar(value="-")
        self.profile_parts: dict[str, str] = {}

        self.radar_source_var = tk.StringVar(value="仿真")
        self.radar_carrier_ghz_var = tk.StringVar(value="76")
        self.radar_sample_rate_msps_var = tk.StringVar(value="30")
        self.radar_bandwidth_mhz_var = tk.StringVar(value="20")
        self.radar_active_us_var = tk.StringVar(value="128")
        self.radar_idle_us_var = tk.StringVar(value="16")
        self.radar_chirp_count_var = tk.StringVar(value="64")
        self.radar_tx_gain_var = tk.StringVar(value="-20")
        self.radar_rx_gain_var = tk.StringVar(value="20")
        self.radar_tx_amplitude_var = tk.StringVar(value="0.40")
        self.radar_tx_channel_var = tk.StringVar(value="0")
        self.radar_rx_channel_var = tk.StringVar(value="0")
        self.radar_cfar_db_var = tk.StringVar(value="12")
        self.radar_display_range_var = tk.StringVar(value="50")
        self.radar_replay_path_var = tk.StringVar(value="")
        self.radar_target_range_var = tk.StringVar(value="25")
        self.radar_target_velocity_var = tk.StringVar(value="1")
        self.radar_target_snr_var = tk.StringVar(value="20")
        self.radar_derived_var = tk.StringVar(value="")
        self.radar_status_var = tk.StringVar(value="就绪")

        self.radar_candidate_label_var = tk.StringVar(value="")
        self.radar_candidate_measured_range_var = tk.StringVar(value="")
        self.radar_candidate_notes_var = tk.StringVar(value="")
        self.radar_candidate_quality_var = tk.StringVar(value="候选质量：等待雷达帧")
        self._best_radar_candidate_score = 0.0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.drain_output)
        self.after(33, self.poll_radar_controller)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.config_tab = ttk.Frame(self.notebook, padding=14)
        self.stream_tab = ttk.Frame(self.notebook, padding=14)
        self.radar_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.config_tab, text="系统配置")
        self.notebook.add(self.stream_tab, text="视频传输")
        self.notebook.add(self.radar_tab, text="FMCW雷达")

        self.config_tab.columnconfigure(0, weight=1)
        self.stream_tab.columnconfigure(0, weight=1)
        self.stream_tab.rowconfigure(2, weight=2)
        self.radar_tab.columnconfigure(0, weight=1)
        self.radar_tab.rowconfigure(1, weight=1)

        file_frame = ttk.LabelFrame(self.config_tab, text="输入")
        file_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        file_frame.columnconfigure(1, weight=1)
        ttk.Label(file_frame, text="视频文件").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.input_var).grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        ttk.Button(file_frame, text="浏览", command=self.browse_input).grid(row=0, column=2, padx=8, pady=8)
        ttk.Label(file_frame, text="工作目录").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.work_dir_var).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ttk.Button(file_frame, text="浏览", command=self.browse_work_dir).grid(row=1, column=2, padx=8, pady=8)

        preset_frame = ttk.LabelFrame(self.config_tab, text="传输配置")
        preset_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        preset_frame.columnconfigure(1, weight=1)
        ttk.Label(preset_frame, text="预设").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        preset_box = ttk.Combobox(
            preset_frame,
            textvariable=self.preset_var,
            values=[preset.label for preset in PRESETS],
            state="readonly",
        )
        preset_box.grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        preset_box.bind("<<ComboboxSelected>>", lambda _event: self.update_preset_description())
        self.preset_description = ttk.Label(preset_frame, text=PRESETS[0].description)
        self.preset_description.grid(row=1, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w")

        ttk.Label(preset_frame, text="附加参数").grid(row=2, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(preset_frame, textvariable=self.extra_args_var).grid(row=2, column=1, padx=8, pady=8, sticky="ew")

        ttk.Checkbutton(preset_frame, text="打开发送端预览", variable=self.source_preview_var).grid(
            row=3, column=0, padx=8, pady=8, sticky="w"
        )
        preview_options = ttk.Frame(preset_frame)
        preview_options.grid(row=3, column=1, padx=8, pady=8, sticky="w")
        ttk.Label(preview_options, text="发送预览延迟").pack(side=tk.LEFT)
        ttk.Spinbox(
            preview_options,
            from_=0.0,
            to=20.0,
            increment=0.5,
            width=6,
            textvariable=self.source_preview_delay_var,
        ).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(preview_options, text="秒，用于和接收播放对齐").pack(side=tk.LEFT)

        display_options = ttk.Frame(preset_frame)
        display_options.grid(row=4, column=1, padx=8, pady=(0, 8), sticky="w")
        ttk.Label(preset_frame, text="对照窗口尺寸").grid(row=4, column=0, padx=8, pady=(0, 8), sticky="w")
        ttk.Spinbox(display_options, from_=240, to=1920, increment=20, width=6, textvariable=self.player_width_var).pack(
            side=tk.LEFT
        )
        ttk.Label(display_options, text=" × ").pack(side=tk.LEFT)
        ttk.Spinbox(display_options, from_=180, to=1080, increment=20, width=6, textvariable=self.player_height_var).pack(
            side=tk.LEFT
        )
        ttk.Label(display_options, text="发送预览与接收播放使用同一显示尺寸").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            preset_frame,
            text="自动按发送分辨率设置尺寸",
            variable=self.auto_player_size_var,
        ).grid(row=5, column=1, padx=8, pady=(0, 8), sticky="w")

        radar_config = ttk.LabelFrame(self.config_tab, text="FMCW雷达配置")
        radar_config.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        radar_config.columnconfigure(1, weight=1)
        radar_config.columnconfigure(3, weight=1)
        fields = (
            ("数据源", self.radar_source_var, ("仿真", "IQ回放", "E310")),
            ("载频 (GHz)", self.radar_carrier_ghz_var, None),
            ("采样率 (MSPS)", self.radar_sample_rate_msps_var, None),
            ("带宽 (MHz)", self.radar_bandwidth_mhz_var, None),
            ("有效时间 (μs)", self.radar_active_us_var, None),
            ("空闲时间 (μs)", self.radar_idle_us_var, None),
            ("Chirp数", self.radar_chirp_count_var, None),
            ("CFAR阈值 (dB)", self.radar_cfar_db_var, None),
            ("显示距离 (m)", self.radar_display_range_var, None),
            ("TX增益 (dB)", self.radar_tx_gain_var, None),
            ("RX增益 (dB)", self.radar_rx_gain_var, None),
            ("TX数字幅度", self.radar_tx_amplitude_var, None),
            ("TX/RX通道", None, None),
        )
        for index, (label, variable, choices) in enumerate(fields):
            row, pair = divmod(index, 2)
            column = pair * 2
            ttk.Label(radar_config, text=label).grid(row=row, column=column, padx=8, pady=4, sticky="w")
            if choices:
                widget = ttk.Combobox(radar_config, textvariable=variable, values=choices, state="readonly")
            elif variable is not None:
                widget = ttk.Entry(radar_config, textvariable=variable)
            else:
                widget = ttk.Frame(radar_config)
                ttk.Entry(widget, textvariable=self.radar_tx_channel_var, width=5).pack(side=tk.LEFT)
                ttk.Label(widget, text=" / ").pack(side=tk.LEFT)
                ttk.Entry(widget, textvariable=self.radar_rx_channel_var, width=5).pack(side=tk.LEFT)
            widget.grid(row=row, column=column + 1, padx=8, pady=4, sticky="ew")
            widget.bind("<FocusOut>", lambda _event: self.update_radar_derived())

        replay_row = (len(fields) + 1) // 2
        ttk.Label(radar_config, text="IQ回放文件").grid(row=replay_row, column=0, padx=8, pady=4, sticky="w")
        ttk.Entry(radar_config, textvariable=self.radar_replay_path_var).grid(
            row=replay_row, column=1, columnspan=2, padx=8, pady=4, sticky="ew"
        )
        ttk.Button(radar_config, text="浏览", command=self.browse_radar_replay).grid(
            row=replay_row, column=3, padx=8, pady=4
        )
        target_row = ttk.Frame(radar_config)
        target_row.grid(row=replay_row + 1, column=1, columnspan=3, padx=8, pady=4, sticky="w")
        ttk.Label(radar_config, text="仿真目标").grid(row=replay_row + 1, column=0, padx=8, pady=4, sticky="w")
        for label, variable in (
            ("距离m", self.radar_target_range_var),
            ("速度m/s", self.radar_target_velocity_var),
            ("SNR dB", self.radar_target_snr_var),
        ):
            ttk.Label(target_row, text=label).pack(side=tk.LEFT, padx=(0, 3))
            ttk.Entry(target_row, textvariable=variable, width=8).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(radar_config, textvariable=self.radar_derived_var).grid(
            row=replay_row + 2, column=0, columnspan=4, padx=8, pady=(5, 8), sticky="w"
        )
        self.update_radar_derived()

        hint = ttk.Label(
            self.config_tab,
            text="配置完成后切换到“视频传输”页，点击开始即可运行 SDR RF 回环视频演示。",
        )
        hint.grid(row=3, column=0, sticky="w", pady=(4, 0))

        controls = ttk.Frame(self.stream_tab)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="开始", command=self.start_stream)
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop_stream, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.RIGHT)

        metrics = ttk.LabelFrame(self.stream_tab, text="实时状态")
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for col in range(8):
            metrics.columnconfigure(col, weight=1)
        self._metric(metrics, 0, "块序号", self.chunk_var)
        self._metric(metrics, 1, "吞吐", self.goodput_var)
        self._metric(metrics, 2, "成功/失败", self.ok_var)
        self._metric(metrics, 3, "重建", self.context_var)
        self._metric(metrics, 4, "接收耗时", self.elapsed_var)
        self._metric(metrics, 5, "发送预览", self.source_status_var)
        self._metric(metrics, 6, "接收播放", self.receiver_status_var)
        self._metric(metrics, 7, "对照延迟", self.delay_status_var)

        video_frame = ttk.LabelFrame(self.stream_tab, text="发送预览与链路质量")
        video_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        video_frame.columnconfigure(0, weight=0)
        video_frame.columnconfigure(1, weight=1)
        video_frame.rowconfigure(0, weight=0)
        video_frame.rowconfigure(1, weight=2)
        self.source_pane = VideoPane(video_frame, "发送视频")
        self.source_pane.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.link_stats = LinkStatsPane(video_frame)
        self.link_stats.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(8, 0))

        log_frame = ttk.LabelFrame(video_frame, text="运行日志")
        log_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(10, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap=tk.NONE, height=10, font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=yscroll.set)

        radar_controls = ttk.Frame(self.radar_tab)
        radar_controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.radar_start_button = ttk.Button(radar_controls, text="启动雷达", command=self.start_radar)
        self.radar_start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.radar_stop_button = ttk.Button(
            radar_controls, text="停止雷达", command=self.stop_radar, state=tk.DISABLED
        )
        self.radar_stop_button.pack(side=tk.LEFT, padx=(0, 8))
        self.radar_calibrate_button = ttk.Button(
            radar_controls,
            text="采集空场背景",
            command=self.calibrate_radar_background,
            state=tk.DISABLED,
        )
        self.radar_calibrate_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(radar_controls, text="保存当前帧", command=self.save_radar_frame).pack(side=tk.LEFT)
        ttk.Label(radar_controls, text="单RX：方位角未测量", foreground="#a55d00").pack(side=tk.RIGHT)
        ttk.Label(radar_controls, textvariable=self.radar_status_var).pack(side=tk.RIGHT, padx=12)
        candidate_controls = ttk.Frame(self.radar_tab)
        candidate_controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(candidate_controls, text="位置标签").pack(side=tk.LEFT)
        ttk.Entry(candidate_controls, textvariable=self.radar_candidate_label_var, width=16).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(candidate_controls, text="实测距离 m").pack(side=tk.LEFT)
        ttk.Entry(candidate_controls, textvariable=self.radar_candidate_measured_range_var, width=8).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(candidate_controls, text="角反朝向/备注").pack(side=tk.LEFT)
        ttk.Entry(candidate_controls, textvariable=self.radar_candidate_notes_var, width=22).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(candidate_controls, text="保存位置候选", command=self.save_position_candidate).pack(side=tk.LEFT)
        ttk.Label(candidate_controls, textvariable=self.radar_candidate_quality_var).pack(side=tk.RIGHT)
        self.radar_dashboard = RadarDashboard(self.radar_tab)
        self.radar_dashboard.grid(row=2, column=0, sticky="nsew")

    @staticmethod
    def _metric(parent: ttk.Frame, column: int, label: str, variable: tk.StringVar) -> None:
        cell = ttk.Frame(parent, padding=8)
        cell.grid(row=0, column=column, sticky="ew")
        ttk.Label(cell, text=label).pack(anchor="w")
        ttk.Label(cell, textvariable=variable, font=("Segoe UI", 11, "bold")).pack(anchor="w")

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(PROJECT_DIR),
            filetypes=[("视频文件", "*.mp4 *.ts *.mov *.mkv *.avi"), ("所有文件", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def browse_work_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=str(PROJECT_DIR / "artifacts"))
        if path:
            self.work_dir_var.set(path)

    def browse_radar_replay(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(PROJECT_DIR / "artifacts"),
            filetypes=[("雷达捕获", "*.npz"), ("所有文件", "*.*")],
        )
        if path:
            self.radar_replay_path_var.set(path)

    def build_radar_config(self) -> RadarConfig:
        for label, variable in (
            ("TX通道", self.radar_tx_channel_var),
            ("RX通道", self.radar_rx_channel_var),
        ):
            channel = int(variable.get())
            if channel not in (0, 1):
                raise ValueError(f"{label}必须是0或1")
        return radar_config_from_values(
            dict(
                carrier_ghz=self.radar_carrier_ghz_var.get(),
                sample_rate_msps=self.radar_sample_rate_msps_var.get(),
                bandwidth_mhz=self.radar_bandwidth_mhz_var.get(),
                active_us=self.radar_active_us_var.get(),
                idle_us=self.radar_idle_us_var.get(),
                chirp_count=self.radar_chirp_count_var.get(),
                cfar_threshold_db=self.radar_cfar_db_var.get(),
                max_display_range_m=self.radar_display_range_var.get(),
            )
        )

    def update_radar_derived(self) -> None:
        try:
            self.radar_derived_var.set(format_derived_config(self.build_radar_config()))
        except (TypeError, ValueError):
            self.radar_derived_var.set("参数待修正")

    def start_radar(self) -> None:
        if self.radar_controller is not None:
            return
        try:
            source_mode = self.radar_source_var.get()
            if source_mode == "IQ回放":
                replay_path = Path(self.radar_replay_path_var.get())
                controller, effective_config = create_radar_runtime(
                    source_mode, None, replay_path, None
                )
            elif source_mode == "E310":
                config = self.build_radar_config()
                radio_config = E310RadioConfig(
                    tx_channel=int(self.radar_tx_channel_var.get()),
                    rx_channel=int(self.radar_rx_channel_var.get()),
                    tx_gain_db=float(self.radar_tx_gain_var.get()),
                    rx_gain_db=float(self.radar_rx_gain_var.get()),
                    tx_amplitude=float(self.radar_tx_amplitude_var.get()),
                )
                controller, effective_config = create_radar_runtime(
                    source_mode, config, None, None, radio_config
                )
            else:
                config = self.build_radar_config()
                _, _, target_range, target_velocity, target_snr = validate_runtime_inputs(
                    self.radar_tx_gain_var.get(),
                    self.radar_rx_gain_var.get(),
                    self.radar_target_range_var.get(),
                    self.radar_target_velocity_var.get(),
                    self.radar_target_snr_var.get(),
                )
                target = SyntheticTarget(
                    target_id="T1",
                    range_m=target_range,
                    radial_velocity_mps=target_velocity,
                    snr_db=target_snr,
                )
                controller, effective_config = create_radar_runtime(
                    source_mode, config, None, target
                )
            controller.start()
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            messagebox.showerror("雷达参数错误", str(error))
            return
        self.radar_controller = controller
        self.radar_source_label = source_mode
        self._latest_radar_frame = None
        self._last_radar_frame_index = None
        self.radar_status_var.set("运行中")
        self.radar_start_button.configure(state=tk.DISABLED)
        self.radar_stop_button.configure(state=tk.NORMAL)
        calibrate_button = getattr(self, "radar_calibrate_button", None)
        if calibrate_button is not None:
            calibrate_button.configure(
                state=tk.NORMAL if source_mode == "E310" else tk.DISABLED
            )
        self.notebook.select(self.radar_tab)
        prefix = "回放配置 | " if source_mode == "IQ回放" else ""
        self.radar_derived_var.set(prefix + format_derived_config(effective_config))

    def stop_radar(self) -> None:
        controller = self.radar_controller
        if controller is None:
            return
        controller.stop()
        status = controller.status()
        self.radar_stop_button.configure(state=tk.DISABLED)
        calibrate_button = getattr(self, "radar_calibrate_button", None)
        if calibrate_button is not None:
            calibrate_button.configure(state=tk.DISABLED)
        if status.running or not status.cleanup_complete:
            self.radar_status_var.set(
                "停止中：等待当前E310采集结束并完成射频清理"
                if status.running
                else f"清理失败：{status.error or status.shutdown_error or '请重试停止'}"
            )
            self.radar_start_button.configure(state=tk.DISABLED)
            self.radar_stop_button.configure(
                state=tk.DISABLED if status.running else tk.NORMAL
            )
            return
        self.radar_controller = None
        self.radar_status_var.set("已停止")
        self.radar_start_button.configure(state=tk.NORMAL)

    def calibrate_radar_background(self) -> None:
        """Collect and apply an in-session empty-room background on the worker."""
        controller = self.radar_controller
        if (
            controller is None
            or self.radar_source_label != "E310"
            or not controller.status().running
        ):
            messagebox.showinfo("空场背景", "请先以 E310 数据源启动雷达")
            return
        controller.processor.begin_background_calibration(cpi_count=16)
        self._radar_calibration_deadline = time.monotonic() + 10.0
        self.radar_calibrate_button.configure(state=tk.DISABLED)
        self.radar_status_var.set("空场标定中：0/16，请保持场景静止且不要放置角反")

    def render(self, frame: object) -> None:
        self._latest_radar_frame = frame
        self.radar_dashboard.render(frame)
        candidate = assess_frame(frame)
        best = candidate.quality_score > self._best_radar_candidate_score
        if best:
            self._best_radar_candidate_score = candidate.quality_score
        marker = "；本次最佳" if best and candidate.quality_score > 0.0 else ""
        self.radar_candidate_quality_var.set(
            f"候选质量：{candidate.quality_score:.1f}/100；当前最佳："
            f"{self._best_radar_candidate_score:.1f}/100{marker}"
        )

    def poll_radar_controller(self) -> None:
        controller = self.radar_controller
        if controller is not None:
            self._last_radar_frame_index, status = poll_radar(
                controller,
                self,
                self._last_radar_frame_index,
                self.radar_source_label,
            )
            if status.running:
                processor = getattr(controller, "processor", None)
                calibration_status_method = getattr(
                    processor,
                    "background_calibration_status",
                    None,
                )
                calibration_status = (
                    calibration_status_method()
                    if calibration_status_method is not None
                    else None
                )
                deadline = getattr(self, "_radar_calibration_deadline", None)
                if (
                    calibration_status is not None
                    and calibration_status.active
                    and deadline is not None
                    and time.monotonic() >= deadline
                ):
                    processor.cancel_background_calibration(
                        "10秒内未收满16个已同步空场CPI"
                    )
                    calibration_status = calibration_status_method()
                    self._radar_calibration_deadline = None
                if status.shutdown_error:
                    self.radar_status_var.set(
                        f"停止中：等待硬件清理（{status.shutdown_error}）"
                    )
                    self.radar_start_button.configure(state=tk.DISABLED)
                    self.radar_stop_button.configure(state=tk.DISABLED)
                elif calibration_status is not None and calibration_status.error:
                    detail = f"空场标定未应用：{calibration_status.error}"
                    if status.error:
                        detail += f"；当前等待同步：{status.error}"
                    self.radar_status_var.set(detail)
                    self._radar_calibration_deadline = None
                    self.radar_start_button.configure(state=tk.DISABLED)
                    self.radar_stop_button.configure(state=tk.NORMAL)
                    calibrate_button = getattr(self, "radar_calibrate_button", None)
                    if calibrate_button is not None:
                        calibrate_button.configure(state=tk.NORMAL)
                elif status.error:
                    if calibration_status is not None and calibration_status.active:
                        self.radar_status_var.set(
                            "空场标定等待同步："
                            f"{calibration_status.collected_cpis}/"
                            f"{calibration_status.required_cpis}（{status.error}）"
                        )
                    else:
                        self.radar_status_var.set(f"等待同步：{status.error}")
                    self.radar_start_button.configure(state=tk.DISABLED)
                    self.radar_stop_button.configure(state=tk.NORMAL)
                    calibrate_button = getattr(self, "radar_calibrate_button", None)
                    if calibrate_button is not None:
                        # A queued calibration only accepts successfully synchronized
                        # CPIs in FmcwProcessor.  Keep the action available while the
                        # radio is recovering, instead of forcing an E310 restart just
                        # to arm the next stable capture sequence.
                        calibrate_button.configure(state=tk.NORMAL)
                else:
                    if calibration_status is not None:
                        calibrate_button = getattr(
                            self, "radar_calibrate_button", None
                        )
                        if calibration_status.active:
                            self.radar_status_var.set(
                                "空场标定中："
                                f"{calibration_status.collected_cpis}/"
                                f"{calibration_status.required_cpis}，请保持场景静止"
                            )
                            if calibrate_button is not None:
                                calibrate_button.configure(state=tk.DISABLED)
                        elif calibration_status.ready:
                            self.radar_status_var.set("运行中：空场背景已应用")
                            self._radar_calibration_deadline = None
                            if calibrate_button is not None:
                                calibrate_button.configure(state=tk.NORMAL)
            elif status.cleanup_complete:
                detail = status.error or status.shutdown_error
                self.radar_status_var.set(
                    f"已停止：{detail}" if detail else "已完成"
                )
                self.radar_controller = None
                self.radar_start_button.configure(state=tk.NORMAL)
                self.radar_stop_button.configure(state=tk.DISABLED)
                calibrate_button = getattr(self, "radar_calibrate_button", None)
                if calibrate_button is not None:
                    calibrate_button.configure(state=tk.DISABLED)
            else:
                self.radar_status_var.set(
                    f"清理失败：{status.error or status.shutdown_error or '请点击停止重试'}"
                )
                self.radar_start_button.configure(state=tk.DISABLED)
                self.radar_stop_button.configure(state=tk.NORMAL)
                calibrate_button = getattr(self, "radar_calibrate_button", None)
                if calibrate_button is not None:
                    calibrate_button.configure(state=tk.DISABLED)
        self.after(33, self.poll_radar_controller)

    def save_radar_frame(self) -> None:
        frame = self._latest_radar_frame
        if frame is None:
            messagebox.showinfo("保存雷达帧", "当前没有可保存的雷达帧")
            return
        directory = filedialog.askdirectory(initialdir=str(PROJECT_DIR / "artifacts"))
        if not directory:
            return
        output = Path(directory) / f"radar_frame_{frame.frame_index:06d}"
        try:
            save_frame(output, frame)
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror("保存失败", str(error))
            return
        self.radar_status_var.set(f"已保存: {output}")

    def save_position_candidate(self) -> None:
        frame = self._latest_radar_frame
        if frame is None:
            messagebox.showinfo("位置候选", "当前没有可保存的雷达帧")
            return
        range_text = self.radar_candidate_measured_range_var.get().strip()
        try:
            measured_range = float(range_text) if range_text else None
            candidate = assess_frame(
                frame,
                label=self.radar_candidate_label_var.get(),
                measured_range_m=measured_range,
                notes=self.radar_candidate_notes_var.get(),
            )
            output = PROJECT_DIR / "artifacts" / "hardware" / "position_candidates.jsonl"
            append_position_candidate(output, candidate)
            save_frame(
                PROJECT_DIR / "artifacts" / "hardware" / "position_candidates"
                / f"candidate_{frame.frame_index:06d}",
                frame,
            )
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror("保存位置候选失败", str(error))
            return
        self.radar_status_var.set(
            f"已保存位置候选：质量 {candidate.quality_score:.1f}/100，{output}"
        )

    def selected_preset(self) -> Preset:
        label = self.preset_var.get()
        return next((preset for preset in PRESETS if preset.label == label), PRESETS[0])

    def update_preset_description(self) -> None:
        self.preset_description.configure(text=self.selected_preset().description)

    def current_stream_args(self) -> list[str]:
        args = list(self.selected_preset().args)
        extra = self.extra_args_var.get().strip()
        if extra:
            args.extend(shlex.split(extra))
        return args

    def build_command(self) -> list[str]:
        output_file = self.receiver_ts_path()
        stream_args = self.current_stream_args()
        no_player = "--no-player" in stream_args
        command = [
            sys.executable,
            str(STREAM_SCRIPT),
            "--input-file",
            self.input_var.get(),
            "--work-dir",
            self.work_dir_var.get(),
            "--output-file",
            str(output_file),
        ]
        command.extend(stream_args)
        if not no_player:
            player_width, player_height = self.player_size(Path(self.input_var.get()), stream_args)
            positions = self.player_positions(player_width)
            command.extend(
                [
                    "--player-title",
                    "接收视频",
                    "--player-width",
                    str(player_width),
                    "--player-height",
                    str(player_height),
                    "--player-left",
                    str(positions["receiver_left"]),
                    "--player-top",
                    str(positions["receiver_top"]),
                ]
            )
        return command

    def player_size(self, input_path: Path | None = None, stream_args: list[str] | None = None) -> tuple[int, int]:
        if self.auto_player_size_var.get():
            width, height = self.auto_video_size(input_path, stream_args or [])
            if width > 0 and height > 0:
                self.player_width_var.set(width)
                self.player_height_var.set(height)
                return width, height
        width = max(240, int(self.player_width_var.get()))
        height = max(180, int(self.player_height_var.get()))
        return width, height

    def auto_video_size(self, input_path: Path | None, stream_args: list[str]) -> tuple[int, int]:
        scale_height = self.scale_height_from_args(stream_args)
        source_width, source_height = self.source_video_size(input_path)
        if source_width <= 0 or source_height <= 0:
            source_width, source_height = 16, 9
        if scale_height <= 0:
            return self.even_dimension(source_width), source_height
        width = self.even_dimension(round(source_width * scale_height / source_height))
        return max(240, width), max(180, scale_height)

    @staticmethod
    def scale_height_from_args(args: list[str]) -> int:
        scale_height = 360
        for index, value in enumerate(args):
            if value == "--scale-height" and index + 1 < len(args):
                try:
                    scale_height = int(args[index + 1])
                except ValueError:
                    pass
        return scale_height

    @staticmethod
    def even_dimension(value: int) -> int:
        value = max(2, int(value))
        return value if value % 2 == 0 else value + 1

    @functools.lru_cache(maxsize=32)
    def source_video_size(self, input_path: Path | None) -> tuple[int, int]:
        if input_path is None or not input_path.exists():
            return 0, 0
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(input_path),
        ]
        try:
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
        except Exception:
            return 0, 0
        text = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if "x" not in text:
            return 0, 0
        width_text, height_text = text.split("x", 1)
        try:
            return int(width_text), int(height_text)
        except ValueError:
            return 0, 0

    def player_positions(self, player_width: int | None = None) -> dict[str, int]:
        self.update_idletasks()
        width = (
            player_width
            if player_width is not None
            else self.player_size(Path(self.input_var.get()), self.current_stream_args())[0]
        )
        left = max(0, self.winfo_rootx() + self.winfo_width() - width - 40)
        top = max(0, self.winfo_rooty() + 120)
        return {"receiver_left": left, "receiver_top": top}

    def receiver_ts_path(self) -> Path:
        return Path(self.work_dir_var.get()) / "gui_recovered.ts"

    def start_stream(self) -> None:
        if self.process is not None:
            return
        input_path = Path(self.input_var.get())
        if not input_path.exists():
            messagebox.showerror("文件不存在", f"视频文件不存在:\n{input_path}")
            return

        self.update_idletasks()
        self.notebook.select(self.stream_tab)
        command = self.build_command()
        self.log_text.delete("1.0", tk.END)
        self.append_log("command=" + " ".join(command))
        self.status_var.set("启动中")
        self.chunk_var.set("-")
        self.goodput_var.set("-")
        self.ok_var.set("-")
        self.context_var.set("-")
        self.elapsed_var.set("-")
        self.link_stats.reset()
        preview_delay = self.source_preview_delay()
        self.source_pane.show_placeholder(
            f"发送预览将在 {preview_delay:.1f}s 后启动" if self.source_preview_var.get() else "发送预览关闭"
        )
        self.source_status_var.set("关闭" if not self.source_preview_var.get() else f"延迟 {preview_delay:.1f}s")
        self.receiver_status_var.set("外部播放器")
        self.delay_status_var.set("RF链路延迟")
        self.profile_parts = {}
        self.link_stats.set_value("input", str(input_path))
        self.link_stats.set_value("output", str(self.receiver_ts_path()))
        player_width, player_height = self.player_size(Path(self.input_var.get()), self.current_stream_args())
        self.source_pane.set_display_size(player_width, player_height)
        auto_size_note = "自动" if self.auto_player_size_var.get() else "手动"
        self.link_stats.set_value("receiver", f"外部 ffplay 低延迟播放，窗口 {player_width}x{player_height}")
        self.link_stats.set_value(
            "delay",
            f"发送预览延迟 {preview_delay:.1f}s；接收视频经过编码、RF、解包和播放器缓冲；窗口尺寸{auto_size_note}配置",
        )

        self.receiver_output_path = self.receiver_ts_path()
        self.receiver_output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.receiver_output_path.unlink()
        except FileNotFoundError:
            pass

        if self.source_preview_var.get():
            self.schedule_source_preview(input_path, preview_delay)

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = subprocess.Popen(
            command,
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("运行中")
        self.reader_thread = threading.Thread(target=self.read_process_output, daemon=True)
        self.reader_thread.start()

    def stop_stream(self) -> None:
        self.cancel_source_preview_schedule()
        self.stop_source_preview()
        if self.process is None:
            return
        self.status_var.set("停止中")
        try:
            if os.name == "nt":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.terminate()
        except Exception:
            try:
                self.process.terminate()
            except Exception:
                pass
        self.after(1500, self.kill_if_running)

    def kill_if_running(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()

    def read_process_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output_queue.put(line.rstrip("\n"))
        return_code = self.process.wait()
        self.output_queue.put(f"process_exit_code={return_code}")
        self.output_queue.put("__PROCESS_DONE__")

    def drain_output(self) -> None:
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "source_frame":
                    self.source_pane.show_image(item[1])
                    self.source_status_var.set("预览中")
                    continue
                line = str(item)
                if line == "__PROCESS_DONE__":
                    self.on_process_done()
                    continue
                self.append_log(line)
                self.update_metrics(line)
        except queue.Empty:
            pass
        self.after(100, self.drain_output)

    def append_log(self, line: str) -> None:
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)

    def update_metrics(self, line: str) -> None:
        if "=" not in line:
            return
        key, value = line.split("=", 1)
        if key == "stream_chunk":
            self.chunk_var.set(value)
        elif key == "stream_goodput_bps":
            number = self.parse_metric_number(value)
            if number is not None:
                goodput = f"{int(number) // 1000} kbps"
                self.goodput_var.set(goodput)
                self.link_stats.set_value("goodput", goodput)
            else:
                self.goodput_var.set(value)
        elif key == "stream_chunks_ok":
            failed = self.ok_var.get().split("/")[-1] if "/" in self.ok_var.get() else "0"
            self.ok_var.set(f"{value}/{failed}")
            self.link_stats.set_value("chunks", self.ok_var.get())
        elif key == "stream_chunks_failed":
            ok = self.ok_var.get().split("/")[0] if "/" in self.ok_var.get() else "0"
            self.ok_var.set(f"{ok}/{value}")
            self.link_stats.set_value("chunks", self.ok_var.get())
        elif key == "stream_context_recreates":
            self.context_var.set(value)
            self.link_stats.set_value("context_recreates", value)
        elif key == "stream_elapsed_sec":
            number = self.parse_metric_number(value)
            if number is not None:
                self.elapsed_var.set(f"{number:.1f}s")
                self.link_stats.set_value("stream_elapsed", f"{number:.1f} s")
        elif key == "player_command":
            self.receiver_status_var.set("已启动")
            self.link_stats.set_value("receiver", "外部 ffplay 已启动")
        elif key == "source_preview_started":
            self.source_status_var.set("已启动")
        elif key == "stream_output_bytes":
            self.receiver_status_var.set("已接收 TS 数据")
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("output_bytes", f"{int(number) // 1024} KB")
        elif key == "packets_decoded":
            self.receiver_status_var.set(f"已解包 {value}")
            self.link_stats.set_value("packets_decoded", value)
        elif key == "source_preview":
            self.link_stats.set_value("profile", f"发送预览={value}")
        elif key == "source_preview_filter":
            self.set_profile_part("滤镜", value.replace("-vf ", ""))
        elif key == "video_bitrate":
            self.set_profile_part("码率", value)
        elif key == "scale_height":
            self.set_profile_part("高度", f"{value}p")
        elif key == "fps":
            self.set_profile_part("帧率", f"{value} fps")
        elif key == "encoder_preset":
            self.set_profile_part("编码", value)
        elif key == "player_buffered":
            self.set_profile_part("播放缓冲", "开启" if value.lower() == "true" else "低延迟")
            self.link_stats.set_value("player_buffered", "开启" if value.lower() == "true" else "低延迟")
        elif key == "output_file":
            self.link_stats.set_value("output", value)
        elif key == "chunk_bytes_effective":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("chunk_effective", f"{int(number) // 1024} KB")
        elif key == "ts_packet_aligned":
            self.link_stats.set_value("ts_aligned", "是" if value.lower() == "true" else "否")
        elif key == "raw_bitrate_bps":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("raw_bitrate", f"{int(number) / 1_000_000:.2f} Mbps")
        elif key == "payload_bitrate_est_bps":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("payload_bitrate", f"{int(number) / 1_000_000:.2f} Mbps")
        elif key == "stream_chunk_bytes":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("chunk_bytes", f"{int(number) // 1024} KB")
        elif key == "lo_hz":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("lo_hz", f"{int(number) / 1_000_000:.2f} MHz")
        elif key == "sample_rate":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("sample_rate", f"{int(number) / 1_000_000:.2f} MSPS")
        elif key == "symbol_rate":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("symbol_rate", f"{int(number) / 1_000_000:.2f} Msym/s")
        elif key == "bandwidth":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("rf_bandwidth", f"{int(number) / 1_000_000:.2f} MHz")
        elif key.startswith("tx_hardwaregain"):
            self.link_stats.set_value("tx_gain", value)
        elif key.startswith("rx_hardwaregain"):
            self.link_stats.set_value("rx_gain", value)
        elif key == "rx_samples":
            self.link_stats.set_value("rx_samples", value)
        elif key == "rx_rms_dbfs":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("rx_rms", f"{number:.2f} dBFS")
        elif key == "rx_peak_dbfs":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("rx_peak", f"{number:.2f} dBFS")
        elif key == "rx_clip_ratio":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("rx_clip", f"{number * 100:.3f}%")
        elif key == "chunk_elapsed_sec":
            number = self.parse_metric_number(value)
            if number is not None:
                self.link_stats.set_value("chunk_elapsed", f"{number:.3f} s")
        elif key == "capture_attempt":
            self.link_stats.set_value("capture_attempts", value)
        elif key == "chunks_ok_in_chunk":
            self.link_stats.set_value("chunks_in_chunk", value)
        elif key == "missing_chunks":
            self.link_stats.set_value("missing_chunks", value)
        elif key == "low_latency_stream_ok":
            self.status_var.set("完成" if value.lower() == "true" else "失败")
            self.link_stats.set_value("stream_ok", "成功" if value.lower() == "true" else "失败")

    def set_profile_part(self, key: str, value: str) -> None:
        self.profile_parts[key] = value
        self.link_stats.set_value("profile", "，".join(f"{k}:{v}" for k, v in self.profile_parts.items()))

    @staticmethod
    def parse_metric_number(value: str) -> float | None:
        first = value.strip().split(maxsplit=1)[0] if value.strip() else ""
        try:
            return float(first)
        except ValueError:
            return None

    def source_preview_delay(self) -> float:
        try:
            return max(0.0, float(self.source_preview_delay_var.get()))
        except (tk.TclError, ValueError):
            return 0.0

    def schedule_source_preview(self, input_path: Path, delay_sec: float) -> None:
        self.cancel_source_preview_schedule()
        delay_ms = int(max(0.0, delay_sec) * 1000)
        self.preview_after_id = self.after(delay_ms, lambda: self.start_source_preview_if_running(input_path))

    def cancel_source_preview_schedule(self) -> None:
        if self.preview_after_id is None:
            return
        try:
            self.after_cancel(self.preview_after_id)
        except Exception:
            pass
        self.preview_after_id = None

    def start_source_preview_if_running(self, input_path: Path) -> None:
        self.preview_after_id = None
        if self.process is None:
            return
        self.source_status_var.set("启动中")
        self.start_source_preview(input_path)

    def start_source_preview(self, input_path: Path) -> None:
        self.stop_source_preview()
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-i",
            str(input_path),
            "-vf",
            "scale=-2:360,fps=12",
            "-an",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            self.preview_process = subprocess.Popen(
                command,
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            self.source_status_var.set("ffmpeg 未找到")
            self.source_pane.show_placeholder("未找到 ffmpeg")
            return
        self.preview_thread = threading.Thread(target=self.read_source_preview, daemon=True)
        self.preview_thread.start()

    def read_source_preview(self) -> None:
        process = self.preview_process
        if process is None or process.stdout is None:
            return
        self.read_mjpeg_frames(process, "source_frame")

    def read_mjpeg_frames(self, process: subprocess.Popen, queue_key: str) -> None:
        buffer = bytearray()
        while process.poll() is None:
            if process.stdout is None:
                return
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", start + 2)
                if start < 0 or end < 0:
                    if len(buffer) > 2_000_000:
                        del buffer[:-2]
                    break
                frame = bytes(buffer[start : end + 2])
                del buffer[: end + 2]
                try:
                    image = Image.open(io.BytesIO(frame)).convert("RGB")
                except Exception:
                    continue
                self.output_queue.put((queue_key, image.copy()))

    def stop_source_preview(self) -> None:
        process = self.preview_process
        self.preview_process = None
        if process is None:
            return
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def on_process_done(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        if self.process is not None and self.process.returncode == 0:
            self.status_var.set("已完成")
        elif self.status_var.get() not in ("完成", "失败"):
            self.status_var.set("已停止")
        self.process = None
        self.reader_thread = None
        self.cancel_source_preview_schedule()
        self.stop_source_preview()

    def on_close(self) -> None:
        self.cancel_source_preview_schedule()
        self.stop_source_preview()
        process = self.process
        self.process = None
        controller = self.radar_controller
        shutdown_video_process(process)
        if controller is not None:
            status = controller.status()
            if status.cleanup_complete:
                self.radar_controller = None
                self.destroy()
                return
            worker = getattr(self, "_radar_shutdown_worker", None)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=controller.stop,
                    name="fmcw-gui-shutdown",
                    daemon=True,
                )
                self._radar_shutdown_worker = worker
                worker.start()
            self.radar_status_var.set(
                "正在退出：等待E310采集结束并完成射频清理"
            )
            self.after(100, lambda: self._poll_radar_shutdown())
            return
        self.destroy()

    def _poll_radar_shutdown(self) -> None:
        controller = self.radar_controller
        if controller is None:
            self.destroy()
            return
        status = controller.status()
        if status.cleanup_complete:
            self.radar_controller = None
            self.destroy()
            return
        worker = getattr(self, "_radar_shutdown_worker", None)
        if status.running or (worker is not None and worker.is_alive()):
            self.after(100, self._poll_radar_shutdown)
            return
        self._radar_shutdown_worker = None
        self.radar_status_var.set(
            f"E310清理失败，窗口保持打开；请再次关闭重试：{status.error or status.shutdown_error or '未知错误'}"
        )


def main() -> int:
    app = SdrVideoGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    if os.environ.get("FMCW_GUI_IMPORT_ONLY") == "1":
        raise SystemExit(0)
    raise SystemExit(main())
