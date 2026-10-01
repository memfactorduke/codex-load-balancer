"""The Claude pool's mark in the menu bar app and the Settings switcher (addons/sienna/menubar_ext.py's MARK,
MARK_SCALE, PoolUI.mark_alpha and draw_glyph, registered into menubar/codexpool_menubar.py's MARK_APPS and
MARK_SCALE by load_pool_extensions): the app and its template files, the template's own alpha thickened by a
pixel, the fallback through the template sizes, the switcher's optical box, and a two-pool snapshot that reads no
app. The mask maths themselves are the core's (tests/test_marks.py). What draws needs PyObjC, that is the menu
bar's own interpreter (~/.codexpool/menubar/.venv/bin/python), and skips without it."""
import ast
import contextlib
import importlib
import io
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
EXT = REPO / 'addons' / 'sienna' / 'menubar_ext.py'
DEMO = REPO / 'docs' / 'images' / 'demo'
ADDON_DEMO = REPO / 'addons' / 'sienna' / 'docs' / 'images' / 'demo'
APP = 'com.anthropic.claudefordesktop'
FILES = ('TrayIconTemplate@3x.png', 'TrayIconTemplate@2x.png', 'TrayIconTemplate.png')


def constant(name):
    """A module-level `NAME = literal` of the extension, without importing it."""
    for node in ast.parse(EXT.read_text(), str(EXT)).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f'{EXT.name} has no module-level {name}')


def png(w, h, pixel):
    """A w*h RGBA PNG (8-bit, no filter) with pixel(x, y) -> (r, g, b, a), from the standard library alone."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    rows = b''.join(b'\x00' + bytes(v for x in range(w) for v in pixel(x, y)) for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def claude_like(w=48, h=48, cx=20, cy=26, r=14):
    """Pixels like a menu bar template: black, with alpha for the shape (a disc, off-centre)."""
    return png(w, h, lambda x, y: (0, 0, 0, 255 if (x - cx) ** 2 + (y - cy) ** 2 <= r * r else 0))


class SourceRules(unittest.TestCase):
    def test_the_app_and_its_template_files(self):
        self.assertEqual(constant('MARK'), (APP, FILES))
        self.assertEqual(constant('MARK_SCALE'), 1.1)


def load(name):
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
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='codexpool-sienna-marks-'))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def fake_app(self, name, files, version='1.0'):
        app = self.tmp / name
        (app / 'Contents' / 'Resources').mkdir(parents=True)
        with open(app / 'Contents' / 'Info.plist', 'wb') as f:
            plistlib.dump({'CFBundleShortVersionString': version}, f)
        for filename, data in files.items():
            (app / 'Contents' / 'Resources' / filename).write_bytes(data)
        return str(app)

    def mark(self, app_path):
        mb.MARKS = 'app'
        with mock.patch.object(mb, 'app_bundle', return_value=None if app_path is None else (app_path, '1.0')), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            image = mb.app_mark('claude')
        return image, err.getvalue()

    def test_registered_with_the_core(self):
        self.assertEqual(mb.POOLS, ('codex', 'claude'))
        self.assertEqual(mb.MARK_APPS['claude'], (APP, FILES))
        self.assertEqual(mb.MARK_SCALE['claude'], 1.1)
        self.assertEqual(mb.POOL_NAME['claude'], 'Claude')

    def test_template_uses_its_own_alpha_at_its_own_pixels(self):
        app = self.fake_app('Claude.app', {'TrayIconTemplate@3x.png': claude_like(cx=20, cy=26, r=14)})
        image, err = self.mark(app)
        self.assertIsNotNone(image, err)
        self.assertEqual(tuple(image.size()), (31, 31))   # the disc's box (29) plus the pixel of thickening a side,
        #                                                   not the 48 x 48 file

    def test_falls_back_through_the_template_sizes(self):
        app = self.fake_app('Claude.app', {'TrayIconTemplate.png': claude_like(w=16, h=16, cx=8, cy=8, r=6)})
        image, err = self.mark(app)
        self.assertIsNotNone(image, err)
        self.assertEqual(tuple(image.size()), (13, 13), 'the 1x file is not thickened')

    def test_no_app_falls_back_and_says_so_once(self):
        image, err = self.mark(None)
        self.assertIsNone(image)
        self.assertIn(f'Claude mark: {APP} is not installed', err)
        self.assertIn('drawing the plain glyph instead', err)

    def test_drawn_glyph_never_looks_the_app_up(self):
        mb.MARKS = 'drawn'
        rep = mb.new_bitmap(32, 32, 1.0)
        ctx = mb.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        mb.NSGraphicsContext.saveGraphicsState()
        try:
            mb.NSGraphicsContext.setCurrentContext_(ctx)
            with mock.patch.object(mb, 'app_bundle', side_effect=AssertionError('/Applications was read')):
                mb.draw_pool_glyph('claude', 16, 16, mb.NSColor.blackColor())
        finally:
            mb.NSGraphicsContext.restoreGraphicsState()

    def test_settings_switcher_image_gets_the_optical_box(self):
        st = load('codexpool_settings')
        alpha = bytes(255 if 2 <= x < 18 and 4 <= y < 8 else 0 for y in range(12) for x in range(20))
        mark = mb.mask_image(alpha, 20, 12)
        mb.MARKS = 'app'
        with mock.patch.object(mb, 'app_mark', return_value=mark):
            img = st.pool_glyph_image('claude', 12.0)
        self.assertTrue(img.isTemplate())
        box = 12.0 * mb.MARK_SCALE['claude']
        self.assertEqual(tuple(img.size()), (box, box), "the logo gets the menu bar's optical box (12 * 1.1)")
        with mock.patch.object(mb, 'app_mark', return_value=None):
            fallback = st.pool_glyph_image('claude', 12.0)
        self.assertEqual(tuple(fallback.size()), (12.0, 12.0), 'without the app: the drawn glyph')
        popen = mock.Mock(return_value=mock.Mock(**{'wait.return_value': 0}))
        with mock.patch.object(mb, 'POOL_DIR', self.tmp), mock.patch.object(mb.subprocess, 'Popen', popen):
            self.assertTrue(mb.open_settings('overview', 'claude'))
        self.assertIn('claude', popen.call_args.args[0])

    def test_two_pool_snapshot_with_drawn_marks_reads_no_app(self):
        """The popover and the strip, both pools, rendered as docs/images/demo/render.py does, with any look-up of
        an app an error: the docs' images never depend on what is in /Applications."""
        out = self.tmp / 'shot.png'
        with mock.patch.object(mb, 'app_bundle', side_effect=AssertionError('/Applications was read')), \
                mock.patch.object(mb, 'app_mark', side_effect=AssertionError('app_mark was called')), \
                contextlib.redirect_stdout(io.StringIO()):
            mb.snapshot(str(out), 'light', DEMO / 'status-regular.json', DEMO / 'history-regular.jsonl', None, '24h',
                        pool_status=ADDON_DEMO / 'claude-status-regular.json',
                        pool_history=ADDON_DEMO / 'claude-history-regular.jsonl', pool='claude')
        self.assertTrue(out.exists())
        self.assertTrue(out.with_name('shot-menubar.png').exists())
        with mock.patch.object(mb, 'app_bundle', return_value=None), contextlib.redirect_stderr(io.StringIO()):
            mb.MARKS = 'app'
            self.assertEqual(mb.mark_keys(), (None, None))


if __name__ == '__main__':
    unittest.main()
