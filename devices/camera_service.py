from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import zmq

DEFAULT_PORT_REP = 6101
DEFAULT_PORT_PUB = 6100
DEFAULT_SHM_NAME = "flycap2_ring_A"
_USE_CLIENT_TIMEOUT = object()


@dataclass
class SidecarHandle:
    own_service: bool
    proc: Optional[subprocess.Popen]
    command: tuple[str, ...] = ()
    service_path: str = ""
    log_path: str = ""


def _ping_sidecar(rep_addr: str, timeout_ms: int = 300) -> bool:
    ctx = zmq.Context.instance()
    socket = ctx.socket(zmq.REQ)
    socket.RCVTIMEO = timeout_ms
    socket.SNDTIMEO = timeout_ms
    socket.LINGER = 0
    try:
        socket.connect(rep_addr)
        socket.send_json({"op": "Ping"})
        reply = socket.recv_json()
        return bool(reply.get("ok"))
    except Exception:
        return False
    finally:
        socket.close(0)


def _resolve_sidecar_path() -> Path:
    raw_path = os.environ.get("SIDECAR")
    if raw_path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return path.resolve()
    return (Path(__file__).resolve().parent / "camera_service_impl.py").resolve()


def _candidate_python_commands() -> list[tuple[str, ...]]:
    candidates: list[tuple[str, ...]] = []
    raw_py38 = os.environ.get("PY38_BIN")
    if raw_py38:
        candidates.append(tuple(shlex.split(raw_py38, posix=False)))

    if os.name == "nt":
        candidates.append(("py", "-3.8"))

    candidates.extend(
        [
            ("python3.8",),
            (sys.executable,),
            ("python",),
        ]
    )

    unique: list[tuple[str, ...]] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return

    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _sidecar_stdio_targets(service_path: Path) -> tuple[Any, Any, str]:
    log_path = os.environ.get("CAMERA_SERVICE_LOG", "").strip()
    debug = os.environ.get("CAMERA_SERVICE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    if log_path:
        resolved = Path(log_path)
        if not resolved.is_absolute():
            resolved = service_path.parent.parent / resolved
        resolved.parent.mkdir(parents=True, exist_ok=True)
        handle = resolved.open("a", encoding="utf-8")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        handle.write(f"\n[{timestamp}] launching camera sidecar {service_path}\n")
        handle.flush()
        return handle, handle, str(resolved)
    if debug:
        return None, None, ""
    return subprocess.DEVNULL, subprocess.DEVNULL, ""


def _ensure_sidecar(
    rep_addr: str = f"tcp://127.0.0.1:{DEFAULT_PORT_REP}",
    timeout_ms: int = 300,
    max_wait_s: float = 6.0,
) -> SidecarHandle:
    """
    Ensure the camera sidecar is reachable.

    If an external sidecar is already running, the current process only acts as a
    client. Otherwise it tries to launch the service using a Python 3.8 command.
    """

    service_path = _resolve_sidecar_path()
    if _ping_sidecar(rep_addr, timeout_ms=timeout_ms):
        return SidecarHandle(
            own_service=False,
            proc=None,
            service_path=str(service_path),
        )

    launch_errors: list[str] = []
    workdir = str(service_path.parent.parent)

    for python_cmd in _candidate_python_commands():
        command = (*python_cmd, str(service_path))
        stdout_target, stderr_target, log_path = _sidecar_stdio_targets(service_path)
        try:
            proc = subprocess.Popen(
                command,
                cwd=workdir,
                stdout=stdout_target,
                stderr=stderr_target,
            )
        except OSError as exc:
            launch_errors.append(f"{' '.join(command)}: {exc}")
            if hasattr(stdout_target, "close"):
                try:
                    stdout_target.close()
                except Exception:
                    pass
            continue

        started_at = time.time()
        while time.time() - started_at < max_wait_s:
            if _ping_sidecar(rep_addr, timeout_ms=timeout_ms):
                return SidecarHandle(
                    own_service=True,
                    proc=proc,
                    command=command,
                    service_path=str(service_path),
                    log_path=log_path,
                )
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        exit_code = proc.poll()
        _terminate_process(proc)
        if exit_code is None:
            launch_errors.append(f"{' '.join(command)}: startup timed out")
        else:
            detail = f"{' '.join(command)}: exited with code {exit_code}"
            if log_path:
                detail += f" (see CAMERA_SERVICE_LOG {log_path})"
            launch_errors.append(detail)

    detail = "; ".join(launch_errors) or "no launch attempts were made"
    raise RuntimeError(f"Unable to start camera sidecar at {service_path}: {detail}")


class CameraServiceClient:
    """
    RPC client for the hardware-facing camera sidecar.

    This layer only speaks the device protocol. It does not own GUI or task logic.
    """

    def __init__(
        self,
        rep_addr: str = f"tcp://127.0.0.1:{DEFAULT_PORT_REP}",
        auto_ensure: bool = True,
        timeout_ms: int = 3000,
    ):
        self.rep_addr = rep_addr
        self.auto_ensure = auto_ensure
        self.timeout_ms = timeout_ms
        self._sidecar: Optional[SidecarHandle] = None

    def ensure_sidecar(self) -> None:
        if self._sidecar is None and self.auto_ensure:
            ping_timeout = max(100, min(self.timeout_ms, 500))
            self._sidecar = _ensure_sidecar(
                rep_addr=self.rep_addr,
                timeout_ms=ping_timeout,
            )

    def ping(self) -> bool:
        return _ping_sidecar(
            self.rep_addr,
            timeout_ms=max(100, min(self.timeout_ms, 500)),
        )

    def get_connection_status(self) -> dict[str, object]:
        proc = self._sidecar.proc if self._sidecar is not None else None
        return {
            "rep_addr": self.rep_addr,
            "sidecar_running": self.ping(),
            "own_sidecar": bool(self._sidecar and self._sidecar.own_service),
            "sidecar_pid": proc.pid if proc is not None else None,
            "sidecar_command": list(self._sidecar.command) if self._sidecar else [],
            "service_path": self._sidecar.service_path if self._sidecar else str(_resolve_sidecar_path()),
            "log_path": self._sidecar.log_path if self._sidecar else "",
        }

    def _request(self, op: str, timeout_ms: object = _USE_CLIENT_TIMEOUT, **kwargs) -> dict:
        if op != "Ping":
            self.ensure_sidecar()

        ctx = zmq.Context.instance()
        socket = ctx.socket(zmq.REQ)
        effective_timeout = self.timeout_ms if timeout_ms is _USE_CLIENT_TIMEOUT else timeout_ms
        if effective_timeout is not None:
            socket.RCVTIMEO = effective_timeout
            socket.SNDTIMEO = effective_timeout
        socket.LINGER = 0
        try:
            socket.connect(self.rep_addr)
            socket.send_json({"op": op, **kwargs})
            return socket.recv_json()
        except zmq.Again as exc:
            raise TimeoutError(f"Timed out waiting for camera service op {op}") from exc
        finally:
            socket.close(0)

    def open_camera(
        self,
        index: int = 0,
        context_type: str = "IIDC",
        *,
        disable_trigger: bool | None = None,
        grab_timeout_ms: int | None = None,
        pixel_format: str | None = None,
        roi: dict[str, object] | None = None,
        properties: list[dict[str, object]] | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "index": index,
            "context_type": context_type,
        }
        if disable_trigger is not None:
            payload["disable_trigger"] = bool(disable_trigger)
        if grab_timeout_ms is not None:
            payload["grab_timeout_ms"] = int(grab_timeout_ms)
        if pixel_format is not None:
            payload["pixel_format"] = pixel_format
        if roi is not None:
            payload["roi"] = dict(roi)
        if properties is not None:
            payload["properties"] = list(properties)
        reply = self._request("OpenCamera", **payload)
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def open_camera_gui(self, index: int = 0, context_type: str = "IIDC") -> None:
        # Deprecated compatibility shim. The flycapture2_c sidecar is headless and
        # returns a structured replacement-ops error instead of opening a GUI.
        reply = self._request(
            "PreConfigGUI",
            timeout_ms=self.timeout_ms,
            index=index,
            context_type=context_type,
        )
        if reply.get("ok"):
            return
        raise RuntimeError(reply)

    def start_stream(self) -> None:
        reply = self._request("StartStream")
        if reply.get("ok"):
            return
        raise RuntimeError(reply)

    def stop_stream(self) -> None:
        reply = self._request("StopStream")
        if reply.get("ok"):
            return
        raise RuntimeError(reply)

    def get_camera_info(self) -> dict:
        reply = self._request("GetCameraInfo")
        if not reply.get("ok"):
            raise RuntimeError(reply)
        return reply.get("info") or reply.get("camera_info") or {}

    def get_backend_info(self) -> dict:
        reply = self._request("GetBackendInfo")
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def get_stream_status(self) -> dict:
        reply = self._request("GetStreamStatus")
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def snapshot_properties(self) -> list[dict]:
        reply = self._request("SnapshotProperties")
        if reply.get("ok"):
            return list(reply.get("properties") or [])
        raise RuntimeError(reply)

    def get_property_info(self, name: str) -> dict:
        reply = self._request("GetPropertyInfo", name=name)
        if reply.get("ok"):
            return dict(reply.get("info") or {})
        raise RuntimeError(reply)

    def get_range(self, name: str) -> tuple[float, float]:
        reply = self._request("GetRange", name=name)
        if reply.get("ok"):
            min_value, max_value = reply["range"]
            return float(min_value), float(max_value)
        raise RuntimeError(reply)

    def get_range_info(self, name: str) -> dict:
        reply = self._request("GetRange", name=name)
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def get_value(self, name: str) -> float:
        reply = self._request("GetValue", name=name)
        if reply.get("ok"):
            return float(reply["value"])
        raise RuntimeError(reply)

    def set_value(self, name: str, value: float) -> None:
        reply = self._request("SetProperty", name=name, value=float(value))
        if reply.get("ok"):
            return
        raise RuntimeError(reply)

    def set_property_auto(self, name: str, auto: bool) -> dict:
        reply = self._request("SetPropertyAuto", name=name, auto=bool(auto))
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def get_trigger_mode(self) -> dict:
        reply = self._request("GetTriggerMode")
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def disable_trigger(self) -> dict:
        reply = self._request("DisableTrigger")
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def set_trigger_mode(
        self,
        *,
        on_off: bool,
        source: int = 0,
        mode: int = 0,
        polarity: int = 1,
        parameter: int = 0,
    ) -> dict:
        reply = self._request(
            "SetTriggerMode",
            on_off=bool(on_off),
            source=int(source),
            mode=int(mode),
            polarity=int(polarity),
            parameter=int(parameter),
        )
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def get_format7_info(self, mode: int = 0) -> dict:
        reply = self._request("GetFormat7Info", mode=int(mode))
        if reply.get("ok"):
            return dict(reply.get("info") or {})
        raise RuntimeError(reply)

    def get_format7_configuration(self) -> dict:
        reply = self._request("GetFormat7Configuration")
        if reply.get("ok"):
            return dict(reply.get("configuration") or {})
        raise RuntimeError(reply)

    def validate_format7(
        self,
        *,
        mode: int = 0,
        offset_x: int = 0,
        offset_y: int = 0,
        width: int | None = None,
        height: int | None = None,
        pixel_format: str = "MONO8",
    ) -> dict:
        payload: dict[str, object] = {
            "mode": int(mode),
            "offset_x": int(offset_x),
            "offset_y": int(offset_y),
            "pixel_format": pixel_format,
        }
        if width is not None:
            payload["width"] = int(width)
        if height is not None:
            payload["height"] = int(height)
        reply = self._request("ValidateFormat7", **payload)
        if reply.get("ok"):
            return dict(reply.get("validation") or {})
        raise RuntimeError(reply)

    def set_pixel_format(self, pixel_format: str, *, mode: int = 0) -> dict:
        reply = self._request("SetPixelFormat", pixel_format=pixel_format, mode=int(mode))
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def set_roi(
        self,
        *,
        offset_x: int = 0,
        offset_y: int = 0,
        width: int | None = None,
        height: int | None = None,
        mode: int = 0,
    ) -> dict:
        payload: dict[str, object] = {
            "offset_x": int(offset_x),
            "offset_y": int(offset_y),
            "mode": int(mode),
        }
        if width is not None:
            payload["width"] = int(width)
        if height is not None:
            payload["height"] = int(height)
        reply = self._request("SetROI", **payload)
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def set_grab_timeout(self, grab_timeout_ms: int) -> dict:
        reply = self._request("SetGrabTimeout", grab_timeout_ms=int(grab_timeout_ms))
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def reconfigure_camera(
        self,
        *,
        disable_trigger: bool | None = None,
        grab_timeout_ms: int | None = None,
        pixel_format: str | None = None,
        roi: dict[str, object] | None = None,
        properties: list[dict[str, object]] | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if disable_trigger is not None:
            payload["disable_trigger"] = bool(disable_trigger)
        if grab_timeout_ms is not None:
            payload["grab_timeout_ms"] = int(grab_timeout_ms)
        if pixel_format is not None:
            payload["pixel_format"] = pixel_format
        if roi is not None:
            payload["roi"] = dict(roi)
        if properties is not None:
            payload["properties"] = list(properties)
        reply = self._request("ReconfigureCamera", **payload)
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def close_camera(self) -> None:
        reply = self._request("CloseCamera")
        if not reply.get("ok"):
            raise RuntimeError(reply)

    def shutdown_sidecar(self) -> None:
        try:
            reply = self._request("Shutdown")
            if not reply.get("ok"):
                raise RuntimeError(reply)
        finally:
            self._terminate_owned_sidecar()
            self._sidecar = None

    def close(self) -> None:
        self._terminate_owned_sidecar()
        self._sidecar = None

    def _terminate_owned_sidecar(self) -> None:
        if self._sidecar is None or not self._sidecar.own_service:
            return
        _terminate_process(self._sidecar.proc)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
