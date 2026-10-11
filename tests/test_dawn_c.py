"""Dawn forge 2026-10-11 — Track C: himinbjorg layout/theme/celestial/input."""

import pytest

from hlidskjalf.himinbjorg.compositor import Compositor, HeadlessCanvas
from hlidskjalf.himinbjorg.input import InputRouter
from hlidskjalf.himinbjorg.layout import GridLayout
from hlidskjalf.himinbjorg.theme import THEMES, Theme, get_theme
from hlidskjalf.himinbjorg.viewports.celestial import CelestialViewport
from hlidskjalf.himinbjorg.viewports.muse_stream import MuseStreamViewport


# C1 — GridLayout construction/placement validation
@pytest.mark.parametrize(
    "kwargs",
    [{"cols": 0}, {"rows": True}, {"cols": 2.0}, {"padding": -1},
     {"padding": float("nan")}, {"padding": "8"}],
)
def test_c1_grid_init_rejects_bad(kwargs):
    with pytest.raises(ValueError):
        GridLayout(**kwargs)


@pytest.mark.parametrize(
    "args", [(0, 0, 0, 1), (0, 0, 1, 0), (0.5, 0, 1, 1), (0, 0, True, 1), (0, 0, -1, 1)],
)
def test_c1_place_rejects_bad_spans_and_coords(args):
    layout = GridLayout(cols=3, rows=3)
    with pytest.raises(ValueError):
        layout.place("v", *args)


def test_c1_valid_placement_unchanged():
    layout = GridLayout(cols=2, rows=2, padding=0)
    layout.place("v", 1, 1)
    tile = layout.layout(100, 100)["v"]
    assert (tile.x, tile.y, tile.w, tile.h) == (50.0, 50.0, 50.0, 50.0)


# C2 — layout() dimension validation
@pytest.mark.parametrize("dims", [(-100, 50), (0, 50), (50, 0), (float("nan"), 50),
                                  (50, float("inf")), (True, 50), ("wide", 50)])
def test_c2_layout_rejects_bad_dims(dims):
    layout = GridLayout()
    layout.place("v", 0, 0)
    with pytest.raises(ValueError):
        layout.layout(*dims)


# C3 — Theme integrity
def _theme_kwargs(**overrides):
    kwargs = dict(
        name="x", background="#000000", panel="#111111", foreground="#eeeeee",
        muted="#888888", accent="#ff9e3d", accent2="#c74b2a", ok="#7fb069",
        warn="#e0a458", crit="#d64545", font_size=14,
    )
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize("overrides", [{"background": "red"}, {"accent": "#fff"},
                                       {"ok": 123}, {"font_size": 0},
                                       {"font_size": -1}, {"font_size": True},
                                       {"name": ""}])
def test_c3_theme_rejects_bad_values(overrides):
    with pytest.raises(ValueError):
        Theme(**_theme_kwargs(**overrides))


def test_c3_builtin_themes_construct_and_lookup():
    assert set(THEMES) == {"ember", "frost"}
    assert get_theme("ember").name == "ember"


@pytest.mark.parametrize("bad", [["ember"], None, 42, {"x": 1}])
def test_c3_get_theme_non_str_raises_keyerror(bad):
    with pytest.raises(KeyError):
        get_theme(bad)


# C4 — Celestial non-finite / bool degrees are skipped
def test_c4_celestial_skips_bad_degrees():
    cel = CelestialViewport()
    cel.update({"planets": {"Mars": float("nan"), "Venus": True,
                            "Earth": float("inf"), "Moon": 42.0, "Sun": "bad"}})
    assert cel.planets == {"Moon": 42.0}


def test_c4_celestial_wraps_valid_degrees():
    cel = CelestialViewport()
    cel.update({"planets": {"Mars": 370.0}})
    assert cel.planets == {"Mars": 10.0}


# C5 — focus_prev from no focus lands on the last focusable
def _router():
    comp = Compositor(HeadlessCanvas())
    comp.register(MuseStreamViewport("one"))
    comp.register(MuseStreamViewport("two"))
    comp.register(MuseStreamViewport("three"))
    return InputRouter(comp)


def test_c5_focus_prev_from_none_goes_last():
    router = _router()
    assert router.focused() is None
    assert router.focus_prev().name == "three"


def test_c5_focus_cycling_still_wraps():
    router = _router()
    assert router.focus_next().name == "one"
    assert router.focus_prev().name == "three"
    assert router.focus_next().name == "one"
