from __future__ import annotations

import re


CSV_HEADER = ["Time(ms)", "ADC", "Voltage(V)"]


def sanitize_run_name(name: str) -> str:
    name = name.strip()
    if name.lower().endswith(".csv"):
        name = name[:-4]

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise ValueError("File name cannot be empty.")

    return name


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
