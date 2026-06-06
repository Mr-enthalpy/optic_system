from __future__ import annotations

import json
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
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def index_string(values: list[str], index: int) -> str:
    if index < 0 or index >= len(values):
        raise ValueError(f"index {index} out of range")
    return values[index]
