from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TLSStatus:
    connected: bool
    device_id: int | None = None
    mono: str | None = None
    port_type: str | None = None
    serial_number: str | None = None
    current_wavelength_nm: float | None = None
    target_wavelength_nm: float | None = None
    grating: int | None = None
    moving: bool = False
    last_error: str | None = None


class TLSServiceError(RuntimeError):
    def __init__(self, operation: str, message: str):
        super().__init__(f"TLS {operation} failed: {message}")
        self.operation = operation
        self.message = message


class TLSServiceUnavailableError(TLSServiceError):
    pass


class TLSServiceTimeoutError(TLSServiceError):
    pass


class TLSService:
    """Thin wrapper around the high-level ``tls_c1`` / ``TLSC1`` SDK facade.

    The module import is intentionally lazy so ``optic_system`` can import on a
    machine without the TLS SDK or vendor DLLs installed.

    If *status_dir* is provided, the service independently writes
    ``tls_state.json`` after every state-changing operation so a read-only
    monitor can observe the latest TLS state without the task.
    """

    def __init__(
        self,
        *,
        module_name: str = "tls_c1",
        device_factory: Callable[[], Any] | None = None,
        default_mono: str = "Omni",
        default_port_type: str = "USB",
        default_serial_number: str | None = None,
        status_dir: Path | str | None = None,
    ):
        self._module_name = module_name
        self._device_factory = device_factory
        self._module: Any | None = None
        self._device: Any | None = None

        self._default_mono = default_mono
        self._default_port_type = default_port_type
        self._default_serial_number = default_serial_number or os.environ.get("TLS_C1_SERIAL")

        self._last_status = TLSStatus(
            connected=False,
            mono=self._default_mono,
            port_type=self._default_port_type,
            serial_number=self._default_serial_number,
        )

        self._status_dir: Path | None = Path(status_dir) if status_dir is not None else None

    def set_status_dir(self, status_dir: Path | str | None) -> None:
        self._status_dir = Path(status_dir) if status_dir is not None else None

    def connect(
        self,
        *,
        mono: str | None = None,
        port_type: str | None = None,
        serial_number: str | None = None,
    ) -> TLSStatus:
        device = self._ensure_device()
        resolved_mono = mono or self._default_mono
        resolved_port = port_type or self._default_port_type
        resolved_serial = serial_number or self._default_serial_number

        try:
            device.connect(
                Mono=resolved_mono,
                port_type=resolved_port,
                serial_number=resolved_serial,
            )
            status = self._refresh_status(
                self._safe_get_status(),
                mono=resolved_mono,
                port_type=resolved_port,
                serial_number=resolved_serial,
                last_error=None,
            )
            self._publish_tls_state()
            return status
        except Exception as exc:
            raise self._wrap_exception("connect", exc) from exc

    def disconnect(self) -> TLSStatus:
        if self._device is None:
            self._last_status = self._disconnected_status()
            self._publish_tls_state()
            return self._last_status

        try:
            self._device.disconnect()
        except Exception as exc:
            raise self._wrap_exception("disconnect", exc) from exc

        self._last_status = self._disconnected_status()
        self._publish_tls_state()
        return self._last_status

    def set_grating(self, grating: int) -> TLSStatus:
        try:
            device = self._require_device()
            device.set_grating(int(grating))
            status = self._refresh_status(
                self._safe_get_status(),
                grating=int(grating),
                last_error=None,
            )
            self._publish_tls_state()
            return status
        except Exception as exc:
            raise self._wrap_exception("set_grating", exc) from exc

    def set_wavelength_nm(self, wavelength_nm: float) -> TLSStatus:
        target = float(wavelength_nm)
        try:
            device = self._require_device()
            device.set_wavelength(target)
            status = self._refresh_status(
                self._safe_get_status(),
                target_wavelength_nm=target,
                last_error=None,
            )
            self._publish_tls_state()
            return status
        except Exception as exc:
            raise self._wrap_exception("set_wavelength", exc) from exc

    def set_pass_through(self, timeout_s: float = 60.0) -> TLSStatus:
        try:
            device = self._require_device()
            self._last_status = TLSStatus(
                connected=self._last_status.connected,
                device_id=self._last_status.device_id,
                mono=self._last_status.mono,
                port_type=self._last_status.port_type,
                serial_number=self._last_status.serial_number,
                current_wavelength_nm=self._last_status.current_wavelength_nm,
                target_wavelength_nm=0.0,
                grating=self._last_status.grating,
                moving=True,
                last_error=None,
            )
            self._publish_tls_state()

            device.set_pass_through(timeout=float(timeout_s))

            status = self._refresh_status(
                self._safe_get_status(),
                target_wavelength_nm=0.0,
                moving=False,
                last_error=None,
            )
            self._publish_tls_state()
            return status
        except Exception as exc:
            raise self._wrap_exception("set_pass_through", exc) from exc

    def move(self, timeout_s: float = 60.0) -> TLSStatus:
        try:
            device = self._require_device()
            self._last_status = TLSStatus(
                connected=self._last_status.connected,
                device_id=self._last_status.device_id,
                mono=self._last_status.mono,
                port_type=self._last_status.port_type,
                serial_number=self._last_status.serial_number,
                current_wavelength_nm=self._last_status.current_wavelength_nm,
                target_wavelength_nm=self._last_status.target_wavelength_nm,
                grating=self._last_status.grating,
                moving=True,
                last_error=None,
            )
            self._publish_tls_state()

            device.move(timeout=float(timeout_s))

            status = self._refresh_status(
                self._safe_get_status(),
                moving=False,
                last_error=None,
            )
            self._publish_tls_state()
            return status
        except Exception as exc:
            raise self._wrap_exception("move", exc) from exc

    def wait_until_idle(
        self,
        *,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.2,
        tolerance_nm: float = 0.5,
    ) -> TLSStatus:
        try:
            device = self._require_device()
            self._last_status = TLSStatus(
                connected=self._last_status.connected,
                device_id=self._last_status.device_id,
                mono=self._last_status.mono,
                port_type=self._last_status.port_type,
                serial_number=self._last_status.serial_number,
                current_wavelength_nm=self._last_status.current_wavelength_nm,
                target_wavelength_nm=self._last_status.target_wavelength_nm,
                grating=self._last_status.grating,
                moving=True,
                last_error=None,
            )
            self._publish_tls_state()

            device.wait_until_idle(
                timeout=float(timeout_s),
                poll_interval=float(poll_interval_s),
                tolerance_nm=float(tolerance_nm),
            )
            status = self._refresh_status(
                self._safe_get_status(),
                moving=False,
                last_error=None,
            )
            self._publish_tls_state()
            return status
        except Exception as exc:
            raise self._wrap_exception("wait_until_idle", exc) from exc

    def get_status(self) -> TLSStatus:
        if self._device is None:
            return self._last_status

        try:
            return self._refresh_status(self._safe_get_status(), last_error=None)
        except Exception as exc:
            raise self._wrap_exception("get_status", exc) from exc

    def close(self) -> None:
        try:
            self.disconnect()
        finally:
            self._device = None

    # ----- status publishing -----

    def _publish_tls_state(self) -> None:
        if self._status_dir is None:
            return
        try:
            from diagnostics.run_status import write_tls_state

            s = self._last_status
            state: dict[str, Any] = {
                "connected": s.connected,
                "device_id": s.device_id,
                "mono": s.mono,
                "port_type": s.port_type,
                "serial_number": s.serial_number,
                "current_wavelength_nm": s.current_wavelength_nm,
                "target_wavelength_nm": s.target_wavelength_nm,
                "grating": s.grating,
                "moving": s.moving,
                "last_error": s.last_error,
            }
            write_tls_state(self._status_dir, state)
        except Exception:
            pass

    # ----- internals -----

    def _ensure_device(self) -> Any:
        if self._device is None:
            try:
                factory = self._device_factory or self._resolve_device_factory()
                self._device = factory()
            except Exception as exc:
                raise self._wrap_exception("initialize", exc) from exc
        return self._device

    def _require_device(self) -> Any:
        if self._device is None:
            raise TLSServiceError("operation", "device is not connected")
        return self._device

    def _resolve_device_factory(self) -> Callable[[], Any]:
        module = self._ensure_module()
        factory = getattr(module, "TLSC1", None) or getattr(module, "tls_c1", None)
        if factory is None:
            raise TLSServiceUnavailableError(
                "initialize",
                f"module {self._module_name!r} does not export TLSC1/tls_c1",
            )
        return factory

    def _ensure_module(self) -> Any:
        if self._module is not None:
            return self._module

        try:
            self._module = importlib.import_module(self._module_name)
        except ModuleNotFoundError as exc:
            detail = f"module {self._module_name!r} is not installed"
            if exc.name != self._module_name:
                detail = f"unable to import {self._module_name!r}: missing dependency {exc.name!r}"
            raise TLSServiceUnavailableError("import", detail) from exc
        except Exception as exc:
            raise TLSServiceUnavailableError("import", str(exc)) from exc

        return self._module

    def _safe_get_status(self) -> Any:
        if self._device is None:
            return None
        return self._device.get_status()

    def _refresh_status(self, raw_status: Any, **overrides: Any) -> TLSStatus:
        status = self._normalize_status(raw_status)
        values = {
            "connected": status.connected,
            "device_id": status.device_id,
            "mono": status.mono,
            "port_type": status.port_type,
            "serial_number": status.serial_number,
            "current_wavelength_nm": status.current_wavelength_nm,
            "target_wavelength_nm": status.target_wavelength_nm,
            "grating": status.grating,
            "moving": status.moving,
            "last_error": status.last_error,
        }
        values.update({key: value for key, value in overrides.items() if value is not None or key == "last_error"})
        self._last_status = TLSStatus(**values)
        return self._last_status

    def _normalize_status(self, raw_status: Any) -> TLSStatus:
        if raw_status is None:
            return self._last_status

        status = TLSStatus(
            connected=bool(self._read_attr(raw_status, "connected", self._last_status.connected)),
            device_id=self._to_int(self._read_attr(raw_status, "device_id", self._last_status.device_id)),
            mono=self._to_str(self._read_attr(raw_status, "mono", self._last_status.mono)),
            port_type=self._to_str(self._read_attr(raw_status, "port_type", self._last_status.port_type)),
            serial_number=self._to_str(
                self._read_attr(raw_status, "serial_number", self._last_status.serial_number)
            ),
            current_wavelength_nm=self._to_float(
                self._read_attr(raw_status, "current_wavelength_nm", self._last_status.current_wavelength_nm)
            ),
            target_wavelength_nm=self._to_float(
                self._read_attr(raw_status, "target_wavelength_nm", self._last_status.target_wavelength_nm)
            ),
            grating=self._to_int(self._read_attr(raw_status, "grating", self._last_status.grating)),
            moving=bool(self._read_attr(raw_status, "moving", self._last_status.moving)),
            last_error=self._to_str(self._read_attr(raw_status, "last_error", self._last_status.last_error)),
        )
        return status

    def _disconnected_status(self) -> TLSStatus:
        return TLSStatus(
            connected=False,
            mono=self._last_status.mono,
            port_type=self._last_status.port_type,
            serial_number=self._last_status.serial_number,
            target_wavelength_nm=self._last_status.target_wavelength_nm,
            grating=self._last_status.grating,
            moving=False,
            last_error=None,
        )

    def _wrap_exception(self, operation: str, exc: Exception) -> TLSServiceError:
        if isinstance(exc, TLSServiceError):
            wrapped = exc
        else:
            message = str(exc) or exc.__class__.__name__
            error_cls: type[TLSServiceError] = TLSServiceError
            module = self._module
            if isinstance(exc, TimeoutError):
                error_cls = TLSServiceTimeoutError
            elif module is not None:
                timeout_types = tuple(
                    cls
                    for cls in (
                        getattr(module, "TLSC1TimeoutError", None),
                        getattr(module, "TLSC1MoveTimeoutError", None),
                    )
                    if isinstance(cls, type)
                )
                base_types = tuple(
                    cls
                    for cls in (
                        getattr(module, "TLSC1Error", None),
                        getattr(module, "TLSC1ConnectionError", None),
                        getattr(module, "TLSC1APIError", None),
                        getattr(module, "TLSC1ValidationError", None),
                    )
                    if isinstance(cls, type)
                )
                if timeout_types and isinstance(exc, timeout_types):
                    error_cls = TLSServiceTimeoutError
                elif base_types and isinstance(exc, base_types):
                    error_cls = TLSServiceError
            wrapped = error_cls(operation, message)

        self._last_status = TLSStatus(
            connected=self._last_status.connected,
            device_id=self._last_status.device_id,
            mono=self._last_status.mono,
            port_type=self._last_status.port_type,
            serial_number=self._last_status.serial_number,
            current_wavelength_nm=self._last_status.current_wavelength_nm,
            target_wavelength_nm=self._last_status.target_wavelength_nm,
            grating=self._last_status.grating,
            moving=False,
            last_error=str(wrapped),
        )
        self._publish_tls_state()
        return wrapped

    @staticmethod
    def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None:
            return None
        return int(value)

    @staticmethod
    def _to_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text or None
