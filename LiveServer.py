from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from serial_utils import parse_sample_line


DEFAULT_SERIAL_PORT = "COM3"
DEFAULT_BAUD_RATE = 115200
DEFAULT_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
MAX_BUFFER_SECONDS = 300
MAX_EXPECTED_SAMPLE_RATE = 500
MAX_BUFFER_SAMPLES = MAX_BUFFER_SECONDS * MAX_EXPECTED_SAMPLE_RATE

ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"


class SerialMonitor:
    def __init__(self, port: str, baud_rate: int) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self._samples: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER_SAMPLES)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = time.monotonic()
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

        with self._lock:
            self._sequence += 1
            self._total_samples += 1
            self._samples.append(
                {
                    "sequence": self._sequence,
                    "arduinoTimeMs": arduino_time_ms,
                    "pcTimeMs": now_ms,
                    "adc": adc,
                    "voltage": voltage,
                }
            )

    def _sample_rate_locked(self) -> float:
        if len(self._samples) < 2:
            return 0.0

        latest_ms = self._samples[-1]["pcTimeMs"]
        cutoff_ms = latest_ms - 1000.0
        count = sum(1 for sample in reversed(self._samples) if sample["pcTimeMs"] >= cutoff_ms)
        return float(count)


def create_app(monitor: SerialMonitor) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        monitor.start()
        try:
            yield
        finally:
            monitor.stop()

    app = FastAPI(title="VCAP Live Monitor", lifespan=lifespan)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return monitor.status()

    @app.get("/api/samples")
    async def api_samples(seconds: float = 60.0) -> dict[str, Any]:
        return {
            "status": monitor.status(),
            "samples": monitor.recent_samples(seconds),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        last_sequence = 0

        try:
            while True:
                samples = monitor.samples_after(last_sequence)
                if samples:
                    last_sequence = samples[-1]["sequence"]

                await websocket.send_text(
                    json.dumps(
                        {
                            "status": monitor.status(),
                            "samples": samples,
                        }
                    )
                )
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            return

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


default_monitor = SerialMonitor(port=DEFAULT_SERIAL_PORT, baud_rate=DEFAULT_BAUD_RATE)
app = create_app(default_monitor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local VCAP live monitor.")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Arduino serial port.")
    parser.add_argument("--baud-rate", type=int, default=DEFAULT_BAUD_RATE, help="Arduino serial baud rate.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP host.")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    monitor = SerialMonitor(port=args.serial_port, baud_rate=args.baud_rate)
    app = create_app(monitor)

    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError("uvicorn is not installed. Run: pip install fastapi uvicorn") from exc

    print(f"Live monitor: http://{args.host}:{args.http_port}")
    print(f"Serial input: {args.serial_port} @ {args.baud_rate} baud")
    uvicorn.run(app, host=args.host, port=args.http_port)


if __name__ == "__main__":
    main()
