from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import numpy as np

from capture.preview_worker import PreviewWorker
from devices.camera_service import CameraServiceClient
from devices.frame_stream import FramePacket
from devices.lcd_service import LCDService
from devices.tls_service import TLSService, TLSServiceError, TLSStatus

from .bus import EventBus
from .commands import (
    ApplyCameraSettings,
    Command,
    ConnectTLS,
    DisconnectTLS,
    MoveTLS,
    RefreshCameraSettings,
    RefreshTLSStatus,
    SetLCDAllOpaque,
    SetLCDAllTransmissive,
    SetTLSGrating,
    SetTLSWavelength,
    ShowLCDDebugPattern,
    ShowLCDMonoMask,
    Shutdown,
)
from .events import (
    CameraError,
    CameraSettingsApplied,
    CameraSettingsRefreshed,
    LCDError,
    LCDAllOpaqueShown,
    LCDAllTransmissiveShown,
    LCDDebugPatternShown,
    LCDMaskShown,
    LCDStatusChanged,
    PreviewFrameUpdated,
    PreviewStatsUpdated,
    StatusMessage,
    TLSConnected,
    TLSDisconnected,
    TLSError,
    TLSMoveFinished,
    TLSMoveStarted,
    TLSStatusUpdated,
    TLSWavelengthTargetSet,
)
from .state import CameraSettingSnapshot, StateStore


GUI_EDITABLE_CAMERA_SETTINGS = frozenset({
    "EXPOSURE",
    "GAIN",
    "SHUTTER",
})


def is_gui_editable_camera_setting(name: str) -> bool:
    return str(name).strip().upper() in GUI_EDITABLE_CAMERA_SETTINGS


class SessionController:
    """
    Controller layer for the hardware-backed camera preview and minimal LCD control.

    It coordinates camera services, the preview worker, and LCD service while
    keeping GUI callbacks free of direct device logic.
    """

    def __init__(
        self,
        camera_service: CameraServiceClient,
        preview_worker: PreviewWorker,
        lcd_service: LCDService | None = None,
        tls_service: TLSService | None = None,
        camera_index: int = 0,
        context_type: str = "IIDC",
        preconfigure: bool = False,
    ):
        self.camera_service = camera_service
        self.preview_worker = preview_worker
        self.lcd_service = lcd_service
        self.tls_service = tls_service
        self.camera_index = camera_index
        self.context_type = context_type
        self.preconfigure = preconfigure

        self.bus = EventBus()
        self.state = StateStore()

        self.preview_worker.on_frame = self._handle_preview_packet
        self.preview_worker.on_error = self._handle_preview_error
        self._started = False
        self._shutting_down = False
        self._tls_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="TLSController",
        )
        self._tls_futures: set[Future[None]] = set()

    def start(self) -> None:
        if self._started:
            return

        self._shutting_down = False
        try:
            if self.preconfigure:
                self.camera_service.open_camera_gui(
                    index=self.camera_index,
                    context_type=self.context_type,
                )

            open_reply = self.camera_service.open_camera(
                index=self.camera_index,
                context_type=self.context_type,
                disable_trigger=True,
            )
            self.state.update(
                camera_open=True,
                last_error=None,
                camera_serial=str(open_reply.get("serial") or "") or None,
                frame_width=int(open_reply.get("width") or 0),
                frame_height=int(open_reply.get("height") or 0),
                frame_stride=int(open_reply.get("stride") or 0),
                pixel_format=str(open_reply.get("format") or "") or None,
            )
            self.state.update(**self._connection_state_updates())

            self._refresh_camera_settings(publish_event=False)

            self.camera_service.start_stream()
            self.state.update(stream_running=True, last_error=None, sidecar_running=True)

            self._initialize_lcd()

            self.preview_worker.start()
            self._started = True
            self.bus.publish(StatusMessage("success", self._build_start_message()))
        except Exception as exc:
            self.state.update(last_error=str(exc))
            self.bus.publish(CameraError(source="startup", message=str(exc)))
            self.bus.publish(StatusMessage("error", f"Failed to start camera session: {exc}"))
            try:
                self.shutdown(force=True)
            except Exception:
                pass
            raise

    def dispatch(self, command: Command) -> None:
        try:
            if isinstance(command, ApplyCameraSettings):
                self._apply_camera_settings(command.settings)
            elif isinstance(command, RefreshCameraSettings):
                self._refresh_camera_settings(publish_event=True)
            elif isinstance(command, ConnectTLS):
                self._connect_tls(command)
            elif isinstance(command, DisconnectTLS):
                self._disconnect_tls()
            elif isinstance(command, SetTLSGrating):
                self._set_tls_grating(command.grating)
            elif isinstance(command, SetTLSWavelength):
                self._set_tls_wavelength(command.wavelength_nm)
            elif isinstance(command, MoveTLS):
                self._move_tls(command.timeout_s)
            elif isinstance(command, RefreshTLSStatus):
                self._refresh_tls_status()
            elif isinstance(command, SetLCDAllTransmissive):
                self.show_lcd_all_transmissive()
            elif isinstance(command, SetLCDAllOpaque):
                self.show_lcd_all_opaque()
            elif isinstance(command, ShowLCDMonoMask):
                self.show_lcd_mono_mask(command.mask, mask_id=command.mask_id)
            elif isinstance(command, ShowLCDDebugPattern):
                self.show_lcd_debug_pattern(command.pattern_name)
            elif isinstance(command, Shutdown):
                self.shutdown(force=command.force)
            else:
                raise RuntimeError(f"Unknown command: {type(command).__name__}")
        except Exception as exc:
            source = self._classify_command_source(command)
            self.state.update(last_error=str(exc))
            if source == "lcd_command":
                self.state.update(lcd_last_error=str(exc))
                self.bus.publish(LCDError(source=source, message=str(exc)))
            elif source == "tls_command":
                self.state.update(tls_last_error=str(exc), tls_moving=False)
                self.bus.publish(TLSError(source=source, message=str(exc)))
                self._publish_tls_status_updated()
            else:
                self.bus.publish(CameraError(source=source, message=str(exc)))
            self.bus.publish(StatusMessage("error", str(exc)))

    def list_camera_settings(self) -> list[CameraSettingSnapshot]:
        state = self.state.get()
        snapshots: list[CameraSettingSnapshot] = []
        for name in sorted(state.camera_settings):
            if not is_gui_editable_camera_setting(name):
                continue
            if name not in state.camera_setting_ranges:
                continue
            value = state.camera_settings[name]
            min_value, max_value = state.camera_setting_ranges[name]
            snapshots.append(
                CameraSettingSnapshot(
                    name=name,
                    min_value=float(min_value),
                    max_value=float(max_value),
                    value=float(value),
                )
            )
        return snapshots

    def set_bayer_pattern(self, pattern: str | None) -> None:
        self.preview_worker.stream.set_bayer_pattern(pattern)

    def _connect_tls(self, command: ConnectTLS) -> None:
        service = self._require_tls_service()
        status = service.connect(
            mono=command.mono,
            port_type=command.port_type,
            serial_number=command.serial_number,
        )
        self._apply_tls_status(status)
        self.bus.publish(TLSConnected(device_id=status.device_id))
        self._publish_tls_status_updated()
        self.bus.publish(StatusMessage("success", self._build_tls_connected_message(status)))

    def _disconnect_tls(self) -> None:
        service = self._require_tls_service()
        previous_device_id = self.state.get().tls_device_id
        status = service.disconnect()
        self._apply_tls_status(status)
        self.bus.publish(TLSDisconnected(device_id=previous_device_id))
        self._publish_tls_status_updated()
        self.bus.publish(StatusMessage("info", "TLS disconnected"))

    def _set_tls_grating(self, grating: int) -> None:
        service = self._require_tls_service()
        status = service.set_grating(grating)
        self._apply_tls_status(status)
        self._publish_tls_status_updated()
        self.bus.publish(StatusMessage("info", f"TLS grating set to {int(grating)}"))

    def _set_tls_wavelength(self, wavelength_nm: float) -> None:
        service = self._require_tls_service()
        status = service.set_wavelength_nm(wavelength_nm)
        self._apply_tls_status(status)
        self.bus.publish(TLSWavelengthTargetSet(target_wavelength_nm=float(wavelength_nm)))
        self._publish_tls_status_updated()
        self.bus.publish(StatusMessage("info", f"TLS target wavelength set to {float(wavelength_nm):.3f} nm"))

    def _move_tls(self, timeout_s: float) -> None:
        service = self._require_tls_service()
        target = self.state.get().tls_target_wavelength_nm
        self.state.update(
            tls_moving=True,
            tls_last_error=None,
            last_error=None,
        )
        self.bus.publish(TLSMoveStarted(target_wavelength_nm=target))
        self._publish_tls_status_updated()
        self.bus.publish(StatusMessage("info", "TLS move started"))

        def run_move() -> None:
            try:
                status = service.move(timeout_s=timeout_s)
                self._apply_tls_status(status)
                self.bus.publish(
                    TLSMoveFinished(
                        current_wavelength_nm=status.current_wavelength_nm,
                        target_wavelength_nm=status.target_wavelength_nm,
                    )
                )
                self._publish_tls_status_updated()
                self.bus.publish(StatusMessage("success", self._build_tls_move_message(status)))
            except Exception as exc:
                self._handle_tls_error("move", exc)

        self._submit_tls_task(run_move)

    def _refresh_tls_status(self) -> None:
        service = self._require_tls_service()
        status = service.get_status()
        self._apply_tls_status(status)
        self._publish_tls_status_updated()

    def show_lcd_all_transmissive(self, publish_status: bool = True) -> None:
        service = self._require_lcd_service()
        metadata = service.get_metadata()
        packed = service.show_all_transmissive()
        self._update_lcd_state(
            metadata=metadata,
            current_mode="all_transmissive",
            current_mask_id="all_transmissive",
            last_error=None,
        )
        physical_shape = metadata["physical_shape"]
        self.bus.publish(
            LCDAllTransmissiveShown(
                physical_shape=physical_shape,
                packed_shape=tuple(packed.shape),
            )
        )
        self._publish_lcd_status_changed()
        if publish_status:
            self.bus.publish(StatusMessage("success", "LCD set to all-transmissive"))

    def show_lcd_all_opaque(self) -> None:
        service = self._require_lcd_service()
        metadata = service.get_metadata()
        packed = service.show_all_opaque()
        self._update_lcd_state(
            metadata=metadata,
            current_mode="all_opaque",
            current_mask_id="all_opaque",
            last_error=None,
        )
        physical_shape = metadata["physical_shape"]
        self.bus.publish(
            LCDAllOpaqueShown(
                physical_shape=physical_shape,
                packed_shape=tuple(packed.shape),
            )
        )
        self._publish_lcd_status_changed()
        self.bus.publish(StatusMessage("success", "LCD set to all-opaque"))

    def show_lcd_mono_mask(self, mask: np.ndarray, mask_id: str | None = None) -> None:
        service = self._require_lcd_service()
        metadata = service.get_metadata()
        packed = service.show_mono_mask(mask, mask_id=mask_id, mode="mono_mask")
        effective_mask_id = mask_id or "custom_mask"
        self._update_lcd_state(
            metadata=metadata,
            current_mode="mono_mask",
            current_mask_id=effective_mask_id,
            last_error=None,
        )
        self.bus.publish(
            LCDMaskShown(
                mask_id=effective_mask_id,
                physical_shape=metadata["physical_shape"],
                packed_shape=tuple(packed.shape),
            )
        )
        self._publish_lcd_status_changed()
        self.bus.publish(StatusMessage("success", f"LCD mono mask shown: {effective_mask_id}"))

    def show_lcd_debug_pattern(self, pattern_name: str) -> None:
        service = self._require_lcd_service()
        metadata = service.get_metadata()
        packed = service.show_debug_pattern(pattern_name)
        self._update_lcd_state(
            metadata=metadata,
            current_mode="debug_pattern",
            current_mask_id=pattern_name,
            last_error=None,
        )
        self.bus.publish(
            LCDDebugPatternShown(
                pattern_name=pattern_name,
                physical_shape=metadata["physical_shape"],
                packed_shape=tuple(packed.shape),
            )
        )
        self._publish_lcd_status_changed()
        self.bus.publish(StatusMessage("success", f"LCD debug pattern shown: {pattern_name}"))

    def _refresh_camera_settings(self, publish_event: bool) -> dict[str, float]:
        info = self.camera_service.get_camera_info()
        state = self.state.get()
        names = list(info.get("setting_names", []))

        self.state.update(
            camera_serial=str(info.get("serial") or "") or state.camera_serial,
            frame_width=int(info.get("width") or state.frame_width),
            frame_height=int(info.get("height") or state.frame_height),
            pixel_format=str(info.get("pix_fmt") or state.pixel_format or "") or None,
        )

        settings: dict[str, float] = {}
        ranges: dict[str, tuple[float, float]] = {}
        for name in names:
            try:
                min_value, max_value = self.camera_service.get_range(name)
                current_value = self.camera_service.get_value(name)
            except Exception as exc:
                self.bus.publish(StatusMessage("warning", f"Skipped camera setting {name}: {exc}"))
                continue

            if float(min_value) == float(max_value):
                continue

            settings[name] = float(current_value)
            ranges[name] = (float(min_value), float(max_value))

        self.state.update(
            camera_settings=settings,
            camera_setting_ranges=ranges,
            last_error=None,
        )

        if publish_event:
            self.bus.publish(CameraSettingsRefreshed(settings=settings))
            self.bus.publish(StatusMessage("info", f"Refreshed {len(settings)} camera settings"))

        return settings

    def _apply_camera_settings(self, settings: dict[str, float]) -> None:
        if not settings:
            self.bus.publish(StatusMessage("info", "No camera settings to apply"))
            return

        requested = {name: float(value) for name, value in settings.items()}
        disallowed = sorted(name for name in requested if not is_gui_editable_camera_setting(name))
        if disallowed:
            raise RuntimeError(
                "Camera setting edits are restricted to exposure/shutter and gain; "
                f"read-only setting(s): {', '.join(disallowed)}"
            )
        applied: dict[str, float] = {}
        for name, value in requested.items():
            self.camera_service.set_value(name, value)
            applied[name] = float(self.camera_service.get_value(name))

        state = self.state.get()
        merged = dict(state.camera_settings)
        merged.update(applied)
        self.state.update(camera_settings=merged, last_error=None)

        self.bus.publish(
            CameraSettingsApplied(
                requested_settings=requested,
                applied_settings=applied,
            )
        )
        self.bus.publish(StatusMessage("success", self._build_settings_message(requested, applied)))

    def _handle_preview_packet(self, packet: FramePacket) -> None:
        meta = packet.meta
        max_pixel = float(np.max(packet.raw))
        state = self.state.get()
        frame_seq = int(meta.get("seq", -1))
        timestamp_ns = int(meta.get("ts_ns", 0))
        width = int(meta.get("width", packet.preview_bgr.shape[1]))
        height = int(meta.get("height", packet.preview_bgr.shape[0]))
        stride = int(meta.get("stride", 0))
        pixel_format = str(meta.get("format", state.pixel_format or ""))

        self.state.update(
            latest_preview_bgr=packet.preview_bgr,
            latest_max_pixel=max_pixel,
            latest_frame_seq=frame_seq,
            latest_frame_timestamp_ns=timestamp_ns,
            frame_width=width,
            frame_height=height,
            frame_stride=stride,
            pixel_format=pixel_format or None,
            last_error=None,
        )
        self.bus.publish(PreviewFrameUpdated(preview_bgr=packet.preview_bgr))
        self.bus.publish(
            PreviewStatsUpdated(
                max_pixel=max_pixel,
                frame_seq=frame_seq,
                timestamp_ns=timestamp_ns,
                width=width,
                height=height,
                stride=stride,
                pixel_format=pixel_format,
            )
        )

    def _handle_preview_error(self, exc: Exception) -> None:
        if self._shutting_down:
            return
        self.state.update(last_error=str(exc))
        self.bus.publish(CameraError(source="preview", message=str(exc)))
        self.bus.publish(StatusMessage("warning", f"Preview warning: {exc}"))

    def shutdown(self, force: bool = False) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        first_error: Exception | None = None

        def run_step(fn) -> None:
            nonlocal first_error
            try:
                fn()
            except Exception as exc:
                if first_error is None:
                    first_error = exc

        try:
            state = self.state.get()
            run_step(self.preview_worker.stop)

            if state.stream_running:
                run_step(self.camera_service.stop_stream)

            if state.camera_open:
                run_step(self.camera_service.close_camera)

            run_step(self.camera_service.close)

            if self.lcd_service is not None:
                run_step(self.lcd_service.close)

            if self.tls_service is not None:
                run_step(self.tls_service.close)

            closed_cleanly = first_error is None
        finally:
            self._cancel_tls_tasks()
            connection = self.camera_service.get_connection_status()
            self.state.update(
                camera_open=False,
                stream_running=False,
                sidecar_running=bool(connection.get("sidecar_running")),
                sidecar_owned=bool(connection.get("own_sidecar")),
                sidecar_pid=connection.get("sidecar_pid"),
                lcd_connected=False,
                lcd_current_mode=None,
                lcd_current_mask_id=None,
                tls_connected=False,
                tls_device_id=None,
                tls_current_wavelength_nm=None,
                tls_target_wavelength_nm=None,
                tls_grating=None,
                tls_moving=False,
                tls_last_error=None,
            )
            self._started = False
            self._shutting_down = False

        if first_error is not None and not force:
            raise first_error

        if closed_cleanly:
            self._publish_lcd_status_changed()
            self._publish_tls_status_updated()
            self.bus.publish(StatusMessage("info", "Camera session shut down"))

    def _initialize_lcd(self) -> None:
        if self.lcd_service is None:
            return

        try:
            metadata = self.lcd_service.get_metadata()
            self._update_lcd_state(
                metadata=metadata,
                current_mode=metadata.get("current_mode"),
                current_mask_id=metadata.get("current_mask_id"),
                last_error=None,
            )
            self.show_lcd_all_transmissive(publish_status=False)
            self.bus.publish(StatusMessage("info", "LCD initialized and set to all-transmissive"))
        except Exception as exc:
            self.state.update(
                lcd_connected=False,
                lcd_last_error=str(exc),
            )
            self.bus.publish(LCDError(source="startup", message=str(exc)))
            self.bus.publish(StatusMessage("warning", f"LCD startup warning: {exc}"))

    def _update_lcd_state(
        self,
        *,
        metadata: dict[str, object],
        current_mode: str | None,
        current_mask_id: str | None,
        last_error: str | None,
    ) -> None:
        self.state.update(
            lcd_connected=True,
            lcd_display_index=int(metadata.get("display_index")) if metadata.get("display_index") is not None else None,
            lcd_reported_shape=tuple(metadata.get("reported_shape")) if metadata.get("reported_shape") is not None else None,
            lcd_physical_shape=tuple(metadata.get("physical_shape")) if metadata.get("physical_shape") is not None else None,
            lcd_current_mode=current_mode,
            lcd_current_mask_id=current_mask_id,
            lcd_transmissive_code=int(metadata.get("transmissive_code")) if metadata.get("transmissive_code") is not None else None,
            lcd_opaque_code=int(metadata.get("opaque_code")) if metadata.get("opaque_code") is not None else None,
            lcd_last_error=last_error,
        )

    def _publish_lcd_status_changed(self) -> None:
        state = self.state.get()
        self.bus.publish(
            LCDStatusChanged(
                connected=state.lcd_connected,
                current_mode=state.lcd_current_mode,
                current_mask_id=state.lcd_current_mask_id,
                reported_shape=state.lcd_reported_shape,
                physical_shape=state.lcd_physical_shape,
            )
        )

    def _apply_tls_status(self, status: TLSStatus) -> None:
        self.state.update(
            tls_connected=status.connected,
            tls_device_id=status.device_id,
            tls_current_wavelength_nm=status.current_wavelength_nm,
            tls_target_wavelength_nm=status.target_wavelength_nm,
            tls_grating=status.grating,
            tls_moving=status.moving,
            tls_last_error=status.last_error,
            last_error=status.last_error,
        )

    def _publish_tls_status_updated(self) -> None:
        state = self.state.get()
        self.bus.publish(
            TLSStatusUpdated(
                connected=state.tls_connected,
                device_id=state.tls_device_id,
                current_wavelength_nm=state.tls_current_wavelength_nm,
                target_wavelength_nm=state.tls_target_wavelength_nm,
                grating=state.tls_grating,
                moving=state.tls_moving,
                last_error=state.tls_last_error,
            )
        )

    def _require_tls_service(self) -> TLSService:
        if self.tls_service is None:
            raise RuntimeError("TLS service is not configured")
        return self.tls_service

    def _submit_tls_task(self, fn) -> None:
        if self._tls_executor is None:
            self._tls_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TLSController")
        future = self._tls_executor.submit(fn)
        self._tls_futures.add(future)
        future.add_done_callback(self._tls_futures.discard)

    def _cancel_tls_tasks(self) -> None:
        for future in list(self._tls_futures):
            future.cancel()
        self._tls_futures.clear()
        if self._tls_executor is not None:
            self._tls_executor.shutdown(wait=False, cancel_futures=True)
            self._tls_executor = None

    def _handle_tls_error(self, source: str, exc: Exception) -> None:
        if self._shutting_down:
            return
        self.state.update(
            tls_moving=False,
            tls_last_error=str(exc),
            last_error=str(exc),
        )
        self.bus.publish(TLSError(source=source, message=str(exc)))
        self._publish_tls_status_updated()
        self.bus.publish(StatusMessage("error", str(exc)))

    def _require_lcd_service(self) -> LCDService:
        if self.lcd_service is None:
            raise RuntimeError("LCD service is not configured")
        return self.lcd_service

    @staticmethod
    def _classify_command_source(command: Command) -> str:
        name = type(command).__name__
        if name.startswith(("SetLCD", "ShowLCD")):
            return "lcd_command"
        if name.endswith("TLS") or name.startswith(("ConnectTLS", "DisconnectTLS", "SetTLS", "MoveTLS", "RefreshTLS")):
            return "tls_command"
        return "command"

    def _connection_state_updates(self) -> dict[str, object]:
        info = self.camera_service.get_connection_status()
        return {
            "sidecar_running": bool(info.get("sidecar_running")),
            "sidecar_owned": bool(info.get("own_sidecar")),
            "sidecar_pid": info.get("sidecar_pid"),
            "sidecar_rep_addr": str(info.get("rep_addr") or self.state.get().sidecar_rep_addr),
        }

    def _build_start_message(self) -> str:
        state = self.state.get()
        serial = state.camera_serial or "unknown"
        dims = (
            f"{state.frame_width}x{state.frame_height}"
            if state.frame_width and state.frame_height
            else "unknown size"
        )
        pixel_format = state.pixel_format or "unknown format"
        sidecar_mode = "owned sidecar" if state.sidecar_owned else "external sidecar"
        lcd_status = "lcd ready" if state.lcd_connected else "lcd unavailable"
        return f"Camera {serial} open, stream running, {dims}, {pixel_format}, {sidecar_mode}, {lcd_status}"

    @staticmethod
    def _build_tls_connected_message(status: TLSStatus) -> str:
        details: list[str] = []
        if status.device_id is not None:
            details.append(f"device {status.device_id}")
        if status.grating is not None:
            details.append(f"grating {status.grating}")
        if status.current_wavelength_nm is not None:
            details.append(f"{status.current_wavelength_nm:.3f} nm")
        return f"TLS connected ({', '.join(details)})" if details else "TLS connected"

    @staticmethod
    def _build_tls_move_message(status: TLSStatus) -> str:
        if status.current_wavelength_nm is None:
            return "TLS move finished"
        return f"TLS move finished at {status.current_wavelength_nm:.3f} nm"

    @staticmethod
    def _build_settings_message(
        requested: dict[str, float],
        applied: dict[str, float],
    ) -> str:
        parts: list[str] = []
        for name in sorted(applied)[:3]:
            parts.append(f"{name} {requested[name]:.2f}->{applied[name]:.2f}")
        suffix = " ..." if len(applied) > 3 else ""
        return f"Updated camera settings: {', '.join(parts)}{suffix}"
