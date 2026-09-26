#!/usr/bin/env python3
"""Renders the README screenshots in docs/images/ from the synthetic pool in this folder.

Run it with a Python that has PyObjC (the menu bar app's venv):

    ~/.codexpool/.venv/bin/python docs/images/demo/render.py [--app menubar/codexpool_menubar.py]

The popovers come straight from the app's own `--snapshot` mode, and the Settings window and Setup assistant
from codexpool_settings.py's (with doctor.json and lanes.json from this folder). The menu bar strip and the hero
image reuse the app's drawing code (imported, never modified) and add only the backdrop around it.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True   # importing the app must not leave a __pycache__ beside it

HERE = Path(__file__).resolve().parent
IMAGES = HERE.parent
REPO = IMAGES.parent.parent
NOW = '2026-09-24T16:41:00Z'
CLOCK = 'Thu Sep 24  9:41 AM'

sys.path.insert(0, str(HERE))
import make_data  # noqa: E402

HISTORY = {'used': 'regular'}   # scenarios that borrow another one's history.jsonl


def rel(path: Path):
    """The path relative to the repository when it is inside it (--out may point elsewhere)."""
    try:
        return path.resolve().relative_to(REPO)
    except ValueError:
        return path


def history_path(scenario: str) -> Path:
    return HERE / f'history-{HISTORY.get(scenario, scenario)}.jsonl'


def load_app(path: Path):
    spec = importlib.util.spec_from_file_location('codexpool_menubar', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def snapshot(app_path: Path, out: Path, appearance: str, scenario: str):
    """The app's own --snapshot of the popover; its extra -menubar.png is dropped."""
    with tempfile.TemporaryDirectory() as tmp:
        shot = Path(tmp) / 'shot.png'
        subprocess.run([sys.executable, str(app_path), '--snapshot', str(shot), '--appearance', appearance,
                        '--status', str(HERE / f'status-{scenario}.json'),
                        '--history', str(history_path(scenario)), '--now', NOW],
                       check=True, stdout=subprocess.DEVNULL, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        shutil.copyfile(shot, out)
    print(rel(out))


SETTINGS_SHOTS = (   # (file name, pane or Setup assistant step, appearance), all from the regular scenario
    ('settings-overview-light', 'overview', 'light'),
    ('settings-overview-dark', 'overview', 'dark'),
    ('settings-seats-light', 'seats', 'light'),
    ('setup-accounts-light', 'setup-accounts', 'light'),
    ('setup-signin-light', 'setup-signin', 'light'),
)


def settings_snapshot(settings_path: Path, out: Path, pane: str, appearance: str):
    """codexpool_settings.py's own --snapshot of one pane (it runs no command)."""
    subprocess.run([sys.executable, str(settings_path), '--snapshot', str(out), '--pane', pane,
                    '--appearance', appearance, '--status', str(HERE / 'status-regular.json'),
                    '--doctor', str(HERE / 'doctor.json'), '--lanes', str(HERE / 'lanes.json'),
                    '--history', str(history_path('regular')), '--now', NOW],
                   check=True, stdout=subprocess.DEVNULL, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    print(rel(out))


# ── backdrop helpers (AppKit, flipped coordinates) ──────────────────────────────────────────────────────

class Kit:
    def __init__(self, app):
        self.app = app
        import AppKit
        for name in ('NSAffineTransform', 'NSAppearance', 'NSAppearanceNameAqua', 'NSAppearanceNameDarkAqua',
                     'NSBezierPath', 'NSColor', 'NSCompositingOperationSourceOver', 'NSFontWeightMedium',
                     'NSFontWeightRegular', 'NSFontWeightSemibold', 'NSGradient', 'NSGraphicsContext', 'NSShadow'):
            setattr(self, name, getattr(AppKit, name))
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)

    def appearance(self, dark: bool):
        return self.NSAppearance.appearanceNamed_(self.NSAppearanceNameDarkAqua if dark else self.NSAppearanceNameAqua)

    def model(self, scenario: str):
        a = self.app
        hist = history_path(scenario)
        src = a.DataSource(HERE / f'status-{scenario}.json', hist if hist.exists() else None)
        src.poll(force=True)
        return src.model(a.parse_time(NOW))

    def rgb(self, hex_rgb: int, alpha: float = 1.0):
        return self.app.srgb(hex_rgb, alpha)

    def linear(self, rect, top, bottom, angle=90):
        self.NSGradient.alloc().initWithStartingColor_endingColor_(top, bottom).drawInRect_angle_(rect, angle)

    def glow(self, cx, cy, r, color):
        g = self.NSGradient.alloc().initWithStartingColor_endingColor_(color, color.colorWithAlphaComponent_(0.0))
        g.drawFromCenter_radius_toCenter_radius_options_((cx, cy), 0, (cx, cy), r, 0)

    def item(self, x: float, h: float, m, scale: float = 1.0, y: float = 0.0):
        """The app's menu bar item (meter + headline) with its left edge at x, centred in a bar h tall."""
        a = self.app
        image, title = a.menubar_image(m), a.menubar_title(m)
        ctx = self.NSGraphicsContext
        ctx.saveGraphicsState()
        t = self.NSAffineTransform.transform()
        t.translateXBy_yBy_(x, y)
        t.scaleBy_(scale)
        t.concat()
        image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            ((0, (h - a.METER_H) / 2), (a.METER_W, a.METER_H)), ((0, 0), (0, 0)),
            self.NSCompositingOperationSourceOver, 1.0, True, None)
        title.drawAtPoint_((a.METER_W + 1, (h - title.size().height) / 2))
        ctx.restoreGraphicsState()
        return a.menubar_length() * scale

    def item_content_width(self, m) -> float:
        """How wide the item's visible content is (the item itself is fixed-width, sized for '100%')."""
        a = self.app
        return a.METER_W + 1 + math.ceil(a.menubar_title(m).size().width)

    def render(self, fn, w: float, h: float, appearance, scale: float):
        """Like the app's render_offscreen, at any pixel scale."""
        a = self.app
        view = a.DrawingView.alloc().initWithFrame_(((0, 0), (w, h)))
        view.fn = fn
        view.setAppearance_(appearance)
        rep = a.new_bitmap(w, h, scale)
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
        return rep

    def system_items(self, x: float, h: float, color, clock: bool = True) -> float:
        """Generic system status items to the right of ours: Wi-Fi, battery, Control Centre, the clock."""
        a = self.app
        for name, w, size in (('wifi', 26, 13.5), ('battery.100percent', 34, 15), ('switch.2', 26, 12.5)):
            a.draw_symbol(name, x + w / 2, h / 2, size, color, self.NSFontWeightRegular, (w - 6, h - 8))
            x += w + 4
        if clock:
            f = a.font(13)
            s = a.attributed(CLOCK, f, color)
            s.drawAtPoint_((x + 4, (h - s.size().height) / 2))
            x += 4 + math.ceil(s.size().width)
        return x

    def menubar_width(self) -> float:
        a = self.app
        return a.menubar_length() + 10 + (26 + 34 + 26 + 12) + 4 + math.ceil(
            a.attributed(CLOCK, a.font(13), a.C.label()).size().width)

    def popover_pixels(self, m, appearance):
        a = self.app
        content = a.PopoverContent.alloc().initWithFrame_(((0, 0), (a.WIDTH, 400)))
        content.setAppearance_(appearance)
        content.show(lambda: a.PopoverLayout(m, '24h').build(), None)
        h = content.height()
        rep = a.new_bitmap(a.WIDTH, h)
        content.cacheDisplayInRect_toBitmapImageRep_(content.bounds(), rep)
        return rep, h

    def popover_path(self, x, y, w, h, r, ax, aw=24.0, ah=10.0):
        """A popover outline: rounded rect with an arrow on top whose tip is at (ax, y - ah)."""
        p = self.NSBezierPath.bezierPath()
        p.moveToPoint_((x + r, y))
        p.lineToPoint_((ax - aw / 2, y))
        p.curveToPoint_controlPoint1_controlPoint2_((ax - 2.2, y - ah + 1.2), (ax - aw / 2 + 5, y), (ax - 5, y - ah + 3.5))
        p.curveToPoint_controlPoint1_controlPoint2_((ax + 2.2, y - ah + 1.2), (ax - 0.9, y - ah - 0.2), (ax + 0.9, y - ah - 0.2))
        p.curveToPoint_controlPoint1_controlPoint2_((ax + aw / 2, y), (ax + 5, y - ah + 3.5), (ax + aw / 2 - 5, y))
        p.lineToPoint_((x + w - r, y))
        p.appendBezierPathWithArcFromPoint_toPoint_radius_((x + w, y), (x + w, y + r), r)
        p.lineToPoint_((x + w, y + h - r))
        p.appendBezierPathWithArcFromPoint_toPoint_radius_((x + w, y + h), (x + w - r, y + h), r)
        p.lineToPoint_((x + r, y + h))
        p.appendBezierPathWithArcFromPoint_toPoint_radius_((x, y + h), (x, y + h - r), r)
        p.lineToPoint_((x, y + r))
        p.appendBezierPathWithArcFromPoint_toPoint_radius_((x, y), (x + r, y), r)
        p.closePath()
        return p

    def draw_popover(self, pixels, x, y, w, h, ax, dark: bool):
        a, ctx = self.app, self.NSGraphicsContext
        r = 12.0
        path = self.popover_path(x, y, w, h, r, ax)
        ctx.saveGraphicsState()
        sh = self.NSShadow.alloc().init()
        sh.setShadowBlurRadius_(34)
        sh.setShadowOffset_((0, -14))
        sh.setShadowColor_(self.NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, 0.55 if dark else 0.25))
        sh.set()
        (a.rgb(0.165, 0.165, 0.18) if dark else a.rgb(0.965, 0.965, 0.972)).setFill()
        path.fill()
        ctx.restoreGraphicsState()
        ctx.saveGraphicsState()
        a.rounded(((x, y), (w, h)), r).addClip()
        pixels.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            ((x, y), (w, h)), ((0, 0), (0, 0)), self.NSCompositingOperationSourceOver, 1.0, True, None)
        ctx.restoreGraphicsState()
        path.setLineWidth_(1.0)
        (a.rgb(1, 1, 1, 0.13) if dark else a.rgb(0, 0, 0, 0.12)).setStroke()
        path.stroke()


# ── menu bar strip: three states × light and dark ───────────────────────────────────────────────────────

STATES = (('regular', 'A regular seat is serving'), ('reserve', 'The reserve seat is serving'),
          ('down', 'Pool down or not reporting'))


def menubar_strip(k: Kit, out: Path):
    a = k.app
    models = {s: k.model(s) for s, _ in STATES}
    bar_h, pad_x = 26.0, 12.0
    cell_w = math.ceil(pad_x + k.menubar_width() + pad_x)
    margin, cap_h, cap_gap, row_gap = 26.0, 14.0, 7.0, 16.0
    half = cell_w + 2 * margin
    W = 2 * half
    H = margin + 3 * (cap_h + cap_gap + bar_h) + 2 * row_gap + margin

    def compose(dark: bool):
        def draw():
            if dark:
                k.linear(((0, 0), (half, H)), k.rgb(0x1E2130), k.rgb(0x12141C))
            else:
                k.linear(((0, 0), (half, H)), k.rgb(0xE9EDF5), k.rgb(0xDCE2EE))
            y = margin
            for scenario, caption in STATES:
                cf = a.font(11, k.NSFontWeightMedium)
                a.draw_text(caption, margin + 2, y, cf, a.C.secondary())
                y += cap_h + cap_gap
                cell = ((margin, y), (cell_w, bar_h))
                # a slice of wallpaper under a translucent menu bar
                k.NSGraphicsContext.saveGraphicsState()
                a.rounded(cell, 8).addClip()
                if dark:
                    k.linear(cell, k.rgb(0x2B3150), k.rgb(0x3A2D4F), 0)
                    a.fill_rect(cell, k.rgb(0x000000, 0.38))
                else:
                    k.linear(cell, k.rgb(0xBFD3F2), k.rgb(0xE8CFE6), 0)
                    a.fill_rect(cell, k.rgb(0xFFFFFF, 0.55))
                k.NSGraphicsContext.restoreGraphicsState()
                a.stroke_rounded(cell, 8, k.rgb(0xFFFFFF, 0.08) if dark else k.rgb(0x000000, 0.07), 1.0)
                k.NSGraphicsContext.saveGraphicsState()
                t = k.NSAffineTransform.transform()
                t.translateXBy_yBy_(margin + pad_x, y)
                t.concat()
                w = k.item(0, bar_h, models[scenario])
                k.system_items(w + 10, bar_h, a.C.label())
                k.NSGraphicsContext.restoreGraphicsState()
                y += bar_h + row_gap
        return draw

    light = a.render_offscreen(compose(False), half, H, k.appearance(False))
    dark = a.render_offscreen(compose(True), half, H, k.appearance(True))

    def join():
        light.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            ((0, 0), (half, H)), ((0, 0), (0, 0)), k.NSCompositingOperationSourceOver, 1.0, True, None)
        dark.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            ((half, 0), (half, H)), ((0, 0), (0, 0)), k.NSCompositingOperationSourceOver, 1.0, True, None)
    a.write_png(a.render_offscreen(join, W, H, k.appearance(False)), str(out))
    print(rel(out))


# ── hero: the menu bar with the popover open under the item, and the item magnified beside it ─────────

def hero(k: Kit, out: Path):
    a = k.app
    m = k.model('regular')
    states = [k.model(sc) for sc, _ in STATES]
    ap = k.appearance(True)
    pixels, pop_h = k.popover_pixels(m, ap)
    bar_h, W = 26.0, 1240.0
    item_w = a.menubar_length()
    item_x = W - 14 - k.menubar_width()                # our item, then the system items and the clock
    arrow_x = item_x + a.METER_W / 2 + 12              # the popover's arrow points at the item
    pop_x = min(W - 40 - a.WIDTH, arrow_x - a.WIDTH / 2)
    pop_y = bar_h + 13
    H = math.ceil(pop_y + pop_h + 44)

    scale = 5.0
    pad = 11.0
    slab_w, slab_h = (k.item_content_width(m) + 2 * pad) * scale, bar_h * scale
    chip_scale, chip_gap = 2.0, 22.0
    chip_w = (max(k.item_content_width(sm) for sm in states) + 20) * chip_scale
    chip_h = bar_h * chip_scale
    title_f, sub_f = a.font(15, k.NSFontWeightSemibold), a.font(13)
    every = m.headline_mode == 'all'
    top_label = 'All seats' if every else 'Regular seats'
    # centre the group (bar labels, glass, state chips) in the space left of the popover
    label_w = 24 + max(a.text_width(t, title_f) for t in (top_label, 'Serving seat'))
    reach = max(slab_w, slab_w / 2 + (3 * chip_w + 2 * chip_gap) / 2)
    slab_x = min(pop_x / 2 - (reach - label_w) / 2, pop_x - 72 - reach)
    group_h = 98 + slab_h + 64 + chip_h + 30        # label, glass, the three states and their captions
    slab_y = round(pop_y + pop_h * 0.5 - group_h / 2 + 98)
    meter_x = slab_x + pad * scale
    meter_y = slab_y + (bar_h - a.METER_H) / 2 * scale
    mono = a.menubar_font()
    num_x = meter_x + (a.METER_W + 1 + a.text_width(' ', mono)) * scale
    num_cx = num_x + a.text_width(a.fmt_pct(m.shown(m.headline)), mono) * scale / 2

    white, muted, rule = k.rgb(0xFFFFFF, 0.93), k.rgb(0xFFFFFF, 0.58), k.rgb(0xFFFFFF, 0.32)

    def leader(p0, p1):
        p = k.NSBezierPath.bezierPath()
        p.moveToPoint_(p0)
        p.lineToPoint_(p1)
        p.setLineWidth_(1.0)
        rule.setStroke()
        p.stroke()
        a.fill_circle(*p1, 6.0, k.rgb(0x000000, 0.35))
        a.fill_circle(*p1, 3.0, white)

    def draw():
        # wallpaper
        k.linear(((0, 0), (W, H)), k.rgb(0x161A2F), k.rgb(0x0A0C15))
        k.glow(slab_x + slab_w * 0.45, slab_y + slab_h * 0.5, 600, k.rgb(0x46378A, 0.58))
        k.glow(pop_x + a.WIDTH * 0.5, pop_y + pop_h * 0.35, 480, k.rgb(0x1C6B58, 0.40))
        k.glow(W * 0.45, H * 1.1, 480, k.rgb(0x22387A, 0.50))

        # menu bar, the item pressed, the popover open under it
        a.fill_rect(((0, 0), (W, bar_h)), k.rgb(0x0B0D16, 0.55))
        a.fill_rect(((0, bar_h - 0.5), (W, 0.5)), k.rgb(0xFFFFFF, 0.07))
        a.fill_rounded(((item_x - 6, 3), (a.METER_W + 12 + a.text_width(' 53%', mono), bar_h - 6)), 5,
                       k.rgb(0xFFFFFF, 0.14))
        k.item(item_x, bar_h, m)
        k.system_items(item_x + item_w + 10, bar_h, a.C.label())
        k.draw_popover(pixels, pop_x, pop_y, a.WIDTH, pop_h, arrow_x, True)

        # the item, magnified, on a slice of menu bar glass
        slab = ((slab_x, slab_y), (slab_w, slab_h))
        k.NSGraphicsContext.saveGraphicsState()
        sh = k.NSShadow.alloc().init()
        sh.setShadowBlurRadius_(44)
        sh.setShadowOffset_((0, -18))
        sh.setShadowColor_(k.rgb(0x000000, 0.55))
        sh.set()
        a.fill_rounded(slab, 30, k.rgb(0x1B1D28, 0.94))
        k.NSGraphicsContext.restoreGraphicsState()
        a.stroke_rounded(slab, 30, k.rgb(0xFFFFFF, 0.10), 1.5)
        k.NSGraphicsContext.saveGraphicsState()
        t = k.NSAffineTransform.transform()
        t.translateXBy_yBy_(meter_x, slab_y)
        t.scaleBy_(scale)
        t.concat()
        k.item(0, bar_h, m)
        k.NSGraphicsContext.restoreGraphicsState()

        # the number: a label above it
        ty = slab_y - 98
        leader((num_cx, ty + 46), (num_cx, slab_y + 30))
        a.draw_text(f'Weekly quota {m.word}, {m.scope}', num_cx - 220, ty, title_f, white, width=440,
                    align='center')
        a.draw_text('weighted by seat size, the reserve included' if every else
                    'weighted by seat size; the reserve is left out', num_cx - 240, ty + 21, sub_f, muted,
                    width=480, align='center')

        # the two bars: labels to the left of the glass
        for bar_y, text in ((meter_y + 3.0 * scale, top_label), (meter_y + 10.0 * scale, 'Serving seat')):
            a.draw_text(text, slab_x - 260, bar_y - a.line_height(title_f) / 2, title_f, white, width=236,
                        align='right')
            leader((slab_x - 14, bar_y), (meter_x + 9, bar_y))

        # the three states, small, under the glass
        row_w = 3 * chip_w + 2 * chip_gap
        cx0 = slab_x + slab_w / 2 - row_w / 2
        cy = slab_y + slab_h + 64
        cap_f = a.font(12.5, k.NSFontWeightMedium)
        for i, (sm, caption) in enumerate(zip(states, ('Regular seat serving', 'Reserve serving', 'Pool down'))):
            x = cx0 + i * (chip_w + chip_gap)
            chip = ((x, cy), (chip_w, chip_h))
            a.fill_rounded(chip, 14, k.rgb(0x1B1D28, 0.90))
            a.stroke_rounded(chip, 14, k.rgb(0xFFFFFF, 0.09), 1.0)
            k.NSGraphicsContext.saveGraphicsState()
            t = k.NSAffineTransform.transform()
            t.translateXBy_yBy_(x + (chip_w - k.item_content_width(sm) * chip_scale) / 2, cy)
            t.scaleBy_(chip_scale)
            t.concat()
            k.item(0, bar_h, sm)
            k.NSGraphicsContext.restoreGraphicsState()
            a.draw_text(caption, x - 20, cy + chip_h + 10, cap_f, muted, width=chip_w + 40, align='center')

    a.write_png(k.render(draw, W, H, ap, 2000 / W), str(out))   # 2000 px wide: 2x for a README column
    print(rel(out))


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--app', type=Path, default=REPO / 'menubar' / 'codexpool_menubar.py')
    p.add_argument('--settings', type=Path, default=REPO / 'menubar' / 'codexpool_settings.py')
    p.add_argument('--out', type=Path, default=IMAGES)
    p.add_argument('--skip-data', action='store_true', help='render the data files already in this folder')
    args = p.parse_args()
    if not args.skip_data:
        sys.argv = [sys.argv[0], '--now', NOW, '--out', str(HERE)]
        make_data.main()
    for name, appearance, scenario in (('popover-dark', 'dark', 'regular'), ('popover-light', 'light', 'regular'),
                                       ('popover-reserve-light', 'light', 'reserve'),
                                       ('popover-used-light', 'light', 'used')):
        snapshot(args.app, args.out / f'{name}.png', appearance, scenario)
    for name, pane, appearance in SETTINGS_SHOTS:
        settings_snapshot(args.settings, args.out / f'{name}.png', pane, appearance)
    k = Kit(load_app(args.app))
    menubar_strip(k, args.out / 'menubar-strip.png')
    hero(k, args.out / 'hero.png')


if __name__ == '__main__':
    main()
