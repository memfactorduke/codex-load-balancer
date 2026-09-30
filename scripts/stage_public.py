#!/opt/homebrew/bin/python3
"""Stage the public codexpool tree from a checkout, then prove it's publishable. Uploads nothing.

usage: stage_public.py <checkout> <stage dir> [--without-workflows]
Copies the checkout including add-on source, excluding runtime state, caches and LOCAL.md; --without-workflows also leaves out
.github/workflows (for a gh token without the workflow scope). Then runs publish_tree's scans and the core and add-on test
suites inside the staged copy. Exits non-zero on any problem.
"""
import pathlib, shutil, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_tree  # noqa: E402

CACHES = {'__pycache__', '.ruff_cache', '.venv', '.pytest_cache', '.git', '.DS_Store'}
ROOT_RUNTIME = publish_tree.RUNTIME_PATHS | {'lanes.json', 'claude'}

def ignored(src, directory, names):
    rel = pathlib.Path(directory).relative_to(src)
    excluded = set(CACHES)
    if rel == pathlib.Path('.'):
        excluded.update(ROOT_RUNTIME)
    if rel == pathlib.Path('bin'):
        excluded.update({'versions', 'current', 'claude-current', '.current.tmp', '.claude-current.tmp'})
    if rel == pathlib.Path('lanes'):
        excluded.update({'secrets', 'bridge.json'})
    return [name for name in names if name in excluded or name.endswith('.pyc')]


def main():
    src, stage = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve()
    if stage.exists():
        sys.exit(f'{stage} exists; pick a new stage dir')
    shutil.copytree(src, stage, ignore=lambda d, names: ignored(src, d, names))
    workflows = stage / '.github' / 'workflows'
    if workflows.exists() and '--without-workflows' in sys.argv[3:]:
        shutil.rmtree(workflows)
        print('left out .github/workflows (push it once the gh token has the workflow scope)')
    paths, hits = publish_tree.scan(stage)
    if hits:
        sys.exit('scan failed:\n  ' + '\n  '.join(hits[:40]))
    print(f'scan clean over {len(paths)} files (secrets and personal data)')
    r = subprocess.run(['sh', 'tests/run_all.sh'], cwd=stage,
                       capture_output=True, text=True)
    summary = [line for line in (r.stdout + r.stderr).splitlines() if line.startswith(('Ran ', 'OK', 'FAILED'))]
    print('core and add-on suites in the staged tree: ' + ' · '.join(summary))
    if r.returncode:
        print(r.stdout + r.stderr)
        sys.exit('core and add-on suites failed in the staged tree')
    print(f'staged and verified: {stage}')


if __name__ == '__main__':
    main()
