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

from pywinauto import Application  # kept for reference / documentation only
from typing import Union


class LegacyPywinautoTLS:
    """
    **DEPRECATED** – pywinauto-based GUI automation class.

    Previously used to control the Zolix TLS-C1 vendor GUI window.
    Superseded by ``TLSService`` (``devices/tls_service.py``).

    Do **NOT** instantiate this class in any active code path.
    """

    def __init__(self, path="C:/Program Files (x86)/Zolix/TLS/CEmenTool.exe"):
        self.app = Application(backend="uia").start(path)
        self.window = self.app.window(title="TLS-C1")
        self.window.wait("ready", timeout=20)
        self.wavelength = self.window.child_window(control_type="Edit", auto_id='1013')
        self.wave_shift = self.window.child_window(control_type="Edit", auto_id='1010')
        self.move_button = self.window.child_window(control_type="Button", auto_id='1014')
        self.grating = self.window.child_window(control_type="ComboBox", auto_id='1144')

    def __del__(self):
        self.app.kill()

    def renew(self):
        self.__init__()

    def set_wavelength(self, wavelength: Union[int, str, float]):
        self.wavelength.set_text(str(wavelength))
        self.wave_shift.set_text('0')
        self.move()

    def move(self):
        self.move_button.wait(wait_for='visible', timeout=10)
        self.move_button.click()
        self.window.wait("ready", timeout=40)

    def set_grating(self, num):
        self.grating.select(str(num))

    def connect(self, Mono='Omni', port_type='USB', serial_number='OM319069'):
        self.disconnect()
        self.window.set_focus()
        self.window.type_keys("%i")
        instrument = self.window.child_window(title='Instrument')
        instrument.click_input()
        _connect = instrument.child_window(title='Mono Connect')
        _connect.click_input()
        connect_setting = self.window.child_window(title='Com Setting')
        # connect_setting.child_window(control_type="ComboBox", auto_id='1119').select(Mono)
        # connect_setting.child_window(control_type="ComboBox", auto_id='1117').select(port_type)
        # connect_setting.child_window(control_type="ComboBox", auto_id='1001').select(serial_number)
        connect_setting.child_window(control_type="Button", auto_id='1').click()
        self.move_button.wait(wait_for='visible', timeout=25)

    def disconnect(self):
        self.window.set_focus()
        self.window.type_keys("%i")
        instrument = self.window.child_window(title='Instrument')
        instrument.click_input()
        _disconnect = instrument.child_window(title='Mono Disconnect')
        _disconnect.click_input()
