from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_source_artifact(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return _repo_root() / path


@dataclass(frozen=True)
class ValidPixelDomain:
    type: str
    frame_shape: tuple[int, int]
    mask: np.ndarray
    policy: dict[str, Any]

    @property
    def valid_pixel_count(self) -> int:
        return int(np.count_nonzero(self.mask))

    @property
    def invalid_pixel_count(self) -> int:
        return int(self.mask.size - np.count_nonzero(self.mask))

    def policy_json(self) -> dict[str, Any]:
        return dict(self.policy)


def build_valid_pixel_mask(
    frame_shape: tuple[int, int],
    cfg: dict[str, Any] | None,
) -> ValidPixelDomain:
    if len(frame_shape) != 2:
        raise ValueError(f"frame_shape must be 2D [H, W], got {frame_shape}")
    height, width = int(frame_shape[0]), int(frame_shape[1])
    if height <= 0 or width <= 0:
        raise ValueError(f"frame_shape must be positive, got {frame_shape}")

    policy_cfg = dict(cfg or {})
    policy_type = str(policy_cfg.get("type") or "full_frame")
    mask = np.ones((height, width), dtype=bool)

    if policy_type == "full_frame":
        pass
    elif policy_type == "exclude_top_rows":
        top_rows = int(policy_cfg.get("top_rows", 0))
        if top_rows < 0 or top_rows >= height:
            raise ValueError(
                f"exclude_top_rows.top_rows must satisfy 0 <= top_rows < {height}, "
                f"got {top_rows}"
            )
        mask[:top_rows, :] = False
    else:
        raise ValueError(
            "valid_pixel_domain.type must be one of: "
            "'full_frame', 'exclude_top_rows'"
        )

    valid_pixel_count = int(np.count_nonzero(mask))
    invalid_pixel_count = int(mask.size - valid_pixel_count)
    if valid_pixel_count <= 0:
        raise ValueError("valid_pixel_domain leaves zero valid pixels")

    source_artifact = policy_cfg.get("source_artifact")
    artifact_exists: bool | None = None
    artifact_hash: str | None = None
    if source_artifact is not None:
        artifact_path = _resolve_source_artifact(str(source_artifact))
        artifact_exists = artifact_path.exists()
        if artifact_exists:
            artifact_hash = hash_file(artifact_path)
        elif not bool(policy_cfg.get("allow_missing_source_artifact", False)):
            raise FileNotFoundError(
                f"valid_pixel_domain.source_artifact not found: {artifact_path}"
            )

    policy: dict[str, Any] = {
        "type": policy_type,
        "frame_shape": [height, width],
        "valid_pixel_count": valid_pixel_count,
        "invalid_pixel_count": invalid_pixel_count,
    }
    if policy_type == "exclude_top_rows":
        policy["top_rows"] = int(policy_cfg.get("top_rows", 0))
    for key in ("source", "source_artifact", "reason"):
        if policy_cfg.get(key) is not None:
            policy[key] = policy_cfg[key]
    if source_artifact is not None:
        policy["source_artifact_exists"] = bool(artifact_exists)
        policy["artifact_hash"] = artifact_hash
    if policy_cfg.get("allow_missing_source_artifact", False):
        policy["allow_missing_source_artifact"] = True

    return ValidPixelDomain(
        type=policy_type,
        frame_shape=(height, width),
        mask=mask,
        policy=policy,
    )
