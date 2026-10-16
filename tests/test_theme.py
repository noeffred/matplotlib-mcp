"""Corporate theme loading, rendering defaults, and request isolation."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import matplotlib
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.colors import to_rgba
from PIL import Image
import pytest
import yaml

from matplotlib_mcp import server, theme
from test_server import DATA, simple_config


def write_theme(tmp_path, document):
    path = tmp_path / "theme.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def corporate_theme(tmp_path):
    return theme.load_theme(write_theme(tmp_path, {
        "colors": {"palette": ["#003B71", "#00A6A6"], "figure_background": "#123456",
                   "axes_background": "#eeeeee", "text": "#243746", "axes": "#64748B", "grid": "#E2E8F0"},
        "font": {"family": "DejaVu Sans", "size": 13, "weight": "bold", "style": "italic"},
    }))


def test_optional_default_and_explicit_missing_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_THEME_PATH", raising=False)
    monkeypatch.setattr(theme, "DEFAULT_THEME_PATH", tmp_path / "missing.yaml")
    assert not theme.load_theme().configured
    with pytest.raises(RuntimeError, match="does not exist"):
        theme.load_theme(tmp_path / "missing.yaml")
    monkeypatch.setenv("MCP_THEME_PATH", str(tmp_path / "missing.yaml"))
    with pytest.raises(RuntimeError, match="does not exist"):
        theme.load_theme()
    monkeypatch.setenv("MCP_THEME_PATH", "")
    with pytest.raises(RuntimeError, match="must not be empty"):
        theme.load_theme()


@pytest.mark.parametrize("document", [
    None, [], {"rcParams": {}}, {"colors": []}, {"colors": {"unknown": "red"}},
    {"colors": {"palette": []}}, {"colors": {"palette": ["invalid"]}},
    {"colors": {"palette": "red"}}, {"colors": {"text": "invalid"}},
    {"font": {"unknown": 1}}, {"font": {"size": 0}}, {"font": {"size": True}},
    {"font": {"size": float("inf")}}, {"font": {"weight": "invalid"}},
    {"font": {"size": 10 ** 400}},
    {"font": {"weight": True}}, {"font": {"weight": -1}},
    {"font": {"style": "invalid"}}, {"font": {"family": ""}},
    {"font": {"family": "Definitely Missing Corporate Font 123456"}},
    {"font": {"files": "font.ttf"}}, {"font": {"files": ["missing.ttf"]}},
    {"font": {"files": ["font.txt"]}},
])
def test_invalid_theme_fails_startup(tmp_path, document):
    with pytest.raises(RuntimeError, match="Invalid MCP theme configuration"):
        theme.load_theme(write_theme(tmp_path, document))


def test_malformed_yaml_and_invalid_font(tmp_path):
    path = tmp_path / "theme.yaml"
    path.write_text("colors: [", encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed YAML"):
        theme.load_theme(path)
    (tmp_path / "bad.ttf").write_bytes(b"not a font")
    with pytest.raises(RuntimeError, match="font file"):
        theme.load_theme(write_theme(tmp_path, {"font": {"files": ["bad.ttf"]}}))


def test_relative_font_files_are_registered(tmp_path, monkeypatch):
    font = Path(font_manager.findfont("DejaVu Sans"))
    target = tmp_path / "fonts" / font.name
    target.parent.mkdir()
    target.write_bytes(font.read_bytes())
    registered = []
    original = font_manager.fontManager.addfont

    def addfont(path):
        registered.append(path)
        original(path)

    monkeypatch.setattr(font_manager.fontManager, "addfont", addfont)
    loaded = theme.load_theme(write_theme(tmp_path, {
        "font": {"family": "DejaVu Sans", "files": [f"fonts/{font.name}"]},
    }))
    assert registered == [target]
    assert "files" not in loaded.describe()["settings"]["font"]
    assert str(tmp_path) not in json.dumps(loaded.describe())


def test_partial_theme_does_not_change_rcparams(tmp_path):
    before = matplotlib.rcParams.copy()
    loaded = theme.load_theme(write_theme(tmp_path, {"colors": {"palette": ["red"]}}))
    assert set(loaded.rc) == {"axes.prop_cycle"}
    assert matplotlib.rcParams == before


def test_render_applies_defaults_and_explicit_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "THEME", corporate_theme(tmp_path))
    captured = []
    original = Figure.savefig

    def savefig(fig, *args, **kwargs):
        ax = fig.axes[0]
        captured.append({
            "colors": [line.get_color() for line in ax.lines],
            "face": ax.get_facecolor(), "text": ax.title.get_color(),
            "family": ax.title.get_fontfamily(), "weight": ax.title.get_fontweight(),
            "style": ax.title.get_fontstyle(), "size": ax.xaxis.label.get_fontsize(),
            "legend": ax.get_legend().get_texts()[0].get_color(),
            "colorbar_text": fig.axes[1].yaxis.get_ticklabels()[0].get_color(),
        })
        return original(fig, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", savefig)
    config = simple_config()
    calls = config["axes"][0]["calls"]
    calls.extend([
        {"method": "plot", "args": [[1, 2], [2, 3]]},
        {"method": "plot", "args": [[1, 2], [3, 4]], "kwargs": {"color": "red", "label": "Override"}},
        {"method": "set_title", "args": ["Title"]},
        {"method": "legend"},
        {"method": "imshow", "args": [[[1, 2], [3, 4]]], "colorbar": True},
    ])
    before = matplotlib.rcParams.copy()
    png, _, _ = server._render(config, server._prepare_data(DATA)[0])
    assert matplotlib.rcParams == before
    assert captured[0] == {
        "colors": ["#003B71", "#00A6A6", "red"], "face": to_rgba("#eeeeee"),
        "text": "#243746", "family": ["DejaVu Sans"], "weight": "bold",
        "style": "italic", "size": 13, "legend": "#243746", "colorbar_text": "#243746",
    }
    assert Image.open(io.BytesIO(png)).getpixel((0, 0)) == (18, 52, 86, 255)
    config["figure"]["facecolor"] = "red"
    config["axes"][0]["calls"].append({"method": "set_title", "args": ["Title"], "kwargs": {"color": "blue"}})
    png, _, _ = server._render(config, server._prepare_data(DATA)[0])
    assert captured[-1]["text"] == "blue"
    assert Image.open(io.BytesIO(png)).getpixel((0, 0)) == (255, 0, 0, 255)


def test_opt_out_concurrency_and_restoration_after_failure(tmp_path, monkeypatch):
    config = simple_config()
    baseline = server._render(config, server._prepare_data(DATA)[0])[0]
    monkeypatch.setattr(server, "THEME", corporate_theme(tmp_path))
    before = matplotlib.rcParams.copy()
    with pytest.raises(server.InputError):
        server._render(config, {})
    assert matplotlib.rcParams == before
    with pytest.raises(server.InputError):
        server.create_chart(simple_config(kwargs={"color": "invalid-color"}), DATA)
    assert matplotlib.rcParams == before
    opted_out = {**config, "theme": False}
    configs = [config, opted_out] * 3
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = list(pool.map(lambda cfg: server.create_chart(cfg, DATA)[0].data, configs))
    assert all(png == baseline for png in images[1::2])
    assert all(png != baseline for png in images[::2])
    assert matplotlib.rcParams == before
    assert matplotlib.rcParams["text.usetex"] is False
    assert matplotlib.rcParams["text.parse_math"] is False


def test_no_theme_flag_preserves_original_rendering(monkeypatch):
    monkeypatch.setattr(server, "THEME", theme.Theme())
    config = simple_config()
    assert server.create_chart(config, DATA)[0].data == server.create_chart({**config, "theme": False}, DATA)[0].data


@pytest.mark.parametrize("value", [None, "true", 1, {}, []])
def test_theme_flag_requires_boolean(value):
    with pytest.raises(server.InputError, match="theme must be boolean"):
        server._validate_config({**simple_config(), "theme": value})


def test_catalog_exposes_theme_and_color_options(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "THEME", corporate_theme(tmp_path))
    catalog = server._catalog()
    assert catalog["theme"]["configured"]
    assert catalog["theme"]["settings"]["colors"]["palette"] == ["#003B71", "#00A6A6"]
    for method in ("ecdf", "errorbar", "quiver"):
        assert "color" in catalog["plot_methods"][method]["keyword_options"]
    for method in ("set_title", "set_xlabel", "set_ylabel", "axhline", "axvline", "axhspan", "axvspan"):
        assert "color" in catalog["setup"][method]["keyword_options"]
    assert str(tmp_path) not in json.dumps(catalog)


@pytest.mark.parametrize("method,args", [
    ("ecdf", [[1, 2, 3]]), ("errorbar", [[1, 2], [2, 3]]),
    ("quiver", [[0, 1], [0, 1], [1, 1], [1, 1]]),
])
def test_added_plot_colors_render(method, args):
    config = simple_config(method, args, {"color": "#003B71"})
    for setup, values in [("set_title", ["Title"]), ("set_xlabel", ["X"]), ("set_ylabel", ["Y"]),
                          ("axhline", [1]), ("axvline", [1]), ("axhspan", [0, 1]), ("axvspan", [0, 1])]:
        config["axes"][0]["calls"].append({"method": setup, "args": values, "kwargs": {"color": "red"}})
    assert server.create_chart(config, DATA)[0].data.startswith(b"\x89PNG")
