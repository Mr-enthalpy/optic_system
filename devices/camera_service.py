from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
        try:
            proc = subprocess.Popen(
                command,
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            launch_errors.append(f"{' '.join(command)}: {exc}")
            continue

        started_at = time.time()
        while time.time() - started_at < max_wait_s:
            if _ping_sidecar(rep_addr, timeout_ms=timeout_ms):
                return SidecarHandle(
                    own_service=True,
                    proc=proc,
                    command=command,
                    service_path=str(service_path),
                )
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        exit_code = proc.poll()
        _terminate_process(proc)
        if exit_code is None:
            launch_errors.append(f"{' '.join(command)}: startup timed out")
        else:
            launch_errors.append(f"{' '.join(command)}: exited with code {exit_code}")

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

    def open_camera(self, index: int = 0, context_type: str = "IIDC") -> dict:
        reply = self._request("OpenCamera", index=index, context_type=context_type)
        if reply.get("ok"):
            return reply
        raise RuntimeError(reply)

    def open_camera_gui(self, index: int = 0, context_type: str = "IIDC") -> None:
        # Match the legacy startup behavior: FlyCapture pre-config GUI blocks until
        # the user closes it, so this request must not inherit the normal RPC timeout.
        reply = self._request(
            "PreConfigGUI",
            timeout_ms=None,
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

    def get_range(self, name: str) -> tuple[float, float]:
        reply = self._request("GetRange", name=name)
        if reply.get("ok"):
            min_value, max_value = reply["range"]
            return float(min_value), float(max_value)
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

    def close_camera(self) -> None:
        try:
            reply = self._request("CloseCamera")
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
