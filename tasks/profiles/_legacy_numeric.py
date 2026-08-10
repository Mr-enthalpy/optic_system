"""Numeric conversions used by the historical v1 profile loaders."""

from decimal import Decimal
import math
from typing import Any


def legacy_binary64(value: Any, name: str, error_type: type[Exception]) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise error_type(f"{name} must be numeric, got {value!r}") from None
    if not math.isfinite(result):
        raise error_type(f"{name} must be finite")
    if isinstance(value, Decimal) and value != 0 and result == 0:
        raise error_type(f"{name} is outside the binary64 range")
    return result


def legacy_int(value: Any, name: str, error_type: type[Exception]) -> int:
    try:
        if isinstance(value, Decimal):
            return int(legacy_binary64(value, name, error_type))
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise error_type(f"{name} must be integer-compatible, got {value!r}") from None
