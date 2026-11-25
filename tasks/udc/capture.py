"""Finite capture/export/decode for one externally confirmed UDC scene."""
import json
from pathlib import Path

from diagnostics.run_status import RunStatusPublisher
from tools.android_raw_probe import device as phone
from tools.android_raw_probe import raw_tools
from .plan import UDCCapturePlan


def run_udc_capture_plan(plan: UDCCapturePlan, *, adb: Path, output: Path,
                         condition_verified: bool = False, camera_id: str | None = None,
                         serial: str | None = None, wait_seconds: float = 180) -> dict:
    if not isinstance(plan, UDCCapturePlan):
        raise TypeError("Expected UDCCapturePlan, not LCD CapturePlan")
    if plan.condition != "unspecified" and condition_verified is not True:
        raise ValueError("Confirm the CURRENT physical condition explicitly before capture")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/"plan.json").write_text(json.dumps(plan.to_dict(), indent=2, allow_nan=False), encoding="utf-8")
    status = RunStatusPublisher(output/"status", run_id=plan.plan_id)
    report = {"schema_version":1,"task":"udc_capture_plan","plan_id":plan.plan_id,"runtime_mode":"hardware",
              "capture_complete":False,"run_succeeded":False,"operator_verified":bool(condition_verified),
              "batches":[],"optical_diffraction_verified":False,"error":None}

    def publish(phase, stage, error=None):
        report["capture_complete"] = len(report["batches"]) == len(plan.batches) and all(b["capture_complete"] for b in report["batches"])
        status.write_frame_stats(report, filename="pipeline.json")
        status.update(plan_id=plan.plan_id, phase=phase, stage=stage, n_captures=len(plan.batches),
                      capture_index=len(report["batches"])-1 if report["batches"] else None,
                      completed=report["run_succeeded"] if phase in ("completed","failed") else None, error=error)

    publish("running", "connect")
    current = None
    stage = "connect"
    try:
        device = phone.Device(Path(adb), serial)
        for index, batch in enumerate(plan.batches):
            batch_dir = output/f"batch_{index:03d}"
            batch_dir.mkdir()
            current = {"batch_index":index,"exposure_us":batch.exposure_us,"frames":batch.frames,
                       "shading":batch.shading,"tonemap":batch.tonemap,"display":batch.display,
                       "companion_size":plan.companion_size,
                       "capture_complete":False,"phone_run_succeeded":False,"export_complete":False,"decode_complete":False}
            report["batches"].append(current)
            stage = "capture"
            publish("running", stage)
            note = json.dumps({"udc_plan_id":plan.plan_id,"batch_index":index,
                               "batch_request":{"exposure_us":batch.exposure_us,"shading":batch.shading,
                                                "tonemap":batch.tonemap,"display":batch.display,
                                                "companion_size":plan.companion_size},
                               "external_setup":{k:v for k,v in plan.to_dict().items() if k not in ("batches","schema_version")}}, allow_nan=False)
            state = phone.capture_run(device, exposure_us=batch.exposure_us, iso=plan.iso, count=batch.frames,
                                      raw_format=plan.raw_format, pair=plan.pair, scene=plan.condition,
                                      shading=batch.shading, tonemap=batch.tonemap, display=batch.display,
                                      companion_size=plan.companion_size,
                                      condition_verified=condition_verified, note=note, camera_id=camera_id, wait_seconds=wait_seconds)
            current.update(phone_run_id=state["run_id"],capture_complete=state["capture_complete"],phone_run_succeeded=state["run_succeeded"])
            stage = "export"
            publish("running", stage)
            current["export"] = phone.export(device, batch_dir/"export", run_ids=[state["run_id"]])
            current["export_complete"] = True
            stage = "decode"
            publish("running", stage)
            source = batch_dir/"export/device_files/captures"/state["run_id"]
            exported = raw_tools.load_json(source/"run.json")
            for key in ("run_id","camera_id","raw_format","requested_iso","requested_exposure_ns","requested_frames","condition","condition_verified_by_operator","requested_shading_mode","requested_tonemap_mode","requested_display_panel","requested_companion_size"):
                if exported.get(key) != state.get(key):
                    raise ValueError(f"Exported run disagrees with capture: {key}")
            if plan.raw_format == "RAW_SENSOR":
                for frame in exported["frames"]:
                    if frame.get("dng_status") != "saved" or not raw_tools.local_file(source, frame.get("dng_file", "")).is_file():
                        raise ValueError("A declared RAW_SENSOR DNG artifact is missing")
                    if raw_tools.local_file(source, frame["dng_file"]).stat().st_size == 0:
                        raise ValueError("A declared RAW_SENSOR DNG artifact is empty")
            summary = raw_tools.decode_run(source, batch_dir/"decoded")
            if (summary["validated_raw_frames"] != batch.frames or not summary["all_manual_readbacks_within_tolerance"]
                    or not summary["capture_complete"] or not summary["run_succeeded"]
                    or summary["illumination"]["label"] != plan.condition
                    or summary["illumination"]["operator_verified"] != condition_verified):
                raise ValueError("Exported batch failed the planned capture/control checks")
            current["decode_complete"] = True
            current["metadata_serialization_issues"] = summary["metadata_serialization_issues"]
            current["max_saturation_fraction"] = max((f["fraction_at_or_above_declared_white"] for f in summary["frames"]
                                                      if f["fraction_at_or_above_declared_white"] is not None), default=None)
        report["run_succeeded"] = True
        publish("completed", "done")
        return report
    except BaseException as error:
        if isinstance(error, phone.CaptureFailure) and current is not None:
            state = error.state
            current.update(phone_run_id=state.get("run_id"),capture_complete=bool(state.get("capture_complete")),
                           phone_run_succeeded=False,phone_failure_type=state.get("failure_type"))
            try:
                current["diagnostic_export"] = phone.export(device, batch_dir/"export", run_ids=[state["run_id"]])
            except Exception as export_error:
                current["diagnostic_export_error"] = str(export_error)
        report.update(error=str(error) or type(error).__name__,failure_stage=stage)
        publish("failed", stage, report["error"])
        raise
