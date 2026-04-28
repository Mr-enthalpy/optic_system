import time
from typing import Union, Tuple

import matplotlib.pyplot as plt
import numpy as np
from numpy import ndarray

from cam import video_init, Video
from circle import get_pupil
from circle import solve_aperture_from_profiles
from lcd import lcd_init, LCDDisplay
from typing import Dict
from ellipse import estimate_ellipse_parameters, create_ellipse_mask
from enum import Enum
# ---------- 基础工具 ----------
def to_rgb(gray_01: np.ndarray) -> np.ndarray:
    """
    将 [0,1] 灰度或二值图扩为 RGB uint8
    灰度值会被裁剪到 [0,1] 范围。
    :param gray_01: 灰度图，float32/float64，范围 [0,1]，shape (H,W) 或 (H,W,1)
    :return: RGB 图，uint8，shape (H,W,3)
    """
    g = np.clip(gray_01, 0, 1).astype(np.float32)
    img = (g * 255.0 + 0.5).astype(np.uint8)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, axis=2)
    return img

def to_gray01(rgb: np.ndarray) -> np.ndarray:
    """
    RGB uint8 -> 灰度 [0,1]
    :param rgb: RGB 图，uint8，shape (H,W,3) 或 灰度图 (H,W)
    :return: 灰度图，float32，范围 [0,1]，shape (H,W)
    """
    if rgb.ndim == 3 and rgb.shape[2] == 3:
        r, g, b = rgb[...,0], rgb[...,1], rgb[...,2]
        y = 0.299*r + 0.587*g + 0.114*b
    else:
        y = rgb
    return y.astype(np.float32) / 255.0

def energy_delta(a: np.ndarray, b: np.ndarray) -> float:
    """
    计算两幅灰度图的光通差 ΔE = Σ (a - b)
    :param a: 图像 a
    :param b: 图像 b
    :return:
        Σ (a - b)
    """
    return float((a - b).sum())

def average_gray_captrue(video: Video, n: int=10) -> np.ndarray:
    """
    多帧灰度图均值捕捉
    仅当 n >= 1 时有效，n < 1 时抛出
    :param video: Video 对象
    :param n: 均值帧数，N >= 1
    :return:
        灰度图，float32，范围 [0,1]，shape (H,W)
    """
    _, rgb = video.get_aver_frame(n)
    gray = to_gray01(rgb)
    return gray




# ---------- 图案生成 ----------
def solid(h: int, w: int, val: float=1.0) -> np.ndarray:
    """全屏灰阶（0..1）"""
    return np.full((h, w), float(val), dtype=np.float32)

def vertical_bar(h: int, w: int, x0: int, width: int, bg: float=1.0, bar: float=0.0):
    """竖向黑条（bar 灰阶），其余为 bg 灰阶"""
    img = np.full((h, w), float(bg), dtype=np.float32)
    x1 = max(0, int(x0))
    x2 = min(w, int(x0 + width))
    if x2 > x1:
        img[:, x1:x2] = float(bar)
    return img

def horizontal_bar(h: int, w: int, y0: int, width: int, bg: float=1.0, bar: float=0.0):
    img = np.full((h, w), float(bg), dtype=np.float32)
    y1 = max(0, int(y0))
    y2 = min(h, int(y0 + width))
    if y2 > y1:
        img[y1:y2, :] = float(bar)
    return img

# ---------- 扫描主程 ----------
def scan_one_axis(lcd: LCDDisplay,
                  video: Video,
                  h: int,
                  w: int,
                  i_ref: float,
                  axis: str='x',
                  bar_w: int=16,
                  step: int=8,
                  bg_level: float=1.0,
                  bar_level: float=0.0,
                  settle_ms: int=60,
                  avg_n: int=1,
                  scan_range: Union[None, Tuple[int, int, int, int]]=None
                  ) -> Tuple[np.ndarray, np.ndarray]:
    """
    在单一轴向上扫描条带，测量光通变化。
    :param lcd: LCDDisplay 对象，控制 LCD 显示
    :param video: Video 对象，控制相机捕捉
    :param h: LCD 高度（像素）
    :param w: LCD 宽度（像素）
    :param i_ref: 基线参考灰阶图，shape (h, w)，
    :param axis: 扫描轴向，'x' 或 'y'
    :param bar_w: 扫描条带宽度（像素）
    :param step: 扫描步长（像素）
    :param bg_level: 背景灰阶（0..1）
    :param bar_level: 条带灰阶（0..1）
    :param settle_ms: LCD 图案稳定等待时间（毫秒）
    :param avg_n: 捕捉时的帧均值数量
    :param scan_range: 可选的扫描范围 (x_start, x_end, y
    :return:
        positions: ndarray, shape (n_steps,) 扫描位置（像素）
        energies: ndarray, shape (n_steps,) 对应的光通变化 ΔE
    """
    # 基线
    energies = []
    positions = []

    if axis == 'x' or axis == 'y':
        if scan_range is None:
            steps = list(range(0, w, step)) if axis == 'x' else list(range(0, h, step))
        else:
            x_start, x_end, y_start, y_end = scan_range
            if axis == 'x':
                steps = list(range(x_start, x_end, step))
            else:
                steps = list(range(y_start, y_end, step))
        for s in steps:
            pat = vertical_bar(h, w, s, bar_w, bg=bg_level, bar=bar_level) if axis == 'x' \
                else horizontal_bar(h, w, s, bar_w, bg=bg_level, bar=bar_level)
            mask = to_rgb(pat)
            lcd.show(mask)
            lcd.show(mask)
            lcd.show(mask)
            time.sleep(settle_ms / 1000.0)
            I = average_gray_captrue(video, n=avg_n)

            energies.append(i_ref - I.sum())
            positions.append(s + bar_w/2.0)  # 用条带中心作为采样位置
    else:
        raise ValueError("axis must be 'x' or 'y'")

    # 恢复基线
    lcd.show(to_rgb(solid(h, w, bg_level)))
    return np.array(positions, dtype=float), np.array(energies, dtype=float)


# ---------- 主流程 ----------
def locate_aperture_and_build_roi(lcd: LCDDisplay,
                                  video: Video,
                                  bar_w: int =16,
                                  step: int =8,
                                  bg_level: float =1.0,
                                  bar_level: float =0.0,
                                  settle_ms: int=60,
                                  avg_n: int = 10,
                                  plot=False,
                                  scan_range: Union[None, Tuple[int, int, int, int]]=None) -> Dict[str, float]:
    """
    :param lcd: LCDDisplay 对象，控制 LCD 显示
    :param video: Video 对象，控制相机捕捉
    :param bar_w: 扫描条带宽度（像素）
    :param step: 扫描步长（像素）
    :param bg_level: 背景灰阶（0..1）
    :param bar_level: 条带灰阶（0..1）
    :param settle_ms: LCD 图案稳定等待时间（毫秒）
    :param avg_n: 捕捉时的帧均值数量
    :param plot: 是否绘图显示结果
    :param scan_range: 可选的扫描范围 (x_start, x_end, y
    :return:
        result: dict with keys: 'xc', 'yc', 'r_x', 'r_y', 'r_avg'
    """
    W, H = lcd.size
    I_ref = get_i_ref(lcd, video, settle_ms=settle_ms, aver_n=avg_n, white_black=True).sum()
    # 2) X、Y 两方向扫描
    pos_x, enr_x = scan_one_axis(lcd, video, H, W, i_ref= I_ref, axis='x',
                                 bar_w=bar_w, step=step,
                                 bg_level=bg_level, bar_level=bar_level,
                                 settle_ms=settle_ms, avg_n=avg_n, scan_range=scan_range)

    pos_y, enr_y = scan_one_axis(lcd, video, H, W, i_ref= I_ref, axis='y',
                                 bar_w=bar_w, step=step,
                                 bg_level=bg_level, bar_level=bar_level,
                                 settle_ms=settle_ms, avg_n=avg_n, scan_range=scan_range)
    x_string = np.vstack([pos_x, enr_x])
    y_string = np.vstack([pos_y, enr_y])
    np.save("x.npy", x_string)
    np.save("y.npy", y_string)
    result = solve_aperture_from_profiles(pos_x, enr_x, pos_y, enr_y)
    # 5) 可选：可视化/打印
    if plot:
        try:
            fig, axs = plt.subplots(1,2, figsize=(12,2))
            axs[0].plot(pos_x, enr_x, '-k'); axs[0].set_title('ΔE vs X')
            axs[1].plot(pos_y, enr_y, '-k'); axs[1].set_title('ΔE vs Y')
            plt.show()
            print(f"xc={result['xc']:.2f}, yc={result['yc']:.2f}, "
                  f"r_x={result['r_x']:.2f}, r_y={result['r_y']:.2f}, r_avg={result['r_avg']:.2f}")
        except Exception as e:
            print("可视化失败:", e)
    return result

def get_i_ref(lcd: LCDDisplay,
              video: Video,
              settle_ms: int = 60,
              aver_n: int = 50,
              white_black: bool = True,
              ) -> ndarray:
    """
    获取基线参考灰阶图 I_ref。
    :param lcd: LCDDisplay 对象
    :param video: Video 对象
    :param settle_ms: LCD 图案稳定等待时间（毫秒）
    :param aver_n: 捕捉时的帧均值数量
    :param white_black: Flase捕捉暗底， True捕捉亮底
    :return:
        ndarray: shape (H, W) 的灰度图，范围 [0, 1]
    """
    W, H = lcd.size
    img = np.ones((H, W, 3), dtype=np.uint8) * 255 if white_black else np.zeros((H, W, 3), dtype=np.uint8)
    lcd.show(img)
    time.sleep(settle_ms / 1000.0)
    I_ref = average_gray_captrue(video, n=aver_n)
    return I_ref

def scan_overlapping_area(lcd: LCDDisplay,
                          video: Video,
                          center: Tuple[float, float],
                          r_range: Tuple[float, float],
                          steps: int,
                          aver_n: int = 10,
                          settle_ms: int = 60,
                          ) -> Tuple[ndarray, ndarray] :
    """
    在粗扫描生成的圆心上下微调半径 r，扫描每个r值对应的光通，并返回该序列。
    :param lcd: LCDDisplay 对象，控制 LCD 显示
    :param video: Video 对象，控制相机捕捉
    :param center: (xc, yc) 圆心
    :param r_range: (r_min, r_max) 半径搜索范围
    :param steps: 搜索步数
    :param aver_n: 每次捕捉时的帧均值数量
    :param settle_ms: LCD 图案稳定等待时间（毫秒）
    :return:
        ndarray: shape (steps,) 的光通序列
    """
    r_sets = np.linspace(r_range[0], r_range[1], steps)
    W, H = lcd.size
    I_ref = get_i_ref(lcd, video, settle_ms=settle_ms, aver_n=aver_n, white_black = False).sum()
    energies = []
    for r in r_sets:
        mask = to_rgb(get_pupil(size = (H, W), center=center, r= r))
        lcd.show(mask)
        time.sleep(settle_ms / 1000.0)
        I = average_gray_captrue(video, n=aver_n)
        energy = I.sum() - I_ref
        energies.append(energy)
    return r_sets, np.array(energies, dtype=float)

def scan_rotation_angle(
        lcd: LCDDisplay,
        video: Video,
        center: Tuple[float, float],
        a: float,
        b: float,
        angle_range: Tuple[float, float],
        steps: int,
        aver_n: int = 10,
        settle_ms: int = 60,
        plot: bool = False,
) -> Tuple[ndarray, ndarray]:
    """
    在椭圆的半长轴 a、半短轴 b ,圆心center已知的情况下，扫描旋转角度，测量每个角度对应的光通，并返回该序列。
    :param plot: 是否绘图显示结果
    :param lcd: LCDDisplay 对象，控制 LCD 显示
    :param video: Video 对象，控制相机捕捉
    :param center: (xc, yc) 圆心
    :param a: 半长轴
    :param b: 半短轴
    :param angle_range: (angle_min, angle_max) 旋转角度范围（度）
    :param steps: 搜索步数
    :param aver_n: 每次捕捉时的帧均值数量
    :param settle_ms: LCD 图案稳定等待时间（毫秒）
    :return:
        ndarray: shape (steps,) 的光通序列
    """
    angle_sets = np.linspace(angle_range[0], angle_range[1], steps)
    W, H = lcd.size
    I_ref = get_i_ref(lcd, video, settle_ms=settle_ms, aver_n=aver_n, white_black=False).sum()
    energies = []
    for angle in angle_sets:
        mask = to_rgb(create_ellipse_mask(center=center, a=a, b=b, image_size=(H, W), rotate_angle=angle))
        lcd.show(mask)
        time.sleep(settle_ms / 1000.0)
        I = average_gray_captrue(video, n=aver_n)
        energy = I.sum() - I_ref
        energies.append(energy)
    if plot:
        try:
            plt.figure(figsize=(6,4))
            plt.plot(angle_sets, energies, '-k')
            plt.title('ΔE vs Angle')
            plt.xlabel('Angle (degrees)')
            plt.ylabel('ΔE')
            plt.grid(True)
            plt.show()
        except Exception as e:
            print("可视化失败:", e)
    return angle_sets, np.array(energies, dtype=float)

class State(Enum):
    LOCATE = 1
    SCAN_RADIUS = 2
    SCAN_ANGLE = 3
    DONE = 4

# ---------- 入口 ----------
def _main(state: State = State.DONE):
    # 1) 初始化
    lcd = lcd_init()
    W, H = lcd.size
    white_img = np.ones((H, W, 3), dtype=np.uint8) * 255
    lcd.show(white_img)
    video = video_init()
    video.start()
    if state == State.LOCATE:
        scan_range = (1900, 2300, 300, 700)

        # 2) 运行定位
        result = locate_aperture_and_build_roi(
            lcd, video,
            bar_w=16, step=4,
            bg_level=1.0, bar_level=0.0,
            settle_ms=2, avg_n=10,
            plot=True,  # 若环境无 GUI，可改为 False
            scan_range = scan_range
        )
        center = (result['xc'], result['yc'])
        print("定位结果:", result)
        r_base = result['r_avg']
        state = State.SCAN_RADIUS
    else:
        pos_x, enr_x = np.load("x.npy")
        pos_y, enr_y = np.load("y.npy")
        result = solve_aperture_from_profiles(pos_x, enr_x, pos_y, enr_y)
        r_base = result['r_avg']
        center = (result['xc'], result['yc'])
    if state == State.SCAN_RADIUS:
        # 3) 半径微调
        r_pos, r_vals = scan_overlapping_area(lcd, video, r_range=(0, 2 * r_base), steps=200, aver_n=20, settle_ms=2, center=center)
        data = np.vstack((r_pos, r_vals))
        np.save("r_scan.npy", data)
        state = State.SCAN_ANGLE
    else:
        r_pos, r_vals = np.load("r_scan.npy")

    # 4) 椭圆拟合
    a, b, k = estimate_ellipse_parameters(r_vals, r_pos, plot=True) # 耗时短，不做缓存

    # 5) 角度扫描
    if state == State.SCAN_ANGLE:
        angle, angle_val = scan_rotation_angle(lcd, video, center=center, a=a, b=b, angle_range=(0, 2), steps=10, aver_n=20, settle_ms=2, plot=True)
        np.savetxt("angle_scan.txt", angle)
        np.savetxt("angle_val_scan.txt", angle_val)
    else:
        return
    video.stop()

def get_center() -> Tuple[float, float]:
    pos_x, enr_x = np.load("x.npy")
    pos_y, enr_y = np.load("y.npy")
    result = solve_aperture_from_profiles(pos_x, enr_x, pos_y, enr_y)
    return result['xc'], result['yc']

def get_ellipse_params() -> Tuple[float, float, float]:
    r_pos, r_vals = np.load("r_scan.npy")
    a, b, k = estimate_ellipse_parameters(r_vals, r_pos, plot=False)
    return a, b, k

if __name__ == "__main__":
    sta = State.SCAN_ANGLE
    _main(sta)