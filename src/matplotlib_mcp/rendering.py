"""Chart validation, catalog, and in-memory rendering without application startup."""

from __future__ import annotations

import csv
import inspect
import io
import json
import logging
import math
import os
import threading
import warnings
from dataclasses import dataclass
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")
matplotlib.rcParams["text.usetex"] = False
matplotlib.rcParams["text.parse_math"] = False
# Font lookup warnings include caller-supplied family names.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from .complexity import Budget
from .errors import InputError
from .registry import PLOT_METHODS, SETUP_METHODS
from .theme import load_theme


@dataclass(frozen=True)
class Limits:
    request_bytes: int = 4 * 1024 * 1024
    data_bytes: int = 1 * 1024 * 1024
    rows: int = 10_000
    columns: int = 50
    array_elements: int = 250_000
    label_chars: int = 500
    image_width: int = 1600
    image_height: int = 1600
    axes: int = 16
    calls: int = 128
    bins: int = 200
    contour_levels: int = 200
    image_pixels: int = 1_000_000
    artists: int = 10_000
    geometry: int = 250_000
    work: int = 5_000_000

    @classmethod
    def from_env(cls) -> "Limits":
        names = {
            "image_pixels": "MAX_IMAGE_PIXELS",
            "artists": "MAX_ARTISTS",
            "geometry": "MAX_GEOMETRY",
            "work": "MAX_WORK",
            "request_bytes": "MAX_REQUEST_BYTES",
            "data_bytes": "MAX_DATA_BYTES",
            "rows": "MAX_ROWS",
            "columns": "MAX_COLUMNS",
            "array_elements": "MAX_ARRAY_ELEMENTS",
            "label_chars": "MAX_LABEL_CHARS",
            "image_width": "MAX_IMAGE_WIDTH",
            "image_height": "MAX_IMAGE_HEIGHT",
            "axes": "MAX_AXES",
            "calls": "MAX_CALLS",
            "bins": "MAX_BINS",
            "contour_levels": "MAX_CONTOUR_LEVELS",
        }
        values: dict[str, int] = {}
        for field, variable in names.items():
            raw = os.environ.get(variable)
            if raw is None:
                continue
            try:
                value = int(raw)
            except ValueError as exc:
                raise RuntimeError(f"{variable} must be a non-negative integer") from exc
            if value < 0:
                raise RuntimeError(f"{variable} must be a non-negative integer")
            values[field] = value
        return cls(**values)

    def describe(self) -> dict[str, int | str]:
        return {
            name.upper(): ("disabled" if value == 0 else value)
            for name, value in (
                ("max_image_pixels", self.image_pixels),
                ("max_artists", self.artists),
                ("max_geometry", self.geometry),
                ("max_work", self.work),
                ("max_request_bytes", self.request_bytes),
                ("max_data_bytes", self.data_bytes),
                ("max_rows", self.rows),
                ("max_columns", self.columns),
                ("max_array_elements", self.array_elements),
                ("max_label_chars", self.label_chars),
                ("max_image_width", self.image_width),
                ("max_image_height", self.image_height),
                ("max_axes", self.axes),
                ("max_calls", self.calls),
                ("max_bins", self.bins),
                ("max_contour_levels", self.contour_levels),
            )
        }


LIMITS = Limits.from_env()
_RENDER_LOCK = threading.RLock()
THEME = load_theme()
_PLOT_DATA_ARGS = {
    "plot": "x and y vectors",
    "scatter": "x and y vectors; optional scalar or vector sizes/colors",
    "bar": "x positions/categories and heights",
    "barh": "y positions/categories and widths",
    "step": "x and y vectors",
    "stem": "y vector, optionally x and y vectors",
    "stairs": "values vector and optional edge vector (one more edge than values)",
    "fill": "x and y polygon vectors",
    "fill_between": "x, y1, and y2 vectors",
    "fill_betweenx": "y, x1, and x2 vectors",
    "stackplot": "x vector and one or more y vectors of equal length",
    "hist": "one vector or a list of vectors",
    "boxplot": "one vector or a list of vectors/groups",
    "violinplot": "one vector or a list of vectors/groups",
    "eventplot": "one vector or a list of position vectors",
    "ecdf": "one sample vector",
    "errorbar": "x and y vectors; optional xerr/yerr vectors or arrays",
    "hist2d": "paired x and y vectors",
    "hexbin": "paired x and y vectors; optional C vector",
    "pie": "non-negative values vector; optional labels vector",
    "imshow": "2D numeric matrix",
    "pcolormesh": "2D numeric matrix C, or coordinate grids X/Y plus C",
    "contour": "2D Z matrix, optionally coordinate grids X and Y",
    "contourf": "2D Z matrix, optionally coordinate grids X and Y",
    "quiver": "coordinate grids X/Y and vector component grids U/V",
    "barbs": "coordinate grids X/Y and vector component grids U/V",
    "streamplot": "1D x/y coordinates and matching U/V vector grids",
    "triplot": "paired x/y vectors, optionally triangle indices",
    "tripcolor": "paired x/y vectors and scalar values, optionally triangles",
    "tricontour": "paired x/y vectors and scalar values, optionally triangles",
    "tricontourf": "paired x/y vectors and scalar values, optionally triangles",
}


def _check_limit(value: int, limit: int, name: str) -> None:
    if limit and value > limit:
        raise InputError(f"{name} exceeds the configured limit ({limit})")


def _walk_values(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    else:
        yield value


def _count_leaves(value: Any) -> int:
    if isinstance(value, list):
        return sum(_count_leaves(item) for item in value)
    if isinstance(value, dict):
        return sum(_count_leaves(item) for item in value.values())
    return 1


def _coerce_column(values: list[Any]) -> list[Any]:
    if not values:
        return values
    converted: list[float] = []
    for item in values:
        if item is None or isinstance(item, (dict, list, bool)):
            return values
        try:
            number = float(item)
        except (ValueError, TypeError):
            return values
        if not math.isfinite(number):
            raise InputError("numeric values must be finite")
        converted.append(number)
    return converted


def _prepare_data(data: Any) -> tuple[dict[str, list[Any]], int, int]:
    if not isinstance(data, dict) or set(data) not in ({"format", "values"}, {"format", "rows"}, {"format", "csv"}):
        raise InputError("data must contain exactly format and one of values, rows, or csv")
    fmt = data.get("format")
    if fmt == "columns" and set(data) == {"format", "values"}:
        columns = data["values"]
        if not isinstance(columns, dict) or not columns:
            raise InputError("columns data requires a non-empty values object")
        if any(not isinstance(name, str) or not name for name in columns):
            raise InputError("column names must be non-empty strings")
        if any(not isinstance(values, list) or not values for values in columns.values()):
            raise InputError("each named column must be a non-empty vector or matrix")
        normalized = {name: values for name, values in columns.items()}
    elif fmt == "rows" and set(data) == {"format", "rows"}:
        rows = data["rows"]
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise InputError("rows must be a non-empty list of JSON objects")
        headers = list(rows[0])
        if not headers or any(not isinstance(key, str) or not key for key in headers):
            raise InputError("rows require non-empty string column names")
        for row in rows:
            if set(row) != set(headers):
                raise InputError("all rows must have the same columns")
        normalized = {key: _coerce_column([row[key] for row in rows]) for key in headers}
    elif fmt == "csv" and set(data) == {"format", "csv"}:
        text = data["csv"]
        if not isinstance(text, str) or not text:
            raise InputError("csv must be a non-empty string")
        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            records = list(reader)
        except csv.Error as exc:
            raise InputError("csv is malformed") from exc
        if len(records) < 2:
            raise InputError("csv must contain a header and at least one data row")
        headers = records[0]
        if not headers or any(not header.strip() for header in headers) or len(set(headers)) != len(headers):
            raise InputError("csv requires unique, non-empty column names")
        if any(len(record) != len(headers) for record in records[1:]):
            raise InputError("every csv row must have the same number of fields as the header")
        normalized = {}
        for key, column in zip(headers, zip(*records[1:])):
            values = [value if value != "" else None for value in column]
            normalized[key] = _coerce_column(values)
    else:
        raise InputError("format must be columns, rows, or csv and match its data field")
    nrows = max(map(len, normalized.values()))
    _check_limit(nrows, LIMITS.rows, "row count")
    _check_limit(len(normalized), LIMITS.columns, "column count")
    elements = _count_leaves(normalized)
    _check_limit(elements, LIMITS.array_elements, "array element count")
    try:
        serialized = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise InputError("data must contain JSON values with finite numbers") from None
    _check_limit(len(serialized), LIMITS.data_bytes, "normalized data size")
    return normalized, nrows, len(serialized)


def _resolve(value: Any, data: dict[str, list[Any]], cache: dict[str, np.ndarray] | None = None) -> Any:
    if cache is None:
        cache = {}
    if isinstance(value, dict):
        if set(value) == {"$data"}:
            key = value["$data"]
            if not isinstance(key, str) or key not in data:
                raise InputError("a $data reference names a missing column")
            if key in cache:
                return cache[key]
            if any(item is None for item in _walk_values(data[key])):
                raise InputError("referenced data columns cannot contain empty values")
            try:
                cache[key] = np.asarray(data[key])
                return cache[key]
            except (TypeError, ValueError):
                raise InputError("referenced data must be a rectangular vector or matrix") from None
        # No free-form objects are passed through. The only nested object is a data reference.
        if any(not isinstance(key, str) for key in value):
            raise InputError("configuration object keys must be strings")
        if set(value) & {"usetex", "parse_math", "fontproperties", "fname"}:
            raise InputError("TeX, math text, and custom font properties are not supported")
        return {key: _resolve(item, data, cache) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, data, cache) for item in value]
    if isinstance(value, str) and len(value) > LIMITS.label_chars and LIMITS.label_chars:
        raise InputError("a configuration string exceeds MAX_LABEL_CHARS")
    if isinstance(value, float) and not math.isfinite(value):
        raise InputError("configuration numbers must be finite")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise InputError("configuration values must be JSON scalars, arrays, or $data references")


def _validate_kwargs(method: str, kwargs: Any) -> dict[str, Any]:
    if not isinstance(kwargs, dict):
        raise InputError("call kwargs must be an object")
    allowed = PLOT_METHODS[method].kwargs if method in PLOT_METHODS else SETUP_METHODS[method][2]
    extra = set(kwargs) - set(allowed)
    if extra:
        key = sorted(extra)[0]
        raise InputError(f"unsupported option {key!r} for {method}; see list_chart_types")
    if method in {"set_xscale", "set_yscale"}:
        scale = kwargs.get("value")
        if scale is not None and (not isinstance(scale, str) or scale not in {"linear", "log", "symlog", "logit", "function", "functionlog"}):
            raise InputError("axis scale is not supported")
    return kwargs


def _validate_call_resources(method: str, args: list[Any], kwargs: dict[str, Any]) -> None:
    """Check allocation sizes after resolving references and positional options."""
    def check_count(value: Any, limit: int, name: str, *, edges: bool = False) -> None:
        if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
            if value < 1:
                raise InputError(f"{name} must be positive")
            _check_limit(int(value), limit, name)
        elif isinstance(value, (list, np.ndarray)) and np.ndim(value) == 1:
            _check_limit(max(0, len(value) - int(edges)), limit, name)
        else:
            raise InputError(f"{name} requires an integer or explicit values")

    if method in {"hist", "hist2d"}:
        position = 1 if method == "hist" else 2
        bins = kwargs.get("bins", args[position] if len(args) > position else 10)
        if LIMITS.bins:
            if method == "hist2d" and isinstance(bins, (list, np.ndarray)):
                if len(bins) != 2:
                    raise InputError("hist2d bins must specify two axes")
                for value in bins:
                    check_count(value, LIMITS.bins, "histogram bins", edges=True)
            else:
                check_count(bins, LIMITS.bins, "histogram bins", edges=True)
    if method == "hexbin" and LIMITS.bins:
        gridsize = kwargs.get("gridsize", args[3] if len(args) > 3 else 100)
        for value in gridsize if isinstance(gridsize, (list, np.ndarray)) else [gridsize]:
            check_count(value, LIMITS.bins, "hexbin gridsize")
        bins = kwargs.get("bins", args[4] if len(args) > 4 else None)
        if bins is not None and not (isinstance(bins, str) and bins == "log"):
            check_count(bins, LIMITS.bins, "hexbin bins")
    if method == "streamplot" and LIMITS.array_elements:
        density = kwargs.get("density", args[4] if len(args) > 4 else 1)
        densities = density if isinstance(density, (list, np.ndarray)) else [density, density]
        if len(densities) != 2 or not all(_positive_finite_number(value) for value in densities):
            raise InputError("streamplot density must contain finite positive numbers")
        cells = math.prod(max(1, int(30 * value)) for value in densities)
        _check_limit(cells, LIMITS.array_elements, "streamplot density grid")
    if method == "violinplot" and LIMITS.array_elements:
        points = kwargs.get("points", args[8] if len(args) > 8 else 100)
        check_count(points, LIMITS.array_elements, "violin sampling points")
    if method in {"contour", "contourf", "tricontour", "tricontourf"} and LIMITS.contour_levels:
        levels = kwargs.get("levels")
        if levels is None:
            if method in {"contour", "contourf"} and len(args) in {2, 4}:
                levels = args[-1]
            elif method in {"tricontour", "tricontourf"}:
                position = 4 if len(args) > 2 and np.ndim(args[2]) == 2 else 3
                if len(args) > position:
                    levels = args[position]
        if levels is not None:
            check_count(levels, LIMITS.contour_levels, "contour levels")


def _positive_finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _validate_config(config: Any) -> tuple[dict[str, Any], int, int, int]:
    if not isinstance(config, dict) or set(config) - {"figure", "axes", "theme"} or "axes" not in config:
        raise InputError("config must contain axes and optional figure and theme settings")
    if not isinstance(config.get("theme", True), bool):
        raise InputError("config.theme must be boolean")
    _check_limit(_count_leaves(config), LIMITS.array_elements, "configuration element count")
    figure = config.get("figure", {})
    if not isinstance(figure, dict) or set(figure) - {"figsize", "dpi", "facecolor", "suptitle", "tight_layout"}:
        raise InputError("figure supports figsize, dpi, facecolor, suptitle, and tight_layout")
    figsize = figure.get("figsize", [8, 5])
    dpi = figure.get("dpi", 100)
    if not isinstance(figsize, list) or len(figsize) != 2 or not all(_positive_finite_number(x) for x in figsize):
        raise InputError("figure.figsize must be two finite positive numbers")
    if not _positive_finite_number(dpi):
        raise InputError("figure.dpi must be a finite positive number")
    pixels = [size * dpi for size in figsize]
    if not all(_positive_finite_number(size) for size in pixels):
        raise InputError("rendered image dimensions must be finite and positive")
    width, height = map(int, pixels)
    if width <= 0 or height <= 0:
        raise InputError("rendered image dimensions must be positive")
    _check_limit(width, LIMITS.image_width, "rendered image width")
    _check_limit(height, LIMITS.image_height, "rendered image height")
    _check_limit(width * height, LIMITS.image_pixels, "rendered image pixels")
    suptitle = figure.get("suptitle")
    if suptitle is not None and not isinstance(suptitle, str):
        raise InputError("figure.suptitle must be a string")
    _check_limit(len(suptitle or ""), LIMITS.label_chars, "figure title length")
    if not isinstance(figure.get("tight_layout", True), bool):
        raise InputError("figure.tight_layout must be boolean")

    axes = config["axes"]
    if not isinstance(axes, list) or not axes:
        raise InputError("axes must be a non-empty list")
    _check_limit(len(axes), LIMITS.axes, "axes count")
    calls_total = 0
    grid = None
    positions: set[int] = set()
    for axis in axes:
        if not isinstance(axis, dict) or set(axis) - {"subplot", "projection", "calls"}:
            raise InputError("each axes entry supports subplot, projection, and calls")
        subplot = axis.get("subplot", [1, 1, 1])
        if not isinstance(subplot, list) or len(subplot) != 3 or any(type(v) is not int or v < 1 for v in subplot):
            raise InputError("subplot must be [nrows, ncols, index] using positive integers")
        nrows, ncols, index = subplot
        _check_limit(nrows * ncols, LIMITS.axes, "subplot grid cells")
        if index > nrows * ncols:
            raise InputError("subplot index must fit within its grid")
        if grid is None:
            grid = (nrows, ncols)
        elif grid != (nrows, ncols):
            raise InputError("all subplot entries must use the same grid")
        if index in positions:
            raise InputError("subplot indices must be unique")
        positions.add(index)
        projection = axis.get("projection", "rectilinear")
        if not isinstance(projection, str) or projection not in {"rectilinear", "polar"}:
            raise InputError("projection must be rectilinear or polar")
        calls = axis.get("calls")
        if not isinstance(calls, list) or not calls:
            raise InputError("each axes entry requires a non-empty calls list")
        calls_total += len(calls)
        for call in calls:
            if not isinstance(call, dict) or set(call) - {"method", "args", "kwargs", "colorbar"}:
                raise InputError("each call supports method, args, kwargs, and optional colorbar")
            method = call.get("method")
            if not isinstance(method, str) or (method not in PLOT_METHODS and method not in SETUP_METHODS):
                raise InputError("method is not supported; call list_chart_types to see allowed methods")
            args = call.get("args", [])
            if not isinstance(args, list):
                raise InputError("call args must be an array")
            kwargs = _validate_kwargs(method, call.get("kwargs", {}))
            if method in SETUP_METHODS:
                _, maximum, _ = SETUP_METHODS[method]
                if len(args) > maximum:
                    raise InputError(f"{method} accepts at most {maximum} positional arguments")
                try:
                    inspect.signature(getattr(Axes, method)).bind(None, *args, **kwargs)
                except TypeError:
                    raise InputError(f"{method} requires valid positional or keyword arguments; see list_chart_types") from None
            if not isinstance(call.get("colorbar", False), bool):
                raise InputError("colorbar must be boolean")
            if call.get("colorbar", False) and method not in {"scatter", "hist2d", "hexbin", "imshow", "pcolormesh", "contour", "contourf", "tripcolor", "tricontour", "tricontourf"}:
                raise InputError("colorbar is only supported for scalar mappable plot methods")
    _check_limit(calls_total, LIMITS.calls, "plot call count")
    if grid and grid[0] * grid[1] < len(axes):
        raise InputError("subplot grid has fewer cells than axes entries")
    return config, width, height, calls_total


def _render(config: dict[str, Any], data: dict[str, list[Any]]) -> tuple[bytes, int, int]:
    # rc_context changes global state, so keep its entire lifetime under the lock.
    with _RENDER_LOCK, matplotlib.rc_context(THEME.rc if config.get("theme", True) else {}):
        _, width, height, _ = _validate_config(config)
        # Resolve once and charge every use before creating any figures or artists.
        cache: dict[str, np.ndarray] = {}
        prepared = []
        budget = Budget(LIMITS)
        for axis in config["axes"]:
            calls = []
            for call in axis["calls"]:
                name = call["method"]
                args = _resolve(call.get("args", []), data, cache)
                kwargs = _resolve(_validate_kwargs(name, call.get("kwargs", {})), data, cache)
                _validate_call_resources(name, args, kwargs)
                budget.charge(name, args, kwargs)
                calls.append((call, args, kwargs))
            prepared.append(calls)
        figure_cfg = config.get("figure", {})
        figsize = figure_cfg.get("figsize", [8, 5])
        dpi = figure_cfg.get("dpi", 100)
        fig = Figure(figsize=figsize, dpi=dpi, facecolor=figure_cfg.get("facecolor"))
        FigureCanvasAgg(fig)
        try:
            for axes_cfg, calls in zip(config["axes"], prepared):
                nr, nc, index = axes_cfg.get("subplot", [1, 1, 1])
                ax = fig.add_subplot(nr, nc, index, projection=axes_cfg.get("projection"))
                for call, args, kwargs in calls:
                    name = call["method"]
                    if name in PLOT_METHODS:
                        result = getattr(ax, name)(*args, **kwargs)
                        if call.get("colorbar", False):
                            target = result
                            if isinstance(result, tuple):
                                target = next((item for item in result if hasattr(item, "get_array")), None)
                            if target is None or not hasattr(target, "get_array"):
                                raise InputError(f"{name} did not return a colorbar-compatible mappable")
                            fig.colorbar(target, ax=ax)
                    else:
                        getattr(ax, name)(*args, **kwargs)
            if figure_cfg.get("suptitle"):
                fig.suptitle(figure_cfg["suptitle"])
            if figure_cfg.get("tight_layout", True):
                fig.tight_layout()
            output = io.BytesIO()
            fig.savefig(output, format="png", dpi=dpi, facecolor=fig.get_facecolor())
            png = output.getvalue()
            return png, width, height
        finally:
            fig.clear()


def _catalog(method: str | None = None) -> dict[str, Any]:
    if method is not None and (not isinstance(method, str) or method not in PLOT_METHODS):
        raise InputError("unknown plot method; call list_chart_types without a filter")
    methods = [method] if method else list(PLOT_METHODS)
    return {
        "setup": {
            name: {
                "positional_args": f"0..{maximum}",
                "required_arguments": [
                    parameter.name
                    for parameter in inspect.signature(getattr(Axes, name)).parameters.values()
                    if parameter.name != "self"
                    and parameter.default is inspect.Parameter.empty
                    and parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
                ],
                "keyword_options": list(options),
            }
            for name, (minimum, maximum, options) in SETUP_METHODS.items()
        },
        "plot_methods": {
            name: {
                "description": PLOT_METHODS[name].summary,
                "native_signature": PLOT_METHODS[name].args,
                "data_required": _PLOT_DATA_ARGS[name],
                "keyword_options": list(PLOT_METHODS[name].kwargs),
                "example": PLOT_METHODS[name].example,
            }
            for name in methods
        },
        "configuration": {
            "theme": "Optional boolean, default true. Set false to disable the server theme for this chart. Explicit kwargs override theme defaults.",
            "axes": "List of {subplot: [nrows,ncols,index], projection?: 'polar', calls: [{method,args,kwargs,colorbar?}]}",
            "figure": "Optional {figsize: [width,height], dpi, facecolor, suptitle, tight_layout}",
            "data_reference": "Use the exact object {\"$data\": \"column_name\"} inside args or kwargs. Vectors and matrices are supported.",
            "setup_methods": "Each axes also accepts native set_title/set_xlabel/set_ylabel/set_xlim/set_ylim/set_xscale/set_yscale/set_xticks/set_yticks/set_xticklabels/set_yticklabels/grid/legend/axhline/axvline/axhspan/axvspan calls.",
        },
        "data_formats": {
            "columns": {"format": "columns", "values": {"x": [1, 2], "y": [3, 4]}},
            "rows": {"format": "rows", "rows": [{"x": 1, "y": 3}, {"x": 2, "y": 4}]},
            "csv": {"format": "csv", "csv": "x,y\n1,3\n2,4"},
        },
        "figure_defaults": {"figsize": [8, 5], "dpi": 100},
        "theme": THEME.describe(),
        "limits": LIMITS.describe(),
    }


def create_chart(config: dict[str, Any], data: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Render a Matplotlib-style configuration from fresh columns, JSON rows, or CSV."""
    try:
        columns, row_count, data_size = _prepare_data(data)
        _, width, height, call_count = _validate_config(config)
        with _RENDER_LOCK, warnings.catch_warnings():
            # Matplotlib warnings can echo caller-supplied labels and font names.
            warnings.simplefilter("ignore")
            png, width, height = _render(config, columns)
    except (InputError, MemoryError):
        raise
    except RecursionError:
        raise InputError("configuration or data is nested too deeply") from None
    except Exception as exc:
        # Do not include exception messages: Matplotlib may include caller-supplied labels/data.
        raise InputError(f"Matplotlib could not render this configuration ({type(exc).__name__})") from None
    metadata = {"mimeType": "image/png", "width": width, "height": height, "rows": row_count, "dataBytes": data_size, "plotCalls": call_count}
    return png, metadata
