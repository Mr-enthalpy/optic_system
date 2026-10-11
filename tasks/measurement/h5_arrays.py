"""HDF5 array helpers for measured-artifact modules."""

from __future__ import annotations

import h5py
import numpy as np


def read_mask_arrays(src: h5py.File) -> np.ndarray | None:
    if "masks/masks_physical" not in src:
        return None
    masks = np.asarray(src["masks/masks_physical"])
    if masks.ndim == 3 and masks.shape[1] > 1 and masks.shape[2] > 1:
        return masks
    return None
