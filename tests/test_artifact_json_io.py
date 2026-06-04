from __future__ import annotations

import json

import h5py

from tasks.artifacts.json_io import canonical_json, decode_h5_string, read_json_dataset_or_attr, write_json_dataset


def test_read_json_dataset_or_attr_handles_bytes_and_strings(tmp_path):
    path = tmp_path / "json.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("metadata")
        group.create_dataset("manifest_json", data=json.dumps({"a": 1}))
        group.attrs["policy_json"] = json.dumps({"b": 2})

    with h5py.File(path, "r") as f:
        group = f["metadata"]
        assert read_json_dataset_or_attr(group, "manifest_json") == {"a": 1}
        assert read_json_dataset_or_attr(group, "policy_json") == {"b": 2}


def test_write_json_dataset_and_canonical_json(tmp_path):
    path = tmp_path / "json_write.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("metadata")
        write_json_dataset(group, "manifest_json", {"b": 2, "a": 1})

    with h5py.File(path, "r") as f:
        assert read_json_dataset_or_attr(f["metadata"], "manifest_json") == {"a": 1, "b": 2}

    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert decode_h5_string(b"abc") == "abc"
