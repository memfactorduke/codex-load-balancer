# Changelog: the sienna add-on

The Claude pool's entries. The core's `CHANGELOG.md` records the codexpool releases; the add-on ships with
codexpool 1.3.0's add-on interface (`core_min` 1.3.0).

## [Unreleased]

### Added

- **Read-only Claude engine lanes (E4 pending).** The Sienna provider uses isolated Claude Code sessions with
  Read/Grep/Glob, held init verification, per-thread locks, supervised pipes and cancellation, strict process
  identity, crash recovery and bridge shutdown. Sealed compaction digests recover history and cursors without
  starting the engine. Engine-specific roles and tests return proposed changes as text.
- **Explicit engine acceptance and terminal continuation.** `lane apply --accept-engine` probes the exact installed
  version before recording acceptance; dry-run never executes it. `claude lane-resume UUID` holds the shared lock
  while an interactive terminal uses the isolated profile. CPA compatibility tests check allowed request changes
  and failed-stream error preservation; captured passthrough, header forwarding, metering, native confinement and
  rendering still require live E4. See the engine section in `docs/LANES.md`.

## [1.3.0] - 2026-09-27

### Fixed

- **Credits follow Anthropic's switch.** Policy off requires credits off at claude.ai. A mismatch is now a doctor
  error and `seats[].credits.mismatch` in `claude-status.json`, for the seat-row and Settings warning. Only the
  upstream switch guarantees no paid requests; the guard observes spending after billing.
- **Scoped exclusions and recovery.** Credits-off exclusion failures log once without parking. Credits-on
  mismatches exclude at 95%, and exclude Fable on plans without known inclusion. Registration gets a bounded
  retry within the pass; persistent failure parks as `credits` and increments escalating alarms. Disabled seats
  recover old scoped parks from saved exclusions or a fresh credits-off reading, without requiring registered
  models. Early recovery requires a successful usage poll newer than the park; stale polls and lost CPA headers
  cannot release a shared-limit park after restart. Passive-mode parks hold until their deadline. Exclusion
  parks honour the escalating backoff and notify once per continuous failure. Manual exclusions remain intact.
- **Credit guard follow-through.** Live overage permission also drives mismatch warnings, doctor errors and
  model exclusions during passive polling. Policy-off enable overrides cover overage and exclusion parks for
  their full duration; enabling a guard-parked last-resort account is refused with an explanation. A later
  shared-limit reading keeps the later of its reset and an existing alarm deadline.
- **Last resort waits.** Last-resort accounts stay parked even with their own plan quota until every other plan,
  including reserves, is spent. A fresh reading below the cap is required; doctor recommends a matching member
  spend limit at claude.ai. The guard re-parks when another plan returns or the cap is observed reached.
  Legitimate spending before another plan returns no longer triggers a policy-violation alarm. Serving within
  its own plan does not show “Spending credits” without an exhausted limit or an overage-in-use signal.
- **Fable failover and precise refusals.** `credits_required` retains CPA's normal model cooldown and failover.
  Only complete fast-mode or long-context entitlement messages stop without rotation or cooldown.
- **Native identity and upgrades.** The gate rejects all `cli-bg` requests; background sessions start direct
  with `CLAUDEPOOL=off`. Foreground token counts still use CPA's native detector. Doctor errors on missing
  `disable-claude-cloak-mode: true` or refusal rules; `codexpool claude install` rewrites outdated configs from
  the template. Gate self-tests remain 11 Codex + 18 Claude cases. Builds check for CPA's required native
  detector and explain an incompatible release before Go compilation, leaving running builds unchanged.
- **Claude launcher helpers (F4):** pooled launches default `ANTHROPIC_DEFAULT_HAIKU_MODEL` to Sonnet,
  preserving nonempty user overrides; doctor detects the override in launchd's global environment. The
  self-test environment now matches the launcher's defaults for empty values too. Opus/Fable 1M defaults
  remain unchanged because credit-safe long-context beta behavior through the pool is unverified;
  `docs/SIENNA.md` records the evidence and manual `/model opus[1m]` opt-in.
- **Team plans and sizes.** Team Premium is recognised from `team_tier_1` or a Team organization
  with the Max 5× rate tier, without misclassifying Enterprise seats. Default session weights are
  Team 1.25 and Team Premium 6.25 (Pro = 1); profile refreshes preserve user-set weights.
- **Login files are private.** CLIProxyAPI writes a new sign-in's file readable by other users; codexpool now
  makes it mode 600 right after every sign-in (seats, Claude accounts, the xAI login), and the guard tightens any
  login file it finds open. `codexpool doctor` flags one it can't fix.

### Added

- **The Claude pool: Claude Code through several Claude accounts** (optional; [docs/SIENNA.md](docs/SIENNA.md)).
  A second CLIProxyAPI instance with its own build (`bin/claude-current`), port (8321), `config-claude.yaml`,
  `auth-claude/`, `claude-seats.json`, log folder (`claude/logs/`) and launchd agent (`com.codexpool.claude`).
  Claude Code reaches it with `ANTHROPIC_BASE_URL` alone and keeps its own claude.ai login, so connectors,
  artifacts and Claude in Chrome are expected to keep working; Remote Control does not with a base URL that
  isn't Anthropic's. One account serves at a time in your order, and an account at its 5-hour or weekly limit is
  replaced on the same request. The Codex pool is unchanged; without `codexpool claude install`, nothing is.
- **`codexpool claude install [--dry-run]`** builds or reuses the Claude pool's CLIProxyAPI (7.3.18 or later;
  `claude_cpa`, else the version it runs, else the newest release), writes its config and folders, loads its
  launchd agent, probes its gate (it stops the pool again if other clients get in), writes the `claude-pool`
  launcher and records it in `install.json`. Safe to re-run. **`codexpool claude uninstall [--yes]`** removes the
  agent, the launcher, the shim and the route file and keeps the account logins, config, builds and logs.
- **`claude-pool`** (in `~/.local/bin`) starts Claude Code through the pool for that one process: it sets
  `ANTHROPIC_BASE_URL`, `ENABLE_TOOL_SEARCH=true` and `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` after a 300 ms probe, and
  starts Claude Code direct with one warning line when the pool doesn't answer, the route is direct or
  `CLAUDEPOOL=off` is set. It never sets `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` or `apiKeyHelper`.
  **`codexpool claude shim install|remove`** manages an optional `~/.codexpool/shims/claude`, the same launcher as
  `claude`, and prints the one `PATH` line to add (codexpool never edits shell profiles).
  **`codexpool claude route [pool|direct]`** says or sets where new sessions go.
- **Claude accounts:** `codexpool claude login LABEL [--no-open] [--no-copy] [--priority N]` (the same sign-in flow
  as seats, link on the clipboard included; the success line names the account's plan), and `enable`, `disable`,
  `label`, `weight`, `priority`, `order`, `reserve [--off]` and `remove --yes`, which work as the Codex commands do,
  on the Claude pool's own files. `codexpool claude status [--json] [--live]`, `codexpool claude logs [-f]
  [-n LINES]`.
- **A credit policy per Claude account:** `codexpool claude credits SEAT off` (the default: the guard parks an
  account at its observed shared plan limit) or `codexpool claude credits SEAT last-resort --cap USD`
  (it may spend credits only once every other account, the reserve included, is out, until it has spent the cap
  this month; codexpool enforces the cap itself). Changing an account's policy ends a `codexpool claude enable`
  override of the old one and applies at once. Credits that rise while an account's plan windows are below their
  limits (fast mode bills that way, and so would billing the pool's requests as third-party) park an `off` account
  for an hour when detected (longer if it happens again within a day); `last-resort` also parks while plan quota
  remains. These are observational protections, not a guarantee against a first charge or cap overshoot.
- **The guard's Claude pass**, after the Codex pass, whenever the Claude pool is installed: each account's plan
  (Pro, Max 5×, Max 20×, Team, Team Premium) once a day, which sets its default weight; its 5-hour, weekly and
  scoped usage and its credits through the Claude pool's `api-call` (the serving account every 3 minutes, the others
  every 10, backing off on 429), falling back to the rate-limit headers CLIProxyAPI keeps when Anthropic refuses
  that call; the credit policy; healing, with an immediate "sign in again" when Anthropic ends a sign-in;
  balancing; notifications; `state/claude-status.json` and `state/claude-history.jsonl`. A failure in one pass
  never stops the other.
- **`codexpool claude selftest A B [--compact] [--model MODEL] [--yes]`** moves a throwaway `claude -p` conversation
  with thinking from account A to account B and checks it carries on. It spends a few requests and asks first.
- **The gate's claude profile.** With `CODEXPOOL_GATE_PROFILE=claude` (set only in the Claude pool's plist) the gate
  also refuses the `app://-` origin and requires Claude Code's `User-Agent: claude-cli/…` and `x-app: cli` on every
  path except the management API (exempt only on its own clean path, as the router sees it, so a
  `/v1beta/models/../../v0/management/…` request is refused); an unknown profile refuses everything. Every build now
  passes the self-test for both profiles (11 + 18 cases). The default profile is unchanged.
- **Settings:** `claude_label`, `claude_port`, `claude_balancing` (`codexpool set claude_balancing priority|reset`)
  and `claude_cpa`.
- **The menu bar shows both pools** when the Claude pool is installed: one item, `⬡ 54%   ✳ 71%`, each number in its
  pool's colour (Codex blue, Claude coral), red while that pool's reserve serves, every account is out or an account
  spends credits, grey with ⚠ when that pool is down. Click the left half for Codex, the right half for Claude. The
  popover has Codex and Claude tiles at the top; the Claude tab shows each account's 5-hour, weekly and scoped bars,
  its plan and its credits, and the footer adds Route: Pool / Direct and Add a Claude account….
- **The Settings window and the Setup assistant do the Claude pool too:** a Codex | Claude switcher on Overview,
  Seats and Balancing; a Usage credits section per account; a Using it card; Add a Claude account and Install the
  Claude Pool in the Setup assistant.
- **`codexpool status` and `codexpool doctor` have Claude sections** once it is installed (`--json` included):
  doctor checks the Claude pool's process, build and gate, its accounts, usage polling and credit policies, the
  launcher, route and shim, thinking-signature refusals and gate rejections in its log, and warns when an
  `ANTHROPIC_*` variable is set in launchd's environment. `codexpool claude status --live` shows the same rows as
  the guard's file (labels, plans, usage and credits as last read, the serving account).
- **The pool's size in the status headers:** `Codex pool  36× total  ·  …` and `Claude pool  31× total  ·  …` (every
  seat's size added up, seats turned off aside), as in the menu bar's summary line. An account serving on usage
  credits doesn't count as ready there, in the menu bar or in Settings.
- `codexpool gui [PANE] --pool codex|claude` opens the Settings window on that pool's side; the menu bar's Settings…
  and Add a … account… open it on the side of the tab you are on.
- [docs/SIENNA.md](docs/SIENNA.md), and Claude sections in the README, AGENTS.md (new invariants 12 to 15),
  ARCHITECTURE.md, TROUBLESHOOTING.md, MENUBAR.md and on the website.

### Changed

- **The menu bar shows the real Codex and Claude logos, taken from the apps on your Mac.** The popover's tiles and
  the Settings switcher show the same. codexpool bundles no logo: without an app, its pool keeps the plain drawn
  mark, and `--snapshot` keeps the drawn marks unless given `--marks app`, so the docs' images stay reproducible.
- **The menu bar number is Codex blue** while a regular seat serves (it was green), and red, not grey, while every
  seat is out. Bars keep their green, orange and red.
- `codexpool set` with no arguments prints four settings.
- Each pool has its own guard lock (`state/guard.lock`, `state/claude-guard.lock`): a selftest in one pool pauses
  only that pool's guard pass, never the other's credit guard.
- `codexpool uninstall` takes the Claude pool out too, since without the guard nothing would enforce its credit
  policies.
- `build/codexpool_gate.go` changed (the claude profile), so its hash did. The Codex pool keeps running its build
  until its next `codexpool upgrade`, which picks the new gate up; nothing forces one, and `codexpool doctor` says
  when a pool runs an older gate.
