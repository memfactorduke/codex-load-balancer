# Changelog

All notable changes to codexpool are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-09-26

The first public release: every ChatGPT seat you have, behind one Codex.

Snapshots of this repository published before 1.0.0 were released under the MIT License, and copies of them
keep it; codexpool 1.0.0 and later are licensed under the PolyForm Noncommercial License 1.0.0.

### Added

- **The pool.** CLIProxyAPI, built from its upstream release source with one added file, the origin gate, and
  a one-line hook in `cmd/server/main.go` that installs it, and served on `127.0.0.1`. The Codex app and the
  Codex CLI reach it through their built-in provider with one line of config (`openai_base_url`), so every
  existing thread stays visible and nothing in Codex is patched. Fill-first by priority, 24-hour session
  affinity, and failover inside a turn: a seat that hits its usage limit is replaced on the same request after a
  pause of a second or two.
- **Threads survive seat changes.** Encrypted reasoning and native compactions carry across seats.
  `codexpool selftest <from> <to> --compact` moves a real throwaway thread between any two of your seats and
  checks that it continues.
- **The origin gate.** It refuses requests with a non-loopback `Host` or browser provenance before CLIProxyAPI
  sees them, WebSocket upgrades included. Every build passes an 11-case gate self-test before it is used.
- **The guard** (`codexpool guard`, every 60 seconds under launchd): usage and banked-reset polls, credit
  parking for seats that would start spending credits, automatic refresh of auth-blocked seats, notifications
  when the pool changes seat, reaches the reserve or runs dry, and `state/status.json` plus usage history for
  the menu bar.
- **Seat sizes and the reserve.** Weights default from each seat's plan (`plus` 1, `prolite` and
  `self_serve_business_prolite` 5, `pro` 20, anything else, `team` included, 1) and change with
  `codexpool weight`. `codexpool reserve` marks the fallback seat.
- **The menu bar app.** A native PyObjC item next to the clock that shows the week's quota left across all
  seats, weighted by size. It counts down, stays green while a regular seat serves, turns red once the reserve
  serves, and turns grey when the pool is down. The popover lists every seat with its 5-hour and weekly bars,
  reset times, a usage chart and seat actions. It reads files only: no Keychain, no network. The `display`
  (`left` or `used`) and `headline` (`all` or `regular`) settings change what the number means;
  `codexpool set display left|used` and `codexpool set headline all|regular` change them from the terminal
  (`codexpool set` alone prints both), and the menu bar follows within seconds.
- **Resets.** Banked free resets are tracked for every seat and redeemed in one click from the menu bar, or
  with `codexpool reset`. Redeeming uses an idempotent request id, so a retry can't spend two, and codexpool
  never buys a reset.
- **Subagent lanes (optional).** Models from other providers, such as xAI and OpenCode, served as native Codex
  subagents with fallback between providers, for token-heavy work that doesn't need the most capable model.
  `codexpool lane add`, `apply`, `test`, `login` and `key`, `codexpool lane list` (`--json` for scripts and the
  Settings window), and a small local bridge for providers whose requests need adapting. Seat traffic never
  touches a lane. The Codex model picker shows one entry per lane. `lane test` pins each member through a member
  alias of its own (`<lane>-<id>`) that exists only while it runs: it offers the aliases, waits for the pool to
  list them, and withdraws them at the end, waiting until the pool no longer lists them. If a test is killed,
  `codexpool doctor` warns about the leftover aliases and `codexpool lane apply` removes them. See
  [docs/LANES.md](docs/LANES.md).
- **The Settings window.** A native macOS window with Overview, Seats, Lanes, General, Health and About panes:
  rename, resize, reorder, reserve, enable, disable and remove seats, redeem a banked reset, test and apply
  lanes, switch the headline and display settings, restart the pool and read `codexpool doctor`'s report.
  "Settings…" (⌘,) in the menu bar opens it, and so does `codexpool gui [PANE]` from the terminal. Every change
  it makes is a `codexpool` command run in the background.
- **The Setup assistant.** Guides a first install: checks that the pool, the Codex app and the menu bar are
  ready, adds ChatGPT accounts one by one (with a private-window option for accounts your browser is not signed
  in to), offers to mark the reserve, and reopens the Codex app at the end. It opens by itself once while the
  pool has no seats, and any time from "Add a ChatGPT account…" in the menu bar or with
  `codexpool gui setup-welcome`.
- **`codexpool setup`**, the same guided setup in the terminal: it checks that the pool answers, lists your
  seats, adds accounts one by one (the sign-in of `codexpool login --no-open`), runs `codexpool doctor` and
  offers to reopen the Codex app, asking before each step.
- **The installer.** `install.sh` for a one-line install: it checks the Mac, finds a Python 3.11+ or gets one
  through uv, downloads the latest release from GitHub (or `--version TAG`), runs `codexpool install` and opens
  the Setup assistant. It never uses sudo, supports `--dry-run`, and upgrades in place when run again.
  `codexpool install` itself fetches Go from go.dev (checksum verified), builds the gated pool, stores a
  management key in the Keychain, sets up the launchd agents and records what it changes, so
  `codexpool uninstall` restores the Codex config. Both find the Codex app by its bundle id and its `codex` CLI
  in the current layout (`Contents/Resources/codex-cli/bin/codex`, since app version 26.924) or the older one
  (`Contents/Resources/codex`).
- **`codexpool doctor`**, a whole-system health check that explains every problem with a fix (`--json` prints
  the checks as JSON, with the same exit status), and `codexpool upgrade`, which builds a new CLIProxyAPI
  release, switches, health-checks and switches back on failure.
- **`codexpool version`** (and `codexpool --version`) prints the codexpool version.
- **The website**, a static site in `site/`, deployed to GitHub Pages by `.github/workflows/pages.yml` or to
  any static host.
- **Project files:** the PolyForm Noncommercial 1.0.0 license, a notice file, contributing and security
  guides, a code of conduct, issue and pull request templates, unit tests in `tests/` (standard library, run in
  a throwaway home: `python3 -m unittest discover -s tests`), and CI that compiles the code, checks Python 3.9
  syntax, checks, dry-runs and shellchecks `install.sh`, runs the unit tests and scans for secrets and personal
  data.

[Unreleased]: https://github.com/memfactorduke/codex-load-balancer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/memfactorduke/codex-load-balancer/releases/tag/v1.0.0
