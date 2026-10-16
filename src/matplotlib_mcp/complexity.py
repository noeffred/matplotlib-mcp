"""Conservative whole-chart estimates before constructing Matplotlib artists."""

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from .errors import InputError


_FORMAT = re.compile(r"%[-+ #0]*(\d*)(?:\.(\d+))?([eEfFgGdiu%])")


def validate_autopct(value: Any, limit: int) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise InputError("pie autopct must be a numeric percent-format string")
    # Numeric percent values fit in this conservative allowance. Never expand a
    # format until all fields have been checked, including literal suffixes.
    maximum = limit or 500
    size = 0
    conversions = 0
    cursor = 0
    while cursor < len(value):
        if value[cursor] != "%":
            size += 1
            cursor += 1
            continue
        match = _FORMAT.match(value, cursor)
        if match is None:
            raise InputError("pie autopct uses unsupported formatting")
        width, precision, conversion = match.groups()
        if len(width) > 6 or (precision and len(precision) > 6):
            raise InputError("pie autopct field exceeds label limit")
        width, precision = int(width or 0), int(precision or 6)
        if width > maximum or precision > maximum:
            raise InputError("pie autopct field exceeds label limit")
        if conversion == "%":
            if match.group() != "%%":
                raise InputError("pie autopct uses unsupported formatting")
            size += 1
        else:
            conversions += 1
            size += max(width, precision + 32)
        cursor = match.end()
    if conversions != 1 or size > maximum:
        raise InputError("pie autopct expanded label exceeds label limit or requires one numeric field")


def _size(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return int(value.size)
    if isinstance(value, list):
        return sum(_size(item) for item in value)
    if isinstance(value, dict):
        return sum(_size(item) for item in value.values())
    return 1


def _length(value: Any) -> int:
    return len(value) if isinstance(value, list) or (isinstance(value, np.ndarray) and value.ndim) else 1


def _groups(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return value.shape[1] if value.ndim == 2 else 1
    if isinstance(value, list) and value and isinstance(value[0], (list, np.ndarray)):
        return len(value)
    return 1


@dataclass
class Budget:
    limits: Any
    artists: int = 0
    geometry: int = 0
    work: int = 0

    def charge(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> None:
        n = max(1, sum(_size(arg) for arg in args) + _size(kwargs))
        first = args[0] if args else next(iter(kwargs.values()), [])
        count = max(1, _size(first))
        artists, geometry, work = 1, n * 2, n
        if method in {"bar", "barh"}:
            # Inputs broadcast; include optional widths, bottoms, and errors.
            vectors = [*args[:4], *(kwargs[k] for k in ("x", "height", "y", "width", "bottom", "left") if k in kwargs)]
            shapes = [np.shape(v) for v in vectors]
            try:
                count = math.prod(np.broadcast_shapes(*shapes)) if shapes else 1
            except ValueError:
                raise InputError("bar inputs must have broadcast-compatible shapes") from None
            artists, geometry = count * 3, count * 12
        elif method == "pie":
            validate_autopct(kwargs.get("autopct", args[4] if len(args) > 4 else None), self.limits.label_chars)
            artists = count * 4
            shadow = kwargs.get("shadow", args[6] if len(args) > 6 else False)
            geometry = count * (128 if shadow else 64)
        elif method in {"plot", "step", "fill", "stackplot"}:
            artists = max(1, n // max(1, _length(first)))
            # Account for 2D series and step expansion.
            geometry = n * (4 if method == "step" else 2)
        elif method in {"scatter", "quiver", "eventplot", "ecdf", "stem", "stairs",
                        "fill_between", "fill_betweenx", "errorbar"}:
            geometry = n * 8
            artists = max(1, _length(first)) if method == "eventplot" else 8
        elif method == "boxplot":
            groups = _groups(first)
            artists, geometry, work = groups * 10, n * 4, n * max(1, math.ceil(math.log2(count + 1)))
        elif method == "violinplot":
            points = kwargs.get("points", args[8] if len(args) > 8 else 100)
            if type(points) is not int or points < 1:
                raise InputError("violin sampling points must be a positive integer")
            groups = _groups(first)
            artists, geometry, work = groups * 8, groups * points * 4, count * points
        elif method in {"hist", "hist2d", "hexbin"}:
            if method == "hexbin":
                option, position, default = "gridsize", 3, 100
            else:
                option = "bins"
                position = 1 if method == "hist" else 2
                default = 10
            bins = kwargs.get(option, args[position] if len(args) > position else default)
            if method == "hexbin" and isinstance(bins, (list, np.ndarray)):
                nx, ny = (int(v) for v in bins)
                cells = 2 * nx * ny + nx + ny + 1
            elif isinstance(bins, (int, np.integer)):
                cells = int(bins) ** (1 if method == "hist" else 2)
            elif method == "hist2d" and isinstance(bins, (list, np.ndarray)):
                cells = 1
                for axis_bins in bins:
                    if np.ndim(axis_bins) == 0:
                        cells *= int(axis_bins)
                    else:
                        cells *= max(1, len(axis_bins) - 1)
            else:
                cells = max(1, _size(bins))
            groups = _groups(first) if method == "hist" else 1
            artists = cells * groups if method == "hist" else 1
            geometry, work = cells * groups * 8, n + cells * groups
        elif method in {"imshow", "pcolormesh"}:
            geometry = n * (4 if method == "pcolormesh" else 1)
        elif method in {"contour", "contourf", "tricontour", "tricontourf"}:
            levels = kwargs.get("levels", 10)
            if method in {"contour", "contourf"} and len(args) in {2, 4}:
                levels = args[-1]
            elif method.startswith("tri"):
                position = 4 if len(args) > 2 and np.ndim(args[2]) == 2 else 3
                if len(args) > position:
                    levels = args[position]
            if isinstance(levels, (int, np.integer)):
                levels = int(levels) + 2
            else:
                levels = _size(levels)
            geometry, work = n * levels * 2, n * levels
        elif method in {"triplot", "tripcolor"}:
            geometry, work = n * 12, n * max(1, math.ceil(math.log2(n + 1)))
        elif method == "streamplot":
            density = kwargs.get("density", args[4] if len(args) > 4 else 1)
            densities = density if isinstance(density, (list, np.ndarray)) else [density, density]
            cells = math.prod(max(1, math.ceil(30 * float(v))) for v in densities)
            artists, geometry, work = cells * 2, cells * 32, cells * n
        elif method == "barbs":
            increments = kwargs.get("barb_increments", {})
            if not isinstance(increments, dict) or set(increments) - {"half", "full", "flag"}:
                raise InputError("barb increments support half, full, and flag")
            inc = {"half": 5, "full": 10, "flag": 50, **increments}
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in inc.values()):
                raise InputError("barb increments must be finite positive numbers")
            offset = 0 if len(args) in {2, 3} else 2
            if len(args) < offset + 2:
                raise InputError("barbs requires vector components")
            u, v = np.broadcast_arrays(np.asarray(args[offset], dtype=float), np.asarray(args[offset + 1], dtype=float))
            geometry = 0
            for a, b in zip(u.flat, v.flat):
                magnitude = math.hypot(float(a), float(b))
                if kwargs.get("rounding", True):
                    magnitude += inc["half"] / 2
                if not math.isfinite(magnitude):
                    raise InputError("barb magnitude must be finite")
                # min increment also bounds unusual administrator/client increment ordering.
                units = magnitude / min(inc.values())
                if not math.isfinite(units) or (self.limits.geometry and units > self.limits.geometry):
                    raise InputError("barb generated geometry exceeds the chart limit")
                geometry += 8 + math.ceil(units) * 6
            work += geometry
        else:
            # Registered setup calls: ticks, tick labels and legend can make artists.
            artists, geometry = n, n * 4
        self.artists += artists
        self.geometry += geometry
        self.work += work
        for name, value in (("artists", self.artists), ("geometry", self.geometry), ("work", self.work)):
            limit = getattr(self.limits, name)
            if limit and value > limit:
                raise InputError(f"chart {name} exceeds the configured limit ({limit})")
