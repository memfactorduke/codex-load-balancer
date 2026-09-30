"""Chain to the core sandbox; never construct a second fake home in one process."""
import importlib.util
import os
import pathlib
import sys

CORE = pathlib.Path(os.environ.get('CODEXPOOL_CORE', pathlib.Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(CORE / 'tests'))
if 'codexpool_test_helpers' in sys.modules:
    core = sys.modules['codexpool_test_helpers']
else:
    spec = importlib.util.spec_from_file_location('codexpool_test_helpers', CORE / 'tests/_helpers.py')
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core
    spec.loader.exec_module(core)
globals().update({name: value for name, value in vars(core).items() if not name.startswith('_')})
addon = next(a for a in cp.ADDONS if a.id == 'sienna')
sienna_pool, sienna_guard, sienna_selftest = addon.pool, addon.guard, addon.selftest
addon.desktop.DESKTOP_TEST_HOME = HOME
