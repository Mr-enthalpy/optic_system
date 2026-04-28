#!/usr/bin/env python3
"""
lcd_driver.py – 极简 HDMI‑LCD 显示封装
依赖: pygame>=2.1, numpy
"""
import argparse
import os
from typing import Optional, Tuple

import numpy as np
import pygame


# ────────────────────────────────────────────────────────────────
# 低层工具
# ────────────────────────────────────────────────────────────────
def _find_lcd_index() -> int:
    """
    返回系统中 *可能* 是外接 LCD 的 display index。
    策略:
        1. 若仅有 1 块物理屏，认为 index 0 就是 LCD。
        2. 若 ≥2 块，返回 index 1  (假设 0 是主屏，1 是外接 LCD)。
    如需更复杂判定可自行扩展 – 例如分辨率、EDID 名称比对等。
    """
    # ── 1. 初始化 & 枚举所有物理屏 ────────────────────────────────────────
    pygame.init()
    sizes = pygame.display.get_desktop_sizes()  # [(w,h), …]:contentReference[oaicite:1]{index=1}
    print("Detected displays:")
    for i, (w, h) in enumerate(sizes):
        print(f"  {i}: {w} × {h}")
    n = pygame.display.get_num_displays()
    if n == 0:
        raise RuntimeError("在 SDL / 驱动层面未检测到任何可用显示器。")
    return 1 if n > 1 else 0


def _validate_ndarray(frame: np.ndarray, size: Tuple[int, int]) -> None:
    """
    确认 ndarray 符合 (H, W, 3) + uint8，且 (W, H) 恰等于 LCD native size。
    """
    if frame.dtype != np.uint8:
        raise TypeError(f"期望 dtype uint8，实际为 {frame.dtype!r}")
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("期望形状 (H, W, 3) 的 RGB 数组")
    h, w = frame.shape[:2]
    if (w, h) != size:
        raise ValueError(
            f"输入尺寸 {w}×{h} 与 LCD {size[0]}×{size[1]} 不一致，将导致缩放"
        )


# ────────────────────────────────────────────────────────────────
# 公开接口
# ────────────────────────────────────────────────────────────────
class LCDDisplay:
    """封装单块 HDMI‑LCD 的全屏 Framebuffer。"""

    def __init__(self, disp_index: int) -> None:
        self._disp_index = disp_index

        # 初始化 SDL / Pygame 并锁定目标显示
        os.environ["SDL_VIDEO_FULLSCREEN_DISPLAY"] = str(disp_index)
        pygame.init()


        # 获取物理像素分辨率
        sizes = pygame.display.get_desktop_sizes()
        self._w, self._h = sizes[disp_index]

        # 创建全屏 Surface（双缓冲 + 硬件加速）
        flags = pygame.HWSURFACE | pygame.DOUBLEBUF
        flags |= pygame.NOFRAME
        size = (self._w, self._h)
        self._screen = pygame.display.set_mode(
            size, flags=flags, display=disp_index
        )

    # ——————————————————————————
    # 公共方法
    # ——————————————————————————
    @property
    def size(self) -> Tuple[int, int]:
        """LCD 原生分辨率 (width, height)。"""
        return self._w, self._h

    def show(self, frame: np.ndarray) -> None:
        """
        将 RGB ndarray 推送到 LCD。
        * 保证 1 ∶ 1 输出；尺寸不符直接抛异常。
        * 调用方负责在外层维护事件循环（或调用 wait()）。
        """
        _validate_ndarray(frame, self.size)

        surface = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
        self._screen.blit(surface, (0, 0))
        pygame.display.flip()

    def wait(self, *, esc_quit: bool = True) -> None:
        """
        简易事件循环 – 阻塞直至窗口关闭或按 Esc。
        对于无人值守显示可省略调用，由上层进程自行维护。
        """
        while True:
            for e in pygame.event.get():
                if esc_quit and e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                    self.close()
                    return
                if e.type == pygame.QUIT:
                    self.close()
                    return

    def close(self) -> None:
        """释放资源并关闭显示。"""
        pygame.quit()

    # ——————————————————————————
    # 语境管理 (with) 方便自动清理
    # ——————————————————————————
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def lcd_init(display_index: Optional[int] = None) -> LCDDisplay:
    """
    自动或按索引初始化 LCDDisplay 并返回。
    参数:
        display_index – 若为 None, 则自动探测; 否则强行使用指定 index。
    抛出:
        RuntimeError / ValueError – 若找不到可用 LCD 或 index 无效。
    """
    if display_index is None:
        display_index = _find_lcd_index()
    return LCDDisplay(display_index)


if __name__ == "__main__":
    # 测试 LCD 显示
    with lcd_init() as lcd:
        print(f"LCD 原生分辨率: {lcd.size}")
        # 创建一个红色渐变图像
        img = np.zeros((lcd.size[1], lcd.size[0], 3), dtype=np.uint8)
        for y in range(lcd.size[1]):
            img[y, :, 0] = int(255 * (y / lcd.size[1]))  # 红色通道渐变
        lcd.show(img)
        lcd.wait()  # 等待用户操作
