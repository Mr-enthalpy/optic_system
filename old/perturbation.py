import numpy as np
from typing import Tuple
def tapered_circular_window(shape: Tuple[int, int], center: Tuple[int, int], r: int, taper: int, dtype=np.float64) -> np.ndarray:
    """
    :param shape: (H, W)，输出矩阵的形状
    :param center: (cy, cx)，圆心坐标；None 则为图像中心
    :param r: 半径（像素）
    :param taper: 边缘羽化宽度（像素）
    - taper=0: 硬圆窗（0/1）
    - taper>0: 软边宽度（像素），边界在 [radius-feather, radius] 平滑过渡到0
      使用 smoothstep 过渡（C1 连续）。
    :param dtype: 输出数据类型
    :return:
    """
    cy, cx = center
    H, W = shape
    yy = np.arange(H, dtype=dtype)[:, None]
    xx = np.arange(W, dtype=dtype)[None, :]
    rr = np.sqrt((yy - cy)**2 + (xx - cx)**2)

    M = np.zeros((H, W), dtype=dtype)

    if taper <= 0:
        M[rr <= r] = 1.0
        return M

    r0 = max(0.0, r - taper)  # 内圈半径：从这里开始降
    inside = rr <= r0
    ramp = (rr > r0) & (rr < r)

    M[inside] = 1.0

    # raised-cosine taper: 在 rr=r0 时为1，在 rr=r 时为0
    t = (rr[ramp] - r0) / max(taper, 1e-12)  # 0..1
    M[ramp] = 0.5 * (1.0 + np.cos(np.pi * t))

    # rr >= r 仍为 0
    return M


def perturbation_disk(shape: Tuple[int, int], center: Tuple[int, int], r: int, taper: int = 32, pr=3.0, theta=0.0, radial_offset=0.5, dtype=np.float64)  -> np.ndarray:
    """
    在边缘附近放一个小圆盘微扰 P（0/1），默认放在 r - radial_offset*taper 处。
    :param dtype: 类型
    :param shape: (H, W)，输出矩阵的形状
    :param center: (cy, cx), 窗中心
    :param r: 半径
    :param taper: 边缘羽化宽度（像素）
    - taper=0: 硬圆窗（0/1）
    - taper>0: 软边宽度（像素），边界在 [radius-feather, radius] 平滑过渡到0
      使用 smoothstep 过渡（C1 连续）。
    :param pr: 微扰圆盘半径（像素）
    :param theta: 角度（弧度），0 表示 +x 方向
    :param radial_offset: 0..1，表示离外边界的相对距离（0贴着r，1贴着r-taper）
    """
    H, W = shape
    cy, cx = center
    # 微扰中心放在 taper 环带内：rp ∈ [r-taper, r]
    rp = r - np.clip(radial_offset, 0.0, 1.0) * taper
    py = cy + rp * np.sin(theta)
    px = cx + rp * np.cos(theta)

    yy = np.arange(H, dtype=dtype)[:, None]
    xx = np.arange(W, dtype=dtype)[None, :]
    rr = np.sqrt((yy - py)**2 + (xx - px)**2)
    P = (rr <= pr).astype(dtype)
    return P


def perturbation_gaussian(shape: Tuple[int, int], center: Tuple[int, int], r: int, taper: int = 32, sigma=3.0, theta=0.0, radial_offset=0.5, dtype=np.float64):
    """
    边缘附近的高斯微扰（连续，峰值为1）。相比 disk 更“平滑”，频谱更干净。
    :param dtype: 类型
    :param shape: (H, W)，输出矩阵的形状
    :param center: (cy, cx), 窗中心
    :param r: 半径
    :param taper: 边缘羽化宽度（像素）
    - taper=0: 硬圆窗（0/1）
    - taper>0: 软边宽度（像素），边界在 [radius-feather, radius] 平滑过渡到0
      使用 smoothstep 过渡（C1 连续）。
    :param sigma: 微扰圆盘半径（像素）
    :param theta: 角度（弧度），0 表示 +x 方向
    :param radial_offset: 0..1，表示离外边界的相对距离（0贴着r，1贴着r-taper）
    """
    H, W = shape
    cy, cx = center
    rp = r - np.clip(radial_offset, 0.0, 1.0) * taper
    py = cy + rp * np.sin(theta)
    px = cx + rp * np.cos(theta)

    yy = np.arange(H, dtype=dtype)[:, None]
    xx = np.arange(W, dtype=dtype)[None, :]
    d2 = (yy - py)**2 + (xx - px)**2
    P = np.exp(-0.5 * d2 / max(sigma**2, 1e-12))
    return P


def apply_perturbation(M, P, delta=-0.1, mode="add"):
    """
    把微扰施加到基准窗 M 上，得到 M_pert。
    delta: 微扰幅度（建议 |delta| << 1，比如 0.02~0.2 视 SNR）
    mode:
      - "add":  M_pert = clip(M + delta * P, 0, 1)
      - "mul":  M_pert = clip(M * (1 + delta * P), 0, 1)  （更像透过率乘扰动）
    """
    if mode == "add":
        out = M + delta * P
    elif mode == "mul":
        out = M * (1.0 + delta * P)
    else:
        raise ValueError("mode must be 'add' or 'mul'")
    return np.clip(out, 0.0, 1.0)