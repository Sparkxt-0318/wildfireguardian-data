"""Pytest fixtures. Helper constructors live in :mod:`helpers`."""

from __future__ import annotations

import numpy as np
import pytest
from helpers import make_raster, planar_surface

from wildfireguardian_data import RasterLayer


@pytest.fixture
def tilted_plane_layer() -> RasterLayer:
    return make_raster(planar_surface(), name="plane")


@pytest.fixture
def flat_layer() -> RasterLayer:
    return make_raster(np.full((12, 12), 412.0), name="flat")
