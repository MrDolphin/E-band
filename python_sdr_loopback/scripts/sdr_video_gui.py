from __future__ import annotations

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
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


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


class VideoPane(ttk.Frame):
    def __init__(self, parent: tk.Widget, title: str) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.photo: ImageTk.PhotoImage | None = None

        ttk.Label(self, text=title, anchor="center").grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.canvas = tk.Canvas(self, background="black", highlightthickness=1, highlightbackground="#333333")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.image: Image.Image | None = None
        self.placeholder = "等待视频"
        self.redraw()

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
        self.receiver_output_path: Path | None = None

        self.input_var = tk.StringVar(value=str(PROJECT_DIR / "small.mp4"))
        self.work_dir_var = tk.StringVar(value=str(PROJECT_DIR / "artifacts" / "gui_stream_demo"))
        self.preset_var = tk.StringVar(value=PRESETS[0].label)
        self.extra_args_var = tk.StringVar(value="")
        self.source_preview_var = tk.BooleanVar(value=True)

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
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.config_tab = ttk.Frame(self.notebook, padding=14)
        self.stream_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.config_tab, text="系统配置")
        self.notebook.add(self.stream_tab, text="视频传输")

        self.config_tab.columnconfigure(0, weight=1)
        self.stream_tab.columnconfigure(0, weight=1)
        self.stream_tab.rowconfigure(2, weight=2)
        self.stream_tab.rowconfigure(4, weight=1)

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
        ttk.Label(preset_frame, text="发送端预览会显示在“视频传输”页固定区域").grid(
            row=3, column=1, padx=8, pady=8, sticky="w"
        )

        hint = ttk.Label(
            self.config_tab,
            text="配置完成后切换到“视频传输”页，点击开始即可运行 SDR RF 回环视频演示。",
        )
        hint.grid(row=2, column=0, sticky="w", pady=(4, 0))

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

        video_frame = ttk.LabelFrame(self.stream_tab, text="视频对照")
        video_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        video_frame.columnconfigure(0, weight=1)
        video_frame.columnconfigure(1, weight=1)
        video_frame.rowconfigure(0, weight=1)
        self.source_pane = VideoPane(video_frame, "发送视频")
        self.source_pane.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.receiver_pane = VideoPane(video_frame, "接收视频")
        self.receiver_pane.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.receiver_pane.show_placeholder("接收视频显示下一步接入")

        log_frame = ttk.LabelFrame(self.stream_tab, text="运行日志")
        log_frame.grid(row=4, column=0, sticky="nsew")
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
        output_file = self.receiver_ts_path()
        no_player = "--no-player" in preset.args
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
        command.extend(preset.args)
        if not no_player:
            positions = self.player_positions()
            command.extend(
                [
                    "--player-title",
                    "接收视频",
                    "--player-left",
                    str(positions["receiver_left"]),
                    "--player-top",
                    str(positions["receiver_top"]),
                ]
            )
        extra = self.extra_args_var.get().strip()
        if extra:
            command.extend(shlex.split(extra))
        return command

    def player_positions(self) -> dict[str, int]:
        self.update_idletasks()
        left = max(0, self.winfo_rootx() + self.winfo_width() - 480)
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
        self.source_pane.show_placeholder("发送预览启动中" if self.source_preview_var.get() else "发送预览关闭")
        self.receiver_pane.show_placeholder("接收视频使用外部流畅播放器")
        self.source_status_var.set("关闭" if not self.source_preview_var.get() else "启动中")
        self.receiver_status_var.set("外部播放器")
        self.delay_status_var.set("-")

        self.receiver_output_path = self.receiver_ts_path()
        self.receiver_output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.receiver_output_path.unlink()
        except FileNotFoundError:
            pass

        if self.source_preview_var.get():
            self.start_source_preview(input_path)

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
        elif key == "stream_output_bytes":
            self.receiver_status_var.set("已接收 TS 数据")
        elif key == "packets_decoded":
            self.receiver_status_var.set(f"已解包 {value}")
        elif key == "low_latency_stream_ok":
            self.status_var.set("完成" if value.lower() == "true" else "失败")

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
        self.stop_source_preview()

    def on_close(self) -> None:
        self.stop_source_preview()
        if self.process is not None:
            self.stop_stream()
        self.destroy()


def main() -> int:
    app = SdrVideoGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
