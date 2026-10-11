"""Run one explicit calibration stage using the existing device services."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("broadband", "pupil", "per-band"))
    parser.add_argument("--plan", required=True, help="calibration stage JSON configuration")
    parser.add_argument("--output", required=True, help="profile JSON output (must not exist)")
    parser.add_argument("--report", required=True, help="probe/scan report JSON output (must not exist)")
    parser.add_argument("--camera-profile", help="broadband CameraProfile for pupil scan")
    parser.add_argument("--pupil-profile", help="PupilProfile for per-band calibration")
    parser.add_argument("--valid-pixel-mask", help="explicit boolean sensor mask .npy")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--lcd-display-index", type=int, required=True)
    parser.add_argument("--lcd-subpixel-axis", type=int, choices=(0, 1), required=True)
    parser.add_argument("--tls-serial-number")
    args = parser.parse_args(argv)
    output, report = Path(args.output), Path(args.report)
    if output.resolve() == report.resolve() or output.exists() or report.exists():
        parser.error("profile and report must be distinct new output paths")
    if args.stage == "pupil" and not args.camera_profile:
        parser.error("pupil scan requires --camera-profile")
    if args.stage == "per-band" and not args.pupil_profile:
        parser.error("per-band calibration requires --pupil-profile")
    if args.stage == "pupil" and args.valid_pixel_mask:
        parser.error("pupil scan integrates full-frame energy; valid pixel exclusion is for exposure/center analysis")

    import numpy as np
    from devices.camera_service import CameraServiceClient
    from devices.frame_stream import FrameStreamClient
    from devices.lcd_service import LCDService
    from devices.tls_service import TLSService
    from capture.frame_capture import FrameCaptureHelper
    from tasks.capture_dataset import CameraCaptureAdapter, LCDAdapter
    from tasks.profiles import (
        CameraProfile, PupilProfile, BroadbandCameraCalibrationPlan,
        PupilScanPlan, PerBandPupilOpenCalibrationPlan,
        calibrate_broadband_camera_profile, run_broadband_pupil_scan,
        calibrate_per_band_pupil_open_camera_profile,
    )
    data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    valid = np.load(args.valid_pixel_mask, allow_pickle=False) if args.valid_pixel_mask else None
    if valid is not None and (valid.ndim != 2 or valid.dtype != np.bool_ or not np.any(valid)):
        parser.error("valid-pixel-mask must be a nonempty 2D boolean sensor mask")
    cam_service = CameraServiceClient()
    tls = TLSService(default_serial_number=args.tls_serial_number)
    lcd_service = stream = None
    try:
        cam_service.open_camera(index=args.camera_index, context_type="IIDC", disable_trigger=True)
        cam_service.start_stream()
        stream = FrameStreamClient()
        camera = CameraCaptureAdapter(FrameCaptureHelper(stream), camera_service=cam_service)
        lcd_service = LCDService(display_index=args.lcd_display_index, subpixel_axis=args.lcd_subpixel_axis)
        lcd = LCDAdapter(lcd_service)
        tls.connect()
        if args.stage == "broadband":
            result = calibrate_broadband_camera_profile(BroadbandCameraCalibrationPlan.from_dict(data), camera=camera, lcd=lcd, tls=tls, valid_pixel_mask=valid)
            profile = result.camera_profile
        elif args.stage == "pupil":
            result = run_broadband_pupil_scan(PupilScanPlan.from_dict(data), camera_profile=CameraProfile.load_json(args.camera_profile), camera=camera, lcd=lcd, tls=tls)
            profile = result.pupil_profile
        else:
            result = calibrate_per_band_pupil_open_camera_profile(PerBandPupilOpenCalibrationPlan.from_dict(data), pupil_profile=PupilProfile.load_json(args.pupil_profile), camera=camera, lcd=lcd, tls=tls, valid_pixel_mask=valid)
            profile = result.camera_profile
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        profile.to_json(output)
        result.write_json(report)
    finally:
        for obj, method in ((tls, "close"), (lcd_service, "close"), (stream, "close"), (cam_service, "stop_stream"), (cam_service, "close_camera"), (cam_service, "close")):
            if obj is not None:
                try: getattr(obj, method)()
                except Exception: pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
