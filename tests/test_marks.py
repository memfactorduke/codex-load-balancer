"""The pools' marks in the menu bar app and the Settings switcher (menubar/codexpool_menubar.py's "the pools' marks"
section, menubar/codexpool_settings.py's pool_glyph_image): the mask maths, the validation, the fallback to the drawn
glyph, --snapshot defaulting to drawn marks, and that the repo holds no logo file.

The pixel maths are pure functions over bytes, so they run on every Python here, loaded from the source without
importing the module (which needs PyObjC). What draws or looks an app up needs PyObjC, that is the menu bar's own
interpreter (~/.codexpool/menubar/.venv/bin/python), and skips without it. No test reads /Applications: the apps are
made-up bundles in a temp dir, with icons written by a stdlib PNG encoder."""
import __future__
import ast
import contextlib
import importlib
import io
import math
import pathlib
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

from _helpers import REPO

MENUBAR = REPO / 'menubar' / 'codexpool_menubar.py'
SETTINGS = REPO / 'menubar' / 'codexpool_settings.py'
DEMO = REPO / 'docs' / 'images' / 'demo'
PURE = ('chroma_alpha', 'flood_exterior', 'solid_inside', 'dilate_alpha', 'spark_dilation', 'mask_bounds', 'crop_alpha',
        'valid_mask')
IMAGE_SUFFIXES = {'.png', '.svg', '.icns', '.jpg', '.jpeg', '.webp', '.ico', '.gif', '.tif', '.tiff', '.pdf', '.bmp'}
IMAGE_DIRS = ('docs/images', 'site/assets/img')   # the only places an image belongs: the docs' renders, the site's
LOGO_NAMES = ('logo', 'icon-codex', 'trayicon', 'openai')


def source_tree():
    return ast.parse(MENUBAR.read_text(), str(MENUBAR))


def constant(name, tree=None):
    """A module-level `NAME = literal` of the menu bar app, without importing it."""
    for node in (tree or source_tree()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f'{MENUBAR.name} has no module-level {name}')


def pure_functions():
    """The PURE functions as the source defines them, loaded on their own: they are pure functions over bytes, ints
    and strings, so the module's AppKit imports are not needed to run them."""
    tree = source_tree()
    defs = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in PURE]
    missing = set(PURE) - {d.name for d in defs}
    assert not missing, f'{MENUBAR.name} lacks {sorted(missing)}'
    module = ast.Module(body=defs, type_ignores=[])
    ns = {}
    exec(compile(module, str(MENUBAR), 'exec', __future__.annotations.compiler_flag, True), ns)
    return ns


def png(w, h, pixel):
    """A w*h RGBA PNG (8-bit, no filter) with pixel(x, y) -> (r, g, b, a), from the standard library alone."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    rows = b''.join(b'\x00' + bytes(v for x in range(w) for v in pixel(x, y)) for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


HIGHLIGHT = (191, 171, 255, 255)   # the real icon's glossy top lobe: lavender, chroma 84, a soft spot under the key
HIGHLIGHT_AT = (-8, -14)           # where codex_like puts its centre, from the disc's centre


def codex_pixel(x, y, cx=32, cy=32, r=22, prompt=True, highlight=False):
    """A pixel of codex_like."""
    if prompt and abs(x - cx) < 4 and abs(y - cy) < 2:
        return (120, 120, 120, 255)
    if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
        hx, hy = cx + HIGHLIGHT_AT[0], cy + HIGHLIGHT_AT[1]
        if highlight and abs(x - hx) <= 4 and abs(y - hy) <= 3:
            return HIGHLIGHT
        return (60, 70, 230, 255)
    return (255, 255, 255, 255)


def codex_like(w=64, h=64, cx=32, cy=32, r=22, prompt=True, highlight=False):
    """Pixels like the Codex icon: a saturated blue-violet disc on an opaque white squircle, with a neutral grey
    `>_` block in the middle when prompt is set (in the real icon the prompt is the only neutral thing inside) and,
    with highlight, a low-chroma lavender patch up and left of it, inside the disc (the real icon's gloss)."""
    return png(w, h, lambda x, y: codex_pixel(x, y, cx, cy, r, prompt, highlight))


def solid(w=64, h=64, rgba=(60, 70, 230, 255)):
    return png(w, h, lambda x, y: rgba)


def rgba_bytes(w, h, pixel):
    return bytes(v for y in range(h) for x in range(w) for v in pixel(x, y))


class PureMaths(unittest.TestCase):
    """chroma_alpha, mask_bounds, crop_alpha and valid_mask on made-up bitmaps."""

    @classmethod
    def setUpClass(cls):
        cls.fn = pure_functions()

    def test_chroma_alpha_keeps_saturated_pixels_and_drops_neutral_ones(self):
        chroma_alpha = self.fn['chroma_alpha']
        pixels = [(60, 70, 230, 255),     # the cloud: saturated
                  (255, 255, 255, 255),   # the squircle: white
                  (120, 120, 120, 255),   # the prompt: grey
                  (0, 0, 0, 255),         # black
                  (0, 0, 0, 90),          # the shadow: translucent black
                  (60, 70, 230, 0),       # saturated but fully transparent
                  (60, 70, 230, 100)]     # saturated, half transparent: capped at its own alpha
        out = chroma_alpha(b''.join(bytes(p) for p in pixels))
        self.assertEqual(list(out), [255, 0, 0, 0, 0, 0, 100])

    def test_chroma_alpha_ramps_between_lo_and_hi(self):
        chroma_alpha = self.fn['chroma_alpha']

        def alpha_of(c, lo=32, hi=112):
            return chroma_alpha(bytes((c, 0, 0, 255)), lo, hi)[0]
        self.assertEqual(alpha_of(32), 0)
        self.assertEqual(alpha_of(112), 255)
        self.assertEqual(alpha_of(72), (72 - 32) * 255 // 80)
        ramp = [alpha_of(c) for c in range(0, 256)]
        self.assertEqual(ramp, sorted(ramp), 'alpha must not fall as chroma rises')
        self.assertEqual(alpha_of(20, lo=10, hi=30), 127)
        self.assertEqual(len(chroma_alpha(b'')), 0)

    def test_flood_exterior_is_the_outside_reached_from_the_border_not_a_hole(self):
        flood_exterior = self.fn['flood_exterior']
        w = h = 9   # a ring: solid at distance 2..3 from the centre, a hole in the middle, a soft pixel on the ring
        alpha = bytearray(w * h)
        for y in range(h):
            for x in range(w):
                d = math.hypot(x - 4, y - 4)
                alpha[y * w + x] = 255 if 2 <= d <= 3.2 else 0
        alpha[2 * w + 4] = 20   # a gap in the ring below the threshold, but not joined to the outside (diagonal)
        ext = flood_exterior(bytes(alpha), w, h)
        self.assertEqual(len(ext), w * h)
        self.assertEqual(ext[0], 1, 'a corner is outside')
        self.assertEqual(ext[4 * w + 4], 0, 'the hole is inside')
        self.assertEqual(ext[4 * w + 1], 0, 'the ring is inside')
        self.assertEqual(sum(ext[y * w + x] for y in range(h) for x in range(w) if alpha[y * w + x] >= 32), 0,
                         'nothing above the threshold is outside')
        alpha[1 * w + 4] = 0   # the ring's outer pixel above the gap goes too: the hole is open to the border, a bay
        ext = flood_exterior(bytes(alpha), w, h)
        self.assertEqual(ext[4 * w + 4], 1, 'reached through the gap')
        self.assertEqual(flood_exterior(bytes([255] * 16), 4, 4), bytes(16), 'solid: nothing outside')
        self.assertEqual(flood_exterior(bytes(16), 4, 4), bytes([1] * 16), 'empty: all outside')
        self.assertEqual(flood_exterior(b'', 0, 0), b'')
        self.assertEqual(flood_exterior(bytes(4), 4, 4), bytes(16), 'a mask of the wrong length: nothing')
        self.assertEqual(flood_exterior(bytes(16), 4, 4, threshold=0), bytes(16), 'nothing is below 0')

    def test_solid_inside_raises_only_the_inside_to_the_softer_key(self):
        solid_inside = self.fn['solid_inside']
        alpha = bytes([0, 100, 200, 255, 40])
        inner = bytes([255, 255, 100, 0, 40])
        ext = bytes([1, 0, 0, 0, 1])
        self.assertEqual(list(solid_inside(alpha, inner, ext)), [0, 255, 200, 255, 40])
        self.assertEqual(solid_inside(b'', b'', b''), b'')

    def test_dilate_alpha(self):
        dilate_alpha = self.fn['dilate_alpha']
        w, h = 5, 4
        alpha = bytearray(w * h)
        alpha[1 * w + 1] = 200   # one pixel: becomes a 3 x 3 block
        alpha[3 * w + 4] = 90    # in a corner: clipped
        out = dilate_alpha(bytes(alpha), w, h)
        expect = [[200, 200, 200, 0, 0],
                  [200, 200, 200, 0, 0],
                  [200, 200, 200, 90, 90],
                  [0, 0, 0, 90, 90]]
        self.assertEqual([list(out[y * w:(y + 1) * w]) for y in range(h)], expect)
        self.assertEqual(dilate_alpha(bytes(alpha), w, h, radius=0), bytes(alpha), 'radius 0: as it was')
        two = dilate_alpha(bytes(alpha), w, h, radius=2)
        self.assertEqual(two[0], 200)
        self.assertEqual(two[3 * w + 0], 200, 'two away')
        self.assertEqual(len(dilate_alpha(b'', 0, 0)), 0)

    def test_spark_dilation_by_template_scale(self):
        spark_dilation = self.fn['spark_dilation']
        self.assertEqual(spark_dilation('TrayIconTemplate@3x.png'), 1)
        self.assertEqual(spark_dilation('TrayIconTemplate@2x.png'), 1)
        self.assertEqual(spark_dilation('TrayIconTemplate.png'), 0, 'a whole point of thickening would be too much')

    def test_codex_like_icon_with_a_highlight_is_solid_inside_with_the_prompt_cut_out(self):
        """The real icon's gloss: chroma_alpha alone leaves it at 65% alpha, a hole in the cloud. Flood-filling the
        outside and keying the inside softer fills it, while the neutral prompt stays a hole and the outside stays
        out (the mark's bounds do not grow)."""
        chroma_alpha, flood_exterior = self.fn['chroma_alpha'], self.fn['flood_exterior']
        solid_inside, mask_bounds = self.fn['solid_inside'], self.fn['mask_bounds']
        w, h, cx, cy, r = 64, 64, 32, 32, 22
        rgba = rgba_bytes(w, h, lambda x, y: codex_pixel(x, y, cx, cy, r, highlight=True))
        hx, hy = cx + HIGHLIGHT_AT[0], cy + HIGHLIGHT_AT[1]
        outline = chroma_alpha(rgba, 32, 112)
        self.assertEqual(outline[hy * w + hx], (84 - 32) * 255 // 80, 'the key alone leaves the gloss soft')
        alpha = solid_inside(outline, chroma_alpha(rgba, 32, 64), flood_exterior(outline, w, h, 32))
        self.assertEqual(alpha[hy * w + hx], 255, 'filled')
        self.assertEqual(alpha[cy * w + cx], 0, 'the prompt is still cut out')
        self.assertEqual(alpha[0], 0, 'the squircle is still out')
        self.assertEqual(mask_bounds(alpha, w, h), mask_bounds(outline, w, h), 'the same box')
        inside = [alpha[y * w + x] for y in range(h) for x in range(w)
                  if (x - cx) ** 2 + (y - cy) ** 2 <= (r - 1) ** 2 and not (abs(x - cx) < 4 and abs(y - cy) < 2)]
        self.assertEqual(min(inside), 255, 'nothing soft inside the disc but the prompt')

    def test_mask_bounds(self):
        mask_bounds = self.fn['mask_bounds']
        w, h = 8, 6
        alpha = bytearray(w * h)
        alpha[1 * w + 2] = 200
        alpha[4 * w + 5] = 9
        self.assertEqual(mask_bounds(bytes(alpha), w, h), (2, 1, 6, 5))
        alpha[4 * w + 5] = 8   # at the threshold: not a mark pixel
        self.assertEqual(mask_bounds(bytes(alpha), w, h), (2, 1, 3, 2))
        self.assertIsNone(mask_bounds(bytes(w * h), w, h))
        self.assertIsNone(mask_bounds(b'', 0, 0))

    def test_crop_alpha(self):
        crop_alpha = self.fn['crop_alpha']
        w = 5
        alpha = bytes(range(25))   # 5 x 5, value = index
        cut, cw, ch = crop_alpha(alpha, w, (1, 2, 4, 4))
        self.assertEqual((cw, ch), (3, 2))
        self.assertEqual(list(cut), [11, 12, 13, 16, 17, 18])

    def test_valid_mask(self):
        mask_bounds, valid_mask = self.fn['mask_bounds'], self.fn['valid_mask']

        def check(w, h, pixel):
            alpha = bytes(pixel(x, y) for y in range(h) for x in range(w))
            return valid_mask(alpha, w, h, mask_bounds(alpha, w, h))
        self.assertTrue(check(40, 40, lambda x, y: 255 if (x - 20) ** 2 + (y - 20) ** 2 <= 15 ** 2 else 0))  # disc
        self.assertFalse(check(100, 100, lambda x, y: 255 if (x, y) == (50, 50) else 0), 'a speck')
        self.assertFalse(check(40, 40, lambda x, y: 255), 'a solid box')
        self.assertFalse(check(40, 40, lambda x, y: 255 if (x, y) in ((0, 0), (39, 39)) else 0), 'two far corners')
        self.assertFalse(check(40, 40, lambda x, y: 0), 'nothing')
        self.assertFalse(valid_mask(bytes(1600), 40, 40, None))
        # the limits are parameters
        alpha = bytes(255 if (x - 20) ** 2 + (y - 20) ** 2 <= 15 ** 2 else 0 for y in range(40) for x in range(40))
        self.assertFalse(valid_mask(alpha, 40, 40, mask_bounds(alpha, 40, 40), coverage=(0.9, 1.0)))
        self.assertFalse(valid_mask(alpha, 40, 40, mask_bounds(alpha, 40, 40), min_side=0.9))

    def test_codex_like_icon_masks_to_the_cloud_with_the_prompt_cut_out(self):
        chroma_alpha, mask_bounds = self.fn['chroma_alpha'], self.fn['mask_bounds']
        crop_alpha, valid_mask = self.fn['crop_alpha'], self.fn['valid_mask']
        w, h, cx, cy, r = 64, 64, 32, 32, 22

        def pixel(x, y):
            if abs(x - cx) < 4 and abs(y - cy) < 2:
                return (120, 120, 120, 255)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                return (60, 70, 230, 255)
            return (255, 255, 255, 255)
        alpha = chroma_alpha(rgba_bytes(w, h, pixel))
        bounds = mask_bounds(alpha, w, h)
        self.assertEqual(bounds, (cx - r, cy - r, cx + r + 1, cy + r + 1), 'the box is the disc, not the squircle')
        self.assertTrue(valid_mask(alpha, w, h, bounds))
        cut, cw, ch = crop_alpha(alpha, w, bounds)
        self.assertEqual(cut[(cy - bounds[1]) * cw + (cx - bounds[0])], 0, 'the prompt is cut out of the cloud')
        self.assertEqual(cut[0], 0, 'a corner of the box is outside the disc')
        self.assertEqual(cut[(cy - bounds[1]) * cw], 255, 'the disc edge is solid')
        self.assertAlmostEqual(sum(cut) / (255.0 * len(cut)), math.pi / 4, delta=0.06)


class SourceRules(unittest.TestCase):
    """What the source promises without running: the module default, the apps, and no logo in the repo."""

    def test_module_default_is_drawn(self):
        self.assertEqual(constant('MARKS'), 'drawn', 'importing the menu bar app must read nothing from /Applications')

    def test_the_apps_and_their_icons(self):
        """The core names the Codex app alone; an add-on's pool registers its own (PoolUI.mark, mark_scale)."""
        apps = constant('MARK_APPS')
        self.assertEqual(apps, {'codex': ('com.openai.codex', ('icon-codex-light.png',))})
        self.assertEqual(set(constant('MARK_SCALE')), {'codex'})

    def test_no_logo_file_in_the_repo(self):
        resources = {name.lower() for _, names in constant('MARK_APPS').values() for name in names}
        images, wrong_place, named_like_logos, app_resources = [], [], [], []
        for path in REPO.rglob('*'):
            if not path.is_file() or any(part in ('.git', '__pycache__', 'node_modules', '.venv', 'addons') for part in path.parts):
                continue
            rel = path.relative_to(REPO).as_posix()
            if path.name.lower() in resources:
                app_resources.append(rel)
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            images.append(rel)
            if not rel.startswith(IMAGE_DIRS):
                wrong_place.append(rel)
            if any(word in path.name.lower() for word in LOGO_NAMES):
                named_like_logos.append(rel)
        self.assertTrue(images, 'the docs renders should be here')
        self.assertEqual(app_resources, [], "an app's icon file is in the repo")
        self.assertEqual(named_like_logos, [], 'an image named like a logo is in the repo')
        self.assertEqual(wrong_place, [], f'images belong under {IMAGE_DIRS}')

    def test_both_snapshots_take_marks(self):
        for source in (MENUBAR, SETTINGS):
            text = source.read_text()
            self.assertIn("'--marks'", text, source.name)
            self.assertIn("choices=('drawn', 'app')", text, source.name)
            self.assertIn('[--marks drawn|app]', text, f'{source.name}: the docstring names the flag')
        self.assertNotIn('not logos', MENUBAR.read_text() + SETTINGS.read_text() + (REPO / 'menubar' / 'SPEC.md').read_text())


def load(name):
    """The menu bar app or the Settings window as a module; None without PyObjC."""
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return None
    if str(MENUBAR.parent) not in sys.path:
        sys.path.insert(0, str(MENUBAR.parent))
    return importlib.import_module(name)


mb = load('codexpool_menubar')


@unittest.skipIf(mb is None, 'needs PyObjC (run with the menu bar app\'s interpreter, ~/.codexpool/menubar/.venv/bin/python)')
class WithAppKit(unittest.TestCase):
    """The parts that draw or look an app up. Every test starts with an empty mark cache and MARKS as it was."""

    def setUp(self):
        saved = (mb.MARKS, dict(mb._MARKS), set(mb._MARK_NOTED))
        mb._MARKS.clear()
        mb._MARK_NOTED.clear()

        def restore():
            mb.MARKS = saved[0]
            mb._MARKS.clear()
            mb._MARKS.update(saved[1])
            mb._MARK_NOTED.clear()
            mb._MARK_NOTED.update(saved[2])
        self.addCleanup(restore)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='codexpool-marks-'))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def fake_app(self, name, files, version='1.0'):
        """A made-up app bundle: Contents/Info.plist and the given Resources files. Returns its path."""
        app = self.tmp / name
        (app / 'Contents' / 'Resources').mkdir(parents=True)
        with open(app / 'Contents' / 'Info.plist', 'wb') as f:
            plistlib.dump({'CFBundleShortVersionString': version}, f)
        for filename, data in files.items():
            (app / 'Contents' / 'Resources' / filename).write_bytes(data)
        return str(app)

    @contextlib.contextmanager
    def stderr(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            yield buf

    def mark(self, pool, app_path, version='1.0'):
        """app_mark(pool) with the app looked up as app_path, in app mode; (mark, stderr text, app_bundle mock)."""
        mb.MARKS = 'app'
        with mock.patch.object(mb, 'app_bundle', return_value=None if app_path is None else (app_path, version)) as ab, \
                self.stderr() as err:
            image = mb.app_mark(pool)
        return image, err.getvalue(), ab

    # -- the loader ------------------------------------------------------------------------------------------

    def test_codex_like_icon_makes_a_mark_the_size_of_the_cloud(self):
        app = self.fake_app('ChatGPT.app', {'icon-codex-light.png': codex_like()})
        image, err, _ = self.mark('codex', app)
        self.assertIsNotNone(image, err)
        self.assertEqual(err, '')
        w, h = image.size()
        expect = (2 * 22 + 1) * mb.MARK_PX / 64   # the disc (45 px across at 64), drawn at MARK_PX: not the icon
        self.assertAlmostEqual(w, expect, delta=8, msg='the resampled edge bleeds a few pixels, no more')
        self.assertAlmostEqual(h, expect, delta=8)
        self.assertEqual(w, h, 'a disc')
        with mock.patch.object(mb, 'app_bundle', return_value=None), self.stderr():   # any other pool: no app
            keys = mb.mark_keys()
        self.assertEqual(keys[0], (app, '1.0'))
        self.assertEqual(len(keys), len(mb.POOLS))

    def test_codex_gloss_is_filled_in(self):
        """The loader's pipeline on an icon with the real one's low-chroma highlight: the mask is solid there."""
        app = self.fake_app('ChatGPT.app', {'icon-codex-light.png': codex_like(highlight=True)})
        image, err, _ = self.mark('codex', app)
        self.assertIsNotNone(image, err)
        rep = image.representations()[0]
        stride, data = rep.bytesPerRow(), bytes(rep.bitmapData()[:rep.bytesPerRow() * rep.pixelsHigh()])
        k = mb.MARK_PX / 64                          # the icon is drawn at MARK_PX; the box starts at the disc's edge
        x0 = y0 = (32 - 22) * k
        hx, hy = (32 + HIGHLIGHT_AT[0]) * k - x0, (32 + HIGHLIGHT_AT[1]) * k - y0

        def alpha(x, y):
            return data[int(y) * stride + int(x) * 4 + 3]
        for dx, dy in ((0, 0), (-6, 0), (6, 0), (0, -4), (0, 4)):
            self.assertGreaterEqual(alpha(hx + dx, hy + dy), 250, f'the gloss at {dx},{dy} is solid')
        self.assertLessEqual(alpha(22 * k, 22 * k), 8, 'the prompt is a hole')

    # -- the fallback: None, one line of stderr per cause, never an exception ------------------------------------

    def test_no_app_falls_back_and_says_so_once(self):
        image, err, _ = self.mark('codex', None)
        self.assertIsNone(image)
        self.assertEqual(err.count('\n'), 1, err)
        self.assertIn('com.openai.codex is not installed', err)
        self.assertIn('drawing the plain glyph instead', err)
        with self.stderr() as again:
            self.assertIsNone(mb.app_mark('codex'))
            self.assertIsNone(mb.app_mark('codex'))
        self.assertEqual(again.getvalue(), '', 'said once')
        with mock.patch.object(mb, 'app_bundle', return_value=None), self.stderr():   # every pool: no app
            self.assertEqual(mb.mark_keys(), (None,) * len(mb.POOLS))

    def test_missing_icon_file_falls_back(self):
        app = self.fake_app('ChatGPT.app', {})
        image, err, _ = self.mark('codex', app)
        self.assertIsNone(image)
        self.assertIn('no icon-codex-light.png in', err)

    def test_unreadable_icon_falls_back(self):
        app = self.fake_app('ChatGPT.app', {'icon-codex-light.png': b'not a png at all'})
        image, err, _ = self.mark('codex', app)
        self.assertIsNone(image)
        self.assertIn('could not read', err)

    def test_icon_that_is_not_a_mark_falls_back(self):
        for name, data in (('solid', solid()), ('white', solid(rgba=(255, 255, 255, 255))),
                           ('speck', codex_like(r=1, prompt=False))):
            with self.subTest(name):
                mb._MARKS.clear()
                mb._MARK_NOTED.clear()
                app = self.fake_app(f'{name}.app', {'icon-codex-light.png': data})
                image, err, _ = self.mark('codex', app)
                self.assertIsNone(image)
                self.assertIn('does not look like a mark', err)

    def test_a_throwing_loader_falls_back(self):
        mb.MARKS = 'app'
        with mock.patch.object(mb, 'app_bundle', side_effect=RuntimeError('boom')), self.stderr() as err:
            self.assertIsNone(mb.app_mark('codex'))
        self.assertIn('looking the app up failed (boom)', err.getvalue())
        mb._MARKS.clear()
        mb._MARK_NOTED.clear()
        app = self.fake_app('ChatGPT.app', {'icon-codex-light.png': codex_like()})
        with mock.patch.object(mb, 'build_mark', side_effect=ValueError('bad bytes')), \
                mock.patch.object(mb, 'app_bundle', return_value=(app, '1.0')), self.stderr() as err:
            self.assertIsNone(mb.app_mark('codex'))
        self.assertIn('could not make it from', err.getvalue())
        self.assertIn('bad bytes', err.getvalue())

    def test_a_throwing_lookup_says_so_once_and_keeps_the_mark(self):
        """One line, not also "is not installed"; a mark the pool had stays until the app can be looked up again;
        the exception's text varies without making a new line."""
        mb.MARKS = 'app'
        with mock.patch.object(mb, 'app_bundle', side_effect=RuntimeError('boom')), self.stderr() as err:
            self.assertIsNone(mb.app_mark('codex'))
        self.assertEqual(err.getvalue().count('\n'), 1, err.getvalue())
        self.assertIn('looking the app up failed (boom)', err.getvalue())
        self.assertNotIn('is not installed', err.getvalue())
        mb._MARKS.clear()
        mb._MARK_NOTED.clear()
        app = self.fake_app('ChatGPT.app', {'icon-codex-light.png': codex_like()})
        first, _, _ = self.mark('codex', app)
        self.assertIsNotNone(first)
        mb._MARKS['codex'][0] -= mb.MARK_RECHECK_S + 1
        with mock.patch.object(mb, 'app_bundle', side_effect=RuntimeError('boom 1')) as ab, self.stderr() as err:
            self.assertIs(mb.app_mark('codex'), first, 'kept')
            self.assertIs(mb.app_mark('codex'), first)
            self.assertEqual(ab.call_count, 1, 'not looked up again before MARK_RECHECK_S')
            mb._MARKS['codex'][0] -= mb.MARK_RECHECK_S + 1
            ab.side_effect = RuntimeError('boom 2')
            self.assertIs(mb.app_mark('codex'), first)
        self.assertEqual(err.getvalue().count('\n'), 1, 'one line for the cause, whatever the text: ' + err.getvalue())
        self.assertEqual(mb.mark_keys()[0], (app, '1.0'), 'still that app')

    def test_a_failed_build_is_tried_again_at_the_next_recheck(self):
        """An app half copied into /Applications (Info.plist there, the icon not yet): the plain glyph now, the
        logo within MARK_RECHECK_S, without a relaunch or a new version."""
        app = self.fake_app('ChatGPT.app', {})
        image, err, _ = self.mark('codex', app)
        self.assertIsNone(image)
        self.assertIn('no icon-codex-light.png in', err)
        self.assertIsNone(mb._MARKS['codex'][1], 'a failure is not remembered under the key')
        with mock.patch.object(mb, 'app_bundle', return_value=(app, '1.0')) as ab, self.stderr() as err:
            self.assertIsNone(mb.app_mark('codex'))
            self.assertEqual(ab.call_count, 0, 'cached until the re-check')
            pathlib.Path(app, 'Contents', 'Resources', 'icon-codex-light.png').write_bytes(codex_like())
            mb._MARKS['codex'][0] -= mb.MARK_RECHECK_S + 1
            image = mb.app_mark('codex')
        self.assertIsNotNone(image, 'built at the re-check')
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(mb._MARKS['codex'][1], (app, '1.0'))
        self.assertEqual(mb.mark_keys()[0], (app, '1.0'))

    # -- the cache: per (app path, version), re-checked at most every MARK_RECHECK_S -------------------------------

    def test_cached_per_process_and_rechecked_on_a_new_version(self):
        app = self.fake_app('ChatGPT.app', {'icon-codex-light.png': codex_like()})
        first, _, ab = self.mark('codex', app)
        self.assertIsNotNone(first)
        with mock.patch.object(mb, 'app_bundle', return_value=(app, '1.0')) as ab:
            self.assertIs(mb.app_mark('codex'), first)
            self.assertEqual(ab.call_count, 0, 'within MARK_RECHECK_S the app is not looked up again')
            mb._MARKS['codex'][0] -= mb.MARK_RECHECK_S + 1   # time passes
            self.assertIs(mb.app_mark('codex'), first)
            self.assertEqual(ab.call_count, 1, 'looked up again, same key: the same mark')
        mb._MARKS['codex'][0] -= mb.MARK_RECHECK_S + 1
        with mock.patch.object(mb, 'app_bundle', return_value=(app, '2.0')):
            second = mb.app_mark('codex')
        self.assertIsNotNone(second)
        self.assertIsNot(second, first, 'a new version rebuilds the mark')
        self.assertEqual(mb._MARKS['codex'][1], (app, '2.0'))

    def test_app_bundle_reads_the_version_from_info_plist(self):
        app = self.fake_app('Example.app', {}, version='3.4.5')
        url = mock.Mock(**{'path.return_value': app})
        workspace = mock.Mock(**{'URLForApplicationWithBundleIdentifier_.return_value': url})
        # the module's name, never the ObjC class: setting an attribute on one rewrites the runtime's method
        with mock.patch.object(mb, 'NSWorkspace', mock.Mock(**{'sharedWorkspace.return_value': workspace})):
            self.assertEqual(mb.app_bundle('com.example.app'), (app, '3.4.5'))
            workspace.URLForApplicationWithBundleIdentifier_.assert_called_with('com.example.app')
            workspace.URLForApplicationWithBundleIdentifier_.return_value = None
            self.assertIsNone(mb.app_bundle('com.example.nothing'))

    def test_app_bundle_survives_a_half_written_info_plist(self):
        """An app being replaced: a cut-off XML plist (an expat error, not a ValueError) or a binary one that is
        not a plist gives the app with an empty version, never an exception."""
        app = self.fake_app('Example.app', {})
        plist = pathlib.Path(app, 'Contents', 'Info.plist')
        url = mock.Mock(**{'path.return_value': app})
        workspace = mock.Mock(**{'URLForApplicationWithBundleIdentifier_.return_value': url})
        with mock.patch.object(mb, 'NSWorkspace', mock.Mock(**{'sharedWorkspace.return_value': workspace})):
            plist.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
                              b'<key>CFBundleShortVersionString</key><str')
            self.assertEqual(mb.app_bundle('com.example.app'), (app, ''))
            plist.write_bytes(b'bplist00\x00\x01')
            self.assertEqual(mb.app_bundle('com.example.app'), (app, ''))
            plist.unlink()
            self.assertEqual(mb.app_bundle('com.example.app'), (app, ''))

    def test_open_settings_passes_the_marks_on(self):
        """The Settings window draws the marks as the menu bar does: --marks drawn here is drawn there."""
        popen = mock.Mock(return_value=mock.Mock(**{'wait.return_value': 0}))
        with mock.patch.object(mb, 'POOL_DIR', self.tmp), mock.patch.object(mb.subprocess, 'Popen', popen):
            for marks in ('drawn', 'app'):
                mb.MARKS = marks
                self.assertTrue(mb.open_settings('overview', 'codex'))
                argv = popen.call_args.args[0]
                self.assertEqual(argv[-2:], ['--marks', marks])
                self.assertEqual(argv[1], str(mb.SETTINGS_SCRIPT))
                self.assertIn('--pane', argv)

    # -- drawing: drawn mode never asks for an app; app mode draws the mask -----------------------------------------

    def test_drawn_mode_never_looks_an_app_up(self):
        mb.MARKS = 'drawn'
        rep = mb.new_bitmap(32, 32, 1.0)
        ctx = mb.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        mb.NSGraphicsContext.saveGraphicsState()
        try:
            mb.NSGraphicsContext.setCurrentContext_(ctx)
            with mock.patch.object(mb, 'app_bundle', side_effect=AssertionError('/Applications was read')), \
                    mock.patch.object(mb, 'app_mark', side_effect=AssertionError('app_mark was called')):
                for pool in mb.POOLS:
                    mb.draw_pool_glyph(pool, 16, 16, mb.NSColor.blackColor())
        finally:
            mb.NSGraphicsContext.restoreGraphicsState()
        self.assertIsNone(mb.mark_keys())

    def test_app_mode_draws_the_mask_in_the_colour(self):
        mb.MARKS = 'app'
        alpha = bytes(255 if 2 <= x < 18 and 4 <= y < 8 else 0 for y in range(12) for x in range(20))
        mark = mb.mask_image(alpha, 20, 12)
        rep = mb.new_bitmap(40, 40, 1.0)
        ctx = mb.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        mb.NSGraphicsContext.saveGraphicsState()
        try:
            mb.NSGraphicsContext.setCurrentContext_(ctx)
            mb.NSColor.clearColor().set()
            mb.NSRectFillUsingOperation(((0, 0), (40, 40)), mb.NSCompositingOperationCopy)
            with mock.patch.object(mb, 'app_mark', return_value=mark):
                mb.draw_pool_glyph('codex', 20, 20, mb.NSColor.redColor(), size=20)
            ctx.flushGraphics()
        finally:
            mb.NSGraphicsContext.restoreGraphicsState()
        stride, data = rep.bytesPerRow(), bytes(rep.bitmapData()[:rep.bytesPerRow() * 40])
        r, g, b, a = data[20 * stride + 20 * 4: 20 * stride + 20 * 4 + 4]
        self.assertGreater(a, 200, 'the middle of the bar is painted')
        self.assertGreater(r, g + 100, 'in the colour given')
        self.assertEqual(data[2 * stride + 2 * 4 + 3], 0, 'a corner outside the mask is not')

    def draw_into(self, scale, fn):
        """fn() drawn into a fresh 40 x 40 pt bitmap at `scale`; its pixels (bytes, pixels wide, stride)."""
        rep = mb.new_bitmap(40, 40, scale)
        ctx = mb.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        mb.NSGraphicsContext.saveGraphicsState()
        try:
            mb.NSGraphicsContext.setCurrentContext_(ctx)
            mb.NSColor.clearColor().set()
            mb.NSRectFillUsingOperation(((0, 0), (40, 40)), mb.NSCompositingOperationCopy)
            fn()
            ctx.flushGraphics()
        finally:
            mb.NSGraphicsContext.restoreGraphicsState()
        return bytes(rep.bitmapData()[:rep.bytesPerRow() * rep.pixelsHigh()]), rep.pixelsWide(), rep.bytesPerRow()

    def test_mark_lands_on_the_pixel_grid(self):
        """A mark centred off the pixel grid (as the spark's box is at 1x) is snapped to it: at 1x, where the mask
        maps pixel for pixel, its hard edge stays hard (no half-covered row or column); at 2x it comes out exactly
        as when centred on the grid. Without the snap, both differ: it is resampled at a fractional offset."""
        self.assertIsNotNone(mb.CGContextGetCTM, 'CoreGraphics bound')
        mb.MARKS = 'app'
        alpha = bytes(255 if 2 <= x < 18 and 4 <= y < 8 else 0 for y in range(12) for x in range(20))
        mark = mb.mask_image(alpha, 20, 12)

        def draw(cx, cy):
            with mock.patch.object(mb, 'app_mark', return_value=mark):
                mb.draw_pool_glyph('codex', cx, cy, mb.NSColor.redColor(), size=20)
        # pixel_rect, on its own, in each context
        for scale, expect in ((1.0, ((10, 15), (20, 12))), (2.0, ((10.5, 14.5), (20, 12)))):
            self.draw_into(scale, lambda: self.assertEqual(mb.pixel_rect(((10.3, 14.6), (20, 12))), expect))
        # 1x: hard edges
        data, n, stride = self.draw_into(1.0, lambda: draw(20.3, 20.6))
        row = [data[(n // 2) * stride + x * 4 + 3] for x in range(n)]
        col = [data[y * stride + (n // 2) * 4 + 3] for y in range(n)]
        for line, name in ((row, 'row'), (col, 'column')):
            self.assertEqual([v for v in line if 8 < v < 247], [], f'a half-covered pixel in the middle {name}: {line}')
        self.assertEqual(sum(1 for v in row if v == 255), 16, 'the bar is 16 pt wide')
        self.assertEqual(sum(1 for v in col if v == 255), 4, 'and 4 pt tall')
        # 2x: the same pixels as on the grid, and not what an unsnapped draw gives
        off_grid, _, _ = self.draw_into(2.0, lambda: draw(20.3, 20.6))
        on_grid, _, _ = self.draw_into(2.0, lambda: draw(20.5, 20.5))
        self.assertEqual(off_grid, on_grid)
        with mock.patch.object(mb, 'pixel_rect', lambda rect: rect):
            unsnapped, _, _ = self.draw_into(2.0, lambda: draw(20.3, 20.6))
        self.assertNotEqual(unsnapped, on_grid, 'the snap is what makes the difference')

    # -- the Settings switcher: the same mask as a template image, the drawn glyph as the fallback -----------------

    def test_settings_switcher_image(self):
        st = load('codexpool_settings')
        alpha = bytes(255 if 2 <= x < 18 and 4 <= y < 8 else 0 for y in range(12) for x in range(20))
        mark = mb.mask_image(alpha, 20, 12)
        mb.MARKS = 'app'
        with mock.patch.object(mb, 'app_mark', return_value=mark):
            img = st.pool_glyph_image('codex', 12.0)
        self.assertTrue(img.isTemplate())
        box = 12.0 * mb.MARK_SCALE['codex']
        self.assertEqual(tuple(img.size()), (box, box), "the logo gets the menu bar's optical box")
        with mock.patch.object(mb, 'app_mark', return_value=None):
            fallback = st.pool_glyph_image('codex', 12.0)
        self.assertTrue(fallback.isTemplate())
        self.assertEqual(tuple(fallback.size()), (12.0, 12.0), 'without the app: the drawn glyph, as before')
        mb.MARKS = 'drawn'
        with mock.patch.object(mb, 'app_mark', side_effect=AssertionError('app_mark was called')):
            self.assertEqual(tuple(st.pool_glyph_image('codex').size()), (12.0, 12.0))

    # -- --snapshot defaults to drawn marks, the live apps to the logos ------------------------------------------

    def test_menubar_main_defaults(self):
        out = str(self.tmp / 'x.png')
        with mock.patch.object(mb, 'snapshot') as snap, mock.patch.object(mb, 'run_app') as live:
            mb.main(['--snapshot', out, '--status', str(DEMO / 'status-regular.json')])
            self.assertEqual(snap.call_args.args[-1], 'drawn')
            mb.main(['--snapshot', out, '--status', str(DEMO / 'status-regular.json'), '--marks', 'app'])
            self.assertEqual(snap.call_args.args[-1], 'app')
            self.assertEqual(live.call_count, 0)
            mb.main([])
            live.assert_called_with('app')
            mb.main(['--marks', 'drawn'])
            live.assert_called_with('drawn')

    def test_settings_main_defaults(self):
        st = load('codexpool_settings')
        out = str(self.tmp / 'x.png')
        with mock.patch.object(st, 'snapshot') as snap, mock.patch.object(st, 'run_app') as live:
            st.main(['--snapshot', out, '--pane', 'overview'])
            self.assertEqual((mb.MARKS, snap.call_count), ('drawn', 1))
            st.main(['--snapshot', out, '--pane', 'overview', '--marks', 'app'])
            self.assertEqual((mb.MARKS, snap.call_count), ('app', 2))
            self.assertEqual(live.call_count, 0)
            st.main(['--pane', 'overview'])
            self.assertEqual((mb.MARKS, live.call_count), ('app', 1))
            st.main(['--pane', 'overview', '--marks', 'drawn'])
            self.assertEqual(mb.MARKS, 'drawn')

    def test_snapshot_with_drawn_marks_reads_no_app(self):
        """The whole popover and the item, rendered as docs/images/demo/render.py does, with any look-up of an app
        an error: the docs' images never depend on what is in /Applications."""
        out = self.tmp / 'shot.png'
        with mock.patch.object(mb, 'app_bundle', side_effect=AssertionError('/Applications was read')), \
                mock.patch.object(mb, 'app_mark', side_effect=AssertionError('app_mark was called')), \
                contextlib.redirect_stdout(io.StringIO()):
            mb.snapshot(str(out), 'light', DEMO / 'status-regular.json', DEMO / 'history-regular.jsonl', None, '24h')
        self.assertTrue(out.exists())
        self.assertTrue(out.with_name('shot-menubar.png').exists())
        self.assertEqual(mb.MARKS, 'drawn')


if __name__ == '__main__':
    unittest.main()
