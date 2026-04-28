# -*- coding: utf-8 -*-
# Python 3.8 环境：pip install pyflycap2 pyzmq
import json
import sys
import threading
import time
from multiprocessing import shared_memory

import numpy as np
import zmq
from pyflycap2.interface import Camera, GUI  # 复用你的封装思路

RING = 8                         # 槽数：按带宽调
PORT_PUB = 6100                  # 帧元数据广播
PORT_REP = 6101                  # 控制命令
SHM_NAME = "flycap2_ring_A"      # 共享内存名

class MyCamLite:
    """精简版：去掉 GUI 弹窗，适合无头服务"""
    def __init__(self, index=0, context_type='IIDC'):
        self.cam = Camera(index=index, context_type=context_type)
        self.cam.connect()
        self.cam.start_capture()
        self.cam.read_next_image()
        self.config = self.cam.get_current_image_config()
        if self.config['pix_fmt'] not in ['rgb', 'raw8', 'raw16']:
            raise ValueError(f"不支持的像素格式: {self.config['pix_fmt']}")
        self.serial = self.cam.serial
        self.setting_names = self.cam.setting_names

    def set_cam_abs_setting_value(self, setting, value):
        self.cam.set_cam_abs_setting_value(setting, value)

    def get_cam_abs_setting_range(self, setting):
        return self.cam.get_cam_abs_setting_range(setting)

    def get_cam_abs_setting_value(self, setting):
        return self.cam.get_cam_abs_setting_value(setting)

    def capture(self):
        self.cam.read_next_image()
        image_data = self.cam.get_current_image()
        pix_fmt = self.config['pix_fmt']
        rows, cols = self.config['rows'], self.config['cols']
        if pix_fmt == 'rgb':
            img = np.frombuffer(image_data['buffer'], dtype=np.uint8).reshape((rows, cols, 3))
            return img[..., ::-1]  # RGB->BGR 视图翻转，无复制
        elif pix_fmt == 'raw8':
            raw = np.frombuffer(image_data['buffer'], dtype=np.uint8).reshape((rows, cols))
            return raw
            # return cv2.cvtColor(raw, cv2.COLOR_BayerGB2BGR)
        elif pix_fmt == 'raw16':
            raw = np.frombuffer(image_data['buffer'], dtype=np.uint16).reshape((rows, cols))
            return raw

    def close(self):
        try:
            self.cam.stop_capture()
        finally:
            self.cam.disconnect()

def main():
    ctx = zmq.Context.instance()
    try:
        pub = ctx.socket(zmq.PUB)
        pub.set_hwm(1)
        pub.bind(f"tcp://127.0.0.1:{PORT_PUB}")
        rep = ctx.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{PORT_REP}")
    except zmq.ZMQError as e:
        print(f"[FATAL] 端口被占用: {e}. 可能已有一个 service 在运行。")
        ctx.term()
        sys.exit(1)

    cam: MyCamLite | None = None
    shm: shared_memory.SharedMemory | None = None
    running: bool = False
    widx: int = 0
    seq: int = 0
    width = height = stride = 0

    stop_flag = threading.Event()

    def stream_loop():
        nonlocal widx, seq, running, width, height, stride
        while not stop_flag.is_set():
            if not running:
                time.sleep(0.01); continue
            # 额外防呆：未打开相机或未分配共享内存则不采集
            if cam is None or shm is None:
                time.sleep(0.05); continue
            try:
                frame = cam.capture()             # HxWx3, uint8
                # 懒初始化共享内存
                if shm is None:
                    return  # 等控制线程创建共享内存；此处防御
                start = widx * stride * height
                mv = memoryview(shm.buf)[start:start + stride * height]
                mv[:frame.nbytes] = frame.reshape(-1).tobytes()

                meta = {
                    "shm": SHM_NAME, "index": widx, "seq": seq,
                    "width": int(width), "height": int(height),
                    "stride": int(stride), "format": cam.config['pix_fmt'],
                    "ts_ns": time.time_ns()
                }
                pub.send_multipart([b"frame", json.dumps(meta).encode("utf-8")])
                widx = (widx + 1) % RING; seq += 1
            except Exception as e:
                pub.send_multipart([b"status", json.dumps({"err": str(e)}).encode("utf-8")])
                time.sleep(0.2)  # 简易退避

    # 控制线程
    def ctrl_loop():
        nonlocal cam, shm, running, width, height, stride
        while not stop_flag.is_set():
            try:
                req = rep.recv_json(flags=0)
            except zmq.ZMQError:
                break
            op = req.get("op")
            try:
                if op == "OpenCamera":
                    idx = int(req.get("index", 0))
                    ctx_type = req.get("context_type", "IIDC")
                    cam = MyCamLite(index=idx, context_type=ctx_type)
                    width = cam.config['cols']; height = cam.config['rows']
                    stride = width * 3 if cam.config['pix_fmt'] == 'rgb' else width
                    if cam.config['pix_fmt'] == 'raw16':
                        stride *= 2
                    # 重置环形缓冲区索引
                    if shm is not None:
                        shm.close(); shm.unlink()
                    shm = shared_memory.SharedMemory(create=True, size=RING * stride * height, name=SHM_NAME)
                    rep.send_json({
                        "ok": True, "serial": cam.serial,
                        "width": width,
                        "height": height,
                        "stride": stride,
                        "format": cam.config['pix_fmt'],
                        "setting_names": cam.setting_names
                    })
                elif op == "GetCameraInfo":
                    if cam is None:
                        rep.send_json({"ok": False, "err": "camera not opened"})
                    else:
                        info = {
                            "serial": cam.serial,
                            "width": cam.config['cols'],
                            "height": cam.config['rows'],
                            "pix_fmt": cam.config['pix_fmt'],
                            "setting_names" : cam.setting_names
                        }
                        rep.send_json({"ok": True, "info": info})
                elif op == "StartStream":
                    running = True; rep.send_json({"ok": True})
                elif op == "StopStream":
                    running = False; rep.send_json({"ok": True})
                elif op == "SetProperty":
                    name = req["name"]; value = req["value"]
                    # 直接透传给底层：示例为绝对值接口；如需映射可自行扩展
                    cam.cam.set_cam_abs_setting_value(name, value)
                    rep.send_json({"ok": True})
                elif op == "GetRange":
                    name = req["name"]
                    rng = cam.get_cam_abs_setting_range(name)
                    rep.send_json({"ok": True, "range": rng})

                elif op == "GetValue":
                    name = req["name"]
                    val = cam.get_cam_abs_setting_value(name)
                    rep.send_json({"ok": True, "value": val})
                elif op == "CloseCamera":
                    running = False
                    stop_flag.set()
                    if cam: cam.close(); cam = None
                    if shm: shm.close(); shm.unlink(); shm = None
                    rep.send_json({"ok": True})
                elif op == "PreConfigGUI":
                    # 可选：支持传入 context_type, 以及阻塞超时
                    ctx_type = req.get("context_type", "IIDC")
                    # 某些 GUI 实现需要在主线程/有消息循环的线程调用；我们这里在控制线程同步调用，阻塞直至用户关闭
                    try:
                        gui = GUI()
                        # 你的代码里用的是 show_selection()；如果你还有“高级设置”窗口的入口，也可以一起调用
                        gui.show_selection()
                        # 某些环境下 show_selection() 会立即返回并在内部事件循环运行；
                        # 如果需要“直到配置完毕才继续”，可在 GUI 内部提供一个“应用/关闭”流程，或在这里轮询窗口状态。
                        rep.send_json({"ok": True})
                    except Exception as e:
                        rep.send_json({"ok": False, "err": str(e)})
                elif op == "Ping":
                    rep.send_json({"ok": True, "ts_ns": time.time_ns()})
                else:
                    rep.send_json({"ok": False, "err": "unknown op"})
            except Exception as e:
                rep.send_json({"ok": False, "err": str(e)})

    t_stream = threading.Thread(target=stream_loop, daemon=True)
    t_ctrl = threading.Thread(target=ctrl_loop, daemon=True)
    t_stream.start(); t_ctrl.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    stop_flag.set()
    try:
        t_stream.join(1.0)
    except:
        pass
    try:
        t_ctrl.join(1.0)
    except:
        pass
    try:
        if cam: cam.close()  # stop_capture / disconnect
    except:
        pass
    try:
        if shm: shm.close(); shm.unlink()
    except:
        pass
    # 最后释放端口与上下文（关键）
    try:
        pub.close(0)
    except:
        pass
    try:
        rep.close(0)
    except:
        pass
    try:
        ctx.term()
    except:
        pass

if __name__ == "__main__":
    main()