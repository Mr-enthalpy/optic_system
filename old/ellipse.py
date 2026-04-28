from typing import Tuple

import matplotlib.pyplot as plt
import numpy as np
import scipy.optimize as opt
from matplotlib import rcParams
from scipy.stats import pearsonr


# 假设 A_theoretical 是你提供的理论模型函数
def _a_theoretical(r: np.ndarray, a: float, b: float) -> np.ndarray:
    """
    计算椭圆与圆的重叠面积
    :param r: 一串半径数组
    :param a: 半长轴
    :param b: 半短轴
    :return:
        半径数组相对应的重叠面积数组
    """
    # 确保对每个 r 值分别进行处理
    result = np.zeros_like(r)
    for i, ri in enumerate(r):
        r_i: float = ri
        if r_i <= b:
            result[i] = np.pi * r_i**2
        elif r_i >= a:
            result[i] = np.pi * a * b
        else:
            x0 = a * np.sqrt((r_i**2 - b**2) / (a**2 - b**2))
            y0 = b * np.sqrt((a**2 - r_i**2) / (a**2 - b**2))
            term_circle = np.pi * r_i**2 - 2 * x0 * y0 - 2 * r_i**2 * np.arcsin(x0 / r_i)
            term_ellipse = 2 * a * b * (np.arcsin(x0 / a) + (x0 / a) * np.sqrt(1 - (x0 / a)**2))
            result[i] = term_circle + term_ellipse
    return result

# 拟合目标函数（含放缩因子）
def _fit_function(r: np.ndarray, k: float, a: float, b: float) -> np.ndarray:
    """
    拟合函数，包含放缩因子 k
    :param r: 圆的半径数组
    :param k: 放缩因子k
    :param a: 半长轴
    :param b: 半短轴
    :return:
        拟合结果数组
    """
    return k * _a_theoretical(r, a, b)

def _residuals(params: Tuple[float, float, float], r: np.ndarray, a_data: np.ndarray) -> np.ndarray:
    """
    计算残差，计算拟合值与标定数据的差值，用于最小二乘
    :param params: 放缩因子 k，半长轴 a，半短轴 b，其中 params = (k, a, b)
    :param r:半径数组
    :param a_data: 标定数据的重叠面积数组
    :return:
        残差数组
    """
    k, a, b = params
    A_pred = _fit_function(r, k, a, b)
    return a_data - A_pred

def create_ellipse_mask(center: Tuple[float, float],
                        a: float,
                        b: float,
                        image_size: Tuple[int, int],
                        rotate_angle: float = 0.0
                        ) -> np.ndarray:
    """
    生成椭圆掩码矩阵
    :param center: 圆心坐标 (cx, cy)
    :param a: 半长轴长度
    :param b: 半短轴长度
    :param image_size: 图像大小 (width, height)
    :param rotate_angle: 旋转角度（度）
    :return:
       二维数组，椭圆内为1，椭圆外为0
    """
    # 将角度转换为弧度
    angle_rad = np.deg2rad(rotate_angle)
    cos_angle = np.cos(angle_rad)
    sin_angle = np.sin(angle_rad)

    # 创建坐标网格
    x = np.arange(image_size[0])
    y = np.arange(image_size[1])
    xx, yy = np.meshgrid(x, y, indexing='ij')

    # 平移坐标到圆心
    x_centered = xx - center[0]
    y_centered = yy - center[1]

    # 旋转坐标（将点旋转回椭圆未旋转时的坐标系）
    x_rot = x_centered * cos_angle + y_centered * sin_angle
    y_rot = -x_centered * sin_angle + y_centered * cos_angle

    # 椭圆方程判断
    ellipse_eq = (x_rot ** 2) / (a ** 2) + (y_rot ** 2) / (b ** 2)
    mask = ellipse_eq <= 1.0

    return mask.astype(np.uint8)

def estimate_ellipse_parameters(a_data: np.ndarray,
                                r_values: np.ndarray,
                                plot: bool = False
                                ) -> Tuple[float, float, float]:
    """
    估计椭圆的长轴和短轴长度。
    :param a_data:标定数据的重叠面积数组
    :param r_values: 对应的 r 值数组
    :param plot: 是否绘制拟合结果图
    返回:
    a_length: 长轴长度
    b_length: 短轴长度
    k: 放缩因子
    """

    # 初始参数猜测 (k, a, b)
    initial_guess = np.array([0.031031015342212198, 125.30328015953916, 68.19561957036889])
    a_data -= a_data.min()  # 去掉偏置
    # 使用最小二乘法拟合
    params_opt, _ = opt.leastsq(func = _residuals, x0 = initial_guess, args=(r_values, a_data))
    # 获取拟合结果
    k_opt, a_opt, b_opt = params_opt
    if plot:
        # 计算拟合值
        A_pred = _fit_function(r_values, k_opt, a_opt, b_opt)

        # 计算 R² (决定系数)
        residual_sum_of_squares = np.sum((a_data - A_pred) ** 2)
        total_sum_of_squares = np.sum((a_data - np.mean(a_data)) ** 2).astype(float)
        R_squared = 1 - residual_sum_of_squares / total_sum_of_squares

        # 计算相关系数 (Pearson correlation coefficient)
        correlation, _ = pearsonr(a_data, A_pred)

        # 输出评价指标
        print(f"决定系数 (R²): {R_squared}")
        print(f"相关系数 (Pearson): {correlation}")
        # 设置字体为支持中文的字体，例如 SimHei 或 Microsoft YaHei
        rcParams['font.family'] = 'SimHei'  # 你可以根据需要选择其他字体
        rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
        # 绘制拟合结果
        plt.plot(r_values, a_data, 'o', label="标定数据")
        plt.plot(r_values, _fit_function(r_values, k_opt, a_opt, b_opt), label="拟合曲线")
        plt.xlabel("r")
        plt.ylabel("重叠面积")
        plt.title(f"拟合结果: a={a_opt:.2f}, b={b_opt:.2f}, k={k_opt:.5f}\nR^2={R_squared:.4f}, 相关系数={correlation:.4f}")
        plt.legend()
        plt.grid(True)
        plt.show()
    return a_opt, b_opt, k_opt
