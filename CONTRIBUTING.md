# Contributing to codexpool

Thanks for helping. codexpool sits in the path of every Codex request on the Macs that run it, so changes are
held to a careful bar. This page covers how to set up, what never to break, how to test and what a pull
request needs.

## Before you start

- Read [AGENTS.md](AGENTS.md), and above all its **invariants**. It is written for coding agents, and it is
  equally the rulebook for people. A change that breaks an invariant is declined however useful it is, unless
  changing that rule is the point of the change and the pull request says why.
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for why things are built the way they are, and the
  [README](README.md) for what the product promises its users.
- For anything larger than a bug fix, open an issue first so we can agree on the approach before you write it.

## Set up from a checkout

You need a Mac with macOS 13 or later and Python 3.11+ (or [uv](https://docs.astral.sh/uv/)). The Codex app
and two ChatGPT seats are only needed to test against a real install.

```sh
git clone https://github.com/memfactorduke/codex-load-balancer.git
cd codex-load-balancer
./bin/codexpool install --dry-run     # prints the full install plan and changes nothing
```

The checkout and the install are separate. You edit the checkout; `./bin/codexpool install` copies the code into
`~/.codexpool`, which is what launchd runs. On a Mac where codexpool is live, **your Codex traffic goes through
it**: a syntax error in `bin/codexpool` stops the guard, which runs that file every minute, and a broken pool
stops Codex, possibly including the agent you are working with. Test in a throwaway home first:

```sh
HOME=$(mktemp -d) python3 bin/codexpool install --dry-run
```

A throwaway `HOME` does not isolate launchd labels or loopback ports, which the live install also uses. For
anything beyond `--dry-run`, first write a `settings.json` in that home that moves `port`, `bridge_port` and all
four `*_label` settings off the live values (AGENTS.md, "How to change things safely").

Layout: `bin/codexpool` (the CLI and guard, one standard-library Python file), `build/codexpool_gate.go` (the
origin gate compiled into the pool), `menubar/` (the menu bar app, the Settings window and the Setup assistant,
PyObjC), `lanes/bridge.py` (the optional lane bridge), `launchd/` and `examples/` (templates the installer
renders), `install.sh` (the one-line installer), `tests/` (the unit tests), `docs/` (with the screenshots and
their synthetic data in `docs/images/`) and `site/` (the website).

## Test your change

Run what applies before you open a pull request. CI runs the first four on every pull request and every push
to main.

1. **Compile everything:**

   ```sh
   python3 -m py_compile bin/codexpool lanes/bridge.py menubar/*.py
   python3 bin/codexpool --help >/dev/null
   ```

2. **Python 3.9 syntax** for `bin/codexpool` and `lanes/bridge.py`, so that `install` and `uninstall` run on a
   fresh Mac's `/usr/bin/python3` (invariant 8):

   ```sh
   python3 -c 'import ast, sys; [ast.parse(open(f).read(), f, feature_version=(3, 9)) for f in sys.argv[1:]]' \
       bin/codexpool lanes/bridge.py
   ```

3. **The installer script:** `/bin/bash -n install.sh`, `shellcheck install.sh` if you have it (CI runs it),
   and `HOME=$(mktemp -d) /bin/bash install.sh --dry-run --no-gui`, which prints the plan and downloads and
   changes nothing. It must keep working with the `/bin/bash` 3.2 that macOS ships.

4. **Unit tests** (standard library, no network; they build a throwaway home with stubbed `launchctl`,
   `security`, `osascript` and `mdfind`, so they never touch a live install):

   ```sh
   python3 -m unittest discover -s tests
   uv run --python 3.9 --no-project python -m unittest discover -s tests   # optional: the oldest Python supported
   ```

   A change to `bin/codexpool` or `install.sh` comes with a test in `tests/`.

5. **A real install**, when your change touches install, the guard, the pool or lanes: `./bin/codexpool install`,
   then `codexpool doctor`, which **must end with `OK`**. Tests that spend quota (`codexpool selftest`,
   `codexpool lane test`) are for when you have decided to spend it.

6. **Snapshots for any UI change.** Render every state you touched in light and dark from the demo data in
   `docs/images/demo/`, look at each image, and attach them to the pull request:

   ```sh
   PY=~/.codexpool/.venv/bin/python          # any Python 3.11+ with PyObjC
   $PY menubar/codexpool_menubar.py --snapshot /tmp/mb.png --appearance dark \
       --status docs/images/demo/status-regular.json --history docs/images/demo/history-regular.jsonl
   $PY menubar/codexpool_settings.py --snapshot /tmp/overview.png --pane overview --appearance light \
       --status docs/images/demo/status-regular.json --doctor docs/images/demo/doctor.json \
       --lanes docs/images/demo/lanes.json
   $PY docs/images/demo/render.py           # rebuilds every image in docs/images/
   ```

   Snapshot mode never runs a command. Don't test the Settings window by clicking its buttons: each one runs a
   real `codexpool` command. Screenshots come only from the synthetic data in `docs/images/demo/`, never from a
   real install. A changed screenshot that the website shows also goes into `site/assets/img/` (see
   [site/README.md](site/README.md)).

## Before every commit

`python3 -m unittest discover -s tests` must pass, and both scans must print nothing. The first catches tokens
and hashes, the second home-directory paths and email addresses:

```sh
git diff --cached | grep -nE 'eyJ[A-Za-z0-9_-]{20,}|"(refresh|access|id)_token"[[:space:]]*:|\$2[aby]\$|sk-[A-Za-z0-9]{20,}'
git diff --cached | grep -nE '/Users/[A-Za-z]|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}'
```

No personal data goes into the repository (invariant 11): no emails, account or workspace ids, real seat
names, company names, `/Users/<name>` paths or personal launchd labels, in code, docs, examples, screenshots or
test fixtures. CI runs the same scans over the whole tree.

## Pull requests

- Keep each pull request to one change, and say what it changes for someone using codexpool.
- Fill in the checklist in the pull request template.
- Update the docs in the same pull request: the README for anything a user sees, `examples/settings.json` and
  the README's settings table for a new setting (invariant 9), AGENTS.md when agents need to know, and
  CHANGELOG.md under "Unreleased".
- Write docs and messages in plain, exact English. Don't name specific models outside the lane files
  (invariant 10).

## License of contributions

codexpool is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). By opening a pull
request you agree that your contribution is licensed under the same license as the project.

## Conduct and security

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Please report security problems
privately, as described in [SECURITY.md](SECURITY.md), not in an issue or pull request.
