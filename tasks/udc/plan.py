"""One operator-arranged physical condition per phone capture plan."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any

from tasks.illumination import IlluminationSpec


def positive_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def positive_int(value, name):
    positive_number(value, name)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


@dataclass(frozen=True)
class UDCBatch:
    exposure_us: int
    frames: int = 10

    def __post_init__(self):
        positive_int(self.exposure_us, "exposure_us")
        positive_int(self.frames, "frames")
        if self.frames > 100:
            raise ValueError("frames must be <=100 per phone batch")


@dataclass(frozen=True)
class UDCCapturePlan:
    plan_id: str
    condition: str
    source_description: str
    display_state: str
    batches: tuple[UDCBatch, ...]
    iso: int = 100
    raw_format: str = "RAW_SENSOR"
    pair: str = "none"
    illumination: IlluminationSpec | None = None
    source_distance_mm: float | None = None
    aperture_diameter_um: float | None = None
    bandwidth_nm: float | None = None

    def __post_init__(self):
        for name in ("plan_id", "source_description", "display_state"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a nonempty string")
        if self.condition not in ("unspecified", "dark", "flat", "point"):
            raise ValueError("One physical condition per plan: unspecified, dark, flat or point")
        if not isinstance(self.batches, tuple) or not self.batches or not all(isinstance(b, UDCBatch) for b in self.batches):
            raise ValueError("batches must be a nonempty tuple of UDCBatch")
        positive_int(self.iso, "iso")
        if self.raw_format not in ("RAW_SENSOR", "RAW10") or self.pair not in ("none", "JPEG", "YUV"):
            raise ValueError("Unsupported Camera2 format or companion")
        for name in ("source_distance_mm", "aperture_diameter_um", "bandwidth_nm"):
            if getattr(self, name) is not None:
                positive_number(getattr(self, name), name)
        if self.illumination is not None:
            if not isinstance(self.illumination, IlluminationSpec) or not self.illumination.is_monochromatic or self.illumination.tls_setpoint_nm is not None:
                raise ValueError("UDC illumination records an externally arranged monochromatic source; no TLS command or fabricated pass-through")
            positive_number(self.illumination.effective_wavelength_nm, "effective_wavelength_nm")
        if self.condition == "point" and (self.illumination is None or self.source_distance_mm is None or self.aperture_diameter_um is None):
            raise ValueError("point requires monochromatic illumination, source_distance_mm and aperture_diameter_um")
        if self.condition == "dark" and self.illumination is not None:
            raise ValueError("Occluded dark observations do not have an incident wavelength")

    def to_dict(self) -> dict[str, Any]:
        data = {"schema_version":1, **asdict(self)}
        data["batches"] = [asdict(batch) for batch in self.batches]
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("Expected UDC plan schema_version=1")
        allowed = set(cls.__dataclass_fields__) | {"schema_version"}
        if set(data) - allowed:
            raise ValueError(f"Unknown UDC plan fields (LCD masks/pupil profiles do not apply): {sorted(set(data)-allowed)}")
        values = {k:v for k,v in data.items() if k != "schema_version"}
        batches = values.get("batches")
        if not isinstance(batches, list):
            raise ValueError("batches must be a nonempty ordered list")
        if any(not isinstance(b, dict) or set(b)-{"exposure_us", "frames"} for b in batches):
            raise ValueError("Each UDC batch contains only exposure_us and frames")
        try:
            values["batches"] = tuple(UDCBatch(**b) for b in batches)
            if values.get("illumination") is not None:
                illum = values["illumination"]
                if not isinstance(illum, dict) or set(illum)-{"mode","effective_wavelength_nm","tls_setpoint_nm","wavelength_label_nm"}:
                    raise ValueError("Invalid illumination fields")
                values["illumination"] = IlluminationSpec.from_dict(illum)
            return cls(**values)
        except TypeError as error:
            raise ValueError(f"Invalid UDC plan: {error}") from error

    @classmethod
    def load_json(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))
