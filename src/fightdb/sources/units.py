"""Parsing helpers that turn source strings into normalised units (cm, seconds, dates)."""

from __future__ import annotations

import re
from datetime import date, datetime

INCH_CM = 2.54

DIVISIONS = (
    "flyweight",
    "bantamweight",
    "featherweight",
    "lightweight",
    "welterweight",
    "middleweight",
    "light_heavyweight",
    "heavyweight",
    "w_strawweight",
    "w_flyweight",
    "w_bantamweight",
    "w_featherweight",
    "catchweight",
    "openweight",
)

# Order matters: "light heavyweight" must match before "heavyweight".
_WEIGHT_KEYWORDS = (
    ("light heavyweight", "light_heavyweight"),
    ("heavyweight", "heavyweight"),
    ("strawweight", "strawweight"),
    ("flyweight", "flyweight"),
    ("bantamweight", "bantamweight"),
    ("featherweight", "featherweight"),
    ("lightweight", "lightweight"),
    ("welterweight", "welterweight"),
    ("middleweight", "middleweight"),
    ("catch", "catchweight"),
    ("open", "openweight"),
    ("super heavyweight", "heavyweight"),
)


def null_if_dash(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    return None if v in {"", "--", "---", "nan", "NaN", "None"} else v


def inches_to_cm(inches: float | int | None) -> float | None:
    if inches is None:
        return None
    return round(float(inches) * INCH_CM, 1)


def parse_height(value: str | None) -> float | None:
    """`5' 11"` -> 180.3 cm."""
    v = null_if_dash(value)
    if v is None:
        return None
    m = re.match(r"(\d+)'\s*(\d+(?:\.\d+)?)?", v)
    if not m:
        return None
    inches = int(m[1]) * 12 + float(m[2] or 0)
    return inches_to_cm(inches)


def parse_reach(value: str | None) -> float | None:
    """`72"` or `72.0"` -> 182.9 cm."""
    v = null_if_dash(value)
    if v is None:
        return None
    m = re.match(r"(\d+(?:\.\d+)?)", v)
    return inches_to_cm(float(m[1])) if m else None


def parse_x_of_y(value: str | None) -> tuple[int | None, int | None]:
    """`24 of 47` -> (24, 47)."""
    v = null_if_dash(value)
    if v is None:
        return (None, None)
    m = re.match(r"(\d+)\s+of\s+(\d+)", v)
    return (int(m[1]), int(m[2])) if m else (None, None)


def parse_mmss(value: str | None) -> int | None:
    """`4:54` -> 294 seconds."""
    v = null_if_dash(value)
    if v is None:
        return None
    m = re.match(r"(\d+):(\d{1,2})$", v)
    return int(m[1]) * 60 + int(m[2]) if m else None


def parse_int(value: object) -> int | None:
    v = null_if_dash(None if value is None else str(value))
    if v is None:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    v = null_if_dash(value)
    if v is None:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def round_lengths(time_format: str | None) -> list[int] | None:
    """Minutes per round from e.g. `3 Rnd (5-5-5)` or `1 Rnd + 2OT (15-3-3)`."""
    v = null_if_dash(time_format)
    if v is None:
        return None
    m = re.search(r"\(([\d\-]+)\)", v)
    if not m:
        return None
    return [int(x) for x in m[1].split("-") if x]


def fight_seconds(
    end_round: int | None, end_time_s: int | None, time_format: str | None
) -> int | None:
    """Total elapsed fight time. Falls back to 5-minute rounds when the format is unknown."""
    if end_round is None or end_time_s is None:
        return None
    lengths = round_lengths(time_format) or []
    full = (
        sum(lengths[: end_round - 1]) * 60
        if len(lengths) >= end_round - 1
        else (end_round - 1) * 300
    )
    return full + end_time_s


def scheduled_rounds(time_format: str | None) -> int | None:
    v = null_if_dash(time_format)
    if v is None:
        return None
    m = re.match(r"(\d+)\s+Rnd", v)
    return int(m[1]) if m else None


def division_from_weight_class(weight_class: str | None) -> tuple[str | None, str | None]:
    """Map any source's weight-class label to (division, sex).

    Handles "UFC Women's Strawweight Title Bout", "Lightweight Bout", "W Flyweight",
    "Catch Weight Bout", "Ultimate Fighter 29 Middleweight Tournament Title Bout", ...
    """
    v = null_if_dash(weight_class)
    if v is None:
        return (None, None)
    low = v.lower()
    female = "women" in low or low.startswith("w ") or low.startswith("w.")
    for keyword, base in _WEIGHT_KEYWORDS:
        if keyword in low:
            if base in ("catchweight", "openweight"):
                return (base, "F" if female else None)
            if base == "strawweight":
                return ("w_strawweight", "F")
            if female:
                return (f"w_{base}", "F")
            return (base, "M")
    return (None, "F" if female else None)


def is_title_bout(weight_class: str | None) -> bool:
    v = (weight_class or "").lower()
    return "title" in v and "tournament" not in v


def method_group(method: str | None) -> str | None:
    v = (null_if_dash(method) or "").lower()
    if not v:
        return None
    if v.startswith("ko") or v.startswith("tko"):
        return "ko_tko"
    if v.startswith("sub"):
        return "sub"
    if v.startswith("dec"):
        return "dec"
    if v.startswith("dq"):
        return "dq"
    if "no contest" in v or v.startswith("overturned"):
        return "nc"
    return "other"


def american_to_prob(odds: int | None) -> float | None:
    if odds is None or odds == 0:
        return None
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)
