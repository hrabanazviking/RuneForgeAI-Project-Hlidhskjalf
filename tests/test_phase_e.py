"""Phase E — Himinbjörg Omni-HUD tests (slices 28–35).

All rendering is asserted through :class:`HeadlessCanvas` recorded draw
calls; pygame is never required.
"""
from fractions import Fraction

import pytest

from hlidskjalf.himinbjorg.compositor import (
    Canvas, Compositor, HeadlessCanvas, PygameCanvas, Rect, Viewport,
)
from hlidskjalf.himinbjorg.input import InputRouter, KeyEvent, normalise_key
from hlidskjalf.himinbjorg.layout import GridLayout, auto_grid
from hlidskjalf.himinbjorg.theme import DEFAULT_THEME, THEMES, get_theme, list_themes
from hlidskjalf.himinbjorg.viewports.celestial import CelestialViewport
from hlidskjalf.himinbjorg.viewports.dice import (
    DiceViewport, distribution, prob_at_least,
)
from hlidskjalf.himinbjorg.viewports.divination import DivinationViewport
from hlidskjalf.himinbjorg.viewports.muse_stream import MuseStreamViewport
from hlidskjalf.himinbjorg.viewports.vitals import VitalsViewport


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _StubViewport(Viewport):
    name = "stub"

    def __init__(self, name="stub", focusable=False):
        super().__init__(name)
        self.can_focus = focusable
        self.rendered = 0
        self.keys = []

    def render(self, canvas):
        self.rendered += 1
        canvas.text(0, 0, f"stub:{self.name}", "#fff", 12)

    def handle_key(self, key):
        self.keys.append(key)
        return True


def _party():
    return {"party": [
        {"name": "Brynhild", "hp": 32, "max_hp": 45, "status": "ok"},
        {"name": "Skarde", "hp": 5, "max_hp": 40, "status": "poisoned"},
        {"name": "Ulf", "hp": 0, "max_hp": 38, "status": "down"},
    ]}


# ===========================================================================
# Slice 28 — compositor + canvas
# ===========================================================================

class TestHeadlessCanvas:
    def test_records_calls_in_order(self):
        c = HeadlessCanvas(800, 600)
        c.clear("#000000")
        c.rect(1, 2, 3, 4, "#ff0000")
        c.text(5, 6, "hello", "#ffffff", 14)
        assert c.calls[0] == ("clear", "#000000")
        assert c.calls[1] == ("rect", 1, 2, 3, 4, "#ff0000", True)
        assert c.calls[2] == ("text", 5, 6, "hello", "#ffffff", 14)

    def test_helpers(self):
        c = HeadlessCanvas()
        c.text(0, 0, "a", "#fff")
        c.text(0, 0, "b", "#fff")
        c.circle(1, 1, 2, "#fff")
        assert c.texts() == ["a", "b"]
        assert len(c.calls_of("text")) == 2
        assert c.summary() == {"text": 2, "circle": 1}
        c.clear_calls()
        assert c.calls == []

    def test_dimensions(self):
        c = HeadlessCanvas(1920, 1080)
        assert (c.width, c.height) == (1920, 1080)


class TestPygameCanvas:
    def test_falls_back_to_headless_without_pygame(self):
        c = PygameCanvas(320, 200)
        assert isinstance(c, HeadlessCanvas)
        # recorded like headless regardless of backend availability
        c.text(1, 2, "x", "#fff")
        assert c.texts() == ["x"]


class TestRect:
    def test_contains_and_inset(self):
        r = Rect(10, 10, 100, 50)
        assert r.contains(50, 30)
        assert not r.contains(200, 30)
        i = r.inset(5)
        assert (i.x, i.y, i.w, i.h) == (15, 15, 90, 40)


class TestCompositor:
    def test_register_remove(self):
        comp = Compositor(HeadlessCanvas())
        v = _StubViewport()
        comp.register(v)
        assert comp.viewport_names() == ["stub"]
        assert comp.remove("stub") is True
        assert comp.viewport_names() == []
        assert comp.remove("stub") is False

    def test_tick_renders_all_viewports(self):
        canvas = HeadlessCanvas()
        comp = Compositor(canvas)
        a, b = _StubViewport("a"), _StubViewport("b")
        comp.register(a)
        comp.register(b)
        comp.tick(1 / 60)
        assert a.rendered == 1 and b.rendered == 1
        assert "stub:a" in canvas.texts() and "stub:b" in canvas.texts()
        # theme background clear first
        assert canvas.calls[0][0] == "clear"

    def test_tick_rejects_bad_dt(self):
        comp = Compositor(HeadlessCanvas())
        with pytest.raises(ValueError):
            comp.tick(0)
        with pytest.raises(ValueError):
            comp.tick(-1)

    def test_fps_tracks_ticks(self):
        comp = Compositor(HeadlessCanvas())
        for _ in range(60):
            comp.tick(1 / 60)
        assert comp.frames == 60
        assert comp.fps == pytest.approx(60.0)

    def test_run_steps_correct_frame_count(self):
        comp = Compositor(HeadlessCanvas())
        comp.register(_StubViewport())
        steps = comp.run(0.5, fps=60)
        assert steps == 30
        assert comp.frames == 30

    def test_run_rejects_bad_duration(self):
        comp = Compositor(HeadlessCanvas())
        with pytest.raises(ValueError):
            comp.run(0)

    def test_set_theme_live(self):
        canvas = HeadlessCanvas()
        comp = Compositor(canvas, theme_name="ember")
        v = _StubViewport()
        comp.register(v)
        comp.tick(1 / 60)
        assert v.theme.name == "ember"
        comp.set_theme("frost")
        assert comp.theme.name == "frost"
        assert v.theme.name == "frost"
        canvas.clear_calls()
        comp.tick(1 / 60)
        assert canvas.calls[0] == ("clear", "#eef2f6")

    def test_render_failure_does_not_kill_hud(self):
        class _Boom(Viewport):
            name = "boom"

            def render(self, canvas):
                raise RuntimeError("sick viewport")

        comp = Compositor(HeadlessCanvas())
        comp.register(_Boom())
        ok = _StubViewport("ok")
        comp.register(ok)
        comp.tick(1 / 60)  # must not raise
        assert ok.rendered == 1

    def test_push_feeds_viewport(self):
        comp = Compositor(HeadlessCanvas())
        vit = VitalsViewport()
        comp.register(vit)
        assert comp.push("vitals", _party()) is True
        assert len(vit.members) == 3
        assert comp.push("nope", {}) is False

    def test_update_all(self):
        comp = Compositor(HeadlessCanvas())
        vit, cel = VitalsViewport(), CelestialViewport()
        comp.register(vit)
        comp.register(cel)
        comp.update_all({"vitals": _party(),
                         "celestial": {"planets": {"Sun": 10}}})
        assert len(vit.members) == 3
        assert cel.planets == {"Sun": 10.0}

    def test_layout_applied_each_tick(self):
        canvas = HeadlessCanvas(800, 600)
        comp = Compositor(canvas)
        vit = VitalsViewport()
        comp.register(vit)
        layout = GridLayout(cols=2, rows=2, padding=8)
        layout.place("vitals", 0, 0)
        comp.set_layout(layout)
        comp.tick(1 / 60)
        assert vit.rect.x == pytest.approx(8.0)
        assert vit.rect.w == pytest.approx((800 - 8 * 3) / 2)


# ===========================================================================
# Slice 29 — vitals
# ===========================================================================

class TestVitalsViewport:
    def test_renders_party(self):
        canvas = HeadlessCanvas(800, 600)
        vit = VitalsViewport()
        vit.rect = Rect(0, 0, 800, 600)
        vit.update(_party())
        vit.render(canvas)
        texts = canvas.texts()
        assert "PARTY VITALS" in texts
        assert "Brynhild" in texts and "Skarde" in texts and "Ulf" in texts
        assert "32/45" in texts
        # one hp bar background + fill per member -> 6 rect fills minimum
        fills = [c for c in canvas.calls_of("rect") if c[6] is True]
        assert len(fills) >= 6

    def test_hp_fraction_clamped(self):
        vit = VitalsViewport()
        vit.update({"party": [{"name": "X", "hp": 999, "max_hp": 10, "status": "ok"},
                              {"name": "Y", "hp": -5, "max_hp": 10, "status": "ok"},
                              {"name": "Z", "hp": 3, "max_hp": 0, "status": "ok"}]})
        assert vit.members[0].fraction == 1.0
        assert vit.members[1].fraction == 0.0
        assert vit.members[2].fraction == 0.0

    def test_accepts_bare_list(self):
        vit = VitalsViewport()
        vit.update([{"name": "Solo", "hp": 1, "max_hp": 2}])
        assert len(vit.members) == 1
        assert vit.members[0].status == "ok"

    def test_empty_party_message(self):
        canvas = HeadlessCanvas()
        vit = VitalsViewport()
        vit.rect = Rect(0, 0, 400, 300)
        vit.update({"party": []})
        vit.render(canvas)
        assert "no party data" in canvas.texts()


# ===========================================================================
# Slice 30 — celestial wheel
# ===========================================================================

class TestCelestialViewport:
    def test_draws_twelve_houses(self):
        canvas = HeadlessCanvas(600, 600)
        cel = CelestialViewport()
        cel.rect = Rect(0, 0, 600, 600)
        cel.update({"planets": {"Sun": 10, "Moon": 200}})
        cel.render(canvas)
        assert "CELESTIAL WHEEL" in canvas.texts()
        # 12 spokes + title rule = 13 lines
        assert len(canvas.calls_of("line")) == 13
        # 2 wheel rings + 2 planet markers
        circles = canvas.calls_of("circle")
        assert len([c for c in circles if c[5] is False]) == 2
        assert len([c for c in circles if c[5] is True]) == 2
        # planet labels drawn
        assert any("Sun 10" in t for t in canvas.texts())
        assert any("Moon 200" in t for t in canvas.texts())
        # house numbers 1..12
        for n in map(str, range(1, 13)):
            assert n in canvas.texts()

    def test_degrees_normalised_and_bad_values_skipped(self):
        cel = CelestialViewport()
        cel.update({"planets": {"Mars": 370, "Venus": "bad", "Jupiter": -30}})
        assert cel.planets["Mars"] == pytest.approx(10.0)
        assert cel.planets["Jupiter"] == pytest.approx(330.0)
        assert "Venus" not in cel.planets

    def test_no_data_message(self):
        canvas = HeadlessCanvas()
        cel = CelestialViewport()
        cel.rect = Rect(0, 0, 400, 400)
        cel.update({})
        cel.render(canvas)
        assert "no ephemeris data" in canvas.texts()


# ===========================================================================
# Slice 31 — divination
# ===========================================================================

class TestDivinationViewport:
    def _spread(self):
        return {"spread": [
            {"name": "The Fool", "position": "past", "reversed": False},
            {"name": "Death", "position": "present", "reversed": True},
            {"name": "The Star", "position": "future", "reversed": False},
        ]}

    def test_renders_spread(self):
        canvas = HeadlessCanvas(900, 400)
        div = DivinationViewport()
        div.rect = Rect(0, 0, 900, 400)
        div.update(self._spread())
        div.render(canvas)
        texts = canvas.texts()
        assert "DIVINATION" in texts
        assert "The Fool" in texts and "Death" in texts and "The Star" in texts
        assert "past" in texts and "future" in texts
        assert "reversed" in texts  # exactly one reversed card
        # 3 card tiles -> 3 outline rects
        outlines = [c for c in canvas.calls_of("rect") if c[6] is False]
        assert len(outlines) >= 3

    def test_empty_spread(self):
        canvas = HeadlessCanvas()
        div = DivinationViewport()
        div.rect = Rect(0, 0, 400, 300)
        div.update({"spread": []})
        div.render(canvas)
        assert "no spread drawn" in canvas.texts()


# ===========================================================================
# Slice 32 — dice probability
# ===========================================================================

class TestDiceMath:
    def test_1d6_uniform(self):
        d = distribution(1, 6)
        assert d == {i: Fraction(1, 6) for i in range(1, 7)}

    def test_2d6_shape(self):
        d = distribution(2, 6)
        expected = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6,
                    8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
        assert d == {t: Fraction(c, 36) for t, c in expected.items()}

    def test_probabilities_sum_to_one(self):
        for num, sides in [(1, 6), (2, 6), (3, 8), (4, 4)]:
            assert sum(distribution(num, sides).values()) == 1

    def test_2d6_seven_is_six_thirty_sixths(self):
        assert distribution(2, 6)[7] == Fraction(6, 36)

    def test_prob_at_least_2d6_ge_7(self):
        assert prob_at_least(2, 6, 7) == Fraction(21, 36)

    def test_prob_at_least_edges(self):
        assert prob_at_least(1, 6, 1) == 1
        assert prob_at_least(1, 6, 6) == Fraction(1, 6)
        assert prob_at_least(1, 6, 7) == 0

    def test_bad_dice_rejected(self):
        with pytest.raises(ValueError):
            distribution(0, 6)
        with pytest.raises(ValueError):
            distribution(2, 1)
        with pytest.raises(ValueError):
            DiceViewport(num=0)


class TestDiceViewport:
    def test_renders_distribution_and_probability(self):
        canvas = HeadlessCanvas(800, 400)
        dv = DiceViewport(num=2, sides=6, target=7)
        dv.rect = Rect(0, 0, 800, 400)
        dv.render(canvas)
        texts = " ".join(canvas.texts())
        assert "2d6" in texts
        assert "P(>=7)" in texts
        assert "0.5833" in texts  # 21/36
        # 11 bars for 2d6 totals
        bars = [c for c in canvas.calls_of("rect") if c[6] is True and c[4] > 0]
        assert len(bars) >= 11

    def test_update_and_target_clamp(self):
        dv = DiceViewport()
        dv.update({"num": 3, "sides": 8, "target": 999})
        assert (dv.num, dv.sides, dv.target) == (3, 8, 24)
        dv.update({"target": -5})
        assert dv.target == 3

    def test_handle_key_nudges_target(self):
        dv = DiceViewport(num=2, sides=6, target=7)
        assert dv.handle_key("up") is True
        assert dv.target == 8
        assert dv.handle_key("down") is True
        assert dv.target == 7
        # clamped at bounds
        dv.target = 12
        assert dv.handle_key("up") is True and dv.target == 12
        dv.target = 2
        assert dv.handle_key("down") is True and dv.target == 2
        assert dv.handle_key("x") is False

    def test_can_focus(self):
        assert DiceViewport.can_focus is True


# ===========================================================================
# Slice 33 — muse stream
# ===========================================================================

class TestMuseStreamViewport:
    def test_append_and_render_latest(self):
        canvas = HeadlessCanvas(800, 300)
        ms = MuseStreamViewport()
        ms.rect = Rect(0, 0, 800, 300)
        ms.append("first thought\nsecond thought")
        ms.append("third")
        assert ms.line_count == 3
        ms.render(canvas)
        texts = canvas.texts()
        assert "MUSE STREAM" in texts
        assert "third" in texts

    def test_scrollback_capped_at_200(self):
        ms = MuseStreamViewport()
        for i in range(250):
            ms.append(f"line {i}")
        assert ms.line_count == 200
        assert ms.lines[0] == "line 50"
        assert ms.lines[-1] == "line 249"

    def test_update_accepts_state(self):
        ms = MuseStreamViewport()
        ms.update({"stream": ["a", "b"]})
        assert ms.lines == ["a", "b"]
        ms.update({"stream": "single\nmulti"})
        assert ms.lines[-2:] == ["single", "multi"]

    def test_render_shows_tail_not_head_when_overflowing(self):
        canvas = HeadlessCanvas(800, 100)
        ms = MuseStreamViewport()
        ms.rect = Rect(0, 0, 800, 100)
        for i in range(50):
            ms.append(f"line {i}")
        ms.render(canvas)
        texts = canvas.texts()
        assert any("line 49" in t for t in texts)
        assert not any(t == "line 0" for t in texts)

    def test_scroll_keys(self):
        ms = MuseStreamViewport()
        for i in range(20):
            ms.append(f"line {i}")
        assert ms.handle_key("up") is True
        assert ms._scroll == 1
        assert ms.handle_key("down") is True
        assert ms._scroll == 0
        assert ms.handle_key("home") is True
        assert ms.handle_key("nope") is False

    def test_can_focus(self):
        assert MuseStreamViewport.can_focus is True


# ===========================================================================
# Slice 34 — themes & layout
# ===========================================================================

class TestThemes:
    def test_two_themes_exist(self):
        assert set(list_themes()) == {"ember", "frost"}
        assert DEFAULT_THEME == "ember"

    def test_ember_is_dark_frost_is_light(self):
        ember, frost = get_theme("ember"), get_theme("frost")

        def luminance(hexcolor):
            h = hexcolor.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            return 0.299 * r + 0.587 * g + 0.114 * b

        assert luminance(ember.background) < luminance(frost.background)

    def test_unknown_theme_raises(self):
        with pytest.raises(KeyError):
            get_theme("void")

    def test_theme_color_lookup(self):
        assert get_theme("ember").color("accent") == "#ff9e3d"
        assert get_theme("ember").color("nope", "#123456") == "#123456"

    def test_theme_immutable(self):
        with pytest.raises(Exception):
            get_theme("ember").background = "#000000"  # type: ignore


class TestGridLayout:
    def test_tiles_fill_canvas(self):
        layout = GridLayout(cols=2, rows=2, padding=0)
        layout.place("a", 0, 0)
        layout.place("b", 1, 1)
        tiles = layout.layout(800, 600)
        assert tiles["a"].as_tuple() == (0, 0, 400, 300)
        assert tiles["b"].as_tuple() == (400, 300, 400, 300)

    def test_padding_and_spans(self):
        layout = GridLayout(cols=2, rows=2, padding=10)
        layout.place("wide", 0, 0, col_span=2)
        tiles = layout.layout(820, 620)
        wide = tiles["wide"]
        assert wide.x == pytest.approx(10.0)
        assert wide.w == pytest.approx(800.0)  # spans both columns + inner pad
        assert wide.h == pytest.approx((620 - 30) / 2)

    def test_out_of_bounds_rejected(self):
        layout = GridLayout(cols=2, rows=2)
        with pytest.raises(ValueError):
            layout.place("x", 2, 0)
        with pytest.raises(ValueError):
            layout.place("x", 0, 0, col_span=3)

    def test_remove_and_clear(self):
        layout = GridLayout()
        layout.place("a", 0, 0)
        assert layout.remove("a") is True
        assert layout.remove("a") is False
        layout.place("b", 0, 0)
        layout.clear()
        assert layout.layout(100, 100) == {}

    def test_auto_grid(self):
        layout = auto_grid(["a", "b", "c"], cols=2)
        assert layout.rows == 2
        tiles = layout.layout(400, 400)
        assert set(tiles) == {"a", "b", "c"}


# ===========================================================================
# Slice 35 — input router
# ===========================================================================

class TestInputRouter:
    def _router(self):
        comp = Compositor(HeadlessCanvas())
        comp.register(_StubViewport("plain"))  # not focusable
        comp.register(_StubViewport("diceish", focusable=True))
        comp.register(_StubViewport("streamish", focusable=True))
        return comp, InputRouter(comp)

    def test_focus_next_cycles_focusables(self):
        comp, router = self._router()
        assert router.focused() is None
        assert router.focus_next().name == "diceish"
        assert router.focus_next().name == "streamish"
        assert router.focus_next().name == "diceish"  # wraps
        assert router.focus_prev().name == "streamish"

    def test_focus_by_name(self):
        comp, router = self._router()
        assert router.focus("streamish") is True
        assert router.focused().name == "streamish"
        assert router.focus("plain") is False  # not focusable
        assert router.focus("missing") is False

    def test_handle_key_routes_to_focused(self):
        comp, router = self._router()
        router.focus("diceish")
        assert router.handle_key("up") is True
        assert comp.viewports["diceish"].keys == ["up"]
        # key forms
        assert router.handle_key({"key": "Down"}) is True
        assert router.handle_key(KeyEvent("LEFT")) is True
        assert comp.viewports["diceish"].keys == ["up", "down", "left"]

    def test_handle_key_without_focus_fails(self):
        comp, router = self._router()
        assert router.handle_key("up") is False

    def test_tab_cycles_focus(self):
        comp, router = self._router()
        assert router.handle_key("tab") is True
        assert router.focused().name == "diceish"
        assert router.handle_key("tab") is True
        assert router.focused().name == "streamish"

    def test_refresh_picks_up_new_viewports(self):
        comp, router = self._router()
        comp.register(_StubViewport("late", focusable=True))
        assert "late" in router.focusables()

    def test_normalise_key(self):
        assert normalise_key("UP") == "up"
        assert normalise_key({"key": "Enter"}) == "enter"
        assert normalise_key(KeyEvent("A")) == "a"

    def test_real_viewports_integrated(self):
        comp = Compositor(HeadlessCanvas())
        dv = DiceViewport(target=7)
        ms = MuseStreamViewport()
        comp.register(dv)
        comp.register(ms)
        router = InputRouter(comp)
        router.focus("dice")
        assert router.handle_key("up") is True
        assert dv.target == 8
        router.focus("muse_stream")
        ms.append("hello")
        assert router.handle_key("up") is True
        assert ms._scroll == 1
