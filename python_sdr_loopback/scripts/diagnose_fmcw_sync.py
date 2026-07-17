import argparse
import datetime
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

# Ensure sdr_loopback package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_loopback.radar.config import RadarConfig
from sdr_loopback.radar.sources import E310CpiSource, E310RadioConfig
from sdr_loopback.radar.synchronizer import ChirpSynchronizer, ChirpSyncError

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose FMCW chirp synchronization and capture RX metrics.")
    parser.add_argument("--source", choices=["e310"], default="e310", help="Data source type")
    parser.add_argument("--frames", type=int, default=100, help="Number of frames to capture")
    
    default_dir = f"artifacts/hardware/sync_diagnosis_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    parser.add_argument("--output-dir", type=str, default=default_dir, help="Directory to save logs and npz files")
    
    parser.add_argument("--tx-gain-db", type=float, default=-20.0, help="E310 TX gain in dB")
    parser.add_argument("--tx-amplitude", type=float, default=0.40, help="E310 TX amplitude (0.0 to 1.0)")
    parser.add_argument("--rx-gain-db", type=float, default=20.0, help="E310 RX gain in dB")
    
    parser.add_argument("--save-captures", choices=["all", "failures", "none"], default="failures", 
                        help="When to save the raw IQ data as .npz")
    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.jsonl"
    
    print(f"Starting diagnosis...")
    print(f"Output directory: {output_dir}")
    print(f"Frames: {args.frames}")
    print(f"TX Gain: {args.tx_gain_db} dB, TX Amp: {args.tx_amplitude}, RX Gain: {args.rx_gain_db} dB")
    print(f"Save Captures: {args.save_captures}")
    print("-" * 50)
    
    config = RadarConfig()
    radio_config = E310RadioConfig(
        tx_gain_db=args.tx_gain_db,
        tx_amplitude=args.tx_amplitude,
        rx_gain_db=args.rx_gain_db,
    )
    
    source = E310CpiSource(config, radio_config)
    synchronizer = ChirpSynchronizer(config, mode="correlation")
    
    source.open()
    interrupted = False
    
    try:
        for i in range(args.frames):
            start_time = time.perf_counter()
            try:
                capture = source.capture()
            except Exception as e:
                print(f"Frame {i:03d} | CAPTURE ERROR: {e}")
                continue
                
            rx_iq = np.asarray(getattr(capture, "rx_iq"))
            
            # RX Metrics
            abs_rx = np.abs(rx_iq)
            peak = float(np.max(abs_rx)) if abs_rx.size > 0 else 0.0
            rms = float(np.sqrt(np.mean(np.square(abs_rx)))) if abs_rx.size > 0 else 0.0
            clipping = float(np.mean(abs_rx > 0.99)) if abs_rx.size > 0 else 0.0
            
            # Sync Metrics
            sync_ok = False
            error_msg = None
            start_sample = -1
            correlation = -1.0
            mean_correlation = -1.0
            periodic_coherence = -1.0
            
            try:
                result = synchronizer.synchronize(capture)
                sync_ok = True
                start_sample = result.start_sample
                correlation = result.correlation
                mean_correlation = result.mean_correlation
                periodic_coherence = result.periodic_coherence if result.periodic_coherence is not None else -1.0
            except ChirpSyncError as e:
                error_msg = str(e)
                try:
                    # Attempt best-effort start sample correlation for debugging
                    start_sample = synchronizer._correlate_start(rx_iq)
                except Exception:
                    pass
                    
            elapsed_s = time.perf_counter() - start_time
            
            metrics = {
                "frame": i,
                "sync_ok": sync_ok,
                "error": error_msg,
                "start_sample": start_sample,
                "correlation": correlation,
                "mean_correlation": mean_correlation,
                "periodic_coherence": periodic_coherence,
                "rx_peak": peak,
                "rx_rms": rms,
                "rx_clipping": clipping,
                "elapsed_s": elapsed_s,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            with open(metrics_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(metrics) + "\n")
                
            status = "OK" if sync_ok else "FAIL"
            print(f"Frame {i:03d}/{args.frames} | {status} | corr={correlation:6.4f} | peak={peak:6.4f} | clip={clipping:5.1%} | {elapsed_s*1000:4.1f}ms")
            if not sync_ok:
                print(f"  -> Error: {error_msg}")
            
            # Save raw captures
            if (args.save_captures == "all") or (args.save_captures == "failures" and not sync_ok):
                tx_iq = np.asarray(getattr(capture, "tx_iq"))
                npz_name = f"frame_{i:04d}_{'ok' if sync_ok else 'fail'}.npz"
                npz_path = output_dir / npz_name
                np.savez_compressed(
                    npz_path, 
                    tx_iq=tx_iq, 
                    rx_iq=rx_iq, 
                    metrics=json.dumps(metrics)
                )
                print(f"  -> Saved {npz_name}")
                
    except KeyboardInterrupt:
        interrupted = True
        print("\nDiagnostic interrupted by user.")
    finally:
        source.close()
        print("Diagnostic complete. SDR closed.")
        
    return 130 if interrupted else 0

if __name__ == "__main__":
    sys.exit(main())
