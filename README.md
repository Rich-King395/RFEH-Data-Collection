# Arduino VCAP Data Logger

This project records voltage samples from an Arduino UNO and saves the results on the computer.

## Project Flow

1. The Arduino runs continuously after power-on.
2. The Arduino samples `A0` every 10 ms and sends CSV-formatted data over serial.
3. `ArduinoSerial.py` records serial data for a user-provided duration.
4. Recorded data is saved under `Data/<name>/`.
5. `DataVisualization.py` generates a voltage-time plot from the recorded CSV.

## Files

### `Arduino/Arduino.ino`

Arduino CLI sketch entry point. It delegates to the VCAP sampler functions.

### `Arduino/VCAP.ino`

Arduino-side sampler for Arduino UNO.

- Reads analog input from `A0`.
- Converts ADC readings to voltage using a 5 V reference.
- Sends data over serial at `115200` baud.
- Does not write to an SD card.
- Does not wait for `START` or `STOP`; it streams data continuously.

Serial output format:

```csv
Time(ms),ADC,Voltage(V)
10,143,0.699
20,144,0.704
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

Python dependencies:

```powershell
conda activate UBSS
pip install pyserial numpy matplotlib
```

### `DataVisualization.py`

CSV plotting utility.

- Reads `Time(ms)` and `Voltage(V)` from a recorded CSV.
- Normalizes the plot time axis to the beginning of the recording.
- Draws raw voltage and moving-average voltage.
- Can be used from `ArduinoSerial.py` or run directly.

Run directly:

```powershell
conda activate UBSS
python DataVisualization.py Data\TEST\TEST.csv
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

- `COM3` and `115200` are configured in `ArduinoSerial.py`.
- Close any serial monitor before running `ArduinoSerial.py`; only one program can use the serial port at a time.
- Existing old CSV/PNG files directly under `Data/` are previous experiment outputs. New recordings are stored in subfolders.
