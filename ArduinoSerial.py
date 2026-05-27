from __future__ import annotations

import csv
import time
from pathlib import Path

from serial_utils import CSV_HEADER, parse_sample_line, sanitize_run_name


SERIAL_PORT = "COM3"
BAUD_RATE = 115200
DATA_DIR = Path(__file__).resolve().parent / "Data"


def ask_record_duration() -> float:
    while True:
        raw_value = input("Record duration (seconds): ").strip()
        try:
            duration = float(raw_value)
        except ValueError:
            print("Please enter a number, for example 60 or 120.5.")
            continue

        if duration <= 0:
            print("Duration must be greater than 0.")
            continue

        return duration


def ask_run_name() -> str:
    while True:
        raw_name = input("File name: ").strip()
        try:
            return sanitize_run_name(raw_name)
        except ValueError as exc:
            print(exc)


RECORDING_FFT_MAX_FREQUENCY_HZ = 3.0


def record_samples(duration_s: float, run_name: str) -> tuple[Path, Path | None, Path | None, Path | None, int]:
    run_dir = DATA_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / f"{run_name}.csv"
    png_path = run_dir / f"{run_name}.png"
    fft_csv_path = run_dir / f"{run_name}_fft.csv"
    fft_png_path = run_dir / f"{run_name}_fft.png"

    print(f"Opening serial port {SERIAL_PORT} at {BAUD_RATE} baud...")

    sample_count = 0
    try:
        import serial
    except ModuleNotFoundError as exc:
        if exc.name != "serial":
            raise
        raise RuntimeError("pyserial is not installed. Run: pip install pyserial") from exc

    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
        time.sleep(2)
        ser.reset_input_buffer()

        print(f"Recording for {duration_s:g} seconds...")
        started_at = time.monotonic()
        next_status_at = started_at + 1.0

        with csv_path.open("w", encoding="utf-8", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADER)

            while True:
                now = time.monotonic()
                if now - started_at >= duration_s:
                    break

                raw_line = ser.readline()
                if not raw_line:
                    continue

                line = raw_line.decode("utf-8", errors="ignore").strip()
                sample = parse_sample_line(line)
                if sample is None:
                    continue

                writer.writerow(sample)
                sample_count += 1

                if now >= next_status_at:
                    elapsed = now - started_at
                    print(f"Recorded {sample_count} samples ({elapsed:.1f}/{duration_s:g}s)")
                    next_status_at = now + 1.0

            csvfile.flush()

    if sample_count == 0:
        raise RuntimeError("No valid samples were recorded. Check the port, baud rate, and Arduino output format.")

    try:
        from DataVisualization import plot_voltage_csv

        plot_voltage_csv(csv_path, png_path)
    except ModuleNotFoundError as exc:
        if exc.name not in {"matplotlib", "numpy"}:
            raise
        print("CSV saved, but plotting dependencies are not installed. Run: pip install numpy matplotlib")
        png_path = None

    try:
        from FrequencyAnalysis import analyze_voltage_fft

        analyze_voltage_fft(
            csv_path=csv_path,
            output_csv_path=fft_csv_path,
            output_png_path=fft_png_path,
            max_frequency=RECORDING_FFT_MAX_FREQUENCY_HZ,
        )
    except ModuleNotFoundError as exc:
        if exc.name not in {"matplotlib", "numpy"}:
            raise
        print("CSV saved, but FFT dependencies are not installed. Run: pip install numpy matplotlib")
        fft_csv_path = None
        fft_png_path = None
    except Exception as exc:
        print(f"CSV saved, but FFT analysis was skipped: {exc}")
        fft_csv_path = None
        fft_png_path = None

    return csv_path, png_path, fft_csv_path, fft_png_path, sample_count


def main() -> None:
    duration_s = ask_record_duration()
    run_name = ask_run_name()

    try:
        csv_path, png_path, fft_csv_path, fft_png_path, sample_count = record_samples(duration_s, run_name)
    except KeyboardInterrupt:
        print("\nRecording interrupted.")
        return
    except RuntimeError as exc:
        print(exc)
        return

    print(f"Saved CSV: {csv_path}")
    if png_path:
        print(f"Saved plot: {png_path}")
    if fft_csv_path:
        print(f"Saved FFT CSV: {fft_csv_path}")
    if fft_png_path:
        print(f"Saved FFT plot: {fft_png_path}")
    print(f"Samples: {sample_count}")


if __name__ == "__main__":
    main()
