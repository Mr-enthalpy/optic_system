# -*- coding: utf-8 -*-
# pip install pyzmq opencv-python
import json
import time
import os
import subprocess
from multiprocessing import Event, Queue
from multiprocessing import shared_memory
from subprocess import Popen
from threading import Thread
from typing import Tuple

import cv2
import numpy as np
import zmq

def _ensure_sidecar() -> tuple[bool, Popen[bytes]] | tuple[bool, None]:
    ctx = zmq.Context.instance()
    ping = ctx.socket(zmq.REQ)
    ping.RCVTIMEO = 300
    ping.LINGER = 0
    try:
        ping.connect("tcp://127.0.0.1:6101")
        ping.send_json({"op":"Ping"})
        r = ping.recv_json()
        if r.get("ok"):
            return False, None  # 已有在跑的侧车，非本进程所有
    except Exception:
        pass
    finally:
        ping.close(0)

    # 拉起新的侧车
    py38 = os.environ.get("PY38_BIN", "python3.8")
    svc  = os.environ.get("SIDECAR", "cam_impl.py")
    proc = subprocess.Popen([py38, svc],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等待就绪
    for _ in range(60):
        try:
            ping = ctx.socket(zmq.REQ)
            ping.RCVTIMEO = 300
            ping.LINGER = 0
            ping.connect("tcp://127.0.0.1:6101")
            ping.send_json({"op":"Ping"})
            if ping.recv_json().get("ok"):
                return True, proc
        except Exception:
            time.sleep(0.1)
        finally:
            ping.close(0)
    raise RuntimeError("sidecar 未就绪")

def video_init(index=0, context_type="IIDC") -> 'Video':
    return Video(index=index, context_type=context_type)

class Video:
    def __init__(self, index=0, context_type="IIDC"):
        self.__PORT_PUB = 6100
        self.__PORT_REP = 6101
        self.__SHM_NAME = "flycap2_ring_A"
        self.__own_service, self.__proc = _ensure_sidecar()
        r = self.__req("PreConfigGUI", context_type=context_type)
        assert r.get("ok"), r  # 如果
        r = self.__req("OpenCamera", index=index, context_type=context_type)
        assert r.get("ok"), r
        self.width, self.height, self.stride = r["width"], r["height"], r["stride"]
        self.__req("StartStream")
        ctx = zmq.Context.instance()
        self.__sub = ctx.socket(zmq.SUB)
        self.__sub.set_hwm(1)
        self.__sub.connect(f"tcp://127.0.0.1:{self.__PORT_PUB}")
        self.__sub.setsockopt(zmq.SUBSCRIBE, b"frame")
        self.__shm = shared_memory.SharedMemory(name=self.__SHM_NAME)
        self.__stop_event = Event()
        self.__capture_event = Event()
        self.__capture_queue = Queue(maxsize=1)
        self.__video_thread_instance = None

        self.__max_pixels = 0
        msg = self.__req("GetCameraInfo")
        assert msg.get("ok"), msg
        self.setting_names = msg["info"]["setting_names"]
        self.__pix_fmt = msg["info"]["pix_fmt"]

    def __req(self, op, **kwargs) -> dict:
        ctx = zmq.Context.instance()
        s = ctx.socket(zmq.REQ)
        s.connect(f"tcp://127.0.0.1:{self.__PORT_REP}")
        s.send_json({"op": op, **kwargs})
        return s.recv_json()

    @property
    def max_pixels(self) -> int:
        return self.__max_pixels

    def stop(self) -> None:
        if self.__video_thread_instance is None or not self.__video_thread_instance.is_alive():
            return
        self.__stop_event.set()
        self.__video_thread_instance.join()

    def start(self) -> None:
        if self.__video_thread_instance is None or not self.__video_thread_instance.is_alive():
            self.__stop_event.clear()
            self.__video_thread_instance = Thread(
                target=self.__video_show,
                args=(),
                daemon=True)
            self.__stop_event.clear()
            self.__video_thread_instance.start()

    def get_range(self, setting: str) -> Tuple[float, float]:
        r = self.__req("GetRange", name=setting)
        if r.get("ok"):
            return r["range"]
        raise RuntimeError(r)

    def get_value(self, setting: str) -> float:
        r = self.__req("GetValue", name=setting)
        if r.get("ok"):
            return r["value"]
        raise RuntimeError(r)

    def set_value(self, setting: str, value: float) -> None:
        r = self.__req("SetProperty", name=setting, value=value)
        if r.get("ok"):
            return
        raise RuntimeError(r)

    def get_frame(self, timeout=5) -> Tuple[np.ndarray, np.ndarray]:
        """
            捕捉一帧
            :param timeout: 超时时间，单位秒
            :return: 返回 (原始图像, RGB 图像)
        """
        if not self.__video_thread_instance.is_alive():
            raise RuntimeError("视频线程未启动")
        if self.__capture_event.is_set():
            raise RuntimeError("已有未处理的捕捉请求")
        self.__capture_event.set()
        try:
            frame: Tuple[np.ndarray, np.ndarray] = self.__capture_queue.get(timeout=timeout)
            return frame
        except:
            raise RuntimeError("获取图像超时")

    def get_aver_frame(self, aver_n=100) -> Tuple[np.ndarray, np.ndarray]:
        """
            捕捉 aver_n 帧
            :param aver_n: 平均帧数
            :return: 返回 aver_n 帧平均后的图像(原始图像, rgb图像)
        """
        if not self.__video_thread_instance.is_alive():
            raise RuntimeError("视频线程未启动")
        if self.__capture_event.is_set():
            raise RuntimeError("已有未处理的捕捉请求")
        aver_raw_frame: np.ndarray = np.zeros((self.height, self.width), dtype=np.float64)
        aver_rgb_frame: np.ndarray = np.zeros((self.height, self.width, 3), dtype=np.float64)
        try:
            for _ in range(aver_n):
                self.__capture_event.set()
                raw, rgb = self.__capture_queue.get(timeout=800)
                aver_raw_frame += raw
                aver_rgb_frame += rgb
            aver_raw_frame /= aver_n
            aver_rgb_frame /= aver_n
            if self.__pix_fmt == "raw16":
                aver_rgb_frame /= 256
            aver_rgb_frame = aver_rgb_frame.astype(np.uint8)
            return aver_raw_frame, aver_rgb_frame
        except Exception as e:
            raise RuntimeError(f"获取图像超时: {e}")

    def __video_show(self) -> None:
        try:
            window_name = "Camera"
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            while True:
                topic, payload = self.__sub.recv_multipart()
                meta = json.loads(payload)
                idx = meta["index"]
                pix_fmt = meta["format"]
                start = idx * self.stride * self.height
                end = start + self.stride * self.height
                mv = memoryview(self.__shm.buf)[start:end]
                if pix_fmt == "rgb":
                    img = np.ndarray((self.height, self.width, 3), dtype=np.uint8, buffer=mv)  # 直接三维视图
                    rgb = img
                elif pix_fmt == "raw8":
                    img = np.ndarray((self.height, self.width), dtype=np.uint8, buffer=mv)  # 二维视图
                    rgb = cv2.cvtColor(img, cv2.COLOR_BayerGB2RGB)  # 转为 RGB
                elif pix_fmt == "raw16":
                    img = np.ndarray((self.height, self.width), dtype=np.uint16, buffer=mv)  # 二维视图
                    rgb = cv2.cvtColor(img, cv2.COLOR_BayerGB2RGB)  # 转为 RGB
                else:
                    raise RuntimeError(f"不支持的像素格式: {pix_fmt}")
                self.__max_pixels = img.max()
                cv2.imshow(window_name, rgb)
                cv2.pollKey()
                if self.__stop_event.is_set():
                    self.__req("StopStream")
                    break
                if self.__capture_event.is_set():
                    self.__capture_event.clear()
                    self.__capture_queue.put((img, rgb))
        finally:
            cv2.destroyAllWindows()
            try:
                self.__req("StopStream")
            except:
                pass

    def __del__(self):
        try:
            self.stop()
        except:
            pass
        try:
            self.__req("StopStream")
        except:
            pass
        try:
            self.__req("CloseCamera")
        except:
            pass
        if self.__own_service:
            if self.__proc and self.__proc.poll() is None:
                try:
                    self.__proc.terminate()
                    self.__proc.wait(timeout=2.0)
                except:
                    pass
                if self.__proc and self.__proc.poll() is None:
                    try:
                        self.__proc.kill()
                    except:
                        pass
            # 本地资源
            try:
                self.__sub.close(0)
            except:
                pass
            try:
                self.__shm.close()
            except:
                pass

