import tkinter as tk
from queue import Queue
from tkinter import ttk
from typing import Callable

import numpy as np
import pygame
from numpy import ndarray

from cam import Video, video_init
from lcd import lcd_init, LCDDisplay


class CameraControlGUI:
    def __init__(self,
                 video_init_fun: Callable[[], Video],
                 lcd_init_fun: Callable[[], LCDDisplay],
                 switch_sets: ndarray):
        lcd: LCDDisplay = lcd_init_fun()
        img = np.ones((H, W, 3), dtype=np.uint8) * 255
        lcd.show(img)
        self.lcd = lcd
        self.video = video_init_fun()
        self.root = tk.Tk()
        self.root.title("Camera Settings")
        self.setting_widgets = {}
        self.create_gui()
        self.update_queue = Queue()
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self.switch_img_sets = switch_sets
        self.show_id = 0
        # Legacy: old pywinauto TLS was instantiated and wavelength was set here.
        # Superseded by control path:
        #   controller.dispatch(ConnectTLS(...))
        #   controller.dispatch(SetTLSWavelength(...))
        #   controller.dispatch(MoveTLS(...))

        # 设置定时检查队列
        self.root.after(100, self.check_queue)
        self.root.after(100, self.update_max_pixel)  # 新增：定时刷新最大像素显示
        lcd.show(switch_sets[self.show_id])

    def start(self) -> None:
        self.video.start()
        self.root.mainloop()


    def create_gui(self) -> None:
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill='both', expand=True)

        # 参数控制区域
        for setting in self.video.setting_names:
            try:
                min_val, max_val = self.video.get_range(setting)
                current_val = self.video.get_value(setting)
            except Exception as e:
                print(f"跳过参数 {setting} (不支持): {str(e)}")
                continue
            if min_val == max_val:
                continue
            frame = ttk.Frame(main_frame)
            frame.pack(fill='x', pady=2)

            lbl = ttk.Label(frame, text=f"{setting}:", width=15, anchor='w')
            lbl.pack(side='left')

            entry = ttk.Entry(frame, width=10)
            entry.insert(0, f"{current_val:.2f}")
            entry.pack(side='left', padx=5)

            range_lbl = ttk.Label(frame, text=f"[{min_val:.2f} - {max_val:.2f}]")
            range_lbl.pack(side='left')

            self.setting_widgets[setting] = (entry, min_val, max_val)


        # 按钮区域
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)
        ok_btn = ttk.Button(
            button_frame,
            text="SWITCH",
            command=self.on_switch_clicked
        )
        ok_btn.pack(side='right', padx=5)
        next_btn = ttk.Button(
            button_frame,
            text="SAVE",
            command=self.on_save_clicked
        )
        next_btn.pack(side='right', padx=5)
        apply_btn = ttk.Button(
            button_frame,
            text="APPLY",
            command=self.on_apply_clicked
        )
        apply_btn.pack(side='right', padx=5)
        capture_btn = ttk.Button(
            button_frame,
            text="CAPTURE",
            command=self.on_capture_clicked
        )
        capture_btn.pack(side='right', padx=5)
        capture_btn = ttk.Button(
            button_frame,
            text="TURN",
            command=self.on_turn_clicked
        )
        capture_btn.pack(side='right', padx=5)
        # ===== 实时监控区域（新增）=====
        monitor_frame = ttk.LabelFrame(main_frame, text="实时监控")
        monitor_frame.pack(fill='x', pady=10)

        ttk.Label(monitor_frame, text="最大像素值:").pack(side='left')

        # 数值显示
        self.max_pixel_var = tk.StringVar(value="--")
        ttk.Label(monitor_frame, textvariable=self.max_pixel_var, width=6).pack(side='left', padx=8)

        # 进度条显示（按 8-bit 255 缩放；更高位深请调整 maximum）
        self.max_pixel_bar = ttk.Progressbar(
            monitor_frame, length=240, mode='determinate', maximum=65535
        )
        self.max_pixel_bar.pack(side='left', padx=8)

    def update_max_pixel(self) -> None:
        """定时读取并显示当前帧最大像素值。"""
        try:
            maxpix = float(self.video.max_pixels)  # 假定 video 线程安全地维护该属性
            # 数字显示（取整更直观；若需小数可改为:.2f）
            self.max_pixel_var.set(f"{maxpix:.0f}")

            # 进度条更新（夹紧到 [0, maximum]）
            vmax = int(self.max_pixel_bar['maximum'])
            v = max(0, min(vmax, int(maxpix)))
            self.max_pixel_bar['value'] = v
        except Exception:
            # 读取异常时不给用户刷屏报错，只做降级显示
            self.max_pixel_var.set("--")
            # 也可按需: self.max_pixel_bar['value'] = 0
        finally:
            # 维持刷新节奏；100ms 已足够平滑，且不会给主线程增加明显负担
            if self.root.winfo_exists():
                self.root.after(100, self.update_max_pixel)

    def on_setting_change(self, setting: dict, value_str: str) -> None:
        _, min_val, max_val = self.setting_widgets[setting]
        try:
            value = float(value_str)
            if min_val <= value <= max_val:
                self.update_queue.put(('setting', (setting, value)))
            else:
                self.show_message("error", "数值超出允许范围！")
        except ValueError:
            self.show_message("error", "请输入有效数字！")

    def on_capture_clicked(self, aver_n: int = 100) -> None:
        # 保存当前图像，捕捉多帧求平均，向视频线程发起捕捉请求
        try:
            raw_img, rgb_img = self.video.get_aver_frame(aver_n=aver_n)
            # rgb_name = f"rgb_image_{self.show_id}_{self.__length}nm.png"
            # raw_name = f"raw_image_{self.show_id}_{self.__length}nm.npy"
            # cv2.imwrite(rgb_name, rgb_img)
            # np.save(raw_name, raw_img)
            # self.update_queue.put(('status', ('success', f"捕捉成功，保存为 {rgb_name}, f{raw_name}")))
        except Exception as e:
            self.update_queue.put(('status', ('error', f"捕捉失败: {str(e)}")))

    def on_turn_clicked(self) -> None:
        """
        **HISTORICAL** -- early wavelength-sweep capture loop.

        This is a legacy / prototype capture path from the pywinauto TLS
        era.  It is NOT an active capture path and must not be used as
        one.  The loop bypasses ``SessionController``, captures without
        metadata, and relies on deleted pywinauto TLS automation.

        Superseded by planned Phase 2 capture tasks
        (``tasks/capture_forward_dataset.py``) that will use the
        ``control.commands`` / ``SessionController`` / ``TLSService``
        stack and preserve raw capture HDF5 metadata.
        """
        for w in range(455, 655, 10):
            # Legacy: tls.set_wavelength(w) was called here.
            for i in range(0, self.switch_img_sets.shape[0]):
                self.lcd.show(self.switch_img_sets[i])
                self.show_id =  i
                self.on_capture_clicked(aver_n=  100)


    def on_save_clicked(self) -> None:
        # 保存当前设置（示例：打印参数值）
        print("\n当前参数设置：")
        for setting in self.setting_widgets:
            entry, _, _ = self.setting_widgets[setting]
            print(f"{setting}: {entry.get()}")
        # 将dict这个字典转换为json格式，并保存在task1.json这个文件里。每组key：value前缩进4个空格
        # with open("setting.json", "w") as out_files:
        #     json.dump(self.video_sets, out_files, indent=4)
        # 发送特殊退出命令
        self.update_queue.put(('command', 'safe_exit'))
        self.root.destroy()

    def on_switch_clicked(self) -> None:
        self.show_id += 1
        if self.show_id >= self.switch_img_sets.shape[0]:
            self.show_id = 0
        self.lcd.show(self.switch_img_sets[self.show_id])

    def on_window_close(self) -> None:
        self.video.stop()
        self.update_queue.put(('command', 'force_exit'))
        self.root.destroy()

    def on_apply_clicked(self) -> None:
        # 应用当前设置
        for setting in self.setting_widgets:
            entry, _, _ = self.setting_widgets[setting]
            try:
                value = float(entry.get())
                self.video.set_value(setting, value)
                current_val = (self.video.get_value(setting))
                if abs(current_val - value) > 0.01:
                    self.update_queue.put(('status', ('error', f"{setting} 更新失败, 当前值: {current_val}")))
                self.update_queue.put(('refresh', (setting, current_val)))
            except Exception as e:
                self.update_queue.put(('status', ('error', f"设置失败: {str(e)}")))
        self.update_queue.put(('status', ('success', f"更新完成")))

    def check_queue(self) -> None:
        while not self.update_queue.empty():
            item = self.update_queue.get()
            if item[0] == 'status':
                self.show_message(*item[1])
            elif item[0] == 'refresh':
                setting, value = item[1]
                if setting in self.setting_widgets:
                    entry, _, _ = self.setting_widgets[setting]
                    entry.delete(0, tk.END)
                    entry.insert(0, f"{value:.2f}")
        self.root.after(100, self.check_queue)

    def show_message(self, msg_type: str, message: str) -> None:
        label = ttk.Label(self.root, text=message,
                          foreground='red' if msg_type == 'error' else 'green')
        label.pack()
        self.root.after(3000, label.destroy)

    def __del__(self):
        self.video.stop()
        # Legacy: pywinauto TLS disconnect/del was called here.
        # Superseded by control.commands.DisconnectTLS.
        pygame.quit()
        print("程序已安全退出")

if __name__ == "__main__":
    W, H = 540, 2560  # LCD分辨率
    # 选择一种光栅（任选其一）
    switch_img_sets = np.ones((3, H, W, 3), dtype=np.uint8) * 255
    # 创建线程通信对象
    gui = CameraControlGUI(
        video_init_fun=video_init,
        lcd_init_fun=lcd_init,
        switch_sets=switch_img_sets
    )
    gui.start()

