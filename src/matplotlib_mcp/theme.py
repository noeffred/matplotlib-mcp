"""Load administrator-provided corporate styling and fonts at startup."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib
from matplotlib import font_manager
from matplotlib.colors import is_color_like
import yaml


DEFAULT_THEME_PATH = Path("/etc/matplotlib-mcp/theme.yaml")
_COLOR_PARAMS = {
    "figure_background": ("figure.facecolor", "savefig.facecolor"),
    "axes_background": ("axes.facecolor",),
    "text": ("text.color", "axes.labelcolor", "xtick.color", "ytick.color"),
    "axes": ("axes.edgecolor",),
    "grid": ("grid.color",),
}


@dataclass(frozen=True)
class Theme:
    rc: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    configured: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "applied_by_default": True,
            "opt_out": "Set config.theme=false to use standard styling.",
            "settings": self.settings,
        }


def _mapping(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        raise ValueError(f"{label} must be a mapping using only: {', '.join(sorted(allowed))}")
    return value


def load_theme(path: str | Path | None = None) -> Theme:
    """Read the optional default file, or require an explicitly selected file."""
    explicit = path is not None or "MCP_THEME_PATH" in os.environ
    selected = path if path is not None else os.environ.get("MCP_THEME_PATH", DEFAULT_THEME_PATH)
    try:
        if not str(selected).strip():
            raise ValueError("MCP_THEME_PATH must not be empty")
        source = Path(selected)
        try:
            contents = source.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not explicit:
                return Theme()
            raise ValueError("theme file does not exist") from None
        document = _mapping(yaml.safe_load(contents), {"colors", "font"}, "theme")
        colors = _mapping(document.get("colors", {}), {*_COLOR_PARAMS, "palette"}, "colors")
        font = _mapping(document.get("font", {}), {"family", "size", "weight", "style", "files"}, "font")
        rc: dict[str, Any] = {}
        for name, value in colors.items():
            if name == "palette":
                if not isinstance(value, list) or not value:
                    raise ValueError("colors.palette must be a non-empty list of colors")
                if not all(isinstance(item, str) and is_color_like(item) for item in value):
                    raise ValueError("colors.palette contains an invalid color")
                rc["axes.prop_cycle"] = matplotlib.cycler(color=value)
            else:
                if not isinstance(value, str) or not is_color_like(value):
                    raise ValueError(f"colors.{name} must be a valid color string")
                for parameter in _COLOR_PARAMS[name]:
                    rc[parameter] = value
        family = font.get("family")
        if "family" in font:
            if not isinstance(family, str) or not family.strip():
                raise ValueError("font.family must be a non-empty font family name")
            rc["font.family"] = [family]
        if "size" in font:
            size = font["size"]
            if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
                raise ValueError("font.size must be a finite positive number")
            rc["font.size"] = size
        if "weight" in font:
            weight = font["weight"]
            if (isinstance(weight, bool) or not isinstance(weight, (str, int, float))
                    or (isinstance(weight, str) and weight not in font_manager.weight_dict)
                    or (isinstance(weight, (int, float)) and not 0 <= weight <= 1000)):
                raise ValueError("font.weight must be a standard weight name or a number from 0 to 1000")
            for parameter in ("font.weight", "axes.titleweight", "axes.labelweight", "figure.titleweight"):
                rc[parameter] = weight
        if "style" in font:
            if not isinstance(font["style"], str) or font["style"] not in {"normal", "italic", "oblique"}:
                raise ValueError("font.style must be normal, italic, or oblique")
            rc["font.style"] = font["style"]
        files = font.get("files", [])
        if not isinstance(files, list) or any(not isinstance(item, str) or not item for item in files):
            raise ValueError("font.files must be a list of font file paths")
        for item in files:
            font_path = source.parent / item
            if font_path.suffix.lower() not in {".ttf", ".otf"}:
                raise ValueError("font.files supports only TTF and OTF fonts")
            try:
                font_manager.fontManager.addfont(font_path)
            except (OSError, RuntimeError, ValueError):
                raise ValueError("a configured font file is unreadable or invalid") from None
        if family is not None:
            try:
                font_manager.findfont(font_manager.FontProperties(family=[family]), fallback_to_default=False)
            except ValueError:
                raise ValueError("font.family cannot be resolved to an installed or configured font") from None
        # Run Matplotlib's own validators without changing process-wide defaults.
        validated = matplotlib.RcParams(rc)
        settings = {"colors": dict(colors), "font": {key: value for key, value in font.items() if key != "files"}}
        return Theme(dict(validated), settings, True)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError, OverflowError) as exc:
        # YAML parser errors may contain complete source lines, including paths.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, yaml.YAMLError) else "file is unreadable or malformed YAML"
        raise RuntimeError(f"Invalid MCP theme configuration: {message}") from None
