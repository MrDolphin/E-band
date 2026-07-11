from __future__ import annotations

import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
STREAM_SCRIPT = SCRIPT_DIR / "run_low_latency_ts_stream.py"


@dataclass(frozen=True)
class Preset:
    label: str
    description: str
    args: tuple[str, ...]


PRESETS: tuple[Preset, ...] = (
    Preset("稳定演示", "250k, 360p, 12fps, veryfast, 接收端缓冲播放", ()),
    Preset("快速测试", "稳定演示参数，但 20 个 chunk 后停止", ("--max-chunks", "20")),
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


class SdrVideoGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ANTSDR E310 实时视频回环演示")
        self.geometry("1080x760")
        self.minsize(900, 620)

        self.process: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()

        self.input_var = tk.StringVar(value=str(PROJECT_DIR / "small.mp4"))
        self.work_dir_var = tk.StringVar(value=str(PROJECT_DIR / "artifacts" / "gui_stream_demo"))
        self.preset_var = tk.StringVar(value=PRESETS[0].label)
        self.extra_args_var = tk.StringVar(value="")
        self.source_preview_var = tk.BooleanVar(value=True)
        self.embed_player_var = tk.BooleanVar(value=True)
        self.player_width_var = tk.StringVar(value="360")
        self.player_height_var = tk.StringVar(value="640")

        self.status_var = tk.StringVar(value="就绪")
        self.chunk_var = tk.StringVar(value="-")
        self.goodput_var = tk.StringVar(value="-")
        self.ok_var = tk.StringVar(value="-")
        self.context_var = tk.StringVar(value="-")
        self.elapsed_var = tk.StringVar(value="-")
        self.source_status_var = tk.StringVar(value="未启动")
        self.receiver_status_var = tk.StringVar(value="未启动")
        self.delay_status_var = tk.StringVar(value="-")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.drain_output)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=3)
        root.rowconfigure(5, weight=2)

        file_frame = ttk.LabelFrame(root, text="输入")
        file_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        file_frame.columnconfigure(1, weight=1)
        ttk.Label(file_frame, text="视频文件").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.input_var).grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        ttk.Button(file_frame, text="浏览", command=self.browse_input).grid(row=0, column=2, padx=8, pady=8)
        ttk.Label(file_frame, text="工作目录").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.work_dir_var).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ttk.Button(file_frame, text="浏览", command=self.browse_work_dir).grid(row=1, column=2, padx=8, pady=8)

        preset_frame = ttk.LabelFrame(root, text="传输配置")
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
        size_frame = ttk.Frame(preset_frame)
        size_frame.grid(row=3, column=1, padx=8, pady=8, sticky="w")
        ttk.Label(size_frame, text="播放窗口").pack(side=tk.LEFT)
        ttk.Entry(size_frame, textvariable=self.player_width_var, width=6).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Label(size_frame, text="x").pack(side=tk.LEFT)
        ttk.Entry(size_frame, textvariable=self.player_height_var, width=6).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(size_frame, text="只放大播放窗口，不提高 RF 码率").pack(side=tk.LEFT)
        ttk.Checkbutton(preset_frame, text="嵌入上位机播放区域", variable=self.embed_player_var).grid(
            row=4, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w"
        )

        controls = ttk.Frame(root)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="开始", command=self.start_stream)
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop_stream, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.RIGHT)

        metrics = ttk.LabelFrame(root, text="实时状态")
        metrics.grid(row=3, column=0, sticky="ew", pady=(0, 10))
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

        preview_frame = ttk.LabelFrame(root, text="视频对照")
        preview_frame.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.columnconfigure(1, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.source_video_host = self._video_panel(preview_frame, 0, "发送视频")
        self.receiver_video_host = self._video_panel(preview_frame, 1, "接收视频")

        log_frame = ttk.LabelFrame(root, text="运行日志")
        log_frame.grid(row=5, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap=tk.NONE, height=18, font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=yscroll.set)

    @staticmethod
    def _metric(parent: ttk.Frame, column: int, label: str, variable: tk.StringVar) -> None:
        cell = ttk.Frame(parent, padding=8)
        cell.grid(row=0, column=column, sticky="ew")
        ttk.Label(cell, text=label).pack(anchor="w")
        ttk.Label(cell, textvariable=variable, font=("Segoe UI", 11, "bold")).pack(anchor="w")

    @staticmethod
    def _video_panel(parent: ttk.Frame, column: int, title: str) -> tk.Frame:
        outer = ttk.LabelFrame(parent, text=title)
        outer.grid(row=0, column=column, sticky="nsew", padx=6, pady=6)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        host = tk.Frame(outer, bg="black", width=360, height=300, highlightthickness=1, highlightbackground="#333333")
        host.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        host.grid_propagate(False)
        return host

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

    def selected_preset(self) -> Preset:
        label = self.preset_var.get()
        return next((preset for preset in PRESETS if preset.label == label), PRESETS[0])

    def update_preset_description(self) -> None:
        self.preset_description.configure(text=self.selected_preset().description)

    def build_command(self) -> list[str]:
        preset = self.selected_preset()
        no_player = "--no-player" in preset.args
        command = [
            sys.executable,
            str(STREAM_SCRIPT),
            "--input-file",
            self.input_var.get(),
            "--work-dir",
            self.work_dir_var.get(),
        ]
        command.extend(preset.args)
        width = self.player_width_var.get().strip()
        height = self.player_height_var.get().strip()
        if width and width != "0":
            command.extend(["--player-width", width])
        if height and height != "0":
            command.extend(["--player-height", height])
        if self.embed_player_var.get() and not no_player:
            command.extend(["--player-window-id", str(self.receiver_video_host.winfo_id())])
            if self.source_preview_var.get():
                command.extend(["--source-player-window-id", str(self.source_video_host.winfo_id())])
        if self.source_preview_var.get() and not no_player:
            command.append("--source-preview")
        extra = self.extra_args_var.get().strip()
        if extra:
            command.extend(shlex.split(extra))
        return command

    def start_stream(self) -> None:
        if self.process is not None:
            return
        input_path = Path(self.input_var.get())
        if not input_path.exists():
            messagebox.showerror("文件不存在", f"视频文件不存在:\n{input_path}")
            return

        self.update_idletasks()
        command = self.build_command()
        self.log_text.delete("1.0", tk.END)
        self.append_log("command=" + " ".join(command))
        self.status_var.set("启动中")
        self.chunk_var.set("-")
        self.goodput_var.set("-")
        self.ok_var.set("-")
        self.context_var.set("-")
        self.elapsed_var.set("-")
        no_player = "--no-player" in self.selected_preset().args
        self.source_status_var.set("关闭" if no_player or not self.source_preview_var.get() else "等待首块")
        self.receiver_status_var.set("关闭" if no_player else "启动中")
        self.delay_status_var.set("-")

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
                line = self.output_queue.get_nowait()
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
            try:
                self.goodput_var.set(f"{int(float(value)) // 1000} kbps")
            except ValueError:
                self.goodput_var.set(value)
        elif key == "stream_chunks_ok":
            failed = self.ok_var.get().split("/")[-1] if "/" in self.ok_var.get() else "0"
            self.ok_var.set(f"{value}/{failed}")
        elif key == "stream_chunks_failed":
            ok = self.ok_var.get().split("/")[0] if "/" in self.ok_var.get() else "0"
            self.ok_var.set(f"{ok}/{value}")
        elif key == "stream_context_recreates":
            self.context_var.set(value)
        elif key == "stream_elapsed_sec":
            self.elapsed_var.set(f"{float(value):.1f}s")
        elif key == "player_command":
            self.receiver_status_var.set("已启动")
        elif key == "source_preview_started":
            self.source_status_var.set("已启动")
            self.delay_status_var.set("首块后启动")
        elif key == "low_latency_stream_ok":
            self.status_var.set("完成" if value.lower() == "true" else "失败")

    def on_process_done(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        if self.process is not None and self.process.returncode == 0:
            self.status_var.set("已完成")
        elif self.status_var.get() not in ("完成", "失败"):
            self.status_var.set("已停止")
        self.process = None
        self.reader_thread = None

    def on_close(self) -> None:
        if self.process is not None:
            self.stop_stream()
        self.destroy()


def main() -> int:
    app = SdrVideoGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
