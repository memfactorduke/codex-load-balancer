#!/usr/bin/env python3
"""Renders the sienna add-on's screenshots in addons/sienna/docs/images/ from the synthetic Claude pool in this
folder, on top of the core's synthetic Codex pool (docs/images/demo/). Run it with the menu bar app's Python:

    ~/.subpool/.venv/bin/python addons/sienna/docs/images/demo/render.py

It imports the core's docs/images/demo/render.py for the snapshot helpers and the core demo data, so the Codex
side of every image is exactly the core's.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
IMAGES = HERE.parent
REPO = HERE.parents[4]
CORE_DEMO = REPO / 'docs' / 'images' / 'demo'

sys.path.insert(0, str(HERE))
import make_claude_data  # noqa: E402

spec = importlib.util.spec_from_file_location('subpool_core_render', CORE_DEMO / 'render.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

CLAUDE = ['--pool-status', str(HERE / 'claude-status-regular.json'),
          '--pool-history', str(HERE / 'claude-history-regular.jsonl'), '--pool', 'claude']


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--app', type=Path, default=REPO / 'menubar' / 'subpool_menubar.py')
    p.add_argument('--settings', type=Path, default=REPO / 'menubar' / 'subpool_settings.py')
    p.add_argument('--out', type=Path, default=IMAGES)
    p.add_argument('--skip-data', action='store_true', help='render the data files already in this folder')
    args = p.parse_args()
    if not args.skip_data:
        sys.argv = [sys.argv[0], '--now', core.NOW, '--out', str(HERE)]
        make_claude_data.main()
    # The Claude pool: its popover tab and the Settings Overview's Claude side (docs/MENUBAR.md, README).
    core.snapshot(args.app, args.out / 'popover-claude-light.png', 'light', 'regular', CLAUDE)
    core.snapshot(args.app, args.out / 'popover-claude-dark.png', 'dark', 'regular', CLAUDE)
    core.settings_snapshot(args.settings, args.out / 'settings-claude-overview-light.png', 'overview', 'light', CLAUDE)


if __name__ == '__main__':
    main()
