"""Native Camera2 observation -> acquired-frame mean/ROI, without inventing a lab Survey."""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import numpy as np

from tasks.measurement.energy_center import estimate_frame_energy_center
from tasks.measurement.roi import ROI
from tools.android_raw_probe import raw_tools


@dataclass
class UDCMeasurement:
    mean_dn: np.ndarray
    roi: ROI
    metadata: dict[str, Any]

    def energy_center(self, *, bg_percentile=5.0, valid_pixel_mask=None) -> dict:
        """A background-corrected array diagnostic, not a physical sensor-center profile."""
        estimate = estimate_frame_energy_center(self.mean_dn, bg_percentile=bg_percentile, valid_pixel_mask=valid_pixel_mask)
        result = asdict(estimate)
        result.update(coordinate_frame="acquired_frame",
                      center_xy=(estimate.center_xy[0]+self.roi.x, estimate.center_xy[1]+self.roi.y),
                      peak_xy=(estimate.peak_xy[0]+self.roi.x, estimate.peak_xy[1]+self.roi.y))
        return result


def load_udc_measurement(run_path: str | Path, *, roi: ROI | None = None) -> UDCMeasurement:
    path = Path(run_path)
    run, records = raw_tools.frame_records(path)
    if not run.get("capture_complete") or not run.get("run_succeeded") or not records:
        raise ValueError("UDC measurement requires a complete successful run; partial runs remain diagnostics")
    if not all(r["stats"]["manual_readback_within_tolerance"] for r in records):
        raise ValueError("UDC manual exposure/ISO readback failed")
    shapes = {tuple(r["stats"]["shape_hw"]) for r in records}
    shape = (run["raw_size"]["height"], run["raw_size"]["width"])
    if shapes != {shape}:
        raise ValueError("Image dimensions disagree with the run")
    selected = roi or ROI(0, 0, shape[1], shape[0])
    selected.validate(shape)
    mean = np.zeros((selected.height, selected.width), dtype=np.float64)
    for n, record in enumerate(records, 1):
        frame, _ = raw_tools.read_image(raw_tools.local_file(path, record["frame"]["raw"]["metadata_file"]))
        values = frame[selected.y:selected.y+selected.height, selected.x:selected.x+selected.width]
        mean += (values-mean)/n
    chars = raw_tools.load_json(path/"characteristics.json")["values"]
    metadata = {"source_kind":"android_camera2_raw_probe", "source_run":str(path.resolve()), "run_id":run["run_id"],
        "coordinate_frame":"acquired_frame", "full_frame_shape_hw":list(shape),
        "roi_xywh":[selected.x,selected.y,selected.width,selected.height],
        "roi_parity_origin_yx":[selected.y%2,selected.x%2],
        "sensor_origin_verified":False,"cfa_image_origin_verified":False,
        "declared_cfa":chars.get("android.sensor.info.colorFilterArrangement"),
        "declared_black_level":chars.get("android.sensor.blackLevelPattern"),
        "declared_white_level":chars.get("android.sensor.info.whiteLevel"),
        "lens_shading_applied":chars.get("android.sensor.info.lensShadingApplied"),
        "condition":run["condition"],"operator_verified":run["condition_verified_by_operator"],
        "condition_notes":run["condition_notes"],"frames":len(records),
        "requested_exposure_ns":run["requested_exposure_ns"],"requested_iso":run["requested_iso"],
        "source_commit":run.get("source_commit"),"source_dirty":run.get("source_dirty"),
        "processing":"arithmetic mean in original DN; no black subtraction, normalization or demosaic",
        "optical_diffraction_verified":False}
    return UDCMeasurement(mean, selected, metadata)
