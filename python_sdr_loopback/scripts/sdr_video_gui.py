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
    Preset(
        "Stable demo",
        "250k, 360p, 12fps, veryfast, buffered player",
        (),
    ),
    Preset(
        "Smoke test",
        "Stable demo, but stops after 20 chunks",
        ("--max-chunks", "20"),
    ),
    Preset(
        "Conservative",
        "Lower bitrate/fps for weaker links",
        (
            "--video-bitrate",
            "200k",
            "--video-bufsize",
            "400k",
            "--fps",
            "10",
            "--gop",
            "10",
            "--chunk-bytes",
            "15000",
        ),
    ),
    Preset(
        "Quality try",
        "Higher bitrate attempt; may stutter",
        (
            "--video-bitrate",
            "300k",
            "--video-bufsize",
            "600k",
            "--fps",
            "12",
            "--gop",
            "12",
        ),
    ),
    Preset(
        "No player debug",
        "Runs RF stream without ffplay, stops after 20 chunks",
        ("--no-player", "--max-chunks", "20"),
    ),
)


class SdrVideoGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ANTSDR E310 Video Stream Demo")
        self.geometry("980x680")
        self.minsize(820, 560)

        self.process: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.started_at: float | None = None

        self.input_var = tk.StringVar(value=str(PROJECT_DIR / "small.mp4"))
        self.work_dir_var = tk.StringVar(value=str(PROJECT_DIR / "artifacts" / "gui_stream_demo"))
        self.preset_var = tk.StringVar(value=PRESETS[0].label)
        self.extra_args_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready")
        self.chunk_var = tk.StringVar(value="-")
        self.goodput_var = tk.StringVar(value="-")
        self.ok_var = tk.StringVar(value="-")
        self.context_var = tk.StringVar(value="-")
        self.elapsed_var = tk.StringVar(value="-")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.drain_output)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        file_frame = ttk.LabelFrame(root, text="Input")
        file_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        file_frame.columnconfigure(1, weight=1)
        ttk.Label(file_frame, text="Video file").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.input_var).grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        ttk.Button(file_frame, text="Browse", command=self.browse_input).grid(row=0, column=2, padx=8, pady=8)
        ttk.Label(file_frame, text="Work dir").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(file_frame, textvariable=self.work_dir_var).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ttk.Button(file_frame, text="Browse", command=self.browse_work_dir).grid(row=1, column=2, padx=8, pady=8)

        preset_frame = ttk.LabelFrame(root, text="Stream Profile")
        preset_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        preset_frame.columnconfigure(1, weight=1)
        ttk.Label(preset_frame, text="Preset").grid(row=0, column=0, padx=8, pady=8, sticky="w")
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
        ttk.Label(preset_frame, text="Extra args").grid(row=2, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(preset_frame, textvariable=self.extra_args_var).grid(row=2, column=1, padx=8, pady=8, sticky="ew")

        controls = ttk.Frame(root)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="Start", command=self.start_stream)
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop_stream, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.RIGHT)

        metrics = ttk.LabelFrame(root, text="Live Metrics")
        metrics.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for col in range(5):
            metrics.columnconfigure(col, weight=1)
        self._metric(metrics, 0, "Chunk", self.chunk_var)
        self._metric(metrics, 1, "Goodput", self.goodput_var)
        self._metric(metrics, 2, "OK / Failed", self.ok_var)
        self._metric(metrics, 3, "Context", self.context_var)
        self._metric(metrics, 4, "Elapsed", self.elapsed_var)

        log_frame = ttk.LabelFrame(root, text="Log")
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
        ttk.Label(cell, textvariable=variable, font=("Segoe UI", 12, "bold")).pack(anchor="w")

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(PROJECT_DIR),
            filetypes=[("Video files", "*.mp4 *.ts *.mov *.mkv *.avi"), ("All files", "*.*")],
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
        command = [
            sys.executable,
            str(STREAM_SCRIPT),
            "--input-file",
            self.input_var.get(),
            "--work-dir",
            self.work_dir_var.get(),
        ]
        command.extend(preset.args)
        extra = self.extra_args_var.get().strip()
        if extra:
            command.extend(shlex.split(extra))
        return command

    def start_stream(self) -> None:
        if self.process is not None:
            return
        input_path = Path(self.input_var.get())
        if not input_path.exists():
            messagebox.showerror("Input missing", f"Video file does not exist:\n{input_path}")
            return

        command = self.build_command()
        self.log_text.delete("1.0", tk.END)
        self.append_log("command=" + " ".join(command))
        self.status_var.set("Starting")
        self.chunk_var.set("-")
        self.goodput_var.set("-")
        self.ok_var.set("-")
        self.context_var.set("-")
        self.elapsed_var.set("-")

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(
            command,
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self.started_at = time.perf_counter()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("Running")
        self.reader_thread = threading.Thread(target=self.read_process_output, daemon=True)
        self.reader_thread.start()

    def stop_stream(self) -> None:
        if self.process is None:
            return
        self.status_var.set("Stopping")
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
        elif key == "low_latency_stream_ok":
            self.status_var.set("OK" if value.lower() == "true" else "Failed")

    def on_process_done(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        if self.process is not None and self.process.returncode == 0:
            self.status_var.set("Finished")
        elif self.status_var.get() not in ("OK", "Failed"):
            self.status_var.set("Stopped")
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
