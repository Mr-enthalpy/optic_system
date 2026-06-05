from __future__ import annotations

import json

import h5py

from tasks.artifacts.json_io import canonical_json, decode_h5_string, read_json_dataset_or_attr, write_json_dataset
from tasks.artifacts.manifest import MeasuredArtifactManifestBase, manifest_to_json_dict


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


def test_measured_artifact_manifest_base_serializes_sensor_metadata():
    manifest = MeasuredArtifactManifestBase(
        artifact_type="example_measured_artifact",
        schema_version=1,
        artifact_id="artifact_001",
        coordinate_frame="sensor_full_frame",
        camera_frame_extent={
            "mode": "full_sensor",
            "origin_xy": [0, 0],
            "shape_hw": [4, 5],
            "sensor_shape_hw": [4, 5],
        },
        frame_shape=(4, 5),
        created_by_task="test_task",
        validation_policy={"requires_full_sensor": True},
    )

    data = manifest_to_json_dict(manifest)

    assert data["software_version"] == "optic_system"
    assert data["coordinate_frame"] == "sensor_full_frame"
    assert data["camera_frame_extent"]["mode"] == "full_sensor"
    assert data["validation_policy"]["requires_full_sensor"] is True
