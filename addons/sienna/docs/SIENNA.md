# The Claude pool: Claude Code through several Claude accounts

subpool 1.3.0 can run a second pool, of Claude accounts, behind Claude Code. It works like the Codex pool: one
account serves at a time, in your order; when it reaches its 5-hour or weekly limit, the next one picks up the same
request; a guard watches every account's usage, and the menu bar shows what is left. Claude Code keeps its own
claude.ai login, its settings and its conversation history.

The Claude pool is optional, and the Codex pool doesn't change when you add it. Without `subpool claude install`,
subpool is exactly what it was.

- [Before you start](#before-you-start)
- [Set it up](#set-it-up)
- [How it works](#how-it-works)
- [What works through the pool](#what-works-through-the-pool)
- [Usage, plans and the guard](#usage-plans-and-the-guard)
- [Usage credits](#usage-credits)
- [Account changes mid-conversation](#account-changes-mid-conversation)
- [Everyday commands](#everyday-commands)
- [Going direct, and rollback](#going-direct-and-rollback)
- [Maintenance](#maintenance)
- [Known limits](#known-limits)
- [Risk](#risk)

## Before you start

- subpool installed (the one-line installer, or `./bin/subpool install` from a checkout). The Claude pool uses
  its toolchain, management key and guard.
- Claude Code installed, in `~/.local/bin/claude` (where its installer puts it) or elsewhere on your `PATH`, and
  signed in with `/login` to a claude.ai account. The pool never reads or changes that login.
- Two or more Claude accounts with a Pro, Max or Team plan, and a browser in which you can sign in to each (a
  browser profile per account, or a private window).
- A few minutes to read [Risk](#risk).

## Set it up

```sh
subpool claude install --dry-run         # every step, nothing changed
subpool claude install
subpool claude login "Max A" --priority 100
subpool claude login "Pro B" --priority 90 --no-open    # open the link where you are signed in to that account
subpool claude reserve "Max A"           # optional: the account used last
subpool claude status
claude-pool                                # Claude Code, through the pool
```

**`subpool claude install`** takes six steps. Each one checks first and changes only what is missing or wrong, so
it is safe to run again:

1. It builds a CLIProxyAPI release of its own for the Claude pool (7.3.18 or later, with the origin gate) and points
   `bin/claude-current` at it. The Codex pool's `bin/current` is left alone.
2. It writes `~/.subpool/config-claude.yaml` (from [examples/config-claude.yaml](../examples/config-claude.yaml),
   mode 600, with the Codex pool's management key and `claude_port`) and creates `auth-claude/` (mode 700) and
   the pool's own log folder, `claude/logs/`.
3. It writes and loads a launchd agent (`com.subpool.claude`) that runs the pool on `127.0.0.1:8321`.
4. It probes the running gate: Claude Code must get in, other clients and browsers must not. If they get in, it
   stops the pool again and goes no further.
5. It writes `~/.local/bin/claude-pool`, the launcher.
6. It records the Claude pool in `state/install.json`.

**`subpool claude login LABEL`** signs one Claude account in to the pool. The browser opens Anthropic's sign-in
page; approve it with the account you want to add. Use `--no-open` for an account your browser is not signed in
to: the link is printed (and copied to the clipboard, unless you pass `--no-copy` or set
`CODEXPOOL_NO_CLIPBOARD=1`), and you open it in a browser profile or private window signed in to that account. The
command ends with a line such as `seat claude-….json: … plan=max_5x …`: the account, its plan tier (from Anthropic's
profile call) and its place in the fill order. Without `--priority`, a new account goes after the others and
before the reserve. Running `login` again for an account that is already in the pool signs it in again and keeps
its name.

This is a login of the pool's own. It is not Claude Code's login, and subpool never copies, imports or reads
Claude Code's credentials.

**`claude-pool`** starts Claude Code with `ANTHROPIC_BASE_URL=http://127.0.0.1:8321`, for that one process. Any
argument goes to `claude` as it is (`claude-pool --resume`, `claude-pool -p "…"`). In Claude Code, `/status` shows
the base URL and your claude.ai login. Plain `claude` still starts direct, on its own account.

In the menu bar, the item now shows two numbers, `⬡ 54%   ✳ 71%`: what is left this week in the Codex pool and in
the Claude pool, each behind its logo (from the Codex and Claude apps on your Mac; a plain drawn mark when an app
isn't there). The Settings window and the Setup assistant have a Codex | Claude switcher; the Setup assistant can
install the Claude pool and add Claude accounts too. See [MENUBAR.md](MENUBAR.md#the-claude-pool-in-the-menu-bar).

**Optional: make plain `claude` use the pool.** Once you trust it:

```sh
subpool claude shim install
```

This writes `~/.subpool/shims/claude`, the same launcher under the name `claude`, and prints the one line to add
to `~/.zprofile` (subpool never edits shell profiles):

```sh
export PATH="$HOME/.subpool/shims:$PATH"
```

Open a new terminal. `claude` then goes through the pool, and `CLAUDEPOOL=off claude` starts direct.
`subpool claude shim remove` takes it out again.

## How it works

```
 claude-pool / claude (the shim)   stock Claude Code, still signed in to its own claude.ai account
        │   ANTHROPIC_BASE_URL=http://127.0.0.1:8321   (for this process only; no API key, no auth token)
        ▼
 CLIProxyAPI on 127.0.0.1:8321     a second instance: its own build, config, logins and launchd agent
        │   gate profile "claude" · one OAuth login per account · fill-first by priority · 24 h affinity
        ▼
 api.anthropic.com, as whichever account is serving
```

**A second instance, not more seats in the first.** The Claude pool is its own CLIProxyAPI process with its own
port, `config-claude.yaml`, `auth-claude/`, log (`~/.subpool/claude/logs/main.log`) and build
(`bin/claude-current`). So one bad build or config change can't take down both Codex and Claude Code at once
(each can be used to repair the other), the Claude pool can move to new CLIProxyAPI releases while Codex stays on
a build that is known to be good, and Codex can't spend Claude accounts: CLIProxyAPI can translate between the two
APIs, and a shared process would serve a Claude model to any client that asked for one.
[ARCHITECTURE.md](ARCHITECTURE.md) has the reasoning.

**The gate's claude profile.** Both pools run the same `build/codexpool_gate.go`. The Claude pool's launchd agent
sets `CODEXPOOL_GATE_PROFILE=claude`, which keeps the loopback and no-browser rules and adds two more: the Codex
app's `app://-` origin is refused, and every request needs Claude Code's `User-Agent: claude-cli/…` and
`x-app: cli` headers (the management API, which needs the key, is the one exception). Other programs, the Codex app
and browsers get 403. Like the default profile, it only accepts or rejects; it never changes a request. Every build
passes the gate self-test for both profiles (11 Codex + 18 Claude cases) before it is used, and `subpool doctor` probes the running gate.

<a name="no-cloaking"></a>**Cloaking and native identity (F5).** The Claude config sets
`disable-claude-cloak-mode: true` with no credential-level cloak settings. This disables payload cloaking for
Messages and streaming, including newer foreground versions the native detector does not recognise.
It does **not** disable all identity reconstruction in CPA's token counter.

For the optional [pooled desktop](DESKTOP.md), the supplied CPA does not confirm
`claude-desktop-3p` as native. The coordinator approved one session-ID exception:
CPA may replace `X-Claude-Code-Session-Id` with its derived stable conversation ID
only when the same request's `metadata.user_id` JSON string has that identical
`session_id`. The capture test covers Messages and streaming and fails if either
value is missing, differs from the other, or differs from CPA's expected derived ID.
The rest of the allowed-difference checks still apply; subpool adds no request
rewriter. The [native-entrypoint proposal](../gate/patches/cpa-native-desktop-3p.patch) is
unapplied, with [upstream PR rationale](../gate/patches/cpa-native-desktop-3p-rationale.md).
Synthetic wire tests do not establish live desktop metering or E2 compatibility.

Source evidence from the inspected CPA source (`internal/runtime/executor/`):

- `claude_executor_cloaking.go`, `resolveClaudeWirePolicy`: the global switch selects `never` without setting
  `CloakConfigured`; `applyCloakingInternal` returns before prompt replacement and identity injection.
- Messages/streaming retain direct OAuth passthrough without credential cloak settings. Their request context
  makes `applyClaudeHeaders` preserve the caller fingerprint.
- `claude_executor_tokens.go` does not set that passthrough context. `claude_executor_request.go` can reconstruct
  User-Agent, Stainless and X-App headers even with cloaking disabled. CPA's native detector accepts `cli`, not
  `cli-bg`, and checks the full UA, recognised entrypoint, beta and version line.
- The gate therefore uses CPA's own `helps.DetectClaudeCodeRequest` for `/v1/messages/count_tokens`, in its
  header-only token-count mode. Unrecognised requests get 403 with an update/direct-session hint before CPA
  rewrites anything. It never reads or changes the body. Native foreground token counts still pass.
- Background sessions (`x-app: cli-bg`) are rejected on every route. Start them with `CLAUDEPOOL=off`.
  To admit them later, CPA must treat `cli-bg` as native on **every** path, including token counting,
  preserving the genuine client's X-App, Stainless and device headers.
- Keep `claude-header-defaults` absent, as in the template: the gate checks CPA's default native fingerprint.
  Doctor flags custom defaults for review. Credential cloak overrides (`cloak_mode`, `cloak_strict_mode`,
  `cloak_sensitive_words`, `cloak_cache_user_id`, per-key `cloak`) must also stay absent, even `mode: never`;
  explicit settings can disable Messages passthrough.

This uses option (a) for Messages plus a restricted form of (b) for token counting. The former gate test's dummy
handler did not exercise CPA's detector; the Go tests now do. Doctor recognises the dedicated rejection log and
suggests updating subpool or running direct.

**Existing installs:** doctor reports an error when the cloaking switch or refusal rules differ from the verified
template. Run `subpool claude install` from a direct session: it rewrites outdated `config-claude.yaml` from
`examples/config-claude.yaml`, including those safety settings. This replaces custom config edits too. A current
config is preserved on repeat installs; custom YAML is marked unverified rather than guessed safe.

**Claude Code's own login.** With only the base URL set, Claude Code keeps using its claude.ai login for everything
that isn't model traffic: connectors, artifacts, Claude in Chrome, settings and the model picker. It also sends
that login's bearer token to the pool with each request. CLIProxyAPI never forwards it: it does not pass an
incoming `Authorization` header on, it always sends the serving account's own token, and its error logs mask
header values. It is the same pattern as the Codex app's bearer on the Codex pool.

**The launcher** (`claude-pool`, and the shim if you install it) is a short shell script that `subpool claude
install` writes and marks as its own. On each start it:

1. starts Claude Code direct when `CLAUDEPOOL=off` is set or the route is `direct` (`subpool claude route`);
2. otherwise probes the pool for 300 ms; if the pool answers, it sets `ANTHROPIC_BASE_URL`, and also
   `ENABLE_TOOL_SEARCH=true` (Claude Code otherwise turns MCP tool search off for a base URL that isn't Anthropic's),
   `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` (keeps the one-hour prompt cache), and
   `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-sonnet-5` (Sonnet for helpers), each unless you set a nonempty value;
3. if the pool does not answer, starts Claude Code direct and prints one warning line.

It finds Claude Code in `~/.local/bin/claude`, else on your `PATH`, and never runs itself. It never sets
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` or `apiKeyHelper`: any of them would switch Claude Code from your
claude.ai login to a gateway credential and turn off connectors, artifacts, Claude in Chrome and voice.

**Helper model.** With a custom base URL, Claude Code otherwise falls back to the main conversation's model for
helper work such as titles, prompt hooks, WebSearch side-queries and probes. The launcher supplies the current
`ANTHROPIC_DEFAULT_HAIKU_MODEL` override; this also changes explicit `haiku` model selections. The deprecated
`ANTHROPIC_SMALL_FAST_MODEL` still takes precedence inside Claude Code if you set it; the launcher leaves it alone.
The launcher, shim and self-test environment all default unset or empty values and preserve nonempty overrides.
Doctor also warns if the helper override is set globally in launchd; these defaults belong to a pooled launch.

**1M context stays opt-in.** The inspected Claude Code 2.1.283 catalog marks Opus 4.7, 4.8, 5 and 5.5,
Fable 5/5.1 and Sonnet 5 as native 1M. Through a custom base URL its context calculation can nevertheless use
200K unless the model has `[1m]`. That suffix selects a 1M window and adds
`context-1m-2025-08-07` to the beta headers, including for a native-1M model. It is a client annotation, not a
separate model family. `CLAUDE_CODE_DISABLE_1M_CONTEXT` still disables the opt-in.

The account research lists native 1M as included in the model's plan access; legacy Opus 4.6 1M on Pro and
Sonnet 4.6 1M on all plans require credits. Fable itself can require credits regardless of context size.
Neither that research nor the client strings establish that the explicit long-context beta on a native-1M
model through this pool can never bill credits or return a credit-required 429. The client also retains a
`unclampedButBilledPast200k` classification covering several native-1M Opus models; that is not proof of server
billing, but prevents treating the catalog flag alone as a guarantee. Consequently the launcher does not set
`ANTHROPIC_DEFAULT_OPUS_MODEL` or `ANTHROPIC_DEFAULT_FABLE_MODEL`. To opt in manually, use `/model opus[1m]`
(or `/model fable[1m]` on an eligible account), then check `/context`. Credit-safe pooled behavior remains
unverified; no live billing probe is part of this change.

Evidence for this decision: the extracted Claude Code strings' `k_()` and `vB()` helper resolution;
`qd()`, `Jy()` and `bh()` context calculation; the `eP` long-context beta rule and `P0n` billing classification;
and the account research's model-access and legacy-long-context sections (reviewed 2026-09-28).

**Files** (all in `~/.subpool`):

| Path | What |
|---|---|
| `config-claude.yaml` | the Claude pool's config (mode 600) |
| `auth-claude/` | one OAuth login per Claude account (mode 700); only the Claude pool refreshes them |
| `claude-seats.json` | label, weight, reserve flag, your fill order and credit policy per account |
| `bin/claude-current` | the Claude pool's build; builds stay in `bin/versions/` |
| `claude/logs/` | the Claude pool's logs (`main.log`, error logs) |
| `state/claude-status.json`, `state/claude-history.jsonl` | what the guard's Claude pass writes, and the menu bar reads |
| `state/claude-guard.json` | the guard's Claude records: parked accounts, plans |
| `state/claude-route` | `pool` or `direct`: where new Claude Code sessions go |
| `shims/claude` | the optional shim |
| `~/.local/bin/claude-pool` | the launcher |

## What works through the pool

Claude Code's documentation ties the claude.ai features to the login, not to the base URL, and the pool leaves the
login alone. The rows marked "expected" follow from that documentation; check them on your own setup with the
commands given.

| Feature | Through the pool | Why |
|---|---|---|
| Model requests, subagents, token counting, long context, thinking, effort, compaction | **Works.** Claude Code's requests pass through as they are | CLIProxyAPI recognises Claude Code as a native client and passes it through |
| claude.ai connectors | **Expected to work** (`/mcp` lists them) | Claude Code fetches them from claude.ai with its login |
| Artifacts | **Expected to work** | publishing needs a session backed by a claude.ai login, which it still is |
| Claude in Chrome | **Expected to work** (`/chrome`) | needs `/login`; only an API key or a setup token turns it off |
| Voice dictation | **Expected to work** | only a gateway credential turns it off |
| MCP tool search | **Works**, because the launcher sets `ENABLE_TOOL_SEARCH=true` | Claude Code turns it off by default for a base URL that isn't Anthropic's |
| Web search and web fetch | **Work** | server tools pass through |
| Fast mode | Always bills credits; stock CPA has no policy-aware fast routing | Anthropic credit refusals stop without cooling ordinary capacity; successful spending can precede the guard (see [Usage credits](#usage-credits)) |
| `/usage` in Claude Code | **Misleading** | it shows the plan of Claude Code's own login and counts pooled use as that account's; the menu bar and `subpool claude status` are the pool's numbers |
| Remote Control | **Does not work** | Claude Code turns it off whenever the base URL is not Anthropic's; start those sessions direct (`CLAUDEPOOL=off claude`) |
| Cloud sessions, Slack, scheduled routines | **Bypass the pool** | they run at Anthropic on Claude Code's own login |
| The Claude desktop app's Code tab | **Stays direct** | it ignores `ANTHROPIC_BASE_URL` |

The pool's log names any request path Claude Code sends that the pool does not serve (a 404). `HEAD /api/hello`
getting 404 is known and harmless.

## Usage, plans and the guard

The guard (every 60 s under launchd, as for the Codex pool) runs a Claude pass after its Codex pass whenever the
Claude pool is installed. A failure in one pass is logged and never stops the other.

- **Usage.** It asks Anthropic's usage endpoint for each account through the Claude pool's management `api-call`,
  so the pool inserts the token and subpool never sees one. The serving account is asked every 3 minutes and the
  others every 10 (the endpoint throttles); a 429 doubles the wait, up to an hour. Each account gets its 5-hour
  window, its weekly window, any scoped weekly caps (per model family, for example) and its usage credits.
- **Passive fallback.** If Anthropic refuses the pool's usage call, the guard reads the rate-limit headers that
  CLIProxyAPI keeps from every answer instead, notifies you once, and retries the call every 6 hours. Idle accounts
  then show usage as of their last served request, credit amounts are unknown (so a `last-resort` account spends
  nothing), and `subpool doctor` says so.
- **Plans and sizes.** Once a day it reads each account's plan from Anthropic's profile call: Pro, Max 5×, Max 20×,
  Team or Team Premium. The plan sets the account's default weight, its share of the headline (Pro 1, Max 5× 5,
  Max 20× 20, Team 1.25, Team Premium 6.25). A Team organization with `seat_tier: team_tier_1`
  or rate tier `default_claude_max_5x` is Premium; an Enterprise organization stays Enterprise even
  when it has that seat tier. Existing accounts pick up corrected defaults on their next profile refresh.
  `subpool claude weight` overrides the default, and profile refreshes keep that user-set size.
- **The headline** is what is left this week across the accounts, weighted by size, like the Codex pool's.
- **Fill order and balancing.** Accounts serve in priority order (`subpool claude order`, `claude priority`),
  the reserve last. `subpool set claude_balancing reset` has the guard put the account whose weekly quota resets
  soonest first, exactly as `balancing` does for Codex; `priority` gives you your order back.
- **Healing.** An account blocked by an auth error is retried; when Anthropic ended a sign-in, the guard reads it
  from the Claude pool's log at once and asks you to sign in again (`subpool claude login LABEL` with the same
  label).
- **Notifications:** Claude now on another account, the reserve serving, every account out (with the next one
  back), an account back after its reset, an account parked for credits, one spending credits as the last resort,
  its cap reached, usage polling off, and the Claude pool not responding.

It writes `state/claude-status.json` (the same shape as `status.json`, with each account's 5-hour, weekly and
scoped windows, plan and credits) and a sample every 10 minutes to `state/claude-history.jsonl`. The Codex pass and
`status.json` are unchanged.

## Usage credits

A proxy can observe spending, never guarantee prevention: Anthropic bills a paid request before its response
reaches the pool. **Only turning usage credits off at claude.ai (Settings → Usage) guarantees no paid requests.**
The guard's dollar cap is observational too; set a matching member spend limit at claude.ai.

```sh
subpool claude credits "Pro B" off
subpool claude credits "Max A" last-resort --cap 200
```

- **`off`, credits disabled at Anthropic:** the normal case. Scoped model exclusions at 100% only save a 429
  round trip. Failed exclusions log once and never park the account; Anthropic refuses paid requests itself.
  An unknown plan needs no exclusion just for being unknown.
- **`off`, credits enabled at Anthropic:** a mismatch, reported as a doctor **error** and in the status file for
  the seat warning. The live overage `allowed` / `allowed_warning` header (or credits in use) counts too, even
  during passive polling. Turn credits off there. Until then, known model families are excluded at 95% of a scoped cap.
  Fable is excluded from the first token unless the plan is known to include it (Max or Team Premium); an unknown
  or Enterprise plan does not prove inclusion. These are mitigations, not a guarantee against fast mode or a
  request crossing the cap. If PATCH fails or model registration remains unverified after the pass's bounded
  retry, the account is parked with reason `credits` and an escalating `alarms` count. That backoff runs to its
  deadline; saved exclusions alone cannot lift it early. One notification covers a continuous registration
  failure, including retries after the backoff.
- **`last-resort`:** the account stays parked, even with its own plan quota left, until every **other** account's
  shared plan quota is spent, including the reserve. A disabled, blocked or unknown account is not evidence of
  spent quota; a scoped cap is not shared-plan exhaustion. A fresh credit reading below the cap is also required.
  It can then serve freely, with credits on intentionally. The guard parks it again at the observed cap or when
  another account's plan returns. A credit increase during an interval previously allowed to spend is not a
  policy violation just because another account has now reset: it is parked again without an alarm.
  Only one last-resort account serves at a time. `claude enable` refuses to override a guard-parked last-resort
  account and explains why; change its policy or cap instead. Serving on its remaining plan quota does not show
  “Spending credits”: that requires an exhausted plan/scoped limit with credits enabled, or an overage-in-use signal.
- **Overage observations:** the guard reads `anthropic-ratelimit-unified-overage-in-use: true` from CPA before
  slow usage/profile polls. Each fresh timestamp is handled once. Repeated parks use the existing exponential
  alarm backoff, up to 24 hours. An overage attributed to a known excluded family gets the rest of the pass to
  verify registration; a Fable cap alone never hides an unrelated fast request's spending.
  If a later poll also finds a shared limit, the park keeps the later of the limit reset and the alarm deadline.
- **Manual override for `off`:** `subpool claude enable SEAT` overrides credit parking until the printed
  deadline, including overage observations and failed exclusions. Exclusions still apply. The policy resumes
  at expiry; changing the credit policy ends the override immediately.

Manual exclusions are preserved; the guard journals and removes only its own additions. CPA unregisters disabled
accounts, so recovery never requires their registered-model list. Old scoped parks are lifted, if no shared
limit remains, after a successful usage poll newer than the park and either credits are off or the needed
exclusions are verified in the auth file. Shared-limit parks also need that newer successful poll to recover
early; stale cached readings and failed poll attempts cannot release them after CPA loses its live headers.
Without a newer poll (including passive mode), they hold until `parked_until`. Alarm/exclusion backoffs hold
until their deadline even with a newer poll. At expiry, remaining limits and exclusions are checked again.
After enabling, the guard checks registration again. Exclusion failures with credits off remain harmless;
with credits on, a persistent failure parks the seat again. The guard never re-enables an account you disabled
manually. File-watcher delays and in-flight requests remain reasons to fix the upstream credit switch.

### Status fields for the seat warning

`state/claude-status.json` adds the boolean `seats[].credits.mismatch`: **true** exactly when the account's
policy is `off` and the last usage poll reports `extra_usage.is_enabled: true`, or the live headers report
overage `allowed`, `allowed_warning`, or `overage-in-use: true`. It is false for last-resort or when neither
source says credits are on. False is not a freshness guarantee: keep using `poll_error`,
`usage_at` / `usage_source` and the file's `generated_at` for stale/unknown status. The existing
`seats[].credits.enabled` remains true/false/null and `seats[].label` supplies the name. Render the warning
independently of `state` (including parked seats), in the seat row and Settings:

> Usage credits are on at claude.ai for <label>: turn them off there (Settings → Usage).
> subpool can't stop every paid request.

The menu bar seat row and Settings already render this warning from the mismatch field, including parked seats.
Short row copy: **Turn credits off at claude.ai (Settings → Usage).**

### Credit refusals and fast mode

The Claude config template uses `oauth-request-scoped-errors` with `action: stop` for HTTP 429s whose complete
message is "Usage credits are required for fast mode", "Usage credits are required for long context", or
"Fast request rejected" (case-insensitive, optional final period). The regexes match a complete JSON message
value or a complete plain-text error, not an incidental substring elsewhere in the body.
A bare `credits_required` is deliberately not stopped: it can mean Fable's included allowance ran out, so CPA
must retain its model cooldown and retry on the next account. Shared quota errors likewise retain failover.
For the matched fast/long-context errors, CPA returns the upstream refusal without model/account cooldown,
credential rotation or outer retries. Ordinary
shared quota failures retain their existing cooldown and fallback. This is config, not a request rewrite.
Doctor checks the rule block; `subpool claude install` rewrites an outdated config when upgrading. Nonempty per-auth
`request_scoped_errors` override provider rules in CPA and must carry equivalent protection.

**Fast mode always bills credits; legacy long-context variants require them too.** Stock CPA cannot select a
credit-permitted account by request mode. With credits disabled at Anthropic the selected account returns its own
credit refusal, rather than cooling ordinary capacity across the pool. With credits enabled there, a successful
fast request on a mismatched `off` account can bill before the guard intervenes. Last-resort accounts are
parked while ineligible. The guard cannot undo a request already in flight, nor enforce an exact dollar cap
before its response. Anthropic's switch and member spend limit are the upstream controls.

### CPA evidence and remaining enforcement work

Verified against the supplied CPA source:

- `internal/runtime/executor/claude_executor_request.go` classifies fast entitlement refusals; generic
  `credits_required` retains ordinary model/quota failover; long-context refusals need the configured rules. The dedicated fast error path is in
  `claude_executor_fast_error.go` and the execute/stream callers.
- `sdk/cliproxy/auth/conductor_request_scoped_errors.go` implements stop without cooldown; execution and streaming
  apply it before `MarkResult`. No CPA code change is needed for refusal handling.
- `sdk/cliproxy/auth/quota_signals.go` and management's `auth_files.go` already expose the overage header. No new
  header collector is needed. Snapshots are response-derived; successful streams may publish only after draining.
  There is no management event subscription. A guard pass still runs once a minute, and concurrent/in-flight
  requests can spend before it sees anything.
- `auth_files_fields.go` PATCH persists exclusions, but registration consumes derived attributes refreshed later
  by file synthesis. The guard queries `/auth-files/models` after PATCH and records a doctor warning if a forbidden
  family remains or an enabled account's registration cannot be checked. After a bounded retry in the pass,
  credits-on failures park the account; credits-off failures only log. PATCH success alone does not prove
  registration. Disabled-account recovery uses saved metadata, because this endpoint returns no models for them.
  Clearing exclusions also relies on that watcher; restored capacity can be delayed. Concurrent manual edits
  between reading metadata and PATCH remain a race because CPA has no conditional metadata update.

[build/claude-exclusions.patch](../gate/patches/claude-exclusions.patch) is an **unapplied proposal**, checked only for
patch applicability. It rebuilds merged exclusion attributes before synchronous registration and clears stale
attributes when removing patterns. It does not enforce credit policy or stop in-flight requests. Upstream should
add PATCH add/clear/global-merge tests before accepting it; no CPA binary here includes it.

No proxy-side change can promise zero spending on a request that crosses a quota boundary: the response arrives
after billing. The accepted guarantee is Anthropic's own credits-off switch. The unapplied patch only improves
exclusion registration; it does not supply that guarantee or an exact cap.

## Account changes mid-conversation

- **Failover.** An account at its 5-hour or weekly limit answers with a 429 before any output. CLIProxyAPI cools
  that account until its reset and replays the same request on the next one, so the turn goes on after a pause of
  a second or two. A limit on one model only (a scoped weekly cap) cools only that model on that account. When every
  account is out, Claude Code gets the error with the time to wait and shows its usual limit message.
- **Affinity.** A conversation stays on its account for 24 hours and moves only when that account can't serve.
  Subagents stay on their parent's account.
- **Thinking.** Anthropic documents thinking signatures as bound to the conversation and the model, not to an
  account, and CLIProxyAPI changes nothing they depend on. If one is ever refused, Claude Code's recovery drops the
  earlier thinking and retries: the conversation continues, without its earlier reasoning. `subpool doctor` looks
  for those refusals in the Claude pool's log.
- **The prompt cache belongs to the account.** Each account change costs one full cache write on the new account,
  from its quota, and so does a pool restart (affinity is kept in memory). Fill-first, affinity and switching only
  at a real limit keep this rare.
- **Claude Code's "approaching limit" warnings** come from the serving account's headers, so their percentage jumps
  after a change.

`subpool claude selftest A B` checks a pair of your accounts: a throwaway `claude -p` conversation with thinking on
A, then A out of rotation and the next turn on B (`--compact` adds a `/compact` on A before the move; `--model`
picks the model). PASS means every turn worked, no thinking signature was refused and the turn on B carried on
from A's answer; INCONCLUSIVE means it all worked but A showed no thinking to carry across. It spends a few
requests on both accounts and takes the other accounts out of rotation for a few minutes, so it asks first; run it
when you are not in the middle of work.

## Everyday commands

| Command | What it does |
|---|---|
| `subpool claude status [--live] [--json]` | the accounts, their windows, credits and states, and where new sessions go (`--live` asks the pool now; `--json` is what the menu bar reads) |
| `subpool claude login LABEL [--no-open] [--no-copy] [--priority N]` | add an account, or sign one in again |
| `subpool claude label SEAT LABEL`, `weight SEAT N` | rename an account, set its size |
| `subpool claude order SEAT [SEAT ...]`, `priority SEAT N` | the fill order |
| `subpool claude reserve SEAT [--off]` | use an account last (red in the menu bar while it serves) |
| `subpool claude enable SEAT`, `disable SEAT` | in or out of rotation |
| `subpool claude credits SEAT off`, `credits SEAT last-resort --cap USD` | the account's credit policy |
| `subpool claude remove SEAT --yes` | delete the account's login from the pool (Anthropic keeps the login; sign it out at claude.ai if you want to) |
| `subpool claude route [pool\|direct]` | where new Claude Code sessions go (no argument: print it) |
| `subpool claude shim install\|remove` | plain `claude` through the pool, or not |
| `subpool claude logs [-f] [-n LINES]` | the Claude pool's log |
| `subpool set claude_balancing priority\|reset` | your fill order, or soonest reset first |
| `subpool claude selftest A B [--compact] [--model MODEL] [--yes]` | the account-change test above |
| `subpool status`, `subpool doctor` | both pools; each has a Claude section once the Claude pool is installed |

`SEAT` is an account's label or part of one. The Settings window does all of this with buttons: switch Overview,
Seats or Balancing to Claude.

`subpool doctor` checks the Claude pool's process, build and config, its gate (Claude Code 200; other clients,
browsers and `app://-` 403), the accounts and their usage polling, the credit policies, the launcher, the route and
the shim, the Claude pool's log (thinking-signature refusals, blocked requests), and that no `ANTHROPIC_*` variable
is set in launchd's environment, where it would reach every app started from the Dock or Finder.

## Going direct, and rollback

Fastest first:

| Situation | Do this | Effect |
|---|---|---|
| One session | `CLAUDEPOOL=off claude-pool`, or plain `claude` without the shim (`CLAUDEPOOL=off claude` with it) | that session runs direct, on Claude Code's own account |
| From now on | `subpool claude route direct`, or **Route: Direct** in the menu bar | new sessions start direct; running ones stay where they are; `route pool` turns it back |
| A running pooled session | `/exit`, then `CLAUDEPOOL=off claude --resume` | the same conversation, direct (one turn without a warm cache) |
| The Claude pool crashed | nothing | launchd restarts it; Claude Code retries connection errors, and new launches start direct until it answers |
| Take it out | `subpool claude uninstall --yes` (without `--yes` it prints the plan) | stops the Claude pool, removes its launchd agent, `claude-pool`, the shim and the route file; keeps `auth-claude/`, `config-claude.yaml`, the builds and logs, so `subpool claude install` brings it back |

`subpool uninstall --yes` takes the Claude pool out too, since without the guard nothing would enforce its credit
policies. Neither touches Claude Code, its login or `~/.claude`. If you added the shim's `PATH` line, remove it
from `~/.zprofile` yourself.

## Maintenance

- **Work on the Claude pool from a direct session.** In pool mode, a pool that is down is a Claude Code that is
  down, including the one doing the repair. Use `CLAUDEPOOL=off claude` (or plain `claude` without the shim) for
  anything that restarts, rebuilds or reconfigures it. [AGENTS.md](../AGENTS.md) says the same to coding agents.
- **CLIProxyAPI versions.** `subpool claude install` builds the newest release the first time and then keeps the
  version it runs; it never moves to a newer release by itself. To change it, set `claude_cpa` in
  `~/.subpool/settings.json` (for example `"claude_cpa": "7.3.20"`; 7.3.18 or later) and run
  `subpool claude install`. Builds stay in `bin/versions/`, so setting the old version back and running install
  again goes back. `subpool upgrade` moves only the Codex pool.
- **The gate.** Both pools build from the same `build/codexpool_gate.go`, and a build is keyed by its hash. After a
  subpool update that changed the gate, `subpool claude install` rebuilds the Claude pool with it, and the Codex
  pool keeps its build until its next `subpool upgrade`; `subpool doctor` says which pool runs an older gate.
- **After a subpool update**, run `subpool claude install` once: it refreshes the launcher, the plist and the
  config if they changed.

**Settings** (in `~/.subpool/settings.json`; see the README's [Configuration](../../../README.md#configuration)):

| Key | Default | Meaning |
|---|---|---|
| `claude_label` | `com.subpool.claude` | launchd label of the Claude pool |
| `claude_port` | `8321` | its loopback port; must differ from `port` and `bridge_port` (unset, it moves to the next free port when those take 8321) |
| `claude_balancing` | `"priority"` | `"priority"` (your fill order) or `"reset"` (soonest weekly reset first) |
| `claude_cpa` | `null` | the Claude pool's CLIProxyAPI version, 7.3.18 or later; `null` = the newest release when it is first built |

## Known limits

- **Remote Control** doesn't work through the pool; start those sessions direct.
- **The Claude desktop app, cloud sessions, Slack and scheduled routines** don't go through the pool.
- **`/usage` in Claude Code** shows its own login's plan, not the pool.
- **Cold cache on every account change**, paid from the new account's quota, and after every pool restart.
- **Usage polling depends on Anthropic accepting the pool's usage call.** If it is refused, idle accounts show
  usage as of their last served request.
- **Fingerprint churn.** Anthropic sets a minimum Claude Code version for new models, and CLIProxyAPI recognises
  Claude Code releases as it knows them. Keep Claude Code current, and expect to move the Claude pool to new
  CLIProxyAPI releases more often than the Codex pool.
- **Re-authentication.** Claude account logins in the pool may need a new sign-in from time to time; the guard
  notifies you.
- **One IP, one device.** Every account is used from the same Mac.
- **Privacy.** The Claude pool's error logs contain request bodies (header values are masked), as the Codex pool's
  do.
- **Shared Macs.** Any process that sends Claude Code's headers from this Mac can use the pool on loopback.

## Risk

Anthropic's documentation describes subscription use of Claude Code as ordinary, individual use, sets its limits on
that assumption, and says it may enforce its policies without prior notice. Several subscriptions pooled behind a
proxy on one Mac is not what that describes. There are public reports of accounts being restricted for use through
third-party clients; the Claude pool runs genuine Claude Code on accounts you own, and whether that makes a
difference is not known. A Team or Enterprise seat belongs to an organization, so action on it affects the
organization, not only you.

Usage credits are the other risk: an account with credits on can keep billing past its limit, and if Anthropic ever
bills pooled traffic as something other than Claude Code, it bills from the first token instead of refusing. The
credit guard reacts to observations, with no guarantee that it prevents the first charge;
turning credits off at claude.ai removes the risk, and fast mode with it.

Whether to run the Claude pool is your call, and the risk is yours. Use it only with accounts you own, and follow the
terms that apply to them. subpool is an independent project, not affiliated with or endorsed by Anthropic.
