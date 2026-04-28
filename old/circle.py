import numpy as np
from typing import Tuple, Dict

def _fit_circle_from_profile(pos: np.ndarray,
                             energy: np.ndarray,
                             smooth_k: int=5,
                             use_top: float =0.6
                             )-> Tuple[float, float, float, Tuple[float, float, float], float]:
    """
      先对 ΔE 去掉基线偏置，平滑；取顶部区间做线性拟合 y=f^2=ax^2+bx+c，
      再由 a,b,c 解析出中心/半径/尺度（s = sqrt(-a)）。
    :param pos:  1D 位置数组（条带中心的像素坐标）
    :param energy: 1D ΔE 曲线（与 pos 对应）
    :param smooth_k: 平滑窗口（奇数；=1 不平滑）
    :param use_top: 仅用峰值的前 use_top 比例样本（避开边缘钝化）
    :return: center, radius, scale, (a,b,c), residual_rms 圆心，半径，尺度，拟合系数，残差

    """
    x = np.asarray(pos).astype(float)
    f = np.asarray(energy).astype(float)

    # 去掉基线（两端 10% 的最小值均值作为偏置）
    n = len(f)
    k = max(1, int(0.1*n))
    baseline = 0.5*(np.min(f[:k]) + np.min(f[-k:]))
    f = f - baseline
    f[f < 0] = 0.0

    # 平滑
    if smooth_k > 1:
        pad = np.pad(f, (smooth_k//2, smooth_k-1-smooth_k//2), mode='edge')
        c = np.cumsum(pad, dtype=float)
        f = (c[smooth_k:] - c[:-smooth_k]) / smooth_k

    # 仅取峰值上方的区间，减少条带宽度/PSF 造成的边缘钝化影响
    thr = (1.0 - use_top) * np.max(f)
    idx = np.where(f >= thr)[0]
    x_fit, f_fit = x[idx], f[idx]

    # 线性最小二乘拟合 y = a x^2 + b x + c
    y_fit = f_fit**2
    A = np.vstack([x_fit**2, x_fit, np.ones_like(x_fit)]).T
    coef, *_ = np.linalg.lstsq(A, y_fit, rcond=None)
    a, b, c = coef

    if a >= 0:
        raise RuntimeError("拟合失败：a 应 < 0（曲线应为开口向下的抛物线）")

    # 解析出中心/半径/尺度
    xc = -b/(2*a)
    s = np.sqrt(-a)                 # 与比例系数成正比
    r = np.sqrt(c/(-a) + xc*xc)

    # 残差
    y_pred = a*x_fit**2 + b*x_fit + c
    rms = np.sqrt(np.mean((y_fit - y_pred)**2))

    return xc, r, s, (a,b,c), rms

def solve_aperture_from_profiles(px: np.ndarray, ex: np.ndarray, py: np.ndarray, ey: np.ndarray) -> Dict[str, float]:
    """
    从 X/Y 两条 ΔE 曲线拟合光阑圆心与半径。
    :param px:  X 扫描的 pos 数组
    :param ex:  X 扫描的 ΔE 数组
    :param py:  Y 扫描的 pos 数组
    :param ey:  Y 扫描的 ΔE 数组
    :return:
        返回字典，包含:圆心 (xc, yc)，X/Y 半径 (r_x, r_y)，平均半径 r_avg，
    """
    xc, r_x, s_x, abc_x, rms_x = _fit_circle_from_profile(px, ex, smooth_k=5, use_top=0.6)
    yc, r_y, s_y, abc_y, rms_y = _fit_circle_from_profile(py, ey, smooth_k=5, use_top=0.6)

    r_avg = 0.5*(r_x + r_y)
    return dict(xc=xc, yc=yc, r_x=r_x, r_y=r_y, r_avg=r_avg,
                s_x=s_x, s_y=s_y, rms_x=rms_x, rms_y=rms_y)

def _build_roi_mask(H: int, W: int, xc: float, yc: float, r: float) -> np.ndarray:
    """
    构建圆形 ROI 掩码。
    :param H: 图像高度（像素）
    :param W: 图像宽度（像素）
    :param xc: 圆心 X 坐标（像素）
    :param yc: 圆心 Y 坐标（像素）
    :param r: 圆半径（像素）
    :return: uint8[H,W] 的掩码数组，圆内为 255，圆外为 0
    """
    yy, xx = np.mgrid[0:H, 0:W]
    mask: np.ndarray = ((xx - xc)**2 + (yy - yc)**2) <= r**2
    return mask.astype(np.uint8)


def get_pupil(size: Tuple[int, int], center: Tuple[float, float], r: float) -> np.ndarray:
    """
    :param size: (H, W), 图像尺寸
    :param center: (xc, yc), 圆心坐标
    :param r: 圆半径
    :return: uint8[H,W] 的掩码数组，圆内为 255，圆外为 0
    """
    H, W  = size
    xc, yc = center
    roi = _build_roi_mask(H, W, xc, yc, r) * 255
    return roi

import numpy as np

def circular_window(shape: Tuple[int, int], center=None, radius=32.0, feather=0.0, dtype=np.float64)-> np.ndarray:
    """
    :param shape: (H, W)，输出矩阵的形状
    :param center: (cy, cx)，圆心坐标；None 则为图像中心
    :param radius: 半径（像素）
    :param feather: 边缘羽化宽度（像素）
    - feather=0: 硬圆窗（0/1）
    - feather>0: 软边宽度（像素），边界在 [radius-feather, radius+feather] 平滑过渡到0
      使用 smoothstep 过渡（C1 连续）。
    :param dtype: 输出数据类型
    :return:
    """
    H, W = shape
    if center is None:
        cy = (H - 1) / 2.0
        cx = (W - 1) / 2.0
    else:
        cy, cx = center

    yy = np.arange(H, dtype=dtype)[:, None]
    xx = np.arange(W, dtype=dtype)[None, :]
    r = np.sqrt((yy - cy)**2 + (xx - cx)**2)

    if feather <= 0:
        return (r <= radius).astype(dtype)

    # smoothstep: t in [0,1] -> 3t^2 - 2t^3
    r0 = radius - feather
    r1 = radius + feather
    t = (r - r0) / (r1 - r0 + 1e-12)
    t = np.clip(t, 0.0, 1.0)
    s = 3*t**2 - 2*t**3

    # r<=r0 -> 1, r>=r1 -> 0
    Wmat = 1.0 - s
    return Wmat.astype(dtype)
