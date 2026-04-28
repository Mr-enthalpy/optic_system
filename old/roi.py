import numpy as np
import cv2
from onnxruntime.transformers.convert_tf_models_to_pytorch import download_tf_checkpoint


def _to_energy_map(img, channel_axis=-1, dtype=np.float64):
    """把输入转成 (H,W) 的能量图：多通道就按通道求和。"""
    x = np.asarray(img)
    if x.ndim == 2:
        return x.astype(dtype)
    if x.ndim == 3:
        if channel_axis == 0:  # (C,H,W) -> (H,W,C)
            x = np.moveaxis(x, 0, -1)
        return np.sum(x.astype(dtype), axis=-1)
    raise ValueError("img must be (H,W), (H,W,C), or (C,H,W)")

def _integral_image(a):
    """积分图，输出 shape=(H+1,W+1)，多一行一列 0 方便做窗口和。"""
    a = np.asarray(a)
    ii = np.pad(a, ((1, 0), (1, 0)), mode="constant")
    return np.cumsum(np.cumsum(ii, axis=0), axis=1)

def _window_sums(ii, S):
    """
    给定积分图 ii(H+1,W+1)，返回所有 SxS 窗口和的矩阵 sums，shape=(H-S+1, W-S+1)。
    """
    return ii[S:, S:] - ii[:-S, S:] - ii[S:, :-S] + ii[:-S, :-S]

def find_max_energy_roi(
    img,
    roi_size=256,
    channel_axis=-1,
    bg_subtract="quantile",  # "quantile" | "min" | None
    bg_q=0.05,
    clip_negative=True,
    search_margin=0,         # 限制 ROI 左上角在 [m, H-S-m] 范围
):
    """
    在所有 roi_size x roi_size 窗口里找能量和最大的窗口位置。

    Returns
    -------
    (y0, x0) : ROI 左上角
    (cy, cx) : ROI 中心（浮点，窗口几何中心）
    max_sum  : 最大窗口能量
    sums     : （可选）所有窗口和矩阵（有时你想看热图）
    """
    E = _to_energy_map(img, channel_axis=channel_axis)

    # 可选：背景扣除（对暗场/散射底噪有用）
    if bg_subtract == "quantile":
        bg = np.quantile(E, bg_q)
        E = E - bg
    elif bg_subtract == "min":
        E = E - np.min(E)
    elif bg_subtract is None:
        pass
    else:
        raise ValueError("bg_subtract must be 'quantile', 'min', or None")

    if clip_negative:
        E = np.maximum(E, 0.0)

    H, W = E.shape
    S = int(roi_size)
    if S <= 0 or S > H or S > W:
        raise ValueError(f"roi_size={S} invalid for image size {(H,W)}")

    ii = _integral_image(E)
    sums = _window_sums(ii, S)  # shape (H-S+1, W-S+1)

    # 限制搜索范围（可选）：避免 ROI 跑到边缘或被边缘噪声吸走
    if search_margin > 0:
        m = int(search_margin)
        y_min, y_max = m, sums.shape[0] - m
        x_min, x_max = m, sums.shape[1] - m
        if y_min >= y_max or x_min >= x_max:
            raise ValueError("search_margin too large")
        sub = sums[y_min:y_max, x_min:x_max]
        dy, dx = np.unravel_index(np.argmax(sub), sub.shape)
        y0 = y_min + dy
        x0 = x_min + dx
        max_sum = float(sub[dy, dx])
    else:
        y0, x0 = np.unravel_index(np.argmax(sums), sums.shape)
        max_sum = float(sums[y0, x0])

    # ROI 几何中心（不是质心）
    cy = y0 + (S - 1) / 2.0
    cx = x0 + (S - 1) / 2.0

    return (int(y0), int(x0)), (float(cy), float(cx)), max_sum, sums

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

def _to_display_image(img, channel_axis=-1, mode="energy"):
    """
    把输入转成可显示的 2D 或 RGB 图。
    mode:
      - "energy": 多通道求和 -> 2D
      - "ch0": 取第0通道 -> 2D
      - "rgb": 若是3通道则返回RGB，否则退化为energy
    """
    x = np.asarray(img)

    if x.ndim == 2:
        return x

    if x.ndim != 3:
        raise ValueError("img must be (H,W), (H,W,C) or (C,H,W)")

    if channel_axis == 0:  # (C,H,W) -> (H,W,C)
        x = np.moveaxis(x, 0, -1)

    H, W, C = x.shape

    if mode == "rgb" and C == 3:
        # 简单归一到[0,1]便于显示
        y = x.astype(np.float64)
        y = y - np.min(y)
        mx = np.max(y)
        if mx > 0:
            y = y / mx
        return y

    if mode == "ch0":
        return x[..., 0]

    # 默认 energy
    return np.sum(x.astype(np.float64), axis=-1)

def get_roi(img, y0, x0, roi_size, channel_axis=-1,) -> np.ndarray:
    if np.asarray(img).ndim == 2:
        roi = img[y0:y0+roi_size, x0:x0+roi_size]
    else:
        if channel_axis == 0:
            roi = img[:, y0:y0+roi_size, x0:x0+roi_size]
        else:
            roi = img[y0:y0+roi_size, x0:x0+roi_size, :]
    return roi

def show_roi(
    img,
    y0, x0, roi_size,
    channel_axis=-1,
    display_mode="energy",     # "energy" | "ch0" | "rgb"
    log_view=True,
    cmap="gray",
    title_prefix="",
):
    """
    在原图上画 ROI 框，并展示 ROI 裁剪结果。
    """
    disp = _to_display_image(img, channel_axis=channel_axis, mode=display_mode)
    total_erenge = img.sum()
    # 裁 ROI（对RGB就会裁出RGB）
    if np.asarray(img).ndim == 2:
        roi = img[y0:y0+roi_size, x0:x0+roi_size]
        psf_energe = roi.sum()
        roi_disp = roi
    else:
        if channel_axis == 0:
            roi = img[:, y0:y0+roi_size, x0:x0+roi_size]
            psf_energe = roi.sum()
            roi_disp = _to_display_image(roi, channel_axis=0, mode=display_mode)
        else:
            roi = img[y0:y0+roi_size, x0:x0+roi_size, :]
            psf_energe = roi.sum()
            roi_disp = _to_display_image(roi, channel_axis=-1, mode=display_mode)
    # 后面显示用 roi_disp

    # 可选对数显示（常用于衍射/PSF）
    def maybe_log(a):
        a = np.asarray(a, dtype=np.float64)
        if not log_view:
            return a
        a = np.maximum(a, 0)
        return np.log1p(a)

    disp_show = maybe_log(disp)
    roi_show = maybe_log(roi_disp)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)

    # 左：原图 + ROI 框
    ax = axes[0]
    if disp_show.ndim == 3:  # RGB
        ax.imshow(disp_show)
    else:
        ax.imshow(disp_show, cmap=cmap)
    rect = Rectangle((x0, y0), roi_size, roi_size, fill=False, linewidth=2)
    ax.add_patch(rect)
    ax.set_title(f"{title_prefix}Full view (ROI box)")
    ax.axis("off")

    # 右：ROI 放大图
    ax = axes[1]
    if roi_show.ndim == 3:
        ax.imshow(roi_show)
    else:
        ax.imshow(roi_show, cmap=cmap)
    ax.set_title(f"{title_prefix}ROI zoom ({roi_size}×{roi_size}) with energy={psf_energe / total_erenge:.5f}")
    ax.axis("off")

    plt.show()

def psf_to_otf(psf: np.ndarray) -> np.ndarray:
    """
    PSF is spatial-domain intensity.
    OTF = FFT2(PSF).  (Use shifts only to align the spatial origin.)
    Returns OTF with DC at [0,0] (unshifted).
    """
    psf = np.asarray(psf, dtype=np.float64)
    if psf.ndim != 2:
        raise ValueError("psf must be 2D.")
    # If PSF peak is at the center (common), move it to (0,0) before FFT
    return np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(psf)))

def _least_squares_scale_complex(target: np.ndarray, ref: np.ndarray, mask: np.ndarray) -> complex:
    """
    Find complex scalar s minimizing || (target - s*ref) * mask ||_2.
    Closed form: s = <ref, target> / <ref, ref>, inner products over masked pixels.
    """
    m = mask.astype(bool)
    a = ref[m].ravel()
    b = target[m].ravel()
    denom = np.vdot(a, a)
    if np.abs(denom) < 1e-20:
        return 1.0 + 0j
    return np.vdot(a, b) / denom

def pad2(x, pad_factor=2):
    H, W = x.shape
    Hp, Wp = int(H*pad_factor), int(W*pad_factor)
    out = np.zeros((Hp, Wp), dtype=x.dtype)
    y0 = (Hp - H)//2
    x0 = (Wp - W)//2
    out[y0:y0+H, x0:x0+W] = x
    return out

def estimate_support_mask_from_otf(otf: np.ndarray, thr: float = 0.02) -> np.ndarray:
    """
    Heuristic: support where |OTF| is above thr * max(|OTF|).
    Returns boolean mask (True = inside support).
    """
    mag = np.abs(otf)
    m = mag > (thr * mag.max() + 1e-30)
    return m

def compute_dotf(psf_ref: np.ndarray,
                 psf_mod: np.ndarray,
                 *,
                 normalize_flux: bool = True,
                 support_thr: float = 0.02,
                 extra_outside_mask: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """
    Compute dOTF = O_mod - s * O_ref (with optional flux normalization).

    normalize_flux:
      If True, estimate complex scale s so that dOTF outside the OTF support is minimized
      (matches the paper's "scale until dOTF is dark outside pupil image/reflection" idea).
      See Fig.1 caption description. :contentReference[oaicite:5]{index=5}

    support_thr:
      Used to estimate OTF support mask from |OTF|.

    extra_outside_mask:
      Optional user-provided mask selecting "outside" region to drive scaling.
      If provided, it overrides the automatically derived outside mask.

    Returns:
      dotf (complex), info dict containing O_ref, O_mod, scale s, masks.
    """
    O_ref = psf_to_otf(psf_ref)
    O_mod = psf_to_otf(psf_mod)

    # Build an "outside" mask for scaling: outside of estimated OTF support
    support = estimate_support_mask_from_otf(O_ref, thr=support_thr) | \
              estimate_support_mask_from_otf(O_mod, thr=support_thr)

    outside = ~support
    if extra_outside_mask is not None:
        outside = extra_outside_mask.astype(bool)

    s = 1.0 + 0j
    if normalize_flux:
        # complex least squares scale so that outside region cancels best
        s = _least_squares_scale_complex(target=O_mod, ref=O_ref, mask=outside)

    dotf = O_mod - s * O_ref
    info = {
        "O_ref": O_ref,
        "O_mod": O_mod,
        "scale": s,
        "support_mask": support,
        "outside_mask": outside,
    }
    return dotf, info

def show_complex_2d(z: np.ndarray, *,
                    log_magnitude: bool = False,
                    phase_range: str = "pi",
                    cmap_mag: str = "viridis",
                    cmap_phase: str = "twilight",
                    title: str | None = None) -> None:
    """
    可视化二维复数数组：幅度 + 相位。

    参数
    - z: 2D complex array (H, W)
    - log_magnitude: 幅度是否用 log1p 压缩动态范围（常用，避免亮点把整体压扁）
    - phase_range:
        - "pi": 相位显示范围 [-pi, pi]（np.angle 默认）
        - "2pi": 相位映射到 [0, 2pi)
    - cmap_mag / cmap_phase: colormap 名称
    - title: 总标题（可选）
    """
    z = np.asarray(z)
    if z.ndim != 2:
        raise ValueError(f"Expected a 2D array, got shape {z.shape}")
    if not np.iscomplexobj(z):
        # 允许用户传 real，但提醒：相位会是 0 或 pi（取决于符号）
        z = z.astype(np.complex128)

    mag = np.abs(z)                 # 现成：幅度
    phase = np.angle(z)             # 现成：相位 in [-pi, pi]

    if log_magnitude:
        mag_vis = np.log1p(mag)     # log(1 + |z|)，避免 mag=0 时 -inf
        mag_label = "log(1 + |z|)"
    else:
        mag_vis = mag
        mag_label = "|z|"

    if phase_range == "2pi":
        phase_vis = np.mod(phase, 2 * np.pi)
        vmin_p, vmax_p = 0.0, 2 * np.pi
        phase_label = "arg(z) in [0, 2π)"
    elif phase_range == "pi":
        phase_vis = phase
        vmin_p, vmax_p = -np.pi, np.pi
        phase_label = "arg(z) in [-π, π]"
    else:
        raise ValueError("phase_range must be 'pi' or '2pi'")

    fig, ax = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)

    im0 = ax[0].imshow(mag_vis, cmap=cmap_mag)
    ax[0].set_title(mag_label)
    ax[0].set_axis_off()
    fig.colorbar(im0, ax=ax[0], fraction=0.046, pad=0.04)

    im1 = ax[1].imshow(phase_vis, cmap=cmap_phase, vmin=vmin_p, vmax=vmax_p)
    ax[1].set_title(phase_label)
    ax[1].set_axis_off()
    fig.colorbar(im1, ax=ax[1], fraction=0.046, pad=0.04,
                 ticks=[vmin_p, 0.0, vmax_p] if phase_range == "pi" else [0.0, np.pi, 2*np.pi])

    if title:
        fig.suptitle(title, y=1.02)

    plt.show()

def demosaic_bayer_gb2bgr(raw: np.ndarray) -> np.ndarray:
    """
    Bilinear demosaicing for Bayer pattern 'GBRG' (cv2.COLOR_BayerGB2BGR).
    Supports float / int input (not restricted to uint8).
    Output: BGR image, shape (H, W, 3), dtype float64.

    BayerGB (GBRG) layout (0-based indices):
        (0,0)=G, (0,1)=B
        (1,0)=R, (1,1)=G
    """
    raw = np.asarray(raw)
    if raw.ndim != 2:
        raise ValueError("raw must be 2D Bayer mosaic array (H, W).")
    H, W = raw.shape

    # Work in float64 for stability
    x = raw.astype(np.float64, copy=False)

    # Masks for known samples
    G = np.zeros((H, W), dtype=np.float64)
    R = np.zeros((H, W), dtype=np.float64)
    B = np.zeros((H, W), dtype=np.float64)

    # Known positions for GBRG
    # G at (even, even) and (odd, odd)
    G[0::2, 0::2] = x[0::2, 0::2]
    G[1::2, 1::2] = x[1::2, 1::2]
    # B at (even, odd)
    B[0::2, 1::2] = x[0::2, 1::2]
    # R at (odd, even)
    R[1::2, 0::2] = x[1::2, 0::2]

    # Helper: reflect-pad then neighbor access
    def pad_reflect(a):
        return np.pad(a, ((1, 1), (1, 1)), mode="reflect")

    def interp_from_neighbors(a, wsum, eps=1e-12):
        # normalized weighted average
        return a / (wsum + eps)

    # Build neighbor sums for bilinear interpolation
    # We'll compute missing values via averaging of appropriate neighbors of the same color.
    Rp = pad_reflect(R)
    Gp = pad_reflect(G)
    Bp = pad_reflect(B)

    # Precompute 4-neighbor (N,S,E,W) and 4-diagonal (NE,NW,SE,SW) sums for each plane
    def nswe(p):
        c = p[1:-1, 1:-1]
        n = p[0:-2, 1:-1]
        s = p[2:  , 1:-1]
        w = p[1:-1, 0:-2]
        e = p[1:-1, 2:  ]
        return n + s + w + e

    def diag4(p):
        nw = p[0:-2, 0:-2]
        ne = p[0:-2, 2:  ]
        sw = p[2:  , 0:-2]
        se = p[2:  , 2:  ]
        return nw + ne + sw + se

    # Also need weights: count of non-zero contributors (since masks are sparse).
    # We'll compute neighbor weights similarly by applying the same neighborhood sums to binary masks.
    Rm = (R != 0).astype(np.float64)
    Gm = (G != 0).astype(np.float64)
    Bm = (B != 0).astype(np.float64)

    Rmp = pad_reflect(Rm)
    Gmp = pad_reflect(Gm)
    Bmp = pad_reflect(Bm)

    R_nswe = nswe(Rp); Rw_nswe = nswe(Rmp)
    R_diag = diag4(Rp); Rw_diag = diag4(Rmp)

    G_nswe = nswe(Gp); Gw_nswe = nswe(Gmp)
    # G diagonal not usually needed for bilinear (G at R/B sites uses NSWE)
    B_nswe = nswe(Bp); Bw_nswe = nswe(Bmp)
    B_diag = diag4(Bp); Bw_diag = diag4(Bmp)

    # Now fill missing values based on site type.
    # Site type masks:
    even_rows = np.zeros((H, W), dtype=bool); even_rows[0::2, :] = True
    odd_rows  = ~even_rows
    even_cols = np.zeros((H, W), dtype=bool); even_cols[:, 0::2] = True
    odd_cols  = ~even_cols

    # Known sites:
    isG1 = even_rows & even_cols       # G at (even, even)
    isB  = even_rows & odd_cols        # B at (even, odd)
    isR  = odd_rows  & even_cols       # R at (odd, even)
    isG2 = odd_rows  & odd_cols        # G at (odd, odd)

    # Interpolate Green at R and B sites using NSWE of G
    missingG_at_RB = isR | isB
    G[missingG_at_RB] = interp_from_neighbors(G_nswe[missingG_at_RB], Gw_nswe[missingG_at_RB])

    # Interpolate Red:
    # - at G sites: use horizontal/vertical depending on which G
    #   For GBRG:
    #   * at G1 (even,even): nearest R are vertical (up/down)
    #   * at G2 (odd,odd):   nearest R are horizontal (left/right)
    # - at B sites: use diagonal
    # We'll implement with neighbor sums but selecting appropriate directions.
    # For directional interpolation, use two-neighbor averages.

    # Prepare directional sums/weights for R
    # vertical (N+S), horizontal (E+W)
    def vert(p): return p[0:-2,1:-1] + p[2:,1:-1]
    def hori(p): return p[1:-1,0:-2] + p[1:-1,2:]
    R_vert = vert(Rp); Rw_vert = vert(Rmp)
    R_hori = hori(Rp); Rw_hori = hori(Rmp)

    # Fill R at G1 (even,even): vertical
    R[isG1] = interp_from_neighbors(R_vert[isG1], Rw_vert[isG1])
    # Fill R at G2 (odd,odd): horizontal
    R[isG2] = interp_from_neighbors(R_hori[isG2], Rw_hori[isG2])
    # Fill R at B sites: diagonal
    R[isB] = interp_from_neighbors(R_diag[isB], Rw_diag[isB])

    # Interpolate Blue (symmetric to Red with roles swapped):
    # - at G sites: use horizontal/vertical depending on which G
    #   For GBRG:
    #   * at G1 (even,even): nearest B are horizontal (left/right)
    #   * at G2 (odd,odd):   nearest B are vertical (up/down)
    # - at R sites: diagonal
    B_vert = vert(Bp); Bw_vert = vert(Bmp)
    B_hori = hori(Bp); Bw_hori = hori(Bmp)

    B[isG1] = interp_from_neighbors(B_hori[isG1], Bw_hori[isG1])
    B[isG2] = interp_from_neighbors(B_vert[isG2], Bw_vert[isG2])
    B[isR]  = interp_from_neighbors(B_diag[isR],  Bw_diag[isR])

    # Stack as BGR
    bgr = np.stack([B, G, R], axis=-1)
    return bgr

if __name__ == "__main__":
    psf_chw = np.load("raw_image_1_555nm.npy")
    delta_psf_chw =np.load("raw_image_2_555nm.npy")
    black_chw = np.load("raw_image_0_555nm.npy")
    PSF = (psf_chw- black_chw).clip(min=0)

    delta_PSF = (delta_psf_chw- black_chw).clip(min=0)

    rgb = cv2.cvtColor((PSF/256).astype(np.uint8), cv2.COLOR_BayerGB2BGR)
    a = 1400
    # (y0, x0), (cy, cx), max_sum, _ = find_max_energy_roi(rgb, roi_size=a, channel_axis=-1)
    cx = 1240
    cy = 1062
    y0 = int(cy - a / 2)
    x0 = int(cx - a / 2)
    show_roi(rgb, y0, x0, a, channel_axis=-1, display_mode="rgb", log_view=False, title_prefix="RGB ")
    psf_roi = pad2(get_roi(PSF, y0, x0, a, channel_axis=-1))
    delta_psf_roi = pad2(get_roi(delta_PSF, y0, x0, a, channel_axis=-1))
    psf_roi_rgb = demosaic_bayer_gb2bgr(psf_roi)
    delta_psf_roi_rgb = demosaic_bayer_gb2bgr(delta_psf_roi)
    psf_roi_g = psf_roi_rgb[..., 1]
    delta_psf_roi_g = delta_psf_roi_rgb[..., 1]
    DOTF, _ = compute_dotf(psf_roi_g, delta_psf_roi_g)
    DOTF /= np.max(np.abs(DOTF))
    show_complex_2d(DOTF)