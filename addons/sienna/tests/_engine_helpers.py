"""Explicit engine imports layered on the core's single test sandbox."""
from _helpers import addon, load_bridge as load_core_bridge

le = addon.lane_engine


_extension = None


def load_bridge():
    global _extension
    if _extension is not None:
        return _extension
    core = load_core_bridge()
    extension = core.load_extension_module(addon.bridge_extension)
    # Generic helpers used by the old shared tests stay owned by the core.
    for name, value in vars(core).items():
        if not hasattr(extension, name):
            setattr(extension, name, value)
    _extension = extension
    return extension
