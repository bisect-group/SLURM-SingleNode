from __future__ import annotations

import re
from typing import Any


_MEM_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([kmgt]i?b?|b)?$", re.IGNORECASE)
_DUR_RE = re.compile(r"^([0-9]+)([smhd])$", re.IGNORECASE)


def memory_to_mb(value: Any) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        raise ValueError("memory value is required")
    text = str(value).strip()
    match = _MEM_RE.match(text)
    if not match:
        raise ValueError(f"invalid memory value: {value!r}")
    number = float(match.group(1))
    unit = (match.group(2) or "mb").lower()
    if unit in {"b"}:
        return int(number / 1024 / 1024)
    if unit in {"k", "kb", "kib"}:
        return int(number / 1024)
    if unit in {"m", "mb", "mib"}:
        return int(number)
    if unit in {"g", "gb", "gib"}:
        return int(number * 1024)
    if unit in {"t", "tb", "tib"}:
        return int(number * 1024 * 1024)
    raise ValueError(f"invalid memory unit: {unit}")


def duration_to_seconds(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if "-" in text or ":" in text:
        days = 0
        if "-" in text:
            days_s, text = text.split("-", 1)
            days = int(days_s)
        parts = [int(p) for p in text.split(":")]
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = 0, parts[0], parts[1]
        else:
            raise ValueError(f"invalid Slurm duration: {value!r}")
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    match = _DUR_RE.match(text)
    if not match:
        raise ValueError(f"invalid duration: {value!r}")
    number = int(match.group(1))
    unit = match.group(2).lower()
    return number * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def seconds_to_slurm(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def normalize_memory(value: Any) -> str:
    return f"{memory_to_mb(value)}MB"


def normalize_duration(value: Any) -> str:
    return seconds_to_slurm(duration_to_seconds(value))
