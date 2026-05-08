from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from typing import Any, Callable, Optional

import numpy as np
import zmq

try:
    from .camera_backend_flycapture2 import (
        MyCamLite,
        flycapture2_import_error_message,
        format7_info_to_dict,
        has_pixel_format_support_matrix,
        is_backend_package_available,
        read_frame_decodable_pixel_format_names,
    )
    from .camera_frame_layout import BACKEND_NAME, PROTOCOL_VERSION, FrameLayout, build_frame_metadata, frame_layout_from_frame
    from .camera_protocol import (
        CameraStateError,
        UnsupportedOperationError,
        deprecated_preconfig_gui_reply,
        error_reply,
        format7_configuration_to_dict,
        json_safe,
        object_to_dict,
        property_info_to_dict,
        property_value_to_dict,
        property_snapshot_to_dict,
        trigger_mode_info_to_dict,
        trigger_mode_to_dict,
    )
except ImportError:  # pragma: no cover - direct script execution path
    from camera_backend_flycapture2 import (
        MyCamLite,
        flycapture2_import_error_message,
        format7_info_to_dict,
        has_pixel_format_support_matrix,
        is_backend_package_available,
        read_frame_decodable_pixel_format_names,
    )
    from camera_frame_layout import BACKEND_NAME, PROTOCOL_VERSION, FrameLayout, build_frame_metadata, frame_layout_from_frame
    from camera_protocol import (
        CameraStateError,
        UnsupportedOperationError,
        deprecated_preconfig_gui_reply,
        error_reply,
        format7_configuration_to_dict,
        json_safe,
        object_to_dict,
        property_info_to_dict,
        property_value_to_dict,
        property_snapshot_to_dict,
        trigger_mode_info_to_dict,
        trigger_mode_to_dict,
    )

RING = 8
PORT_PUB = 6100
PORT_REP = 6101
SHM_NAME = "flycap2_ring_A"


@dataclass
class CameraServiceState:
    camera_cls: Any = None
    cam: Optional[MyCamLite] = None
    shm: Optional[shared_memory.SharedMemory] = None
    layout: Optional[FrameLayout] = None
    running: bool = False
    widx: int = 0
    seq: int = 0
    dropped_frames: int = 0
    last_frame_ts_ns: int | None = None
    last_error: str | None = None
    lock: Any = field(default_factory=threading.RLock)
    stop_event: threading.Event = field(default_factory=threading.Event)

    def package_available(self) -> bool:
        return self.camera_cls is not None or is_backend_package_available()


def _release_shm(shm: Any) -> None:
    if shm is None:
        return
    try:
        shm.close()
    except Exception:
        pass
    try:
        shm.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _create_shm(size: int) -> shared_memory.SharedMemory:
    try:
        return shared_memory.SharedMemory(create=True, size=int(size), name=SHM_NAME)
    except FileExistsError:
        stale = shared_memory.SharedMemory(name=SHM_NAME)
        try:
            stale.close()
            stale.unlink()
        finally:
            pass
        return shared_memory.SharedMemory(create=True, size=int(size), name=SHM_NAME)


def _replace_shm_locked(state: CameraServiceState, layout: FrameLayout) -> bool:
    old = state.shm
    state.shm = None
    _release_shm(old)
    state.shm = _create_shm(RING * layout.frame_nbytes)
    state.layout = layout
    state.widx = 0
    return True


def _close_camera_locked(state: CameraServiceState) -> bool:
    released_shm = state.shm is not None
    state.running = False
    if state.cam is not None:
        try:
            state.cam.close()
        finally:
            state.cam = None
    _release_shm(state.shm)
    state.shm = None
    state.layout = None
    return released_shm


def _require_camera(state: CameraServiceState) -> MyCamLite:
    if state.cam is None:
        raise CameraStateError("camera not opened")
    return state.cam


def _stream_status_locked(state: CameraServiceState) -> dict[str, Any]:
    layout = state.layout
    return {
        "ok": True,
        "running": bool(state.running),
        "camera_open": state.cam is not None,
        "capturing": bool(state.cam and state.cam.is_capturing),
        "seq": int(state.seq),
        "last_frame_ts_ns": state.last_frame_ts_ns,
        "last_error": state.last_error,
        "shm": SHM_NAME if state.shm is not None else None,
        "ring_size": RING if state.shm is not None else None,
        "width": layout.width if layout else 0,
        "height": layout.height if layout else 0,
        "stride": layout.stride if layout else 0,
        "row_bytes": layout.row_bytes if layout else 0,
        "frame_nbytes": layout.frame_nbytes if layout else 0,
        "dtype": layout.dtype if layout else None,
        "shape": layout.shape if layout else None,
        "pixel_format": layout.pixel_format if layout else None,
        "format": layout.format if layout else None,
    }


def _camera_info_payload(cam: MyCamLite) -> dict[str, Any]:
    raw_info = cam.get_camera_info(refresh=False)
    info_dict = object_to_dict(raw_info)
    layout = cam.layout
    serial = info_dict.get("serial_number", info_dict.get("serial"))
    payload = {
        **info_dict,
        "serial": serial,
        "serial_number": serial,
        "model_name": info_dict.get("model_name", info_dict.get("modelName", "")),
        "vendor_name": info_dict.get("vendor_name", info_dict.get("vendorName", "")),
        "sensor_info": info_dict.get("sensor_info", ""),
        "sensor_resolution": info_dict.get("sensor_resolution", ""),
        "firmware_version": info_dict.get("firmware_version", ""),
        "interface_type": info_dict.get("interface_type"),
        "setting_names": list(cam.setting_names),
        "capabilities": json_safe(cam.capabilities),
    }
    if layout is not None:
        payload.update(layout.to_dict())
        payload["pix_fmt"] = layout.format
    return json_safe(payload)


def _get_property_range(cam: MyCamLite, name: str) -> dict[str, Any]:
    info = property_info_to_dict(cam.get_property_info(name))
    if not info.get("present"):
        raise UnsupportedOperationError(f"Property {name} is not present on this camera.")
    abs_supported = bool(info.get("abs_val_supported"))
    if abs_supported:
        range_values = [float(info["abs_min"]), float(info["abs_max"])]
    else:
        range_values = [int(info["min"]), int(info["max"])]
    return {
        "ok": True,
        "range": range_values,
        "units": info.get("units", ""),
        "integer_range": [int(info["min"]), int(info["max"])],
        "abs_supported": abs_supported,
        "info": info,
    }


def _get_property_value(cam: MyCamLite, name: str) -> dict[str, Any]:
    value = property_value_to_dict(cam.get_property_value(name))
    result_value = value["abs_value"] if value.get("abs_control") else value["value_a"]
    return {"ok": True, "value": result_value, "property": value}


def _set_property_abs(cam: MyCamLite, name: str, value: float, *, auto: bool = False) -> dict[str, Any]:
    info = property_info_to_dict(cam.get_property_info(name))
    if not info.get("present"):
        raise UnsupportedOperationError(f"Property {name} is not present on this camera.")
    if not info.get("abs_val_supported"):
        raise UnsupportedOperationError(
            f"Property {name} does not support absolute values. Use property-specific integer controls."
        )
    updated = cam.set_property_abs(name, float(value), auto=bool(auto))
    return {"ok": True, "property": property_value_to_dict(updated)}


def _reconfigure_locked(state: CameraServiceState, req: dict[str, Any]) -> dict[str, Any]:
    cam = _require_camera(state)
    old_layout = state.layout
    validation = cam.validate_config(pixel_format=req.get("pixel_format"), roi=req.get("roi"))
    was_running = bool(state.running)
    state.running = False
    cam.stop_capture()
    shm_recreated = False
    try:
        cam.apply_config(
            disable_trigger=req.get("disable_trigger"),
            grab_timeout_ms=req.get("grab_timeout_ms"),
            pixel_format=req.get("pixel_format"),
            pixel_format_mode=int(req.get("pixel_format_mode", req.get("mode", 0))),
            roi=req.get("roi"),
            properties=req.get("properties"),
            validate=False,
        )
        cam.start_capture()
        first_frame = cam._read_frame()
        new_layout = frame_layout_from_frame(first_frame)
        cam.layout = new_layout
        cam.setting_names = cam._discover_setting_names()
        layout_changed = old_layout != new_layout
        if layout_changed:
            shm_recreated = _replace_shm_locked(state, new_layout)
        else:
            state.layout = new_layout
        state.running = was_running
        state.last_error = None
        status = _stream_status_locked(state)
        return {
            "ok": True,
            "restarted": was_running,
            "old_layout": old_layout.to_dict() if old_layout else None,
            "new_layout": new_layout.to_dict(),
            "layout": new_layout.to_dict(),
            "layout_changed": layout_changed,
            "shm_recreated": shm_recreated,
            "validation": validation,
            "status": {
                **status,
                "event": "stream_layout_changed" if layout_changed else "stream_reconfigured",
                "old_layout": old_layout.to_dict() if old_layout else None,
                "new_layout": new_layout.to_dict(),
                "layout_changed": layout_changed,
                "shm_recreated": shm_recreated,
            },
        }
    except Exception:
        state.running = False
        try:
            cam.start_capture()
        except Exception:
            pass
        raise


def _publish_status(pub: zmq.Socket, payload: dict[str, Any]) -> None:
    status = {"protocol_version": PROTOCOL_VERSION, "backend": BACKEND_NAME, "ts_ns": time.time_ns(), **payload}
    pub.send_multipart([b"status", json.dumps(json_safe(status)).encode("utf-8")])


def handle_request(
    state: CameraServiceState,
    req: dict[str, Any],
    *,
    publish_status: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    op = str(req.get("op") or "")
    try:
        if op == "Ping":
            return {
                "ok": True,
                "ts_ns": time.time_ns(),
                "backend": BACKEND_NAME,
                "protocol_version": PROTOCOL_VERSION,
                "package_available": state.package_available(),
            }

        if op == "GetBackendInfo":
            with state.lock:
                return {
                    "ok": True,
                    "backend": BACKEND_NAME,
                    "protocol_version": PROTOCOL_VERSION,
                    "package_available": state.package_available(),
                    "camera_open": state.cam is not None,
                    "capturing": bool(state.cam and state.cam.is_capturing),
                    "running": bool(state.running),
                    "import_error": None if state.package_available() else flycapture2_import_error_message(),
                    "pixel_format_support_matrix": has_pixel_format_support_matrix(),
                    "read_frame_decodable_pixel_formats": read_frame_decodable_pixel_format_names(),
                }

        if op == "PreConfigGUI":
            return deprecated_preconfig_gui_reply()

        if op == "OpenCamera":
            with state.lock:
                _close_camera_locked(state)
                disable_trigger_requested = req.get("disable_trigger") is True
                cam = MyCamLite.open(
                    index=int(req.get("index", 0)),
                    context_type=str(req.get("context_type", "IIDC")),
                    disable_trigger=disable_trigger_requested,
                    grab_timeout_ms=req.get("grab_timeout_ms"),
                    pixel_format=req.get("pixel_format"),
                    roi=req.get("roi"),
                    properties=req.get("properties") or [],
                    camera_cls=state.camera_cls,
                )
                if cam.layout is None:
                    raise CameraStateError("camera opened but frame layout is unavailable")
                state.cam = cam
                state.seq = 0
                state.widx = 0
                state.dropped_frames = 0
                state.last_frame_ts_ns = None
                state.last_error = None
                _replace_shm_locked(state, cam.layout)
                info = _camera_info_payload(cam)
                return {
                    "ok": True,
                    "backend": BACKEND_NAME,
                    "protocol_version": PROTOCOL_VERSION,
                    "serial": info.get("serial"),
                    "width": cam.layout.width,
                    "height": cam.layout.height,
                    "stride": cam.layout.stride,
                    "format": cam.layout.format,
                    "pixel_format": cam.layout.pixel_format,
                    "dtype": cam.layout.dtype,
                    "shape": cam.layout.shape,
                    "shm": SHM_NAME,
                    "ring_size": RING,
                    "setting_names": list(cam.setting_names),
                    "configuration_applied": json_safe(cam.configuration_applied),
                    "info": info,
                }

        if op == "GetCameraInfo":
            with state.lock:
                return {"ok": True, "info": _camera_info_payload(_require_camera(state))}

        if op == "StartStream":
            with state.lock:
                _require_camera(state)
                if state.shm is None or state.layout is None:
                    raise CameraStateError("shared memory is not ready")
                state.running = True
                state.last_error = None
                return _stream_status_locked(state)

        if op == "StopStream":
            with state.lock:
                state.running = False
                return _stream_status_locked(state)

        if op == "GetStreamStatus":
            with state.lock:
                return _stream_status_locked(state)

        if op == "SnapshotProperties":
            with state.lock:
                cam = _require_camera(state)
                return {"ok": True, "properties": [property_snapshot_to_dict(item) for item in cam.snapshot_properties()]}

        if op == "GetPropertyInfo":
            with state.lock:
                cam = _require_camera(state)
                return {"ok": True, "info": property_info_to_dict(cam.get_property_info(str(req["name"])))}

        if op == "GetRange":
            with state.lock:
                return _get_property_range(_require_camera(state), str(req["name"]))

        if op == "GetValue":
            with state.lock:
                return _get_property_value(_require_camera(state), str(req["name"]))

        if op == "SetProperty":
            with state.lock:
                return _set_property_abs(
                    _require_camera(state),
                    str(req["name"]),
                    float(req["value"]),
                    auto=bool(req.get("auto", False)),
                )

        if op == "SetPropertyAuto":
            with state.lock:
                updated = _require_camera(state).set_property_auto(str(req["name"]), auto=bool(req.get("auto", True)))
                return {"ok": True, "property": property_value_to_dict(updated)}

        if op == "GetTriggerMode":
            with state.lock:
                cam = _require_camera(state)
                return {
                    "ok": True,
                    "trigger": trigger_mode_to_dict(cam.get_trigger_mode()),
                    "info": trigger_mode_info_to_dict(cam.get_trigger_mode_info()),
                }

        if op == "DisableTrigger":
            with state.lock:
                mode = _require_camera(state).disable_trigger()
                return {"ok": True, "trigger": trigger_mode_to_dict(mode)}

        if op == "SetTriggerMode":
            with state.lock:
                kwargs = {key: req[key] for key in ("on_off", "source", "mode", "polarity", "parameter") if key in req}
                mode = _require_camera(state).set_trigger_mode(**kwargs)
                return {"ok": True, "trigger": trigger_mode_to_dict(mode)}

        if op == "GetFormat7Info":
            with state.lock:
                info = _require_camera(state).get_format7_info(mode=int(req.get("mode", 0)))
                return {"ok": True, "info": format7_info_to_dict(info)}

        if op == "GetFormat7Configuration":
            with state.lock:
                return {"ok": True, "configuration": format7_configuration_to_dict(_require_camera(state).get_format7_configuration())}

        if op == "ValidateFormat7":
            with state.lock:
                kwargs = {
                    "mode": int(req.get("mode", 0)),
                    "offset_x": int(req.get("offset_x", 0)),
                    "offset_y": int(req.get("offset_y", 0)),
                    "width": req.get("width"),
                    "height": req.get("height"),
                    "pixel_format": req.get("pixel_format", "MONO8"),
                }
                validation = _require_camera(state).validate_format7(**kwargs)
                return {"ok": True, "validation": json_safe(validation)}

        if op == "SetPixelFormat":
            with state.lock:
                reply = _reconfigure_locked(
                    state,
                    {"pixel_format": req["pixel_format"], "pixel_format_mode": int(req.get("mode", 0))},
                )
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "SetROI":
            with state.lock:
                roi = {key: req[key] for key in ("offset_x", "offset_y", "width", "height", "mode") if key in req}
                reply = _reconfigure_locked(state, {"roi": roi})
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "SetGrabTimeout":
            with state.lock:
                reply = _reconfigure_locked(state, {"grab_timeout_ms": int(req["grab_timeout_ms"])})
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "ReconfigureCamera":
            with state.lock:
                reply = _reconfigure_locked(state, req)
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "CloseCamera":
            with state.lock:
                shm_released = _close_camera_locked(state)
                state.last_error = None
                return {"ok": True, "service_running": True, "shm_released": shm_released}

        if op == "Shutdown":
            with state.lock:
                shm_released = _close_camera_locked(state)
                state.stop_event.set()
                return {"ok": True, "service_running": False, "shm_released": shm_released}

        return error_reply(op, "unknown op", error_type="UnknownOperation")
    except Exception as exc:
        with state.lock:
            state.last_error = str(exc)
        return error_reply(op, exc)


def stream_loop(state: CameraServiceState, pub: zmq.Socket) -> None:
    last_error_report_ns = 0
    while not state.stop_event.is_set():
        with state.lock:
            should_run = bool(state.running and state.cam is not None and state.shm is not None and state.layout is not None)
        if not should_run:
            time.sleep(0.01)
            continue

        try:
            with state.lock:
                if not (state.running and state.cam is not None and state.shm is not None and state.layout is not None):
                    continue
                array, frame, observed_layout = state.cam.capture()
                if observed_layout != state.layout:
                    raise CameraStateError(
                        "Frame layout changed while streaming. Stop stream or use ReconfigureCamera first."
                    )

                slot_nbytes = state.layout.frame_nbytes
                start = state.widx * slot_nbytes
                end = start + slot_nbytes
                frame_bytes = np.ascontiguousarray(array).tobytes()
                if len(frame_bytes) != slot_nbytes:
                    raise CameraStateError(
                        f"Frame byte size {len(frame_bytes)} does not match shared memory slot {slot_nbytes}."
                    )
                mv = memoryview(state.shm.buf)[start:end]
                try:
                    mv[:] = frame_bytes
                finally:
                    mv.release()

                meta = build_frame_metadata(
                    state.layout,
                    index=state.widx,
                    seq=state.seq,
                    shm_name=SHM_NAME,
                    ring_size=RING,
                    timestamp_sdk=getattr(frame, "timestamp", None),
                    embedded_metadata=getattr(frame, "metadata", None),
                    dropped_frames=state.dropped_frames,
                )
                state.widx = (state.widx + 1) % RING
                state.seq += 1
                state.last_frame_ts_ns = int(meta["ts_ns"])
                state.last_error = None

            pub.send_multipart([b"frame", json.dumps(meta).encode("utf-8")])
        except Exception as exc:
            now = time.time_ns()
            with state.lock:
                state.last_error = str(exc)
                state.dropped_frames += 1
            if now - last_error_report_ns > 1_000_000_000:
                last_error_report_ns = now
                payload = error_reply("StreamLoop", exc)
                payload["ts_ns"] = now
                try:
                    pub.send_multipart([b"status", json.dumps(payload).encode("utf-8")])
                except Exception:
                    pass
            time.sleep(0.2)


def control_loop(state: CameraServiceState, rep: zmq.Socket, pub: zmq.Socket) -> None:
    rep.RCVTIMEO = 200
    while not state.stop_event.is_set():
        try:
            req = rep.recv_json(flags=0)
        except zmq.Again:
            continue
        except zmq.ZMQError:
            break
        reply = handle_request(state, req, publish_status=lambda payload: _publish_status(pub, payload))
        try:
            rep.send_json(reply)
        except zmq.ZMQError:
            break


def main() -> None:
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    rep = ctx.socket(zmq.REP)
    try:
        pub.set_hwm(1)
        pub.bind(f"tcp://127.0.0.1:{PORT_PUB}")
        rep.bind(f"tcp://127.0.0.1:{PORT_REP}")
    except zmq.ZMQError as exc:
        print(f"[FATAL] Camera service port bind failed: {exc}", file=sys.stderr, flush=True)
        pub.close(0)
        rep.close(0)
        ctx.term()
        sys.exit(1)

    state = CameraServiceState()
    t_stream = threading.Thread(target=stream_loop, args=(state, pub), daemon=True)
    t_ctrl = threading.Thread(target=control_loop, args=(state, rep, pub), daemon=True)
    t_stream.start()
    t_ctrl.start()

    try:
        while not state.stop_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        state.stop_event.set()
    finally:
        state.stop_event.set()
        with state.lock:
            _close_camera_locked(state)
        t_stream.join(1.0)
        t_ctrl.join(1.0)
        pub.close(0)
        rep.close(0)
        ctx.term()


if __name__ == "__main__":
    main()
