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

**Soonest reset first is a fill order the guard keeps.** Fill-first in a fixed order can leave quota unused: a
seat low in the order may reach its weekly reset with most of its week untouched. With `"balancing": "reset"`
the guard, once a pass (after parking and healing), sorts the regular seats by the reset of the window whose
quota would go to waste soonest (the weekly one, else the 5-hour one) and gives them priorities in that order
through the management API, so CLIProxyAPI's own fill-first does the rest and nothing new enters the request path.
Seats without usage data follow in your order; seats that are out, parked, blocked or off serve nothing, so they
stay where they are and don't count; reserve seats stay below every regular seat. It writes priorities only when
the pool's order is wrong, and reset times within five minutes of each other count as a tie (live times jitter by
seconds), so a stable pool sees no churn, just one log line when the order does change and no notification (a new
order moves new threads while the previous seat still serves, which is no news). Session affinity still holds:
running threads stay on their seat and only new threads follow the new order, so a reorder costs no prompt cache.
Your own order is kept in `seats.json` as `manual_priority` (recorded from the pool's priorities the first time
the guard sorts, and by `codexpool order` and `codexpool priority`, which under `"reset"` change only that
record), and the first pass after switching back to `"priority"` writes it back; seats added in the meantime go
after yours, and the reserve last. Seats with equal priorities are recorded in the order the pool gave them, so
that order is the one that comes back. A fill order you change yourself (`order`, `priority`, `reserve`, Make
first) is not announced as "Codex now on …" either: those commands leave a mark in `guard.json` that the next
pass treats like its own reorder.

**Failover at admission.** CLIProxyAPI cools an exhausted seat until its `resets_at` and replays the request on
the next seat (`request-retry: 3`). Cooldowns are not saved across restarts, so a restart is a clean slate and
the pool relearns from the next 429.

**One OAuth login per seat, owned by the pool.** Seats are added with `codexpool login` (the sign-in that
`codexpool setup` and the Setup assistant also use), which runs CLIProxyAPI's own login flow and writes a new
file into `auth/`. Only the pool refreshes these tokens. The Codex app keeps its own separate login (a
different token family, even for the same account) for its usage meter, cloud tasks, plugins and sign-in.

**codexpool never handles tokens.** Usage and reset calls go through the pool's management `api-call` endpoint
with a `$TOKEN$` placeholder, and the pool substitutes the seat's token. codexpool reads only identity claims
(email, plan, account id) and the time of the last sign-in or refresh (`last_refresh`) from seat files. The
single-seat refresh response embeds tokens, so `codexpool refresh` prints only the status, and the guard reads only
the error of a refresh that failed.

**The guard parks seats that would spend credits.** It runs every 60 seconds from launchd. When a seat that the
pool still considers ready is at 100% and would pay with credits, the guard disables it until its window resets
and notifies you. The park record is written before the seat is disabled, so a crash can't leave an orphaned
disabled seat. `codexpool enable` turns a park into an override that lasts until the reset.

**The guard heals auth blocks.** A seat blocked by an auth error is refreshed every 15 minutes (hourly once you
have been told). After three failed attempts it notifies you to sign in again. A healed seat leaves probation
once a real request succeeds, or after two hours without another block; until then its flapping isn't announced.
A sign-in that OpenAI ended is not healed: when a `credential refresh failed` line in the pool's log (the pool
still serves the seat on its access token) or the answer to the guard's own refresh (the pool blocked the seat)
shows `refresh_token_invalidated` ("Your session has ended"), `refresh_token_reused`, `refresh_token_revoked`,
`refresh_token_expired` or `invalid_grant`, no refresh can work again, so the guard marks the seat, notifies you on
that pass, and stops refreshing it. The mark holds until the seat file's `last_refresh` changes (a new sign-in, or a
refresh that worked after all); a log line older than it doesn't count. The pool's seat list is no evidence: it only
says "unauthorized", and its state can outlast a new sign-in. The pool keeps serving a marked seat on an access
token that has not run out (up to a day), so while it does, the seat stays ready ("sign in again soon", a doctor
warning) and the guard announces no move and no dry pool; once the pool stops serving it, it is blocked until the
new sign-in.

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
rejected requests never reach them. Its own log line quotes every field, so a page can't forge log lines. An
[add-on](#add-ons) can run the same gate with a stricter profile of its own.

**Build from source instead of forking.** `codexpool build` downloads the release tag's source, replaces one
anchor line in `cmd/server/main.go` (`serverOptions := []api.ServerOption(nil)`) to install the gate, adds the gate
file, and builds with CGO off using a Go toolchain downloaded from go.dev with its sha256 checked. Before the
build is used, an 11-case self-test runs it on a scratch port with an empty auth dir: Codex-like requests,
browser origins, CORS preflight, no-cors cross-site, DNS rebinding, the Codex renderer origin, management with
the key and again after six keyless browser attempts (no loopback ban), and three WebSocket upgrades. An add-on's gate
profile adds its own cases to the run. The build refuses to
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
`model_catalog_json` in `state/install.json`. It sets `openai_base_url` only once the pool has a seat: before
that the pool can serve nothing, and pointing Codex at it would cut Codex off (a Codex agent running the install
included), so install notes the switch in `state/install.json` and the first seat sign-in makes it. Uninstall
restores `openai_base_url` unless you have changed it since, and puts back only the keys `--fix-config` removed. A symlinked `config.toml` is edited where it points.
Install writes `~/.codex/config.toml` whatever `CODEX_HOME` says, because the Codex app started from Finder
never sees that variable. The management key counts as missing only when the Keychain says it is not there
(`security` exit 44); a locked or unreachable Keychain stops install instead of minting a second key.

**The CLI is standard-library Python.** It needs nothing installed. The file keeps Python 3.9 syntax so that
`install` and `uninstall` run on a fresh Mac's `/usr/bin/python3`; every other command re-runs itself under the
configured Python 3.11+. Nothing that runs in the background relies on the script's `#!/usr/bin/env python3`:
the launchd agents, the `~/.local/bin/codexpool` wrapper and the menu bar app's actions all name their
interpreter, because under launchd `PATH` is `/usr/bin:/bin` and `python3` there is the system one.

**The menu bar app reads files only.** It reads `status.json` and `history.jsonl` (and an add-on pool's own status and
history files) and runs `codexpool` for
actions. It never touches the Keychain, because a locked Keychain would pop password dialogs, and it never calls
the network or the management API.

**The Settings window and the Setup assistant follow the same rules.** They are one separate PyObjC process
(`menubar/codexpool_settings.py`, single instance), started on demand by the menu bar app, `codexpool gui` or
`install.sh`. They read `status.json` and `history.jsonl` (through the menu bar app's own parsing code) and the
JSON that `codexpool doctor --json`, `codexpool lane list --json`, `codexpool lane providers --json`,
`codexpool lane models PROVIDER --json` and `codexpool version` print, and change
things only by running `codexpool` commands in the background. Every action in a window is therefore also a
terminal command, and the CLI stays the one place that changes state.

**The one-line installer is a bootstrap.** `install.sh` checks the Mac, finds or gets a Python 3.11+ (through uv,
after asking), downloads a release's source and hands over to `bin/codexpool install`, then opens the Setup
assistant. The install logic lives only in `bin/codexpool`, so the one-liner, a clone and a re-run for an upgrade
all take the same checked, repeatable steps.

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
`headline`, `display` and `balancing` settings, so the menu bar needs nothing from `settings.json` to draw them
(with `"balancing": "reset"` each seat also has an `order_reason`, such as "resets in 1d 4h"). `display`
(`"left"` by default, or `"used"`) only changes presentation: what is left is 100 minus the used figure.
`history.jsonl` records both figures (`all`, and `used` for the regular seats) whatever the settings, so switching
`headline` keeps the chart's history.

## Lanes

Lanes let the Codex main agent spawn subagents on models from other providers. The pool serves each lane under
its own alias from an ordered list of members, each one provider and one model. They are optional; how to use
them is in [LANES.md](LANES.md).

**Why payload rules instead of a proxy.** Lane models need a few request changes: the lane's reasoning effort,
and for xAI, integer types for four tool arguments that xAI otherwise fills with floats (`30000.0`) and Codex
rejects. A proxy in front of the pool would put custom code in every seat request, which is exactly what
invariant 1 rules out, because proxies that rewrite Codex payloads break on Codex updates. CLIProxyAPI's payload
rules make these changes inside the pool, declaratively. Each rule names only a lane's own aliases, with a
protocol (`codex` for xAI, `meta` for bridge members), so no seat request can match one: lane names are unique,
`lane apply` refuses one the pool already serves (doctor checks the same), and `lane test` refuses a member
alias the same way before it offers one. The type rule changes a field only where it is currently `"number"`,
so if Codex reshapes its tools the rule stops matching rather than corrupting them. The worst a Codex update can
do is break a lane, never a seat.

**Why the bridge sits behind the pool.** Some providers speak the OpenAI Responses API but not Codex's dialect
of it: namespaced and custom tools, agent-message items, compaction triggers, a required session header. The
adapter for them, `lanes/bridge.py`, is a `meta-api-key` upstream of the pool, not a proxy in front of it. It
only sees requests the pool has already routed to a lane member, and the pool keeps doing for lanes what it
does for seats: priority, cooldowns, session affinity and failover. Each member gets its own base URL on the
bridge (`/lane/<lane>/<id>/v1`), so each is a distinct credential with its own priority and cooldown. The
provider key stays in the bridge; `config.yaml` holds only the bridge key, which admits the pool. The bridge
reports provider limits as the quota-shaped 429 with `resets_at` that the pool already treats as a credential
cooldown. Like the pool, it is loopback-only and refuses browser origins; it also requires its key (only
`GET /healthz`, a list of model ids, skips these checks), and it never logs content.

**Why compaction checkpoints are sealed.** Bridge providers have no compaction of their own, so the bridge
answers Codex's inline compaction trigger with a model-written summary, returned as a compaction item. It signs
the item with HMAC under a local seal key, so that on later requests it can tell its own checkpoints from
anything else in the thread: a checkpoint from another provider is dropped, and a tampered one is refused. The
seal authenticates; it doesn't encrypt.

**Why role files turn off apps.** The pool advertises lane models without tool search or code mode, so Codex
sends every ChatGPT connector's tool schema inline, about 130k input tokens on each child request.
`apps = false` in the lane's role file cut a child request from about 156k tokens to about 28k in testing, and
a role file's config applies to the child only, so the main agent keeps its connectors. The same role file tells the
child to edit files through `apply_patch` in the shell, because Codex gives lane models no `apply_patch` tool.

**Why a block in `~/.codex/AGENTS.md`.** The main agent has to know which lanes exist, what each is for and how
to spawn one. The block is generated from `lanes.json` along with the role files, so the two can't drift apart.
It and the role files name models, which invariant 10 otherwise forbids: a lane's instructions are only useful
if they say what the lane runs.

**Priority and fallback.** The pool chooses among a lane's members the way it chooses seats: fill-first by
credential priority, with session affinity. The xAI login keeps its own priority, 0, and serves every lane with
an xAI member (at most one per lane). Each bridge member gets a priority from its position relative to the xAI
member, 10 per position above 0 if it comes before and 10 per position below 0 if it comes after (without an
xAI member, 10·n for the first of n and 10 less for each next). A member that answers a quota 429 is cooled
until its `resets_at` and the same request is replayed on the next member, inside the request; this was tested
live in both directions. A child thread stays on the member serving it. Lane aliases exist only on lane
credentials, so a lane never falls back to a seat and a seat never serves a lane.

**Why member aliases exist only during a lane test.** `lane test` has to pin one member, and the pool pins by
model name, so each member has an alias of its own, `<lane>-<id>`. Offered all the time, those aliases put every
member in the Codex model picker next to its lane, and a thread started on one is pinned to that member, without
the lane's fallback. So `lane apply` renders only the lane alias, one picker entry per lane, and `lane test` adds
the member aliases to the block while it runs and withdraws them in its cleanup, which also runs on Ctrl-C,
SIGTERM and SIGHUP. A test that is killed outright can still leave them behind; doctor warns about that, and
`lane apply` removes them. A member's display name ends in "only", so a leftover stands out in the picker. The
xAI alias entry for a lane carries the same display name as the lane's bridge entries: when two providers offer
one alias, the picker takes the name of whichever registered last, so with different names the lane's label
changed with provider load order.

**One source, rendered.** Lanes are defined in one file, `~/.codexpool/lanes.json`. `codexpool lane apply`
renders everything else from it, deterministically: a marked block in `config.yaml`, `lanes/bridge.json` and the
bridge's launchd job, role files, the `AGENTS.md` block. `codexpool doctor` compares what is on disk with a
fresh render. Generated files carry a marker; apply refuses to overwrite a file without it, and refuses to merge
its pool sections with top-level ones the user wrote, rather than guess.

## Add-ons

A second pool for another tool is an add-on: a directory `addons/<id>/` with an `addon.py` that registers its pool
instance, guard pass, doctor and status sections, gate profile, lane provider and menu bar `PoolUI` through the hooks
in [ADDONS.md](ADDONS.md). None ship with codexpool. The registry exists so the core never names another product:
every hook site iterates `ADDONS`, and a broken add-on is reported by `doctor` and skipped, never a reason for the
Codex pool to stop. The gate's profile registry is core for the same reason: an add-on's pool needs stricter
admission rules than the Codex pool, and the gate must fail closed on any profile it does not know.

## Known limits

- **Terms of service.** OpenAI's terms prohibit circumventing usage limits, and every seat is used from one IP
  address and one Codex installation. Fill-first plus affinity keeps seat changes rare. See the README's
  "Terms of service and risk".
- **Affinity lives in memory.** A pool restart re-places open threads, which is an ordinary (tested) seat change.
- **The Codex app's usage meter** shows only the account the app is signed in to. The menu bar shows the pool.
- **Attestation.** `x-oai-attestation` is not forwarded for pooled requests. It has not mattered so far.
- **Shared Macs.** Any non-browser process on the machine, including other users', can use the pool on loopback.
- **Platform.** macOS only; developed on Apple Silicon, Intel untested.
- **Lanes.** Bridge members re-reason every turn, and a lane thread that changes provider loses the other
  provider's compaction checkpoint and reasoning. See [LANES.md](LANES.md#known-limits).
