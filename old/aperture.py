import numpy as np

from lcd import LCDDisplay, lcd_init
from typing import Tuple, Dict

class Aperture:
    def __init__(self, coordinates: Tuple[int, int], a: int, r: int) -> None:
        """
        初始化 LCD 显示
        :param coordinates: LCD上有效圆形光阑的外切正方形的左上角坐标 (x, y)
        :param a: 外切正方形的边长
        :param r: 有效圆形光阑的半径
        """
        self.__lcd = lcd_init()
        self.__coordinates = coordinates
        self.__a = a
        self.__r = r
        base_mask = np.zeros((a, a), dtype=np.uint8)
        cx, cy = a // 2, a // 2
        Y, X = np.ogrid[:a, :a]
        mask = (X - cx) ** 2 + (Y - cy) ** 2 <= r ** 2
        base_mask[mask] = 1
        self.__pupil_mask = base_mask
        self.show(np.ones((a, a, 3), dtype=np.uint8) * 255)

    @property
    def size(self) -> Tuple[int, int]:
        """返回 LCD 的分辨率 (W, H)"""
        return self.__a, self.__a

    @property
    def r(self) -> int:
        """返回光阑半径"""
        return self.__r

    @property
    def pupil(self) -> np.ndarray:
        """返回光阑掩码"""
        return self.__pupil_mask

    def show(self, mask: np.ndarray) -> None:
        H, W = self.__lcd.size
        base_mask = np.zeros((W, H, 3), dtype=np.uint8)
        x, y = self.__coordinates
        mask = mask  * self.__pupil_mask[..., None]
        base_mask[y:(y+self.__a), x:(x+self.__a), :] = mask
        self.__lcd.show(base_mask)
