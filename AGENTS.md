# AGENTS.md: working on codexpool

This file is for coding agents working on this repository or on a live install. Read it and the
[README](README.md) before changing anything; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains why things
are the way they are.

**On a live install, the Codex app, the Codex CLI and any other agent send their model traffic through the pool.
If you break it, Codex stops working for the user, possibly including you.**

## Two places

- **The repository** (a git checkout anywhere). Code and docs only.
- **The install**, always `~/.codexpool`. `./bin/codexpool install` copies the code files there (unless the
  checkout *is* `~/.codexpool`) and keeps all runtime state there: seat logins, config, status, logs, builds.
  The launchd agents run the copy in `~/.codexpool`, not the checkout.

After editing the repository, `./bin/codexpool install` from the checkout updates the install. It copies the
code, re-renders changed launchd plists and changes nothing else that is already right.

The one-line installer, `install.sh`, is not a third place: it downloads a release (or `--version TAG`) into a
temporary folder, runs that copy's `bin/codexpool install`, opens the Setup assistant on a first install and
deletes the folder. Install logic belongs in `bin/codexpool install`; `install.sh` only checks the Mac, finds a
Python, downloads and hands over.

If `~/.codexpool/LOCAL.md` exists, read it first: the owner's notes about this particular install (which seat
is which account, local decisions, history). It is never committed and `install` never overwrites it.

## What runs

Labels are the defaults; the real ones are in `~/.codexpool/settings.json`.

| Component | What | Where | launchd label |
|---|---|---|---|
| Pool | CLIProxyAPI built from upstream source + `build/codexpool_gate.go`, on `127.0.0.1:<port>` (8319) | `bin/current/cli-proxy-api`, `config.yaml` | `com.codexpool.pool` |
| Guard | `codexpool guard` every 60 s: usage and reset polls, credit parking, healing, the fill order under `"balancing": "reset"`, notifications, `state/status.json`, `state/history.jsonl` | `bin/codexpool` | `com.codexpool.guard` |
| Menu bar | Native PyObjC app; reads status and history files only | `menubar/codexpool_menubar.py`, `menubar/SPEC.md` | `com.codexpool.menubar` |
| Settings window | Native PyObjC window (Overview, Seats, Balancing, Lanes, General, Health, About) and the Setup assistant, in a process of its own. Opened from the menu bar ("Settings…", "Add a ChatGPT account…"; the Setup assistant by itself on a first run with no seats), by `codexpool gui [PANE]` and by `install.sh` on a first install. Reads `state/status.json` and the JSON of `codexpool doctor --json`, `codexpool lane list --json`, `codexpool lane providers --json`, `codexpool lane models PROVIDER --json` and `codexpool version`; every change it makes is a `codexpool …` command run in the background (a provider key goes to `codexpool lane key NAME -` on stdin) | `menubar/codexpool_settings.py`, the GUI section of `menubar/SPEC.md` | none |
| CLI | `codexpool …`, standard-library Python | `bin/codexpool`, wrapper at `~/.local/bin/codexpool` | none |
| Lane bridge (optional) | Standard-library Python on `127.0.0.1:<bridge_port>` (8320): an upstream behind the pool that adapts requests for lane members on providers such as OpenCode; runs only when `lanes/bridge.json` exists | `lanes/bridge.py`, `lanes/bridge.json`, `lanes/secrets/` | `com.codexpool.bridge` |

The Codex app points at the pool with one line in `~/.codex/config.toml`:
`openai_base_url = "http://127.0.0.1:8319/v1"`.

Subagent lanes are optional and defined in `~/.codexpool/lanes.json`. `codexpool lane apply` generates a marked
block in `config.yaml`, the bridge's `lanes/bridge.json`, role files in `~/.codex/agents/` and a marked block in
`~/.codex/AGENTS.md`. See [docs/LANES.md](docs/LANES.md).

## Invariants: never break these

1. **Nothing custom in the seat request path.** Only CLIProxyAPI sits between Codex and OpenAI. Don't add a
   proxy, don't rewrite Codex payloads, don't mutate tool schemas. The gate is the one allowed change to
   upstream, and it only accepts or rejects. Lanes are the one exception, scoped to lane models: the CLIProxyAPI
   payload rules and the local bridge that `codexpool lane apply` generates. Seat traffic never touches either:
   every payload rule names a lane's own aliases only (apply refuses a lane name the pool already serves, and
   doctor checks it; `lane test` refuses a member alias the same way before it offers one), and the bridge is an
   upstream behind the pool, never in front of it.
2. **The Codex config stays minimal.** Never add `model_provider` (it hides every existing thread) or
   `model_catalog_json` (it freezes the model picker) to `~/.codex/config.toml`.
3. **Never copy, print or move token material.** Seat files in `~/.codexpool/auth/` and `~/.codex/auth.json` hold
   rotating refresh tokens; two holders of one copy sign each other out. Seats are added only with
   `codexpool login`. codexpool never prints tokens and reaches chatgpt.com only through the pool's `api-call`
   with `$TOKEN$`; keep it that way. (The single-seat refresh response embeds tokens: print only its status.)
   The same goes for lane credentials: the xAI login in `auth/` comes only from `codexpool lane login xai`, and
   provider keys in `lanes/secrets/` only from `codexpool lane key`, which never prints them.
4. **The pool stays loopback-only and gated.** `host: "127.0.0.1"`, `api-keys: []` and the origin gate go
   together. Don't open the port, and don't remove or weaken the gate.
5. **The menu bar app and the Settings window read files only.** No Keychain, no network, no management API. A
   locked Keychain pops password dialogs. They change things only by running `codexpool …` commands in the
   background; the main thread never waits for one.
6. **Resets are redeemed, never bought.** `codexpool reset` uses only the banked-credit `consume` endpoint with
   an idempotent `redeem_request_id`. Never add a call to a purchase flow.
7. **The guard's hardened logic stays behaviourally identical** unless the change is the point: state model,
   credit parking (record written before the seat is disabled), healing, selftest journal and recovery, upgrade
   rollback, gate self-test. One deliberate, tested extension: with `"balancing": "reset"` the guard's balancing
   step (after healing) sets the regular seats' priorities soonest weekly reset first, through the management
   API and only when the pool's order is wrong, keeps reserve seats last, logs one line and never notifies; the
   first pass back on `"priority"` restores your order from `seats.json` (`manual_priority`). A fill order the
   user changes (`order`, `priority`, `reserve` leave `you_reordered` in guard.json) is treated like that step's
   reorder: no "Codex now on" while the previous seat still serves. With `"balancing": "priority"`, nothing to
   restore and no reorder of yours, the guard behaves exactly as before.
8. **`bin/codexpool` keeps Python 3.9 syntax** so `install` and `uninstall` run on a fresh Mac's
   `/usr/bin/python3`. It also stays standard-library only.
9. **Settings are explicit.** A new setting goes into `SETTINGS_DEFAULTS`, `examples/settings.json`, the README's
   settings table and, if the menu bar needs it, the app. An unknown key must keep stopping every command.
10. **Don't hard-code model names** in docs or instructions; use the model in `~/.codex/config.toml`. Lane
    definitions and generated lane instructions are the exception: `lanes.json`, `examples/lanes.json`,
    `docs/LANES.md`, the description of the providers it adapts in `lanes/bridge.py`, the generated role files
    and the generated `~/.codex/AGENTS.md` block name models. No other doc or instruction does.
11. **No personal data in the repository.** No emails, account or workspace ids, real seat names, company names,
    `/Users/<name>` paths or personal launchd labels, in code, docs, examples or screenshots. Screenshots come
    from the synthetic data in `docs/images/demo/`.

## How to change things safely

| Change | Do this | Then verify |
|---|---|---|
| `bin/codexpool` | Edit, then `python3 -m py_compile bin/codexpool && python3 bin/codexpool --help >/dev/null`. The guard runs this file every minute; a syntax error stops the guard (the pool keeps serving). | On an install: `./bin/codexpool install`, then `codexpool guard && codexpool doctor` |
| Install / uninstall | Dry-run in a throwaway home: `HOME=$(mktemp -d) python3 bin/codexpool install --dry-run` (a live pool on the same port shows up as a port conflict; that is expected) | Every step prints; nothing is written |
| Pool settings (`config.yaml`) | Edit `~/.codexpool/config.yaml`; CLIProxyAPI hot-reloads most keys. Defaults for new installs live in `examples/config.yaml`. | `codexpool logs -n 20` shows the reload; `codexpool doctor` |
| Seat names, sizes, reserve | `codexpool label`, `codexpool weight`, `codexpool reserve` (they write `seats.json`; `reserve` also moves the seat last in the fill order when it is not) | `codexpool status` |
| Fill order and balancing | `codexpool order SEAT [SEAT ...]` (priorities from 1000 down, reserve seats last; also recorded as `manual_priority` in `seats.json`, as `codexpool priority` does), `codexpool set balancing priority\|reset`. Under `reset` the guard owns the pool's priorities: change your order with `order`, never by hand, and expect it to take effect once you switch back | `codexpool status` (its first line names the mode); `codexpool logs --guard` for "guard: balancing" lines |
| Hide or show models | `oauth-excluded-models: codex:` in `config.yaml` (exact names) | `curl -s http://127.0.0.1:8319/v1/models` |
| Lanes | Edit `~/.codexpool/lanes.json` (or use `codexpool lane add` / `lane edit` / `lane remove`, each with `--dry-run` first), then `codexpool lane apply --dry-run` and `codexpool lane apply`. Never hand-edit what apply generates: the lanes block in `config.yaml`, `lanes/bridge.json`, the role files in `~/.codex/agents/`, the lanes block in `~/.codex/AGENTS.md`. Keys only through `codexpool lane key` (`-` reads stdin). `codexpool lane providers` and `lane models PROVIDER` are read-only (`lane models` for an OpenCode provider sends the stored key to that provider's `/models`). | `codexpool lane test` (with the user's go-ahead: it spends quota), then `codexpool doctor` |
| Lane code in `bin/codexpool` | Test in a throwaway home, and only with `--dry-run` or pure render functions. `HOME=$(mktemp -d)` alone is not enough: launchd labels and loopback ports are shared with the live install, so first write a `settings.json` there that moves `port`, `bridge_port` and all four `*_label` settings off the live ones. | The diff shows the expected block, bridge config, role files and `AGENTS.md` block; nothing is written |
| Upgrade CLIProxyAPI | `codexpool upgrade <version\|latest>` (builds from source with the gate, self-tests, switches, rolls back on failure) | `codexpool doctor` shows `+gate.<hash>` and the gate probe 200/403 |
| The gate (`build/codexpool_gate.go`) | Edit, run `./bin/codexpool install` to copy it, then `codexpool upgrade <current version>`; the build is keyed by the gate's hash, so a changed gate is a new build | The 11-case gate self-test in the upgrade output; `codexpool doctor` |
| Menu bar app | Edit `menubar/codexpool_menubar.py`; render it with `~/.codexpool/.venv/bin/python menubar/codexpool_menubar.py --snapshot /tmp/mb.png --appearance dark` (add `--status docs/images/demo/status-regular.json --history docs/images/demo/history-regular.jsonl` for demo data, or `status-used.json` for the `"display": "used"` look); restart with `launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar` | Look at the PNGs; the live item |
| Settings window and Setup assistant | Edit `menubar/codexpool_settings.py` (spec: the GUI section of `menubar/SPEC.md`). Test it only through snapshots, which show no window and run no command: `~/.codexpool/.venv/bin/python menubar/codexpool_settings.py --snapshot /tmp/settings.png --pane overview --appearance dark --status docs/images/demo/status-regular.json --doctor docs/images/demo/doctor.json --lanes docs/images/demo/lanes.json` for each pane you touched (`overview`, `seats`, `balancing`, `lanes`, `general`, `health`, `about`, `setup-welcome`, `setup-accounts`, `setup-signin`, `setup-added`, `setup-again`, `setup-done`, and the Lanes sheets `lanes-edit`, `lanes-new`, `lanes-model`, `lanes-model-key`, `lanes-key`, `lanes-signin`), in both appearances. Never click its buttons to test: each runs a real `codexpool` command against the live install. The JSON of `doctor --json`, `lane list --json`, `lane providers --json`, `lane models PROVIDER --json` and `version`, and the `status.json` fields it reads (`pool.balancing`, each seat's `order_reason`), are its interface; change both sides together. | Look at the PNGs; on an install, after `./bin/codexpool install`, quit an open window (⌘Q) and run `codexpool gui` |
| Screenshots | `~/.codexpool/.venv/bin/python docs/images/demo/render.py` rebuilds everything in `docs/images/` from `docs/images/demo/`; `site/assets/img/` holds copies (see `site/README.md`) | Look at every image |
| Menu bar settings | `codexpool set display left\|used`, `codexpool set headline all\|regular`, `codexpool set balancing priority\|reset` (writes that key in `settings.json`, keeps the rest, rewrites `state/status.json`); `codexpool set` alone prints all three | The menu bar within seconds; a balancing change on the guard's next pass |
| `install.sh` | Edit, then `/bin/bash -n install.sh`, `shellcheck install.sh` (CI runs it) and `HOME=$(mktemp -d) /bin/bash install.sh --dry-run --no-gui`, which downloads and changes nothing. It must run on macOS's `/bin/bash` 3.2 under `set -euo pipefail`: no `mapfile`, no associative arrays, no `${var,,}`, and no expanding an array that can be empty. | The dry run prints every step; `python3 -m unittest discover -s tests` (it runs the Codex app check) |
| The website (`site/`) | Static files, no build step: edit `site/index.html`, `site/assets/style.css`, `site/assets/site.js`; preview with `python3 -m http.server 8000 --directory site`. `.github/workflows/pages.yml` deploys `site/` to GitHub Pages on pushes to main that touch it once the repository variable `PAGES_ENABLED` is `true`, and whenever it is run by hand from the Actions tab (going live once: Settings → Pages → Source: GitHub Actions, then `gh variable set PAGES_ENABLED --body true`). Screenshots only from the demo data; the footer's disclaimers stay. | Headless Chrome at 1440 px and 390 px wide, light and dark (`?theme=light`, `?theme=dark`); look at each render |
| Tests (`tests/`) | Standard-library `unittest`, Python 3.9+. Every module imports `tests/_helpers.py` first: it builds a throwaway home with stubbed `launchctl`, `security`, `osascript`, `mdfind` and `pbcopy` (sign-in links never reach the real clipboard), random free ports and test launchd labels, and refuses to run unless `bin/codexpool` points at it. New behaviour in `bin/codexpool` or `install.sh` gets a test there. | `python3 -m unittest discover -s tests` passes |
| Add a seat | `codexpool setup` (guided, in the terminal), the Setup assistant (`codexpool gui setup-welcome`), or `codexpool login <Label> --priority <n>` (`--no-open` + private window for other accounts; the link is also on the clipboard; without `--priority` a new seat goes after the others and before the reserve), then `codexpool weight <Label> <n>` if needed | `codexpool status --live` |

After any change to an install: **`codexpool doctor` must end with `OK`.**

## Tests you can run

- `python3 -m unittest discover -s tests`, from the checkout: the unit tests (also under Python 3.9). They run in
  a throwaway home with stubbed macOS tools, so they touch nothing live and need no network.
- `codexpool doctor`: whole-system health (pool process, running build, gate probe, key, Codex config, seats,
  guard, menu bar, recent errors, and lanes when `lanes.json` exists). Read-only. `codexpool doctor --json`
  prints the same checks as JSON, which the Settings window reads; the exit status is the same.
- `codexpool status --json`: the same data the menu bar reads.
- `codexpool version`: the codexpool version (`VERSION` in `bin/codexpool`).
- `codexpool install --dry-run`: the full install plan, without changes. `bash install.sh --dry-run` does the
  same for the one-line installer.
- `codexpool lane` (or `codexpool lane list`, `--json` for the Settings window's view) lists lanes and their
  members' states; `codexpool lane apply --dry-run` prints every change `lane apply` would make as a diff, and
  `lane add ... --dry-run`, `lane edit ... --dry-run` and `lane remove ... --dry-run` do the same for a
  `lanes.json` change. `codexpool lane providers [--json]` says which providers are ready and
  `codexpool lane models PROVIDER [--json]` lists a provider's models. All of these are read-only.
- `codexpool selftest <from> <to> --compact`: moves a real throwaway thread between two seats with encrypted
  reasoning and a native compaction. It spends a little quota and pauses the other seats for 1 to 3 minutes, so
  run it only when the user isn't mid-task, and only with their go-ahead.
- `codexpool lane test [<lane>] [--member <id>] [--compaction]`: spawns a real lane subagent through Codex for
  each member and then the lane, and checks that it edited files with `apply_patch`, ran on the expected model
  and hit no argument-parse failures; `--compaction` also forces a compaction. To pin members it adds their
  aliases (`<lane>-<id>`) to the lanes block in `config.yaml` while it runs and withdraws them when it ends; if
  it is killed, doctor warns about the leftovers and `codexpool lane apply` removes them. It spends
  lane-provider quota and a little seat quota, so run it only with the user's go-ahead.
- `codexpool upgrade …` and `codexpool build …` run the gate self-test automatically before a build is used.

Commands that change a live install (`setup`, `login`, `set`, `label`, `weight`, `enable`, `disable`, `priority`,
`order`, `reserve`, `reset`, `remove`, `refresh`, `restart`, `upgrade`, `install`, `uninstall`, `selftest`,
`lane add`, `lane edit`, `lane remove`, `lane apply`, `lane test`, `lane login`, `lane key`) need the user's
say-so, and so does every
button in the Settings window, which runs one of them. `reset` spends one of their banked resets; `lane test`
spends lane-provider quota.

## Undo

- `codexpool uninstall --yes` stops the agents and restores the recorded Codex config keys; `~/.codexpool` stays.
- Previous CLIProxyAPI builds stay in `bin/versions/`; `codexpool upgrade <older version>` switches back.
- `state/codex-config.pre-install-<date>.toml` is the user's Codex config as it was before each install that
  recorded it (`state/install.json` names the current one).
- `state/config.yaml.before-lane-apply` is `config.yaml` as it was before the last `codexpool lane apply`.
  `codexpool lane remove <lane>` takes a lane out together with everything generated for it.
- `codexpool set balancing priority` hands the fill order back to the user: the guard's next pass restores the
  order recorded in `seats.json` (`manual_priority`).

## Before every commit

The unit tests must pass:

```sh
python3 -m unittest discover -s tests
```

The `.gitignore` keeps runtime state out even when the checkout is `~/.codexpool`. Still, scan what you stage.
Both commands must print nothing:

```sh
git diff --cached | grep -nE 'eyJ[A-Za-z0-9_-]{20,}|"(refresh|access|id)_token"[[:space:]]*:|\$2[aby]\$|sk-[A-Za-z0-9]{20,}'
git diff --cached | grep -nE '/Users/[A-Za-z]|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}'
```

The first catches tokens and hashes, the second home-directory paths and email addresses.

## Releasing

1. In `CHANGELOG.md`, move the entries under "Unreleased" into a new `## [X.Y.Z] - YYYY-MM-DD` section, and at
   the bottom add its link and start the `[Unreleased]` compare link at `vX.Y.Z` (semantic versioning: a new
   command or setting is a minor release).
2. Set `VERSION` in `bin/codexpool` to `X.Y.Z`. The unit tests check that it matches the newest CHANGELOG release.
3. Run everything in "Before every commit", commit, and push to main.
4. Tag and publish the release: `git tag vX.Y.Z && git push origin vX.Y.Z`, then
   `gh release create vX.Y.Z --title "codexpool X.Y.Z" --notes "<that CHANGELOG section>"`. `install.sh` installs
   the latest published release (a bare tag is not enough), so from then on the one-liner and its upgrades get it.
