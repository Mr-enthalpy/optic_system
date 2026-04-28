from pywinauto import Application
from typing import Union

class tls_c1:
    def __init__(self, path="C:/Program Files (x86)/Zolix/TLS/CEmenTool.exe"):
        self.app = Application(backend="uia").start(path)
        self.window = self.app.window(title="TLS-C1")
        # 显式等待主窗口完全就绪（包括子控件）
        self.window.wait("ready", timeout=20)
        # 其他控件初始化
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
        self.window.set_focus()  # 确保窗口获得焦点
        self.window.type_keys("%i")  # 发送 Alt 键（激活菜单栏）
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
        self.window.set_focus()  # 确保窗口获得焦点
        self.window.type_keys("%i")  # 发送 Alt 键（激活菜单栏）
        instrument = self.window.child_window(title='Instrument')
        instrument.click_input()
        _disconnect = instrument.child_window(title='Mono Disconnect')
        _disconnect.click_input()


if __name__ == "__main__":
    tls = tls_c1()
    # tls.connect()
    # tls.set_wavelength(405)
    # tls.disconnect()
    # del tls
