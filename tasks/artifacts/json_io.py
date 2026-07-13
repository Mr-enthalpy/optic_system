from __future__ import annotations

import json
import math
from typing import Any, Mapping

import h5py
import numpy as np


def decode_h5_string(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.bytes_):
        return value.tobytes().decode("utf-8")
    if isinstance(value, np.ndarray):
        if value.shape == ():
            return decode_h5_string(value[()])
        if value.size == 1:
            return decode_h5_string(value.flat[0])
    return str(value)


def json_dumps_stable(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def wavelengths_from_json(values: list[Any]) -> list[float]:
    """Decode JSON wavelength values, mapping broadband ``null`` to NaN."""
    return [float("nan") if value is None else float(value) for value in values]


def wavelengths_to_json(values: list[Any]) -> list[float | None]:
    """Encode finite wavelengths and the broadband NaN sentinel as strict JSON."""
    result: list[float | None] = []
    for index, value in enumerate(values):
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"wavelengths[{index}] must be a number")
        number = float(value)
        if math.isnan(number):
            result.append(None)
        elif math.isfinite(number):
            result.append(number)
        else:
            raise ValueError(f"wavelengths[{index}] must be finite or broadband NaN")
    return result


def read_json_dataset_or_attr(group: h5py.Group, name: str) -> dict[str, Any]:
    if name in group:
        text = decode_h5_string(group[name][()])
        data = json.loads(text) if text else {}
    elif name in group.attrs:
        text = decode_h5_string(group.attrs[name])
        data = json.loads(text) if text else {}
    else:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} must decode to a JSON object")
    return data


def write_json_dataset(group: h5py.Group, name: str, data: Mapping[str, Any]) -> None:
    string_dtype = h5py.string_dtype(encoding="utf-8")
    if name in group:
        del group[name]
    group.create_dataset(name, data=json_dumps_stable(dict(data)), dtype=string_dtype)


def h5_string_dtype() -> h5py.Datatype:
    return h5py.string_dtype(encoding="utf-8")


def read_scalar_string(dataset: h5py.Dataset) -> str:
    value = dataset[()]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray):
        value = value.flat[0]
        if isinstance(value, bytes):
            return value.decode("utf-8")
    return str(value)


def read_optional_dataset_string(src: h5py.File, path: str) -> str | None:
    if path not in src:
        return None
    value = read_scalar_string(src[path]).strip()
    return value or None


def read_string_array(dataset: h5py.Dataset) -> list[str]:
    values = dataset[()]
    result: list[str] = []
    for value in values:
        result.append(value.decode("utf-8") if isinstance(value, bytes) else str(value))
    return result


def loads_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def unique_preserve_order(values: list[Any]) -> list[Any]:
    """Return order-preserving unique values with stable NaN equivalence."""
    result: list[Any] = []
    for value in values:
        if not any(_json_value_equal(value, existing) for existing in result):
            result.append(value)
    return result


def sequence_equal_nan_aware(left: list[Any], right: list[Any]) -> bool:
    """Compare serialized-value sequences while treating two NaN sentinels equal."""
    return len(left) == len(right) and all(
        _json_value_equal(first, second)
        for first, second in zip(left, right, strict=True)
    )


def _json_value_equal(first: Any, second: Any) -> bool:
    if _is_nan_scalar(first) and _is_nan_scalar(second):
        return True
    try:
        equal = first == second
    except (TypeError, ValueError):
        return False
    return bool(equal) if isinstance(equal, (bool, np.bool_)) else False


def _is_nan_scalar(value: Any) -> bool:
    return isinstance(value, (float, np.floating)) and math.isnan(float(value))


def index_string(values: list[str], index: int) -> str:
    if index < 0 or index >= len(values):
        raise ValueError(f"index {index} out of range")
    return values[index]
