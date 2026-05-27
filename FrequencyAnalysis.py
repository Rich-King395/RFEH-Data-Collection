from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


FFT_CSV_HEADER = ["Frequency(Hz)", "Amplitude(V)"]


def load_time_voltage_for_fft(csv_path: Path) -> tuple[np.ndarray, np.ndarray, str]:
    pc_elapsed_ms: list[float] = []
    arduino_time_ms: list[float] = []
    voltage_v: list[float] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []

        if "Voltage(V)" not in fieldnames:
            raise ValueError("CSV must contain a Voltage(V) column.")
        if "PCElapsed(ms)" not in fieldnames and "Time(ms)" not in fieldnames:
            raise ValueError("CSV must contain PCElapsed(ms) or Time(ms).")

        for row in reader:
            try:
                voltage = float(row["Voltage(V)"])
            except (TypeError, ValueError):
                continue

            pc_time = _try_float(row.get("PCElapsed(ms)"))
            arduino_time = _try_float(row.get("Time(ms)"))

            if pc_time is None and arduino_time is None:
                continue

            pc_elapsed_ms.append(pc_time if pc_time is not None else np.nan)
            arduino_time_ms.append(arduino_time if arduino_time is not None else np.nan)
            voltage_v.append(voltage)

    if len(voltage_v) < 8:
        raise ValueError("At least 8 valid samples are required for FFT analysis.")

    pc_array = np.asarray(pc_elapsed_ms, dtype=float)
    arduino_array = np.asarray(arduino_time_ms, dtype=float)
    voltage_array = np.asarray(voltage_v, dtype=float)

    if _is_strictly_increasing(pc_array):
        return pc_array / 1000.0, voltage_array, "PCElapsed(ms)"
    if _is_strictly_increasing(arduino_array):
        return arduino_array / 1000.0, voltage_array, "Time(ms)"

    raise ValueError("No strictly increasing time column found for FFT analysis.")


def compute_voltage_fft(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    max_frequency: float | None = 3,
    drop_dc: bool = True,
) -> dict[str, np.ndarray | float | str]:
    if time_s.size != voltage_v.size:
        raise ValueError("time_s and voltage_v must have the same length.")
    if time_s.size < 8:
        raise ValueError("At least 8 samples are required for FFT analysis.")

    time_s = np.asarray(time_s, dtype=float)
    voltage_v = np.asarray(voltage_v, dtype=float)

    dt = float(np.mean(np.diff(time_s)))
    if dt <= 0:
        raise ValueError("Mean sample interval must be greater than zero.")

    sample_rate = 1.0 / dt
    nyquist = sample_rate / 2.0

    centered = voltage_v - float(np.mean(voltage_v))
    window = np.hanning(centered.size)
    windowed = centered * window

    frequencies = np.fft.rfftfreq(windowed.size, d=dt)
    spectrum = np.fft.rfft(windowed)

    # Coherent-gain correction for Hann window amplitude scaling.
    amplitude = (2.0 / np.sum(window)) * np.abs(spectrum)

    if drop_dc and frequencies.size > 0:
        frequencies = frequencies[1:]
        amplitude = amplitude[1:]

    if max_frequency is not None:
        max_frequency = min(float(max_frequency), nyquist)
        mask = frequencies <= max_frequency
        frequencies = frequencies[mask]
        amplitude = amplitude[mask]

    return {
        "frequency_hz": frequencies,
        "amplitude_v": amplitude,
        "sample_rate_hz": sample_rate,
        "nyquist_hz": nyquist,
        "sample_interval_s": dt,
        "preprocess": "demean + hann",
    }


def save_fft_csv(frequency_hz: np.ndarray, amplitude_v: np.ndarray, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(FFT_CSV_HEADER)
        for frequency, amplitude in zip(frequency_hz, amplitude_v):
            writer.writerow([f"{float(frequency):.9f}", f"{float(amplitude):.12g}"])

    return output_path


def plot_fft(
    frequency_hz: np.ndarray,
    amplitude_v: np.ndarray,
    output_path: Path,
    title: str = "Voltage FFT Spectrum",
    show: bool = False,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 5))
    plt.plot(frequency_hz, amplitude_v, color="tab:purple", linewidth=1.2)
    plt.title(title)
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Amplitude (V)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path, dpi=600)

    if show:
        plt.show()
    else:
        plt.close()

    return output_path


def analyze_voltage_fft(
    csv_path: Path | str,
    output_csv_path: Path | str | None = None,
    output_png_path: Path | str | None = None,
    max_frequency: float | None = 3,
    show: bool = False,
) -> dict[str, object]:
    csv_path = Path(csv_path)
    output_csv_path = Path(output_csv_path) if output_csv_path else csv_path.with_name(f"{csv_path.stem}_fft.csv")
    output_png_path = Path(output_png_path) if output_png_path else csv_path.with_name(f"{csv_path.stem}_fft.png")

    time_s, voltage_v, time_source = load_time_voltage_for_fft(csv_path)
    result = compute_voltage_fft(time_s, voltage_v, max_frequency=max_frequency)

    frequency_hz = result["frequency_hz"]
    amplitude_v = result["amplitude_v"]
    assert isinstance(frequency_hz, np.ndarray)
    assert isinstance(amplitude_v, np.ndarray)

    save_fft_csv(frequency_hz, amplitude_v, output_csv_path)
    plot_fft(
        frequency_hz,
        amplitude_v,
        output_png_path,
        title=f"Voltage FFT Spectrum ({csv_path.stem})",
        show=show,
    )

    return {
        "fftCsvPath": output_csv_path,
        "fftPngPath": output_png_path,
        "timeSource": time_source,
        "sampleRateHz": result["sample_rate_hz"],
        "nyquistHz": result["nyquist_hz"],
        "sampleIntervalS": result["sample_interval_s"],
        "preprocess": result["preprocess"],
        "maxFrequencyHz": max_frequency,
        "binCount": int(frequency_hz.size),
    }


def _try_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_strictly_increasing(values: np.ndarray) -> bool:
    if values.size < 2 or not np.all(np.isfinite(values)):
        return False
    return bool(np.all(np.diff(values) > 0))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute FFT spectrum from a recorded voltage CSV.")
    parser.add_argument("csv_path", type=Path, help="CSV file with Voltage(V) and time columns.")
    parser.add_argument("-o", "--output-csv", type=Path, help="Output FFT CSV path.")
    parser.add_argument("-p", "--output-png", type=Path, help="Output FFT PNG path.")
    parser.add_argument("--max-frequency", type=float, default=10.0, help="Maximum frequency to save/plot in Hz.")
    parser.add_argument("--full-range", action="store_true", help="Save/plot up to the Nyquist frequency.")
    parser.add_argument("--show", action="store_true", help="Show the plot window after saving.")
    args = parser.parse_args()

    result = analyze_voltage_fft(
        csv_path=args.csv_path,
        output_csv_path=args.output_csv,
        output_png_path=args.output_png,
        max_frequency=None if args.full_range else args.max_frequency,
        show=args.show,
    )

    print(f"Saved FFT CSV: {result['fftCsvPath']}")
    print(f"Saved FFT plot: {result['fftPngPath']}")
    print(f"Time source: {result['timeSource']}")
    print(f"Sample rate: {float(result['sampleRateHz']):.3f} Hz")
    print(f"Nyquist frequency: {float(result['nyquistHz']):.3f} Hz")


if __name__ == "__main__":
    main()
