from __future__ import annotations

import argparse
import asyncio
import csv
import json
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from FrequencyAnalysis import analyze_voltage_fft, compute_voltage_fft
from serial_utils import parse_sample_line, sanitize_run_name


DEFAULT_SERIAL_PORT = "COM3"
DEFAULT_BAUD_RATE = 115200
DEFAULT_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
MAX_BUFFER_SECONDS = 300
MAX_EXPECTED_SAMPLE_RATE = 500
MAX_BUFFER_SAMPLES = MAX_BUFFER_SECONDS * MAX_EXPECTED_SAMPLE_RATE
FFT_WINDOW_SECONDS = 60.0
FFT_MAX_FREQUENCY_HZ = 1.0
FFT_UPDATE_INTERVAL_SECONDS = 1.0
FFT_MIN_SAMPLES = 32
RECORDING_FFT_MAX_FREQUENCY_HZ = 3.0
RECORDING_FFT_MIN_FREQUENCY_HZ = 0.1

ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
DATA_DIR = ROOT_DIR / "Data"
RAW_CSV_HEADER = ["Time(ms)", "PCElapsed(ms)", "ADC", "Voltage(V)"]
SMOOTH_CSV_HEADER = ["Time(ms)", "PCElapsed(ms)", "ADC", "Voltage(V)", "SmoothedVoltage(V)"]


def normalize_smooth_window(value: Any) -> int:
    try:
        window = int(value)
    except (TypeError, ValueError):
        window = 11

    window = max(1, min(window, 501))
    if window % 2 == 0:
        window += 1
    return min(window, 501)


def normalize_recording_fft_max_frequency(value: Any) -> float | None:
    if value == "full":
        return None
    if value is None or value == "":
        return RECORDING_FFT_MAX_FREQUENCY_HZ

    try:
        frequency = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Offline FFT range must be a number or full range.") from exc

    if frequency < RECORDING_FFT_MIN_FREQUENCY_HZ:
        raise ValueError(f"Offline FFT range must be at least {RECORDING_FFT_MIN_FREQUENCY_HZ:g} Hz.")
    return frequency


class RecordingManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = threading.RLock()
        self._active = False
        self._completed = False
        self._saving = False
        self._error = ""
        self._folder_name = ""
        self._duration_s = 0.0
        self._smooth = True
        self._show_raw = True
        self._smooth_window = 11
        self._recording_fft_max_frequency_hz: float | None = RECORDING_FFT_MAX_FREQUENCY_HZ
        self._started_at = 0.0
        self._sample_count = 0
        self._samples: list[dict[str, Any]] = []
        self._run_dir: Path | None = None
        self._raw_csv_path: Path | None = None
        self._smooth_csv_path: Path | None = None
        self._png_path: Path | None = None
        self._fft_csv_path: Path | None = None
        self._fft_png_path: Path | None = None
        self._meta_path: Path | None = None
        self._fft_error = ""
        self._csv_file = None
        self._writer: csv.writer | None = None
        self._result: dict[str, Any] = {}

    def start(
        self,
        config: dict[str, Any],
        serial_connected: bool,
        started_at: float | None = None,
        run_dir_override: Path | None = None,
        file_stem_override: str | None = None,
        group_name: str | None = None,
    ) -> dict[str, Any]:
        if not serial_connected:
            raise ValueError("Serial port is not connected.")

        folder_name = sanitize_run_name(str(config.get("folderName", "")))
        try:
            duration_s = float(config.get("durationSeconds", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Duration must be a number.") from exc

        if duration_s <= 0:
            raise ValueError("Duration must be greater than 0 seconds.")

        smooth = bool(config.get("smooth", True))
        show_raw = bool(config.get("showRaw", True))
        if not smooth and not show_raw:
            raise ValueError("At least one of smooth or raw plotting must be enabled.")

        smooth_window = normalize_smooth_window(config.get("smoothWindow", 11))
        recording_fft_max_frequency_hz = normalize_recording_fft_max_frequency(
            config.get("offlineFftMaxFrequencyHz", RECORDING_FFT_MAX_FREQUENCY_HZ)
        )
        file_stem = sanitize_run_name(file_stem_override) if file_stem_override else folder_name
        run_dir = run_dir_override if run_dir_override is not None else self.data_dir / folder_name
        raw_csv_path = run_dir / f"{file_stem}.csv"

        with self._lock:
            if self._active or self._saving:
                raise RuntimeError("A recording is already active.")
            if run_dir.exists():
                raise FileExistsError(f"Output folder already exists: {run_dir}")

            run_dir.mkdir(parents=True, exist_ok=False)
            self._reset_locked()
            self._active = True
            self._completed = False
            self._folder_name = folder_name if group_name is None else f"{group_name}/{file_stem}"
            self._duration_s = duration_s
            self._smooth = smooth
            self._show_raw = show_raw
            self._smooth_window = smooth_window
            self._recording_fft_max_frequency_hz = recording_fft_max_frequency_hz
            self._started_at = started_at if started_at is not None else time.monotonic()
            self._run_dir = run_dir
            self._raw_csv_path = raw_csv_path
            self._smooth_csv_path = run_dir / f"{file_stem}_smooth.csv" if smooth else None
            self._png_path = run_dir / f"{file_stem}.png"
            self._fft_csv_path = run_dir / f"{file_stem}_fft.csv"
            self._fft_png_path = run_dir / f"{file_stem}_fft.png"
            self._meta_path = run_dir / f"{file_stem}_meta.json"
            self._csv_file = raw_csv_path.open("w", encoding="utf-8", newline="")
            self._writer = csv.writer(self._csv_file)
            self._writer.writerow(RAW_CSV_HEADER)
            self._csv_file.flush()

            self._write_meta_locked(status="recording")
            return self.status()

    def append_sample(self, sample: dict[str, Any]) -> None:
        should_finish = False

        with self._lock:
            if not self._active or not self._writer or not self._csv_file:
                return

            elapsed_ms = (time.monotonic() - self._started_at) * 1000.0
            row = [
                f"{sample['arduinoTimeMs']:.0f}",
                f"{elapsed_ms:.3f}",
                sample["adc"],
                f"{sample['voltage']:.3f}",
            ]
            self._writer.writerow(row)
            self._sample_count += 1
            self._samples.append(
                {
                    "arduinoTimeMs": sample["arduinoTimeMs"],
                    "pcElapsedMs": elapsed_ms,
                    "adc": sample["adc"],
                    "voltage": sample["voltage"],
                }
            )

            if self._sample_count % 100 == 0:
                self._csv_file.flush()

            should_finish = elapsed_ms >= self._duration_s * 1000.0

        if should_finish:
            self.stop(reason="duration reached")

    def stop(self, reason: str = "manual stop") -> dict[str, Any]:
        with self._lock:
            if not self._active and not self._saving:
                return self.status()
            if self._saving:
                return self.status()

            self._active = False
            self._saving = True
            self._close_raw_csv_locked()

        try:
            self._finalize(reason)
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
                self._completed = False
        finally:
            with self._lock:
                self._saving = False
                self._write_meta_locked(status="completed" if self._completed else "error")
                return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed_s = 0.0
            if self._active or self._saving or self._completed:
                elapsed_s = time.monotonic() - self._started_at if self._started_at else 0.0
                elapsed_s = min(elapsed_s, self._duration_s) if self._duration_s else elapsed_s

            return {
                "active": self._active,
                "saving": self._saving,
                "completed": self._completed,
                "folderName": self._folder_name,
                "durationSeconds": self._duration_s,
                "elapsedSeconds": elapsed_s,
                "sampleCount": self._sample_count,
                "smooth": self._smooth,
                "showRaw": self._show_raw,
                "smoothWindow": self._smooth_window,
                "offlineFftMaxFrequencyHz": self._recording_fft_max_frequency_hz,
                "error": self._error,
                "outputDir": self._relative_path(self._run_dir),
                "csvPath": self._relative_path(self._raw_csv_path),
                "smoothCsvPath": self._relative_path(self._smooth_csv_path),
                "pngPath": self._relative_path(self._png_path),
                "fftCsvPath": self._relative_path(self._fft_csv_path),
                "fftPngPath": self._relative_path(self._fft_png_path),
                "metaPath": self._relative_path(self._meta_path),
                "result": self._result,
            }

    def _finalize(self, reason: str) -> None:
        with self._lock:
            if self._sample_count == 0:
                raise RuntimeError("No samples were recorded.")

            samples = list(self._samples)
            raw_csv_path = self._raw_csv_path
            smooth_csv_path = self._smooth_csv_path
            png_path = self._png_path
            fft_csv_path = self._fft_csv_path
            fft_png_path = self._fft_png_path
            smooth = self._smooth
            show_raw = self._show_raw
            smooth_window = self._smooth_window
            recording_fft_max_frequency_hz = self._recording_fft_max_frequency_hz

        if raw_csv_path is None or png_path is None or fft_csv_path is None or fft_png_path is None:
            raise RuntimeError("Recording paths were not initialized.")

        if smooth and smooth_csv_path is not None:
            self._write_smooth_csv(samples, smooth_csv_path, smooth_window)

        from DataVisualization import plot_voltage_csv

        plot_voltage_csv(
            csv_path=raw_csv_path,
            output_path=png_path,
            window_size=smooth_window,
            smooth=smooth,
            show_raw=show_raw,
        )

        fft_error = ""
        fft_result: dict[str, Any] = {
            "fftCsvPath": self._relative_path(fft_csv_path),
            "fftPngPath": self._relative_path(fft_png_path),
            "fftMaxFrequencyHz": recording_fft_max_frequency_hz,
        }
        try:
            analysis = analyze_voltage_fft(
                csv_path=raw_csv_path,
                output_csv_path=fft_csv_path,
                output_png_path=fft_png_path,
                max_frequency=recording_fft_max_frequency_hz,
            )
            fft_result.update(
                {
                    "fftTimeSource": analysis["timeSource"],
                    "fftSampleRateHz": float(analysis["sampleRateHz"]),
                    "fftNyquistHz": float(analysis["nyquistHz"]),
                    "fftSampleIntervalS": float(analysis["sampleIntervalS"]),
                    "fftPreprocess": analysis["preprocess"],
                    "fftBinCount": int(analysis["binCount"]),
                }
            )
        except Exception as exc:
            fft_error = str(exc)
            fft_result["fftError"] = fft_error

        with self._lock:
            self._completed = True
            self._active = False
            self._saving = False
            self._error = ""
            self._fft_error = fft_error
            self._result = {
                "reason": reason,
                "completedAt": datetime.now().isoformat(timespec="seconds"),
                "sampleCount": self._sample_count,
                "csvPath": self._relative_path(self._raw_csv_path),
                "smoothCsvPath": self._relative_path(self._smooth_csv_path),
                "pngPath": self._relative_path(self._png_path),
                **fft_result,
            }

    def _write_smooth_csv(self, samples: list[dict[str, Any]], output_path: Path, window_size: int) -> None:
        import numpy as np

        from DataVisualization import moving_average

        voltages = np.asarray([sample["voltage"] for sample in samples], dtype=float)
        smoothed = moving_average(voltages, window_size)

        with output_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(SMOOTH_CSV_HEADER)
            for sample, smooth_voltage in zip(samples, smoothed):
                writer.writerow(
                    [
                        f"{sample['arduinoTimeMs']:.0f}",
                        f"{sample['pcElapsedMs']:.3f}",
                        sample["adc"],
                        f"{sample['voltage']:.3f}",
                        f"{float(smooth_voltage):.6f}",
                    ]
                )

    def _write_meta_locked(self, status: str) -> None:
        if self._meta_path is None:
            return

        meta = {
            "status": status,
            "folderName": self._folder_name,
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "durationSeconds": self._duration_s,
            "sampleCount": self._sample_count,
            "smooth": self._smooth,
            "showRaw": self._show_raw,
            "smoothWindow": self._smooth_window,
            "rawHeader": RAW_CSV_HEADER,
            "smoothHeader": SMOOTH_CSV_HEADER if self._smooth else None,
            "fftMaxFrequencyHz": self._recording_fft_max_frequency_hz,
            "files": {
                "rawCsv": self._relative_path(self._raw_csv_path),
                "smoothCsv": self._relative_path(self._smooth_csv_path),
                "plot": self._relative_path(self._png_path),
                "fftCsv": self._relative_path(self._fft_csv_path),
                "fftPlot": self._relative_path(self._fft_png_path),
            },
            "error": self._error,
            "fftError": self._fft_error,
            "result": self._result,
        }

        self._meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _close_raw_csv_locked(self) -> None:
        if self._csv_file:
            self._csv_file.flush()
            self._csv_file.close()
        self._csv_file = None
        self._writer = None

    def _reset_locked(self) -> None:
        self._close_raw_csv_locked()
        self._active = False
        self._completed = False
        self._saving = False
        self._error = ""
        self._folder_name = ""
        self._duration_s = 0.0
        self._smooth = True
        self._show_raw = True
        self._smooth_window = 11
        self._recording_fft_max_frequency_hz = RECORDING_FFT_MAX_FREQUENCY_HZ
        self._started_at = 0.0
        self._sample_count = 0
        self._samples = []
        self._run_dir = None
        self._raw_csv_path = None
        self._smooth_csv_path = None
        self._png_path = None
        self._fft_csv_path = None
        self._fft_png_path = None
        self._meta_path = None
        self._fft_error = ""
        self._result = {}

    def _relative_path(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.relative_to(ROOT_DIR))
        except ValueError:
            return str(path)


class SerialMonitor:
    def __init__(
        self,
        port: str,
        baud_rate: int,
        recorder: RecordingManager | None = None,
        time_origin: float | None = None,
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.recorder = recorder
        self._samples: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER_SAMPLES)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = time_origin if time_origin is not None else time.monotonic()
        self._sequence = 0
        self._latest_error = ""
        self._connected = False
        self._total_samples = 0
        self._invalid_lines = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="serial-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        with self._lock:
            sample_rate = self._sample_rate_locked()
            latest = self._samples[-1] if self._samples else None
            return {
                "connected": self._connected,
                "port": self.port,
                "baudRate": self.baud_rate,
                "sampleRate": sample_rate,
                "totalSamples": self._total_samples,
                "invalidLines": self._invalid_lines,
                "latestError": self._latest_error,
                "latest": latest,
            }

    def samples_after(self, sequence: int) -> list[dict[str, Any]]:
        with self._lock:
            return [sample for sample in self._samples if sample["sequence"] > sequence]

    def recent_samples(self, seconds: float) -> list[dict[str, Any]]:
        cutoff_ms = (time.monotonic() - self._started_at - seconds) * 1000.0
        with self._lock:
            return [sample for sample in self._samples if sample["pcTimeMs"] >= cutoff_ms]

    def _run(self) -> None:
        try:
            import serial
        except ModuleNotFoundError:
            with self._lock:
                self._latest_error = "pyserial is not installed. Run: pip install pyserial"
            return

        while not self._stop_event.is_set():
            try:
                with serial.Serial(self.port, self.baud_rate, timeout=1) as ser:
                    time.sleep(2)
                    ser.reset_input_buffer()
                    with self._lock:
                        self._connected = True
                        self._latest_error = ""

                    while not self._stop_event.is_set():
                        raw_line = ser.readline()
                        if not raw_line:
                            continue

                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        sample_parts = parse_sample_line(line)
                        if sample_parts is None:
                            with self._lock:
                                self._invalid_lines += 1
                            continue

                        self._append_sample(sample_parts)

            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._latest_error = str(exc)
                self._stop_event.wait(2)

    def _append_sample(self, sample_parts: list[str]) -> None:
        now_ms = (time.monotonic() - self._started_at) * 1000.0
        arduino_time_ms = float(sample_parts[0])
        adc = int(sample_parts[1])
        voltage = float(sample_parts[2])

        sample = {
            "sequence": 0,
            "arduinoTimeMs": arduino_time_ms,
            "pcTimeMs": now_ms,
            "adc": adc,
            "voltage": voltage,
        }

        with self._lock:
            self._sequence += 1
            self._total_samples += 1
            sample["sequence"] = self._sequence
            self._samples.append(sample)

        if self.recorder:
            self.recorder.append_sample(sample)

    def _sample_rate_locked(self) -> float:
        if len(self._samples) < 2:
            return 0.0

        latest_ms = self._samples[-1]["pcTimeMs"]
        cutoff_ms = latest_ms - 1000.0
        count = sum(1 for sample in reversed(self._samples) if sample["pcTimeMs"] >= cutoff_ms)
        return float(count)


class RealtimeFftManager:
    def __init__(
        self,
        window_seconds: float = FFT_WINDOW_SECONDS,
        max_frequency_hz: float = FFT_MAX_FREQUENCY_HZ,
        update_interval_seconds: float = FFT_UPDATE_INTERVAL_SECONDS,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_frequency_hz = max_frequency_hz
        self.update_interval_seconds = update_interval_seconds
        self._last_update = 0.0
        self._cached: dict[str, Any] = self._empty_result("Waiting for enough samples")

    def update_config(self, window_seconds: Any = None, max_frequency_hz: Any = None) -> dict[str, Any]:
        if window_seconds is not None:
            self.window_seconds = self._clamp_float(window_seconds, minimum=5.0, maximum=MAX_BUFFER_SECONDS)
        if max_frequency_hz is not None:
            self.max_frequency_hz = self._clamp_float(max_frequency_hz, minimum=0.1, maximum=100.0)

        self._last_update = 0.0
        self._cached = self._empty_result("Waiting for enough samples")
        return {
            "windowSeconds": self.window_seconds,
            "maxFrequencyHz": self.max_frequency_hz,
        }

    def maybe_compute(self, samples: list[dict[str, Any]]) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._last_update < self.update_interval_seconds:
            return self._cached

        self._last_update = now
        self._cached = self._compute(samples)
        return self._cached

    def _compute(self, samples: list[dict[str, Any]]) -> dict[str, Any]:
        if len(samples) < FFT_MIN_SAMPLES:
            return self._empty_result("Waiting for enough samples")

        time_ms = [sample["arduinoTimeMs"] for sample in samples]
        voltage_v = [sample["voltage"] for sample in samples]

        unique_time_ms: list[float] = []
        unique_voltage_v: list[float] = []
        last_time = None
        for sample_time, voltage in zip(time_ms, voltage_v):
            if last_time is None or sample_time > last_time:
                unique_time_ms.append(float(sample_time))
                unique_voltage_v.append(float(voltage))
                last_time = sample_time

        if len(unique_time_ms) < FFT_MIN_SAMPLES:
            return self._empty_result("Waiting for strictly increasing samples")

        time_s = np_array(unique_time_ms) / 1000.0
        voltage = np_array(unique_voltage_v)

        try:
            result = compute_voltage_fft(time_s, voltage, max_frequency=self.max_frequency_hz)
        except ValueError as exc:
            return self._empty_result(str(exc))

        frequency_hz = result["frequency_hz"]
        amplitude_v = result["amplitude_v"]

        return {
            "ready": True,
            "message": "",
            "windowSeconds": self.window_seconds,
            "maxFrequencyHz": self.max_frequency_hz,
            "frequencyHz": [round(float(value), 6) for value in frequency_hz],
            "amplitudeV": [float(value) for value in amplitude_v],
            "sampleRateHz": float(result["sample_rate_hz"]),
            "nyquistHz": float(result["nyquist_hz"]),
            "preprocess": result["preprocess"],
            "sampleCount": len(unique_time_ms),
            "updatedAt": time.time(),
        }

    def _empty_result(self, message: str) -> dict[str, Any]:
        return {
            "ready": False,
            "message": message,
            "windowSeconds": self.window_seconds,
            "maxFrequencyHz": self.max_frequency_hz,
            "frequencyHz": [],
            "amplitudeV": [],
            "sampleRateHz": 0.0,
            "nyquistHz": 0.0,
            "preprocess": "demean + hann",
            "sampleCount": 0,
            "updatedAt": time.time(),
        }

    def _clamp_float(self, value: Any, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = minimum
        return max(minimum, min(parsed, maximum))


def np_array(values: list[float]):
    import numpy as np

    return np.asarray(values, dtype=float)


class MonitorChannel:
    def __init__(self, channel_id: str, port: str, baud_rate: int, data_dir: Path, time_origin: float) -> None:
        self.id = channel_id
        self.port = port
        self.baud_rate = baud_rate
        self.recorder = RecordingManager(data_dir)
        self.monitor = SerialMonitor(port=port, baud_rate=baud_rate, recorder=self.recorder, time_origin=time_origin)
        self.fft_manager = RealtimeFftManager()

    def start(self) -> None:
        self.monitor.start()

    def stop(self, reason: str = "server shutdown") -> None:
        self.recorder.stop(reason=reason)
        self.monitor.stop()

    def payload(self, samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        fft_samples = self.monitor.recent_samples(self.fft_manager.window_seconds)
        return {
            "id": self.id,
            "port": self.port,
            "baudRate": self.baud_rate,
            "status": self.monitor.status(),
            "recording": self.recorder.status(),
            "samples": samples if samples is not None else [],
            "fft": self.fft_manager.maybe_compute(fft_samples),
        }


class ChannelManager:
    def __init__(self, ports: list[str], baud_rate: int, data_dir: Path) -> None:
        unique_ports = self._unique_ports(ports)
        self.data_dir = data_dir
        self.time_origin = time.monotonic()
        self.channels = {
            port: MonitorChannel(
                channel_id=port,
                port=port,
                baud_rate=baud_rate,
                data_dir=data_dir,
                time_origin=self.time_origin,
            )
            for port in unique_ports
        }
        self.primary_id = unique_ports[0]

    def start(self) -> None:
        for channel in self.channels.values():
            channel.start()

    def stop(self, reason: str = "server shutdown") -> None:
        for channel in self.channels.values():
            channel.stop(reason=reason)

    def primary(self) -> MonitorChannel:
        return self.channels[self.primary_id]

    def get(self, channel_id: str | None) -> MonitorChannel:
        if not channel_id:
            return self.primary()
        try:
            return self.channels[channel_id]
        except KeyError as exc:
            raise ValueError(f"Unknown channel: {channel_id}") from exc

    def payload(self) -> dict[str, Any]:
        return {channel_id: channel.payload() for channel_id, channel in self.channels.items()}

    def start_recording(self, config: dict[str, Any]) -> dict[str, Any]:
        channel_ids = self._resolve_channel_ids(config.get("channelIds"))
        if len(channel_ids) == 1:
            channel = self.get(channel_ids[0])
            return {
                "multi": False,
                "channelId": channel.id,
                "primaryChannelId": channel.id,
                "recording": channel.recorder.start(
                    config=config,
                    serial_connected=channel.monitor.status()["connected"],
                ),
            }

        folder_name = sanitize_run_name(str(config.get("folderName", "")))
        group_dir = self.data_dir / folder_name
        if group_dir.exists():
            raise FileExistsError(f"Output folder already exists: {group_dir}")

        for channel_id in channel_ids:
            channel = self.get(channel_id)
            if not channel.monitor.status()["connected"]:
                raise ValueError(f"Serial port is not connected: {channel_id}")
            recording = channel.recorder.status()
            if recording["active"] or recording["saving"]:
                raise RuntimeError(f"A recording is already active on {channel_id}.")

        started_at = time.monotonic()
        statuses: dict[str, Any] = {}
        started_channels: list[MonitorChannel] = []
        try:
            for channel_id in channel_ids:
                channel = self.get(channel_id)
                channel_stem = sanitize_run_name(channel_id)
                statuses[channel_id] = channel.recorder.start(
                    config={**config, "folderName": channel_stem},
                    serial_connected=True,
                    started_at=started_at,
                    run_dir_override=group_dir / channel_stem,
                    file_stem_override=channel_stem,
                    group_name=folder_name,
                )
                started_channels.append(channel)
        except Exception:
            for channel in started_channels:
                channel.recorder.stop(reason="multi-channel start rollback")
            raise

        return {
            "multi": True,
            "folderName": folder_name,
            "startedAt": started_at,
            "channelIds": channel_ids,
            "channels": statuses,
        }

    def stop_recording(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        channel_ids = self._resolve_channel_ids((config or {}).get("channelIds"))
        if len(channel_ids) == 1:
            channel = self.get(channel_ids[0])
            return {
                "multi": False,
                "channelId": channel.id,
                "primaryChannelId": channel.id,
                "recording": channel.recorder.stop(reason="manual stop"),
            }

        return {
            "multi": True,
            "channelIds": channel_ids,
            "channels": {
                channel_id: self.get(channel_id).recorder.stop(reason="manual stop") for channel_id in channel_ids
            },
        }

    def _resolve_channel_ids(self, requested: Any) -> list[str]:
        if requested == "all":
            return list(self.channels.keys())
        if isinstance(requested, list):
            channel_ids = []
            for channel_id in requested:
                normalized = str(channel_id)
                if normalized not in self.channels:
                    raise ValueError(f"Unknown channel: {normalized}")
                channel_ids.append(normalized)
        else:
            channel_ids = []

        if not channel_ids:
            channel_ids = [self.primary_id]

        return self._unique_ports(channel_ids)

    @staticmethod
    def _unique_ports(ports: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for port in ports:
            normalized = str(port).strip()
            if not normalized or normalized in seen:
                continue
            unique.append(normalized)
            seen.add(normalized)

        if not unique:
            unique.append(DEFAULT_SERIAL_PORT)
        return unique


def create_app(channels: ChannelManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        channels.start()
        try:
            yield
        finally:
            channels.stop(reason="server shutdown")

    app = FastAPI(title="VCAP Live Monitor", lifespan=lifespan)

    @app.get("/")
    async def index() -> FileResponse:
        response = FileResponse(WEB_DIR / "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        primary_payload = channels.primary().payload()
        return {
            "status": primary_payload["status"],
            "recording": primary_payload["recording"],
            "fft": primary_payload["fft"],
            "channels": channels.payload(),
        }

    @app.get("/api/samples")
    async def api_samples(seconds: float = 60.0) -> dict[str, Any]:
        channel_payloads: dict[str, Any] = {}
        for channel_id, channel in channels.channels.items():
            samples = channel.monitor.recent_samples(seconds)
            channel_payloads[channel_id] = channel.payload(samples=samples)

        primary_payload = channel_payloads[channels.primary_id]
        return {
            "status": primary_payload["status"],
            "recording": primary_payload["recording"],
            "samples": primary_payload["samples"],
            "fft": primary_payload["fft"],
            "channels": channel_payloads,
        }

    @app.get("/api/channels")
    async def api_channels() -> dict[str, Any]:
        return {
            "primaryChannelId": channels.primary_id,
            "channels": channels.payload(),
        }

    @app.get("/api/recording/status")
    async def recording_status(channel: str | None = None) -> dict[str, Any]:
        try:
            return channels.get(channel).recorder.status()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/recording/start")
    async def recording_start(config: dict[str, Any]) -> dict[str, Any]:
        try:
            if config.get("channelIds"):
                return channels.start_recording(config)

            channel = channels.get(config.get("channelId") or config.get("channel"))
            return channel.recorder.start(config=config, serial_connected=channel.monitor.status()["connected"])
        except (ValueError, FileExistsError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/recording/stop")
    async def recording_stop(config: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            if config and config.get("channelIds"):
                return channels.stop_recording(config)

            channel_id = (config or {}).get("channelId") or (config or {}).get("channel")
            return channels.get(channel_id).recorder.stop(reason="manual stop")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        last_sequences = {channel_id: 0 for channel_id in channels.channels}

        try:
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=0.001)
                    data = json.loads(message)
                    if data.get("type") == "fft_config":
                        channel = channels.get(data.get("channelId") or data.get("channel"))
                        channel.fft_manager.update_config(
                            window_seconds=data.get("windowSeconds"),
                            max_frequency_hz=data.get("maxFrequencyHz"),
                        )
                    elif data.get("type") == "fft_config_all":
                        for channel in channels.channels.values():
                            channel.fft_manager.update_config(
                                window_seconds=data.get("windowSeconds"),
                                max_frequency_hz=data.get("maxFrequencyHz"),
                            )
                except asyncio.TimeoutError:
                    pass
                except (json.JSONDecodeError, ValueError):
                    pass

                channel_payloads: dict[str, Any] = {}
                for channel_id, channel in channels.channels.items():
                    samples = channel.monitor.samples_after(last_sequences.get(channel_id, 0))
                    if samples:
                        last_sequences[channel_id] = samples[-1]["sequence"]
                    channel_payloads[channel_id] = channel.payload(samples=samples)

                primary_payload = channel_payloads[channels.primary_id]

                await websocket.send_text(
                    json.dumps(
                        {
                            "primaryChannelId": channels.primary_id,
                            "channels": channel_payloads,
                            "status": primary_payload["status"],
                            "recording": primary_payload["recording"],
                            "samples": primary_payload["samples"],
                            "fft": primary_payload["fft"],
                        }
                    )
                )
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            return

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


default_channels = ChannelManager(ports=[DEFAULT_SERIAL_PORT], baud_rate=DEFAULT_BAUD_RATE, data_dir=DATA_DIR)
app = create_app(default_channels)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local VCAP live monitor.")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Arduino serial port.")
    parser.add_argument(
        "--serial-ports",
        nargs="+",
        help="One or more Arduino serial ports, for example: --serial-ports COM3 COM4",
    )
    parser.add_argument("--baud-rate", type=int, default=DEFAULT_BAUD_RATE, help="Arduino serial baud rate.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP host.")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serial_ports = args.serial_ports if args.serial_ports else [args.serial_port]
    channels = ChannelManager(ports=serial_ports, baud_rate=args.baud_rate, data_dir=DATA_DIR)
    app = create_app(channels)

    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError("uvicorn is not installed. Run: pip install fastapi uvicorn") from exc

    print(f"Live monitor: http://{args.host}:{args.http_port}")
    print(f"Serial inputs: {', '.join(channels.channels)} @ {args.baud_rate} baud")
    uvicorn.run(app, host=args.host, port=args.http_port)


if __name__ == "__main__":
    main()
