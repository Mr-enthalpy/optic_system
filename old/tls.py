"""
DEPRECATED: pywinauto-based TLS GUI automation.

Superseded by ``devices/tls_service.py`` which wraps the ``tls_c1`` SDK
vendor library.  This module is retained for reference only and must not
be used as an active TLS backend.

New code should follow the documented path::

    GUI / CLI / task intent
      -> control command
      -> SessionController
      -> TLSService
      -> tls_c1 high-level API
      -> vendor SDK
"""

from __future__ import annotations

from typing import Union


class LegacyPywinautoTLS:
    """
    **DEPRECATED** -- pywinauto-based GUI automation class.

    Previously used to control the Zolix TLS-C1 vendor GUI window.
    Superseded by ``TLSService`` (``devices/tls_service.py``).

    Instantiation raises ``RuntimeError``.  The class body is retained for
    reference only.  Do **NOT** call this class in any active code path.
    """

    def __init__(self, path="C:/Program Files (x86)/Zolix/TLS/CEmenTool.exe"):
        raise RuntimeError(
            "Legacy pywinauto TLS path is deprecated. "
            "Use control.commands.ConnectTLS/SetTLSWavelength/MoveTLS "
            "via SessionController + TLSService."
        )

    # ---- reference implementation (kept for documentation) ----
    # The original implementation used pywinauto to automate the vendor GUI:
    #
    #     from pywinauto import Application
    #     self.app = Application(backend="uia").start(path)
    #     self.window = self.app.window(title="TLS-C1")
    #     self.window.wait("ready", timeout=20)
    #     self.wavelength = self.window.child_window(control_type="Edit", auto_id='1013')
    #     self.wave_shift = self.window.child_window(control_type="Edit", auto_id='1010')
    #     self.move_button = self.window.child_window(control_type="Button", auto_id='1014')
    #     self.grating = self.window.child_window(control_type="ComboBox", auto_id='1144')
    #
    # Methods: connect, disconnect, set_wavelength, set_grating, move, renew.
    # See git history for the full implementation.

    def __del__(self):
        pass  # no-op: instantiation is now forbidden

    def set_wavelength(self, wavelength: Union[int, str, float]):
        raise RuntimeError("Legacy pywinauto TLS path is deprecated")

    def move(self):
        raise RuntimeError("Legacy pywinauto TLS path is deprecated")

    def set_grating(self, num):
        raise RuntimeError("Legacy pywinauto TLS path is deprecated")

    def connect(self, Mono='Omni', port_type='USB', serial_number='OM319069'):
        raise RuntimeError("Legacy pywinauto TLS path is deprecated")

    def disconnect(self):
        raise RuntimeError("Legacy pywinauto TLS path is deprecated")
