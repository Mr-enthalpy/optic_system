import numpy as np
from math import cos, sin, pi

def _to_rgb(mask, on=255, off=0):
    """把二值/灰度 mask 扩成 3 通道 RGB；mask ∈ [0,1] 或 0/1"""
    img = (off + (on - off) * mask).astype(np.uint8)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, axis=2)
    return img

def rect_grating(W, H, period=16, duty=0.5, angle_deg=0.0, antialias=False):
    """
    矩形条纹（1D 光栅）。period: 像素周期；duty: 占空比；angle: 旋转角度。
    antialias=True 时使用 1 像素高斯近似软边（更接近实际透过率）。
    """
    yy, xx = np.mgrid[0:H, 0:W]
    # 以图像中心为原点会更直观
    xc = xx - W/2.0
    yc = yy - H/2.0
    th = angle_deg * pi / 180.0
    # 旋转到“光栅坐标”，u 方向是条纹法线方向
    u =  xc * cos(th) + yc * sin(th)
    # 归一到一个周期内
    phase = (u / period) % 1.0
    mask = (phase < duty).astype(np.float32)

    if antialias:
        # 用正弦近似给边缘一点软过渡，减少锯齿
        edge = 0.15  # 过渡宽度(周期的比例)
        t1 = duty - edge
        t2 = duty + edge
        soft = np.clip((phase - t1) / (t2 - t1), 0, 1)
        mask = np.where(phase < t1, 1.0, np.where(phase > t2, 0.0, 1.0 - soft))
    return _to_rgb(mask)

def checkerboard(W, H, period=16, duty=0.5):
    """二维棋盘栅格（Rect × Rect 的乘积）"""
    yy, xx = np.mgrid[0:H, 0:W]
    xphase = ((xx - W/2.0) / period) % 1.0
    yphase = ((yy - H/2.0) / period) % 1.0
    mx = (xphase < duty).astype(np.float32)
    my = (yphase < duty).astype(np.float32)
    mask = (mx * my) + ((1-mx) * (1-my))  # 黑白交替
    return _to_rgb(mask)

def radial_rings(W, H, period=16, duty=0.5):
    """同心环栅格（基于半径的矩形波）"""
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(xx - W/2.0, yy - H/2.0)
    phase = (r / period) % 1.0
    mask = (phase < duty).astype(np.float32)
    return _to_rgb(mask)

def two_d_sinusoid(W, H, fx=1/16, fy=0, phase0=0, contrast=1.0, bias=0.5):
    """
    连续正弦光栅（用于做灰度透过率测试）。fx,fy 是每像素的空间频率（周/像素）。
    """
    yy, xx = np.mgrid[0:H, 0:W]
    phi = 2*pi*(fx*(xx - W/2.0) + fy*(yy - H/2.0)) + phase0
    g = bias + (contrast/2.0) * np.cos(phi)
    g = np.clip(g, 0.0, 1.0).astype(np.float32)
    return _to_rgb(g)

def moire_safe_grating(W, H, period=24, duty=0.5, angle_deg=0):
    """
    更接近“显示器像素”的安全条纹（加轻微软边 + 周期不整除分辨率，减少固定摩尔纹）
    """
    return rect_grating(W, H, period=period, duty=duty, angle_deg=angle_deg, antialias=True)
