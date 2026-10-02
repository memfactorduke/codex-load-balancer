# AGENTS.md: working on the sienna add-on (the Claude pool)

Read the core's [AGENTS.md](../../AGENTS.md) first: everything there applies. This file is what the add-on adds.
`addons/sienna/` is expressly allowed in the public subpool repository, including source, docs, tests, demo
assets and patches. Claude, Anthropic, sienna and Cowork may be named in public docs and tests. Runtime state
and credentials stay local under the core publication rules. The Claude implementation lives here:
[README.md](README.md), [docs/SIENNA.md](docs/SIENNA.md), [docs/DESKTOP.md](docs/DESKTOP.md),
[docs/ENGINE-LANE.md](docs/ENGINE-LANE.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/MENUBAR.md](docs/MENUBAR.md), [docs/SPEC.md](docs/SPEC.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
and [CHANGELOG.md](CHANGELOG.md).

**On a live install with the Claude pool, Claude Code sessions started with `claude-pool` (or the optional
`claude` shim) go through it. If you are Claude Code, check `/status` for a base URL before you touch it, and work
on it only from a direct session (invariant S2).**

## What it is

| Component | What | Where | launchd label |
|---|---|---|---|
| Claude pool | A second CLIProxyAPI instance, its own build + the core gate with `CODEXPOOL_GATE_PROFILE=claude`, on `127.0.0.1:<claude_port>` (8321); installed by `subpool sienna install` (`claude` is an alias) | `bin/claude-current/cli-proxy-api`, `config-claude.yaml`, `auth-claude/`, `claude-seats.json`, logs in `claude/logs/` | `com.subpool.claude` |
| Claude launcher | POSIX sh: starts Claude Code with `ANTHROPIC_BASE_URL=http://127.0.0.1:<claude_port>` for that one process after a 300 ms probe, else direct with one warning; honours `CLAUDEPOOL=off` and `state/claude-route` | `~/.local/bin/claude-pool` (and `sienna`), `~/.subpool/shims/claude` after `subpool sienna shim install` | none |
| The add-on itself | `addon.py` (registration), `pool.py`, `guard.py`, `selftest.py`, `desktop.py`, `desktop_build.py`, `lane_engine.py`, `bridge_engine.py`, `gate_build.py` + `gate/`, `menubar_ext.py` (the `PoolUI`), `examples/config-claude.yaml`, `launchd/claude.plist.template` | `addons/sienna/` | |

Claude Code points at the Claude pool only through the launcher's environment: nothing in `~/.claude`, the shell
profiles or launchd's environment changes. See [docs/SIENNA.md](docs/SIENNA.md). The guard's Claude pass runs after
the Codex pass, only when the Claude pool's plist exists, with its own state (`claude-guard.json`,
`claude-status.json`, `claude-history.jsonl`); a failure in one pass never stops the other. The menu bar app and
the Settings window read `claude-status.json` and `claude-history.jsonl` through `menubar_ext.py` and nothing else.

## Invariants (on top of the core's)

S1. **Claude Code keeps its own login, and subpool never configures it globally.** Never set
    `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` or `apiKeyHelper` anywhere: each replaces the claude.ai login with a
    gateway credential and turns off connectors, artifacts, Claude in Chrome and voice. `ANTHROPIC_BASE_URL` is set
    only by the launcher, for the one process it starts. Never edit `~/.claude`, `~/.claude.json`, shell profiles or
    launchd's environment, and never replace or wrap `~/.local/bin/claude`; the shim lives in `~/.subpool/shims/`
    and the user adds it to `PATH`. Tests set these variables only in subprocesses with a throwaway home.
    **Never read, copy or import Claude Code's own credentials** (its Keychain item, `~/.claude`, `~/.claude.json`):
    accounts go into `auth-claude/` only through `subpool sienna login`, and usage and profile calls go through
    the Claude pool's `api-call` with `$TOKEN$`.
S2. **Maintain the Claude pool from a direct Claude Code session.** In pool mode, a Claude pool that is down is a
    Claude Code that is down, including the agent repairing it. Anything that restarts, rebuilds, reconfigures or
    uninstalls the Claude pool runs from `CLAUDEPOOL=off claude` (or plain `claude` without the shim). If `/status`
    shows a base URL of `127.0.0.1`, you are on the pool: say so and ask the user to restart you direct.
S3. **The claude gate profile keeps everything but Claude Code out.** With `CODEXPOOL_GATE_PROFILE=claude` (set in
    the Claude pool's plist) the gate also refuses the `app://-` origin and requires `User-Agent: claude-cli/…` and
    `x-app: cli` on every path except the management API; an unknown profile refuses everything. Background
    sessions (`x-app: cli-bg`) run direct with `CLAUDEPOOL=off`: CPA must treat them as native on every path before
    the gate can admit them without rewriting their identification. The default profile stays exactly as it was.
    Both profiles only accept or reject, and every build passes both self-tests (11 codex + 18 sienna cases).
S4. **Two instances, never mixed.** The Codex pool and the Claude pool are separate CLIProxyAPI processes, each
    with its own port, config, auth directory, seats file, logs, build link (`bin/current`, `bin/claude-current`)
    and launchd agent. Never put Claude logins in the Codex pool or point Codex, lanes or scripts at `claude_port`:
    CLIProxyAPI translates between the APIs, and that would spend Claude accounts as third-party traffic.
    `subpool upgrade` moves only the Codex pool; `claude_cpa` and `subpool sienna install` move the Claude pool.
S5. **Anthropic's credits-off switch is the guarantee.** The Claude policy `off` expects usage credits disabled at
    claude.ai; the pool can only observe spending after billing. An enabled switch is a mismatch: doctor errors
    and `seats[].credits.mismatch` drives the UI warning. With credits disabled, scoped exclusions are optional
    and failures only log. For a mismatch, exclude scoped models at 95%; an unknown plan does not prove model
    inclusion. If exclusions remain unverified after one pass, park with reason `credits` and count an alarm.
    Shared plan-limit parks use `claude_limit_until`; below-limit overage alarms use the escalating backoff.
    A last-resort account stays parked until every other account's plan quota is spent, reserve included, then
    serves subject to a fresh credit reading and the observed cap. Recommend a matching member spend limit at
    claude.ai. See [Usage credits](docs/SIENNA.md#usage-credits) for exclusion recovery and refusal rules.
S6. **Keep the implementation modular.** Claude runtime behavior lives under `addons/sienna/`; the core loads
    it through `addon.py` and the interface in `docs/ADDONS.md`. Public docs and tests may describe the integration.
    Preserve existing runtime names and pool isolation.

## How to change things safely

| Change | Do this | Then verify |
|---|---|---|
| The Claude pool | Only from a direct Claude Code session (S2). `subpool sienna install --dry-run`, then `subpool sienna install` (idempotent: build, `config-claude.yaml` from `examples/config-claude.yaml`, `auth-claude/`, plist, gate probe, `claude-pool`). Its config keys hot-reload like `config.yaml`'s; its CLIProxyAPI version is `claude_cpa` in `settings.json`, applied by `subpool sienna install`. In a throwaway home, move `claude_port` and `claude_label` off the live ones too. The live Codex pool's PID, build link, port, config and gate must stay unchanged throughout: assert it after each install | `subpool doctor` (the Claude pool, Claude accounts and Claude Code sections), `subpool sienna status` |
| Claude accounts | `subpool sienna login <Label> --priority <n>` (`--no-open` for another account; the success line names the plan), then `subpool sienna label`, `weight`, `reserve`, `order`, `priority`, `enable`, `disable`, `remove --yes` (the Codex commands, run against `claude-seats.json` and `claude-guard.json`), `subpool sienna credits SEAT off` or `last-resort --cap USD` | `subpool sienna status --live` |
| The launcher and shim | Their text is `launcher_text()` in `pool.py`; `subpool sienna install` rewrites `~/.local/bin/claude-pool` (and a shim that is there), `subpool sienna shim install\|remove` the shim. Test only in a throwaway home with a stub `claude`; never run them against the real `~/.local/bin/claude` with the pool environment | `subpool doctor` (launcher present and current); `sh -n` on the script |
| The gate profile (`gate/codexpool_gate_sienna.go`) | Edit, `./bin/subpool install`, then `subpool sienna install` (rebuilds the Claude pool on the new hash; the Codex pool keeps its build) | The gate self-test: 11 codex + 18 sienna cases; `subpool doctor` |
| The menu bar and Settings side (`menubar_ext.py`) | Snapshot with the core scripts plus `--pool claude --pool-status docs/images/demo/claude-status-regular.json --pool-history docs/images/demo/claude-history-regular.jsonl` (the other `claude-status-*.json` fixtures for the other states; without `--pool-status` the Claude side reads as not installed), and the Claude-only panes `setup-install`, `setup-installing`, `balancing-credits`, `balancing-credits-warn`, `overview-desktop`. The `claude-status.json` fields it reads (`pool.route`, `pool.installed`, `pool.desktop`, each account's `plan`, `weight`, `five_hour`, `week`, `scoped`, `credits`; [docs/SPEC.md](docs/SPEC.md)) are its interface with `guard.py`; change both together | Look at the PNGs; `launchctl kickstart -k gui/$(id -u)/com.subpool.menubar` and look at the item |
| Screenshots | `~/.subpool/.venv/bin/python addons/sienna/docs/images/demo/render.py` rebuilds `docs/images/` here from `docs/images/demo/` (made-up accounts Max A, Max B, Pro C and the Max 20x reserve; `make_claude_data.py`) on top of the core's demo pool | Look at every image |
| Tests | `python3 -m unittest discover -s addons/sienna/tests` from the core checkout (or `CODEXPOOL_CORE=/path/to/checkout`); `tests/_helpers.py` chains to the core's fake home. `sh tests/run_all.sh` from the core runs both suites | Both suites pass; the core suite also passes in a copy without `addons/` |

## Tests you can run

- `subpool sienna status [--json] [--live]`: the Claude accounts (`--json` is `claude-status.json`, what the menu
  bar reads). `subpool sienna install --dry-run` prints its plan and changes nothing. Both are read-only.
- `subpool sienna selftest <A> <B> [--compact]`: moves a throwaway `claude -p` conversation with thinking from
  account A to B through the Claude pool. It spends a few requests and takes the other accounts out of rotation for a
  few minutes, so only with the user's go-ahead. It uses the launcher's environment, never a global one.
- Every `subpool sienna` command except `status`, `logs`, `desktop status` and `install --dry-run` changes the
  live install and needs the user's say-so.

## Undo

- `CLAUDEPOOL=off claude` starts one session direct, `subpool sienna route direct` sends every new session direct,
  and `subpool sienna uninstall --yes` removes its agent, launcher, shim and route file and keeps `auth-claude/`,
  `config-claude.yaml` and the builds (`subpool sienna install` brings it back). Claude Code, its login and
  `~/.claude` are never touched, so there is nothing of theirs to undo.
- `subpool uninstall --yes` takes the Claude pool out with the rest.
