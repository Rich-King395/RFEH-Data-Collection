# Arduino VCAP Data Logger

This project records and monitors VCAP voltage samples from an Arduino UNO.

## Project Flow

1. The Arduino runs continuously after power-on.
2. The Arduino samples `A0` and sends CSV-formatted data over serial.
3. `ArduinoSerial.py` records serial data for a user-provided duration.
4. Recorded data is saved under `Data/<name>/`.
5. `DataVisualization.py` generates voltage-time plots from recorded CSV files.
6. `LiveServer.py` streams serial data to a local web page for live monitoring.

## Files

### `Arduino/Arduino.ino`

Arduino CLI sketch entry point. It delegates to the VCAP sampler functions.

### `Arduino/VCAP.ino`

Arduino-side sampler for Arduino UNO.

- Reads analog input from `A0`.
- Uses the internal `1.1 V` ADC reference.
- Sends data over serial at `115200` baud.
- Does not write to an SD card.
- Does not wait for `START` or `STOP`; it streams data continuously.

Serial output format:

```csv
Time(ms),ADC,Voltage(V)
10,143,0.154
20,144,0.155
```

### `ArduinoSerial.py`

Computer-side recording script.

- Prompts for recording duration in seconds.
- Prompts for a file name.
- Saves the CSV file to `Data/<name>/<name>.csv`.
- Calls `DataVisualization.py` after recording.
- Saves the plot to `Data/<name>/<name>.png`.

Run:

```powershell
conda activate UBSS
python ArduinoSerial.py
```

### `LiveServer.py`

Local web live monitor.

- Opens the Arduino serial port.
- Reads the same CSV-formatted serial data as `ArduinoSerial.py`.
- Streams live samples to the browser through WebSocket.
- Serves an English web interface from `web/`.
- Does not save CSV files in this first prototype.

Run:

```powershell
conda activate UBSS
python LiveServer.py
```

Then open:

```text
http://127.0.0.1:8000
```

Optional arguments:

```powershell
python LiveServer.py --serial-port COM4 --baud-rate 115200 --http-port 8001
```

### `serial_utils.py`

Shared helpers for serial CSV parsing and safe experiment names.

### `web/`

Browser UI for the live monitor.

- `index.html`: page structure.
- `style.css`: layout and visual style.
- `app.js`: WebSocket client and live Canvas chart.

### `DataVisualization.py`

CSV plotting utility.

- Reads `Time(ms)` and `Voltage(V)` from a recorded CSV.
- Normalizes the plot time axis to the beginning of the recording.
- Draws raw voltage and moving-average voltage.
- Supports turning smoothing and raw-line drawing on or off.
- Can be used from `ArduinoSerial.py` or run directly.

Run directly:

```powershell
conda activate UBSS
python DataVisualization.py Data\TEST\TEST.csv
```

## Python Dependencies

```powershell
conda activate UBSS
pip install -r requirements.txt
```

## Arduino UNO Commands

Compile:

```powershell
arduino-cli compile --fqbn arduino:avr:uno .\Arduino
```

Upload:

```powershell
arduino-cli upload --fqbn arduino:avr:uno -p COM3 .\Arduino
```

If the Arduino is on a different port, check it with:

```powershell
arduino-cli board list
```

## Notes

- `COM3` and `115200` are the default serial settings.
- Close any serial monitor before running `ArduinoSerial.py` or `LiveServer.py`; only one program can use the serial port at a time.
- Do not run `ArduinoSerial.py` and `LiveServer.py` at the same time unless they use different serial ports.
- Existing old CSV/PNG files directly under `Data/` are previous experiment outputs. New recordings are stored in subfolders.
