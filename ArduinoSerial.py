from __future__ import annotations

import csv
import re
import time
from pathlib import Path


SERIAL_PORT = "COM3"
BAUD_RATE = 115200
DATA_DIR = Path(__file__).resolve().parent / "Data"
CSV_HEADER = ["Time(ms)", "ADC", "Voltage(V)"]


def sanitize_run_name(name: str) -> str:
    name = name.strip()
    if name.lower().endswith(".csv"):
        name = name[:-4]

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise ValueError("File name cannot be empty.")

    return name


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


def parse_sample_line(line: str) -> list[str] | None:
    if line == ",".join(CSV_HEADER):
        return None

    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 3:
        return None

    try:
        float(parts[0])
        int(parts[1])
        float(parts[2])
    except ValueError:
        return None

    return parts


def record_samples(duration_s: float, run_name: str) -> tuple[Path, Path | None, int]:
    run_dir = DATA_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / f"{run_name}.csv"
    png_path = run_dir / f"{run_name}.png"

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

    return csv_path, png_path, sample_count


def main() -> None:
    duration_s = ask_record_duration()
    run_name = ask_run_name()

    try:
        csv_path, png_path, sample_count = record_samples(duration_s, run_name)
    except KeyboardInterrupt:
        print("\nRecording interrupted.")
        return
    except RuntimeError as exc:
        print(exc)
        return

    print(f"Saved CSV: {csv_path}")
    if png_path:
        print(f"Saved plot: {png_path}")
    print(f"Samples: {sample_count}")


if __name__ == "__main__":
    main()
