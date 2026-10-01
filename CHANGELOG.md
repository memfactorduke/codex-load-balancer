# Changelog

All notable changes to codexpool are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Publish the optional Claude integration, including its docs, tests, demo assets and patches; explicitly allow
  it in the publication rules and validate both suites in CI. Credentials and runtime state remain local.

### Changed

- Add Claude CLI reserve accounts: hold them out of automatic rotation until enabled regular accounts reach the switching threshold; retain manual switching and explicit exclusions.

- Replace the supported Claude proxy, desktop and engine-lane product with a native UI over upstream cswap.
  Account switching and auto-rotation are delegated to cswap; live migration is explicit.
- Label the product selectors Codex Desktop/CLI and Claude CLI, replace the normal-state “Regular” badge
  with “Ready”, and remove Claude desktop setup controls from the normal interface.

- **The popover's chart shows your use as bars.** Each bar is how much of the pool's quota went in one half hour
  (four hours on the 7-day view), scaled so the busiest bucket fills the chart; the quota-left (or usage) line
  stays on its 0–100 % scale on top, and a weekly reset is marked with a dashed line and a triangle instead of
  counting as use. The caption under the chart says what the tallest bar is worth. The chart is 16 pt taller.
  A `PoolUI.chart_area` is ignored now (there is no gradient under the line any more).
- **A seat with more than one limit labels each bar.** A seat with a 5-hour window (Plus, Team) shows a "Week"
  row and a "5h" row, each with its own bar and "% left", and the limit that binds the seat now reads first and
  bolder. Before, the bars were stacked unlabelled at different thicknesses in the same colour.

## [1.3.0] - 2026-09-28

### Fixed

- **Login files are private.** CLIProxyAPI writes a new sign-in's file readable by other users; codexpool now
  makes it mode 600 right after every sign-in (seats and the xAI login), and the guard tightens any login file it
  finds open. `codexpool doctor` flags one it can't fix.

### Added

- **Add-ons.** A directory `~/.codexpool/addons/<id>/` with an `addon.py` can register a second pool instance, its
  guard pass, doctor and status sections, a gate profile, a lane provider and a menu bar tab through the hooks in
  `docs/ADDONS.md`; `codexpool addon list|install|remove`. None ship with codexpool. With an add-on's pool
  installed the menu bar item shows both numbers and the Settings window gets a pool switcher
  (`codexpool gui PANE --pool ID`).
- **The pool's size in the status header:** `Codex pool  36× total  ·  …` (every seat's size added up, seats
  turned off aside), as in the menu bar's summary line.
- `codexpool set` with no arguments prints the settings it changes.

### Changed

- **The menu bar shows the real Codex logo, taken from the app on your Mac.** The popover's tiles and the
  Settings switcher show the same. codexpool bundles no logo: without the app, the pool keeps the plain drawn
  mark, and `--snapshot` keeps the drawn marks unless given `--marks app`, so the docs' images stay reproducible.
- **The menu bar number is Codex blue** while a regular seat serves (it was green), and red, not grey, while every
  seat is out. Bars keep their green, orange and red.
- Each pool instance has its own guard lock (`state/guard.lock`): a selftest in one pool pauses only that pool's
  guard pass, never another's.
- `build/codexpool_gate.go` gained a profile registry (`CODEXPOOL_GATE_PROFILE`: unset is the Codex pool; any
  profile no add-on registers refuses every request), so its hash changed. The Codex pool keeps running its build
  until its next `codexpool upgrade`, which picks the new gate up; nothing forces one, and `codexpool doctor` says
  when a pool runs an older gate.

## [1.2.0] - 2026-09-27

### Added

- **Load balancing: soonest reset first.** A new setting, `"balancing"`, decides which seat new threads get.
  `"priority"` (the default) is the fill order you set, as before: the first seat until it runs out, then the
  next. `"reset"` has the guard order the regular seats by their weekly resets, the soonest first, so quota that
  is about to reset gets used before it goes to waste: a seat without a weekly window counts by its 5-hour
  window, seats without usage data follow in your order, seats that are out, parked, blocked or off stay where
  they are, and the reserve stays last. The guard changes priorities only when the order is wrong (resets within
  five minutes of each other count as a tie), writes one line to the guard log and never notifies; running
  threads stay on their seat, new threads follow the new order. Switching back to `"priority"` restores your own
  order on the guard's next pass. `codexpool set balancing priority|reset` changes it (`codexpool set` alone now
  prints all three settings), `codexpool status` shows the mode in its first line and each seat's reset in its
  note, and `status.json` has `pool.balancing` and, per seat, `order_reason` ("resets in 1d 4h").
- **`codexpool order SEAT [SEAT ...]` sets the fill order in one go.** The seats named come first in that order,
  the other regular seats keep their order after them, and reserve seats always come last. Priorities go down
  from 1000 in steps of 10. `seats.json` keeps the order as yours (`manual_priority`, which `codexpool priority`
  now writes too), for the way back from `"reset"`. Under `"reset"`, `order` and `priority` change only that saved
  order, so the live order never flips for a minute. A fill order you change yourself (`order`, `priority`,
  `reserve`, Make first) is no longer announced as "Codex now on …", and `priority` and `reserve` now wait for
  a running guard pass like `order` does.
- **Lanes can be edited in place.** `codexpool lane edit NAME` changes a lane and applies it: `--rename NEW`,
  `--role TEXT` (line breaks become spaces, in `lane add` too), `--effort E`, `--display TEXT` or `--no-display`,
  `--add-member PROVIDER:MODEL[:DISPLAY NAME]`, `--remove-member ID` and `--move-member ID --to POS`, applied in
  the order given and checked like `lane add`; `--dry-run` prints the change. `lane edit` writes every member's
  id into `lanes.json`, so moving or removing one never renames the others, and `--rename` takes a responses
  member's own key (`<lane>-<id>`) along.
- **What a lane can use.** `codexpool lane providers [--json]` lists the lane providers, how each signs in (xAI
  sign-in or an API key) and whether it is ready. `codexpool lane models PROVIDER [--json]` lists a provider's
  models: xAI's from the pool, OpenCode's from its `/models` with your stored key (sent with the bridge's
  User-Agent, only to the provider's own address; a redirect is refused, and the key is never printed), and a
  responses endpoint's with `--base-url` and `--key-name` (its own key: never the bridge's or an OpenCode key).
  With `--json`, an error is `{"error": "..."}` and exit status 1.
- **The Settings window edits lanes.** The Lanes pane has New Lane…, Edit… and Delete… for each lane, a lane
  editor (picker label, effort, role, members in fallback order, Add Model… with each provider's model list),
  and the xAI sign-in and provider keys (a key goes from a secure field straight to `codexpool lane key NAME -`;
  a key the lane still needs when you save, such as a new responses member's, is asked for right there).
- **A Balancing pane in the Settings window.** Choose "Your order" or "Soonest reset first", set the seat order,
  and mark the reserve, which is used only when every other seat is out.
- `codexpool lane login xai` copies the sign-in link to the clipboard, as seat sign-ins do (`--no-copy` leaves
  the clipboard alone). `codexpool gui balancing` opens the Balancing pane.

### Fixed

- **The menu bar popover closes when you click anywhere else**, press Escape or switch to another app, as menu
  bar popovers do. Clicks inside it and on the menu bar item work as before.

## [1.1.0] - 2026-09-27

### Added

- **Sign-in links go on the clipboard.** Whenever a sign-in prints its link, `codexpool login` and
  `codexpool setup` copy it with `pbcopy` and say so right under it ("Link copied to the clipboard: paste it into a
  private window signed in to that account."): with `--no-open` (and so from the menu bar's **Re-login…**), with
  `--device`, in `codexpool setup`, and when the browser did not open. A `codexpool login` that opens the browser
  prints no link, so it has nothing to copy. A copy that fails never fails the sign-in. `codexpool login --no-copy`
  leaves the clipboard alone, and `CODEXPOOL_NO_CLIPBOARD=1` turns it off for scripts. The Setup assistant puts the
  link on the clipboard as soon as it appears and shows "Copied" next to Copy Link, which stays for copying it
  again.
- **A lane's name in the model picker can be set.** Give a lane `"display"` in `lanes.json` (one line, 1 to 40
  characters), or use `codexpool lane add --display TEXT`. `codexpool lane list` shows each lane's picker name,
  and `lane list --json` has it as `"display"`. The role files and the `~/.codex/AGENTS.md` block still name the
  members in full.
- **Set it up with your coding agent.** The README ("Set it up with your coding agent") and the website have a
  prompt to paste into your coding agent (Codex or another) on the Mac you are setting up. The agent runs
  the installer, asks what to call each ChatGPT account, hands you a sign-in link for each one and checks the
  result; all you do is sign in. It works when you are not at that Mac's screen too: through Screen Sharing, an
  SSH tunnel, or by sending the agent the address the sign-in ends on.

### Fixed

- **Ended sign-ins are read from the pool log by position, not by time.** The pool writes naive local time
  stamps, so after the Mac changed time zone, old "session ended" lines read as newer than a fresh sign-in
  and marked healthy seats. The guard no longer reads old log lines back on its first pass, and a line
  counts only when it comes after the point in the log where the seat's current tokens were marked (one
  pass after the guard first saw them), so a backlog, a refresh still in flight during a new sign-in, or a
  time-zone change can no longer misplace one.
- **Codex keeps working until the first seat is in.** `codexpool install` used to point Codex at the pool even
  when the pool had no seats yet, so Codex could not reach anything until one was added, and a Codex agent
  running the install cut itself off halfway through. Install still backs up and records the Codex settings,
  but while the pool has no seats it leaves `openai_base_url` alone, says that Codex keeps its own login until
  then, and notes the pending switch in `state/install.json`. The first seat sign-in (`codexpool login`,
  `codexpool setup` or the Setup assistant) sets `openai_base_url` and says to quit and reopen the Codex app. It
  makes that switch once, only while the install is still there, never after `codexpool uninstall`, and never
  over an `openai_base_url` you changed by hand in the meantime (it says so, and `codexpool install` makes the
  switch). Until the first seat, `codexpool doctor` shows the Codex config as a warning, not a problem; once
  there are seats and Codex is not pointed at the pool, it is a problem again, and `codexpool install` fixes it.
  Uninstall works as before, and with the switch still pending it has nothing to take out of the Codex config.
- **No more Codex pointed at an empty pool.** Removing the last ChatGPT seat (`codexpool remove`) puts
  `openai_base_url` back as it was before install, so Codex goes back to its own login, and the next seat
  sign-in points it at the pool again. `codexpool install` does the same for an install that points Codex at a
  pool without seats (one from before 1.1.0, for instance).
- **Install, uninstall and a sign-in no longer race.** They take a lock around their changes to the Codex
  config, so a sign-in that finishes while `codexpool uninstall` runs can no longer leave Codex pointed at a pool
  that is gone.
- **Signing in to an account that is already a seat says so.** `codexpool login "<Name>"` for an account the
  pool has already (a browser that reused an earlier account's session, say) used to give that seat the new name
  and print a normal `seat ...` line. It now keeps the seat's name, prints "That was <seat> once more ... nothing
  was added" and says how to sign in to another account. `codexpool login --no-open` no longer says it is
  opening the browser.
- **Lane names fit the Codex model picker.** The picker cut long lane names short ("Bulk lane (Grok 4.7 Fast,
  then Muse…"). A lane's entry is now just its name with a capital first letter, "Bulk" for the example lane,
  with the members under the hood (a `"display"` of your own still replaces it). If you use lanes, run
  `codexpool lane apply` once after upgrading to write the new names (`codexpool install` reminds you); until then
  `codexpool doctor` says the `config.yaml` lanes block does not match `lanes.json`. Lane names, roles and member
  names with a Unicode line or paragraph separator are refused, and a `"display"` has its surrounding spaces
  trimmed.
- **An ended sign-in asks for a new one at once.** When OpenAI refuses a seat's refresh for good
  (`refresh_token_invalidated`, "Your session has ended", `refresh_token_reused`, `refresh_token_revoked`,
  `refresh_token_expired` or `invalid_grant`), the guard used to retry it three times 15 minutes apart before
  asking for a re-login, so a seat that needed a new sign-in looked like a passing block for 45 minutes. Now the
  first guard pass that sees it (in the pool's log or in the answer to its own refresh) notifies you with the
  `codexpool login` line to run and stops refreshing the seat; a new sign-in clears it. While the pool still serves
  the seat on its access token (up to a day), it stays ready and says "Re-login soon" in the menu bar and the
  Settings window (a warning in `codexpool doctor`), and the guard announces no move to another seat and no dry
  pool; once the pool stops serving it, it is blocked ("OpenAI ended this sign-in"). `codexpool doctor` gives only
  the sign-in as the fix. Any other auth error is retried as before.
- **The Setup assistant shows a failed switch.** When a sign-in could not point Codex at the pool (the Codex
  config could not be written), the assistant says so on the account's card and on its last step, with the fix
  (`codexpool install`), instead of saying that you're all set.

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

[Unreleased]: https://github.com/memfactorduke/codex-load-balancer/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/memfactorduke/codex-load-balancer/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/memfactorduke/codex-load-balancer/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/memfactorduke/codex-load-balancer/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/memfactorduke/codex-load-balancer/releases/tag/v1.0.0
