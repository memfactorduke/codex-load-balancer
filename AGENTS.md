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

If `~/.codexpool/LOCAL.md` exists, read it first: the owner's notes about this particular install (which seat
is which account, local decisions, history). It is never committed and `install` never overwrites it.

## What runs

Labels are the defaults; the real ones are in `~/.codexpool/settings.json`.

| Component | What | Where | launchd label |
|---|---|---|---|
| Pool | CLIProxyAPI built from upstream source + `build/codexpool_gate.go`, on `127.0.0.1:<port>` (8319) | `bin/current/cli-proxy-api`, `config.yaml` | `com.codexpool.pool` |
| Guard | `codexpool guard` every 60 s: usage and reset polls, credit parking, healing, notifications, `state/status.json`, `state/history.jsonl` | `bin/codexpool` | `com.codexpool.guard` |
| Menu bar | Native PyObjC app; reads status and history files only | `menubar/codexpool_menubar.py`, `menubar/SPEC.md` | `com.codexpool.menubar` |
| CLI | `codexpool …`, standard-library Python | `bin/codexpool`, wrapper at `~/.local/bin/codexpool` | none |

The Codex app points at the pool with one line in `~/.codex/config.toml`:
`openai_base_url = "http://127.0.0.1:8319/v1"`.

## Invariants: never break these

1. **Nothing custom in the request path.** Only CLIProxyAPI sits between Codex and OpenAI. Don't add a proxy,
   don't rewrite Codex payloads, don't mutate tool schemas. The gate is the one allowed change to upstream, and
   it only accepts or rejects.
2. **The Codex config stays minimal.** Never add `model_provider` (it hides every existing thread) or
   `model_catalog_json` (it freezes the model picker) to `~/.codex/config.toml`.
3. **Never copy, print or move token material.** Seat files in `~/.codexpool/auth/` and `~/.codex/auth.json` hold
   rotating refresh tokens; two holders of one copy sign each other out. Seats are added only with
   `codexpool login`. codexpool never prints tokens and reaches chatgpt.com only through the pool's `api-call`
   with `$TOKEN$`; keep it that way. (The single-seat refresh response embeds tokens: print only its status.)
4. **The pool stays loopback-only and gated.** `host: "127.0.0.1"`, `api-keys: []` and the origin gate go
   together. Don't open the port, and don't remove or weaken the gate.
5. **The menu bar app reads files only.** No Keychain, no network, no management API. A locked Keychain pops
   password dialogs.
6. **Resets are redeemed, never bought.** `codexpool reset` uses only the banked-credit `consume` endpoint with
   an idempotent `redeem_request_id`. Never add a call to a purchase flow.
7. **The guard's hardened logic stays behaviourally identical** unless the change is the point: state model,
   credit parking (record written before the seat is disabled), healing, selftest journal and recovery, upgrade
   rollback, gate self-test.
8. **`bin/codexpool` keeps Python 3.9 syntax** so `install` and `uninstall` run on a fresh Mac's
   `/usr/bin/python3`. It also stays standard-library only.
9. **Settings are explicit.** A new setting goes into `SETTINGS_DEFAULTS`, `examples/settings.json`, the README's
   settings table and, if the menu bar needs it, the app. An unknown key must keep stopping every command.
10. **Don't hard-code model names** in docs or instructions; use the model in `~/.codex/config.toml`.
11. **No personal data in the repository.** No emails, account or workspace ids, real seat names, company names,
    `/Users/<name>` paths or personal launchd labels, in code, docs, examples or screenshots. Screenshots come
    from the synthetic data in `docs/images/demo/`.

## How to change things safely

| Change | Do this | Then verify |
|---|---|---|
| `bin/codexpool` | Edit, then `python3 -m py_compile bin/codexpool && python3 bin/codexpool --help >/dev/null`. The guard runs this file every minute; a syntax error stops the guard (the pool keeps serving). | On an install: `./bin/codexpool install`, then `codexpool guard && codexpool doctor` |
| Install / uninstall | Dry-run in a throwaway home: `HOME=$(mktemp -d) python3 bin/codexpool install --dry-run` (a live pool on the same port shows up as a port conflict; that is expected) | Every step prints; nothing is written |
| Pool settings (`config.yaml`) | Edit `~/.codexpool/config.yaml`; CLIProxyAPI hot-reloads most keys. Defaults for new installs live in `examples/config.yaml`. | `codexpool logs -n 20` shows the reload; `codexpool doctor` |
| Seat names, sizes, reserve | `codexpool label`, `codexpool weight`, `codexpool reserve` (they write `seats.json`) | `codexpool status` |
| Hide or show models | `oauth-excluded-models: codex:` in `config.yaml` (exact names) | `curl -s http://127.0.0.1:8319/v1/models` |
| Upgrade CLIProxyAPI | `codexpool upgrade <version\|latest>` (builds from source with the gate, self-tests, switches, rolls back on failure) | `codexpool doctor` shows `+gate.<hash>` and the gate probe 200/403 |
| The gate (`build/codexpool_gate.go`) | Edit, run `./bin/codexpool install` to copy it, then `codexpool upgrade <current version>`; the build is keyed by the gate's hash, so a changed gate is a new build | The 11-case gate self-test in the upgrade output; `codexpool doctor` |
| Menu bar app | Edit `menubar/codexpool_menubar.py`; render it with `~/.codexpool/.venv/bin/python menubar/codexpool_menubar.py --snapshot /tmp/mb.png --appearance dark` (add `--status docs/images/demo/status-regular.json --history docs/images/demo/history-regular.jsonl` for demo data, or `status-used.json` for the `"display": "used"` look); restart with `launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar` | Look at the PNGs; the live item |
| Screenshots | `~/.codexpool/.venv/bin/python docs/images/demo/render.py` rebuilds everything in `docs/images/` from `docs/images/demo/` | Look at every image |
| Add a seat | `codexpool login <Label> --priority <n>` (`--no-open` + private window for other accounts), then `codexpool weight <Label> <n>` if needed | `codexpool status --live` |

After any change to an install: **`codexpool doctor` must end with `OK`.**

## Tests you can run

- `codexpool doctor`: whole-system health (pool process, running build, gate probe, key, Codex config, seats,
  guard, menu bar, recent errors). Read-only.
- `codexpool status --json`: the same data the menu bar reads.
- `codexpool install --dry-run`: the full install plan, without changes.
- `codexpool selftest <from> <to> --compact`: moves a real throwaway thread between two seats with encrypted
  reasoning and a native compaction. It spends a little quota and pauses the other seats for 1 to 3 minutes, so
  run it only when the user isn't mid-task, and only with their go-ahead.
- `codexpool upgrade …` and `codexpool build …` run the gate self-test automatically before a build is used.

Commands that change a live install (`enable`, `disable`, `priority`, `reset`, `remove`, `restart`, `upgrade`,
`install`, `uninstall`, `selftest`) need the user's say-so. `reset` spends one of their banked resets.

## Undo

- `codexpool uninstall --yes` stops the agents and restores the recorded Codex config keys; `~/.codexpool` stays.
- Previous CLIProxyAPI builds stay in `bin/versions/`; `codexpool upgrade <older version>` switches back.
- `state/codex-config.pre-install-<date>.toml` is the user's Codex config as it was before each install that
  recorded it (`state/install.json` names the current one).

## Before every commit

The `.gitignore` keeps runtime state out even when the checkout is `~/.codexpool`. Still, scan what you stage.
Both commands must print nothing:

```sh
git diff --cached | grep -nE 'eyJ[A-Za-z0-9_-]{20,}|"(refresh|access|id)_token"[[:space:]]*:|\$2[aby]\$|sk-[A-Za-z0-9]{20,}'
git diff --cached | grep -nE '/Users/[A-Za-z]|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}'
```

The first catches tokens and hashes, the second home-directory paths and email addresses.
