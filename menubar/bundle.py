"""Build subpool.app around the installed CPython and PyObjC runtime; no compiler or extra packages."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile


def runtime_description(python):
    code = ('import json,sys,sysconfig; print(json.dumps(dict(base=sys.base_prefix, '
            'executable=sys._base_executable, site=sysconfig.get_path("purelib"))))')
    result = subprocess.run([str(python), '-c', code], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def launcher_text(root, runtime):
    q = shlex.quote
    return ('#!/bin/sh\n# subpool.app launcher\n'
            'bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
            'export PYTHONHOME=' + q(runtime['base']) + '\n'
            'export PYTHONPATH=' + q(runtime['site']) + '\n'
            'export PYTHONNOUSERSITE=1\n'
            'exec "$bundle_dir/MacOS/subpool" ' + q(str(Path(root) / 'menubar' / 'subpool_settings.py'))
            + ' "$@"\n')


def check_bundle(app, runtime):
    env = dict(os.environ, PYTHONHOME=runtime['base'], PYTHONPATH=runtime['site'], PYTHONNOUSERSITE='1')
    code = ('from AppKit import NSApplication,NSRunningApplication; '
            'a=NSApplication.sharedApplication(); a.setActivationPolicy_(2); '
            'r=NSRunningApplication.currentApplication(); '
            'assert r.localizedName()=="subpool.app", r.localizedName(); '
            'assert r.bundleIdentifier()=="com.subpool.settings", r.bundleIdentifier()')
    result = subprocess.run([str(app / 'Contents' / 'MacOS' / 'subpool'), '-c', code],
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('subpool.app could not use the configured Python runtime: ' + result.stderr[-1200:])


def build_bundle(root, python, version):
    root = Path(root)
    runtime = runtime_description(python)
    executable = Path(runtime['executable']).resolve()
    identity = dict(runtime, version=version, executable_mtime=executable.stat().st_mtime_ns,
                    launcher=launcher_text(root, runtime))
    apps = root / 'apps'
    app = apps / 'subpool.app'
    stamp = app / 'Contents' / 'Resources' / 'runtime.json'
    if stamp.exists() and json.loads(stamp.read_text()) == identity:
        check_bundle(app, runtime)
        return app
    apps.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.subpool-app-', dir=apps))
    candidate = work / 'subpool.app'
    contents = candidate / 'Contents'
    mac = contents / 'MacOS'
    resources = contents / 'Resources'
    mac.mkdir(parents=True)
    resources.mkdir()
    try:
        # Copy only the interpreter executable. Libraries and packages stay in the existing runtime.
        shutil.copy2(executable, mac / 'subpool')
        (contents / 'lib').symlink_to(Path(runtime['base']) / 'lib', target_is_directory=True)
        if (Path(runtime['base']) / 'Python').exists():
            (contents / 'Python').symlink_to(Path(runtime['base']) / 'Python')
        launch = mac / 'launch'
        launch.write_text(identity['launcher'])
        launch.chmod(0o755)
        info = {'CFBundleName': 'subpool.app', 'CFBundleDisplayName': 'subpool.app',
                'CFBundleIdentifier': 'com.subpool.settings', 'CFBundleExecutable': 'launch',
                'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': version,
                'CFBundleVersion': version, 'NSHighResolutionCapable': True}
        icon = root / 'docs' / 'images' / 'subpool-mark.png'
        if icon.exists():
            iconset = work / 'subpool.iconset'
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    name = 'icon_' + str(size) + 'x' + str(size) + ('@2x' if scale == 2 else '') + '.png'
                    subprocess.run(['/usr/bin/sips', '-z', str(size * scale), str(size * scale), str(icon),
                                    '--out', str(iconset / name)], check=True, capture_output=True)
            subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o',
                            str(resources / 'subpool.icns')], check=True, capture_output=True)
            info['CFBundleIconFile'] = 'subpool.icns'
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        (resources / 'runtime.json').write_text(json.dumps(identity, indent=2) + '\n')
        check_bundle(candidate, runtime)
        previous = work / 'previous.app'
        if app.exists():
            app.rename(previous)
        try:
            candidate.rename(app)
        except OSError:
            if previous.exists():
                previous.rename(app)
            raise
        return app
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    import sys
    print(build_bundle(Path(sys.argv[1]), sys.executable, sys.argv[2]))
