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
