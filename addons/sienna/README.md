# sienna: the Claude pool add-on for codexpool

A publicly included, optional add-on (`addons/sienna/`), loaded through the hooks in
[the core add-on interface](../../docs/ADDONS.md). Source, docs, tests and demo assets ship with codexpool;
account logins and runtime state stay on your Mac.

codexpool can also pool your Claude accounts (Pro, Max or Team) behind Claude Code. It is a second CLIProxyAPI
instance with its own build, port (8321), config, logins and launchd agent, so the Codex pool doesn't change and
each pool fails alone. Claude Code reaches it with `ANTHROPIC_BASE_URL` alone and keeps its own claude.ai login, so
claude.ai connectors, artifacts and Claude in Chrome are expected to keep working (Remote Control doesn't, with any
base URL that isn't Anthropic's).

```sh
codexpool claude install                              # the build, config, launchd agent and the claude-pool launcher
codexpool claude login "Max A" --priority 100         # once per account; --no-open for a private window
codexpool claude credits "Max A" last-resort --cap 200   # optional; every account starts at "off"
claude-pool                                           # Claude Code through the pool
```

- **Opt in per launch.** `claude-pool` starts Claude Code through the pool for that one process; plain `claude`
  stays direct. `codexpool claude shim install` makes plain `claude` use the pool too (you add one `PATH` line;
  codexpool never edits shell profiles). If the pool doesn't answer within 300 ms, Claude Code starts direct with a
  one-line warning. `CLAUDEPOOL=off` or `codexpool claude route direct` go direct on purpose.
- **Failover and balancing** work as for Codex: fill-first in your order, the reserve last, the request replayed on
  the next account at a limit, or soonest weekly reset first with `codexpool set claude_balancing reset`.
- **Usage credits.** Only turning credits off at claude.ai (Settings → Usage) guarantees no paid requests;
  the pool observes spending after billing. The default policy, `off`, expects that switch to be off. If it is
  on, doctor reports an error and the seat shows a warning. Until corrected, the guard excludes scoped models
  at 95% and parks the account if exclusions remain unverified after one pass; at a shared plan limit it parks
  until the used-up limits reset. With credits off at Anthropic, exclusions only save a 429 round trip and a
  failed exclusion never parks the account. `last-resort` stays parked until every other account's plan quota
  is spent, including the reserve. Its dollar cap is observational: set a matching member spend limit at claude.ai.
- **The gate** runs a stricter profile on the Claude pool: only Claude Code gets in. Codex, lanes, scripts and
  browsers get 403, so nothing else can spend Claude accounts.
  Background sessions (`x-app: cli-bg`) run direct: start them with `CLAUDEPOOL=off`.
- **The menu bar** shows two numbers, `⬡ 54%   ✳ 71%`, one per pool behind its logo; click either half for that
  pool's popover. The logos come from the Codex and Claude apps installed on your Mac, with simple drawn marks when
  an app isn't there. The Settings window and the Setup assistant have a Codex | Claude switcher.

A conversation that moves to another account starts with a cold prompt cache there, and Claude Code's `/usage`
shows only its own login. [docs/SIENNA.md](docs/SIENNA.md) covers setup, how it works, what works through the pool, the credit policy,
rollback, known limits and the risk.


More: [docs/SIENNA.md](docs/SIENNA.md) (setup, how it works, the credit policy, rollback, known limits, risk),
[docs/DESKTOP.md](docs/DESKTOP.md) (the pooled desktop app), [docs/ENGINE-LANE.md](docs/ENGINE-LANE.md)
(Claude Code as a read-only lane), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/MENUBAR.md](docs/MENUBAR.md),
[docs/SPEC.md](docs/SPEC.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), [CHANGELOG.md](CHANGELOG.md)
and [AGENTS.md](AGENTS.md) (for coding agents).
