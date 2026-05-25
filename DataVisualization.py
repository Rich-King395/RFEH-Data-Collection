from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def load_time_voltage(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    time_ms: list[float] = []
    voltage_v: list[float] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []

        if "Time(ms)" not in fieldnames or "Voltage(V)" not in fieldnames:
            raise ValueError("CSV must contain Time(ms) and Voltage(V) columns.")

        for row in reader:
            try:
                time_ms.append(float(row["Time(ms)"]))
                voltage_v.append(float(row["Voltage(V)"]))
            except (TypeError, ValueError):
                continue

    if not time_ms:
        raise ValueError(f"No valid samples found in {csv_path}.")

    return np.asarray(time_ms, dtype=float), np.asarray(voltage_v, dtype=float)


def moving_average(values: np.ndarray, window_size: int = 11) -> np.ndarray:
    if window_size < 1:
        raise ValueError("window_size must be >= 1.")

    if window_size % 2 == 0:
        window_size += 1

    array = np.asarray(values, dtype=float)
    if array.size < window_size:
        return array

    pad = window_size // 2
    padded = np.pad(array, (pad, pad), mode="edge")
    kernel = np.ones(window_size, dtype=float) / window_size
    return np.convolve(padded, kernel, mode="valid")


def plot_voltage_csv(
    csv_path: Path | str,
    output_path: Path | str | None = None,
    window_size: int = 11,
    normalize_time: bool = True,
    show: bool = False,
) -> Path:
    csv_path = Path(csv_path)
    output_path = Path(output_path) if output_path else csv_path.with_suffix(".png")

    time_ms, voltage_v = load_time_voltage(csv_path)
    if normalize_time:
        time_ms = time_ms - time_ms[0]

    time_s = time_ms / 1000.0
    voltage_smooth = moving_average(voltage_v, window_size)

    plt.figure(figsize=(10, 5))
    plt.plot(time_s, voltage_v, color="tab:blue", linewidth=0.8, alpha=0.5, label="Raw")
    plt.plot(
        time_s,
        voltage_smooth,
        color="tab:red",
        linewidth=2.0,
        label=f"Smoothed (MA, window={window_size})",
    )
    plt.title("Voltage vs Time")
    plt.xlabel("Time (s)")
    plt.ylabel("Voltage (V)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=600)

    if show:
        plt.show()
    else:
        plt.close()

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot voltage data from a recorded CSV file.")
    parser.add_argument("csv_path", type=Path, help="Path to a CSV file with Time(ms), ADC, Voltage(V).")
    parser.add_argument("-o", "--output", type=Path, help="Output PNG path. Defaults to CSV path with .png.")
    parser.add_argument("-w", "--window", type=int, default=11, help="Moving average window size.")
    parser.add_argument("--no-normalize-time", action="store_true", help="Plot Arduino uptime instead of record-relative time.")
    parser.add_argument("--show", action="store_true", help="Show the plot window after saving.")
    args = parser.parse_args()

    output_path = plot_voltage_csv(
        csv_path=args.csv_path,
        output_path=args.output,
        window_size=args.window,
        normalize_time=not args.no_normalize_time,
        show=args.show,
    )
    print(f"Saved plot: {output_path}")


if __name__ == "__main__":
    main()
