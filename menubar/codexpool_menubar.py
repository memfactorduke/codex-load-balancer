#!/usr/bin/env python3
"""Compatibility entry point for installations made before the subpool rename."""
import runpy
from pathlib import Path
if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().with_name('subpool_menubar.py')), run_name="__main__")
