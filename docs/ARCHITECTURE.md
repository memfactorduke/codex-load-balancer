# Architecture and decisions

This records why codexpool is built the way it is, so later changes don't undo lessons that were paid for. The
[README](../README.md) says how to use it; [AGENTS.md](../AGENTS.md) says how to change it safely.

## The problem

One ChatGPT seat's Codex limit can be spent in a few hours of agent work. Many people have access to several
seats (a personal plan, one or more work workspaces). The goal: one Codex desktop app with one thread history
that uses every seat automatically, with no account switching and nothing that breaks when Codex updates.

## Research findings that shaped the design

- **Codex has no usable multi-account support.** The desktop app's account switcher is web-session code that is
  wired to nothing in the Electron build. Switching accounts means signing out and back in, which revokes the
  app's login every time.
- **A custom `model_provider` hides your threads.** The desktop sidebar filters threads by provider id, so
  pointing Codex at a proxy through a new provider makes every existing thread disappear. Codex's built-in
  `openai` provider honours `openai_base_url`, which moves the traffic and keeps the history.
- **A static `model_catalog_json` freezes the model picker**, so new models never appear. The live catalog passes
  through the pool unchanged.
- **Proxies that rewrite Codex payloads break on Codex updates.** Tool schemas, subagent handoffs and request
  shapes change often. Anything custom that parses or rewrites them has to be fixed after each release.
- **Routing by model name strands quota.** A setup that sends model X to account A and model Y to account B
  leaves one account idle while the other is refused.
- **ChatGPT refresh tokens rotate on use.** A used refresh token stops working shortly after it is exchanged.
  Two programs holding one copy of a login therefore sign each other out (`refresh_token_reused`). Every login
  must have exactly one owner.
- **An exhausted seat is refused at admission.** It answers `429 usage_limit_reached` before producing any
  output, in every case observed. That makes a transparent retry on another seat safe: nothing has been streamed
  to the client yet. Codex treats a 429 as final, so the retry must happen in the pool.
- **Encrypted thread state crosses seats.** Encrypted reasoning items and native compaction blobs issued under
  one seat were accepted by another, in both directions, in live tests between a Business workspace seat and a
  personal Pro seat. Other pairs are untested; `codexpool selftest` checks any pair.
- **Seats with credits don't stop at 100%.** A seat holding a credit balance keeps answering past its plan limit
  and pays with credits, without any error the pool can see. In one test a Pro seat spent several hundred credits
  in under half an hour.
- **Banked resets can be redeemed per seat.** `GET /backend-api/wham/rate-limit-reset-credits` lists a seat's
  free reset credits (id, title, expiry, status). `POST …/consume` with a `credit_id` and a client-chosen
  `redeem_request_id` redeems one. The answer's `code` is `reset` on success, or `already_redeemed`,
  `nothing_to_reset` (the credit is kept) or `no_credit`. Reusing the same `redeem_request_id` makes a retry safe.

## Design

**The pool is stock CLIProxyAPI.** It is a maintained open-source proxy that tracks Codex releases closely. Codex
reaches it through `openai_base_url` alone. The only custom code in the request path is the origin gate, and it
only accepts or rejects.

**Fill-first by priority, with 24-hour session affinity.** One seat is drained at a time, so reset times are
staggered across seats. Each thread stays on its seat, which keeps prompt caching effective and makes account
moves rare (a few a week). Subagents ride their parent's seat. Round-robin was rejected: it burns every seat's
5-hour and weekly windows in parallel and moves threads between accounts on every request.

**Failover at admission.** CLIProxyAPI cools an exhausted seat until its `resets_at` and replays the request on
the next seat (`request-retry: 3`). Cooldowns are not saved across restarts, so a restart is a clean slate and
the pool relearns from the next 429.

**One OAuth login per seat, owned by the pool.** Seats are added with `codexpool login`, which runs
CLIProxyAPI's own login flow and writes a new file into `auth/`. Only the pool refreshes these tokens. The Codex
app keeps its own separate login (a different token family, even for the same account) for its usage meter,
cloud tasks, plugins and sign-in.

**codexpool never handles tokens.** Usage and reset calls go through the pool's management `api-call` endpoint
with a `$TOKEN$` placeholder, and the pool substitutes the seat's token. codexpool reads only identity claims
(email, plan, account id) from seat files. The single-seat refresh response embeds tokens, so `codexpool refresh`
prints only the status.

**The guard parks seats that would spend credits.** It runs every 60 seconds from launchd. When a seat that the
pool still considers ready is at 100% and would pay with credits, the guard disables it until its window resets
and notifies you. The park record is written before the seat is disabled, so a crash can't leave an orphaned
disabled seat. `codexpool enable` turns a park into an override that lasts until the reset.

**The guard heals auth blocks.** A seat blocked by an auth error is refreshed every 15 minutes (hourly once you
have been told). After three failed attempts it notifies you to sign in again. A healed seat leaves probation
once a real request succeeds, or after two hours without another block; until then its flapping isn't announced.

**Resets are redeemed, never bought.** The guard polls each seat's banked resets every 10 minutes and puts the
count and soonest expiry into `status.json`. `codexpool reset` redeems the soonest-expiring credit, then calls
the pool's `reset-quota` so CLIProxyAPI drops the seat's cooldown and serves it at once, then refreshes the
seat's numbers for the menu bar. There is no code path to a purchase endpoint.

**The origin gate.** The Codex app can only send its own ChatGPT bearer, so the pool must accept loopback
requests without a client key (`api-keys: []`). CLIProxyAPI answers every origin with CORS `*`, so without a gate
any web page could drive the pool through your browser, and five keyless management calls from a page would get
loopback banned from the management API. `build/codexpool_gate.go` rejects a request when its `Host` is not
`127.0.0.1`, `localhost` or `::1` (DNS rebinding), or when it carries browser provenance: an `Origin` other than
the Codex renderer's `app://-`, or a `Sec-Fetch-Site` other than `none`. It is installed through
`api.WithEngineConfigurator`, which runs before CLIProxyAPI's access logger, CORS, auth, IP ban and routes, so
rejected requests never reach them. Its own log line quotes every field, so a page can't forge log lines.

**Build from source instead of forking.** `codexpool build` downloads the release tag's source, replaces one
anchor line in `cmd/server/main.go` (`serverOptions := []api.ServerOption(nil)`) to install the gate, adds the gate
file, and builds with CGO off using a Go toolchain downloaded from go.dev with its sha256 checked. Before the
build is used, an 11-case self-test runs it on a scratch port with an empty auth dir: Codex-like requests,
browser origins, CORS preflight, no-cors cross-site, DNS rebinding, the Codex renderer origin, management with
the key and again after six keyless browser attempts (no loopback ban), and three WebSocket upgrades. The build refuses to
continue if the anchor moved or if upstream starts using the engine-configurator slot itself. The version string
`X.Y.Z+gate.<hash>` names the upstream release and a hash of the gate source and the anchor edit.

**Upgrades switch back on failure.** `codexpool upgrade` builds, repoints `bin/current`, restarts the pool, and
checks the version it reports, the seat count and a live gate probe. Any failure, or a signal mid-way, restores
the previous build.

**One codebase, per-machine settings.** Launchd labels, the port, the interpreters and the Codex binary come from
`~/.codexpool/settings.json`, so the same code serves every install. A broken settings file stops every command,
the guard included, rather than letting it run against the wrong port or labels.

**Install is idempotent and reversible.** Each step checks first and changes only what is missing or wrong;
`--dry-run` prints the plan. Before touching `~/.codex/config.toml`, install backs it up (a dated copy in
`state/`, never overwritten) and records the original values of `openai_base_url`, `model_provider` and
`model_catalog_json` in `state/install.json`. Uninstall restores `openai_base_url` unless you have changed it
since, and puts back only the keys `--fix-config` removed. A symlinked `config.toml` is edited where it points.
Install writes `~/.codex/config.toml` whatever `CODEX_HOME` says, because the Codex app started from Finder
never sees that variable. The management key counts as missing only when the Keychain says it is not there
(`security` exit 44); a locked or unreachable Keychain stops install instead of minting a second key.

**The CLI is standard-library Python.** It needs nothing installed. The file keeps Python 3.9 syntax so that
`install` and `uninstall` run on a fresh Mac's `/usr/bin/python3`; every other command re-runs itself under the
configured Python 3.11+. Nothing that runs in the background relies on the script's `#!/usr/bin/env python3`:
the launchd agents, the `~/.local/bin/codexpool` wrapper and the menu bar app's actions all name their
interpreter, because under launchd `PATH` is `/usr/bin:/bin` and `python3` there is the system one.

**The menu bar app reads files only.** It reads `status.json` and `history.jsonl` and runs `codexpool` for
actions. It never touches the Keychain, because a locked Keychain would pop password dialogs, and it never calls
the network or the management API.

## Seat state model

CLIProxyAPI's structured `cooldowns[]` view decides a seat's state, not regexes over error messages:

| Cooldown reason | State | Shown as |
|---|---|---|
| `credential_quota`, `quota` | exhausted | Out |
| `unauthorized`, `invalid_grant`, `payment_required` | blocked | Blocked |
| anything else, still in the future | cooldown | Out |

Model-scoped cooldowns don't mark the whole seat. A seat the pool still thinks is ready but whose own usage says
it is out (a window at 100% with no credits, or `allowed: false`) is shown as exhausted early; the pool learns on
its next request. A disabled seat with a guard park record is **parked**; one without is **off** (you did it).
The first ready seat in fill order is **serving**. When CLIProxyAPI gives no structured view, codexpool falls back
to the status message.

Usage comes from two sources, merged field by field with the newest first: the `x-codex-*` headers CLIProxyAPI
records from each seat's last response, and a `wham/usage` poll made through the pool. The serving seat reports
live; idle seats are polled every 10 minutes. A window past its reset time is ignored as stale.

**The headline** is the weight-averaged weekly usage of the seats that are not off and have a known weekly figure
(a seat that is out or blocked with no figure counts as 100% used): all of them by default (`"headline": "all"`),
or only the regular ones, reserve seats left out (`"headline": "regular"`). Weights default from the plan string in
the login (`plus` 1, `prolite` and `self_serve_business_prolite` 5, `pro` 20, anything else, `team` included, 1).
`status.json` carries both figures (`used_pct_all`, `used_pct_regular`), the selected one as `used_pct`, and the
`headline` and `display` settings, so the menu bar needs nothing from `settings.json` to draw them. `display`
(`"left"` by default, or `"used"`) only changes presentation: what is left is 100 minus the used figure.
`history.jsonl` records both figures (`all`, and `used` for the regular seats) whatever the settings, so switching
`headline` keeps the chart's history.

## Known limits

- **Terms of service.** OpenAI's terms prohibit circumventing usage limits, and every seat is used from one IP
  address and one Codex installation. Fill-first plus affinity keeps seat changes rare. See the README's
  "Terms of service and risk".
- **Affinity lives in memory.** A pool restart re-places open threads, which is an ordinary (tested) seat change.
- **The Codex app's usage meter** shows only the account the app is signed in to. The menu bar shows the pool.
- **Attestation.** `x-oai-attestation` is not forwarded for pooled requests. It has not mattered so far.
- **Shared Macs.** Any non-browser process on the machine, including other users', can use the pool on loopback.
- **Platform.** macOS only; developed on Apple Silicon, Intel untested.
