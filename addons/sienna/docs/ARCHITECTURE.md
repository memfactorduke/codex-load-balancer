# The Claude pool: architecture

Part of the sienna add-on. The core's `docs/ARCHITECTURE.md` covers the Codex pool, the gate and the lanes;
this is why the Claude pool is built the way it is.

The Claude pool (1.3.0, optional) puts several Claude accounts behind Claude Code the way the Codex pool puts
seats behind Codex. How to use it is in [SIENNA.md](SIENNA.md).

**Why a second CLIProxyAPI instance, not Claude seats in the Codex pool.** One process could hold both kinds of
login, but:

- *Blast radius.* Codex and Claude Code are each other's repair kit. A bad build or config reload in a shared
  process would take both down at once, leaving no agent to fix it. Two instances fail alone.
- *Who can spend Claude accounts.* CLIProxyAPI translates between the OpenAI and Anthropic APIs, so a shared process
  would serve a Claude model to Codex, a lane or any script that asked, as a third-party request on a subscription
  login. A dedicated instance whose gate admits only Claude Code closes that path.
- *Upgrade cadence.* CLIProxyAPI's Claude path changes quickly as Claude Code's wire format does. The Claude pool
  has its own build (`bin/claude-current`, setting `claude_cpa`), so it can move to a new release while the Codex
  pool stays on a build that is known to be good.
- *Separate logs and state.* Its own port (`claude_port`, 8321), `config-claude.yaml`, `auth-claude/`,
  `claude-seats.json`, log folder (`claude/logs/`) and launchd agent (`claude_label`) mean the doctor, the guard and
  a human can tell the pools apart, and uninstalling one leaves the other as it was.

The cost is one more process and one more port, and the guard polls two management APIs. It reuses everything
else: the build pipeline, the gate source, the management key, the guard's state model, the sign-in flow, the seat
commands (parameterised by pool) and the menu bar app.

**Why CLIProxyAPI for Claude too.** It passes a confirmed native Claude Code client through unchanged, regenerates
the request signature Anthropic checks, rewrites the per-account user id, cools a whole account on a 5-hour or
weekly rejection and only the model on a model-scoped one, and replays the request on the next account before any
output reaches the client. The config (from `examples/config-claude.yaml`) copies the Codex pool's routing
(fill-first, 24-hour affinity, subagents with their parent, `request-retry: 3`, passthrough headers) and adds
`claude.model-level-cooling: false`, `claude-code.disable-cloaking-model-list: true` and
`disable-claude-cloak-mode: true`. Credential-level cloak settings stay absent: they can override that default
and disable direct Messages passthrough. See [SIENNA.md](SIENNA.md#no-cloaking) for the source evidence.

**The gate's claude profile.** The Claude pool's launchd agent sets `CODEXPOOL_GATE_PROFILE=claude`. On top of the
default rules, the profile refuses the Codex renderer's `app://-` origin and requires `User-Agent: claude-cli/…`
and `x-app: cli` on every request except the management API (checked on the cleaned path, so `/v0/management/..`
can't climb out). It applies to every path, not only `/v1/*`: otherwise another client could reach the Claude
accounts through a path such as `/v1beta`. Any other profile value refuses every request, so a typo fails closed.
Unrecognised token-count clients are rejected using CPA's native header detector, because that endpoint lacks
the Messages passthrough context and would reconstruct identity headers. The default profile is unchanged,
and the gate still only accepts or rejects (AGENTS.md invariant 1). A
User-Agent check is not authentication, and any local process can send those headers; its job is to keep Codex,
lanes, scripts and browsers from reaching Claude accounts by accident or through translation.

The gate imports CPA's internal `helps.DetectClaudeCodeRequest` so its token-count check stays at least as
strict as CPA's own detector. Both pool builds require that symbol. The build checks the downloaded source
before compiling and stops with a subpool compatibility message if it is absent; it never weakens the gate
or changes a running build to accommodate an older CPA. Changes to the detector's signature still require
updating subpool and passing the gate tests.

**Base URL only, never a credential.** Claude Code reaches the pool with `ANTHROPIC_BASE_URL` alone and keeps its
claude.ai login. Setting `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` or `apiKeyHelper` would switch it to a
gateway credential, which turns off connectors, artifacts, Claude in Chrome and voice. The login's bearer that
Claude Code sends to 127.0.0.1 is never forwarded upstream: CLIProxyAPI's caller-header allowlist leaves out
`Authorization`, the upstream token is always the serving account's, and error logs mask header values.

**A launcher, not a global setting.** `claude-pool` sets the base URL for the one process it starts, after a
300 ms probe (a pool that doesn't answer means a direct start with one warning), and honours `CLAUDEPOOL=off` and
`state/claude-route`. The optional shim is the same script named `claude` in `~/.subpool/shims`; the user adds
it to `PATH`. The base URL is never put in `~/.claude/settings.json`: a settings value beats a shell export, so it
couldn't be bypassed for one launch, it has no fallback when the pool is down, and a running session might pick it
up. Nothing in `~/.claude`, the shell profiles or launchd's environment is changed, so a running Claude Code session
(an agent working on subpool, say) never moves onto the pool by surprise.

**Usage without Claude Code's credentials.** The guard reads usage only with tokens the pool owns, through the
Claude pool's `api-call` with `$TOKEN$`, like the Codex pass. It never reads Claude Code's Keychain item: reading
another program's item is what makes some usage meters prompt for the Keychain password. If Anthropic refuses the
pool's call, the guard falls back to the rate-limit headers CLIProxyAPI keeps from every answer, which are free and
current for the serving account.

**Claude credit policy is observational.** Only Anthropic's credits-off switch guarantees no paid requests.
Policy `off` with credits on is a doctor error and a `credits.mismatch` seat-status warning. Scoped exclusions
at 95% mitigate that mismatch; unverified exclusions park with reason `credits` and escalating alarms. With
credits off, scoped exclusions at 100% are optional and failures only log. Disabled-seat recovery checks saved
exclusions rather than CPA's empty registry. Last-resort stays parked until every other plan, including reserves,
is spent and a fresh credit amount is below the cap; doctor recommends a matching upstream member spend limit.
Live overage permission also drives the mismatch warning. A limit park survives lost CPA headers until its
deadline or a successful usage reading newer than the park. Alarm/exclusion parks persist a minimum deadline
(`parked_not_before`), so saved metadata or a shorter shared-limit reset cannot erase the backoff. Exclusion
notifications are deduplicated until registration succeeds. Policy-off overrides cover all credit parking;
last-resort overrides are refused. Credit deltas retain the prior reading's permission, so returning plan quota
causes a normal re-park rather than a false violation. Last-resort eligibility alone is not reported as spending.
Only precise fast/long-context refusal messages stop without rotation; Fable quota refusals retain failover.
Background Claude Code sessions run direct with `CLAUDEPOOL=off`: CPA must recognise `cli-bg` natively on every
path before the gate may admit it. Doctor flags old safety configs; `subpool claude install` rewrites them.
See [SIENNA.md](SIENNA.md#usage-credits) for the field contract, timing and unapplied registration proposal.

## Known limits

- **The Claude pool.** Remote Control doesn't work with a base URL that isn't Anthropic's, every account change
  costs one cold prompt cache on the new account, and cross-account thinking signatures rest on Anthropic's
  documentation plus `subpool claude selftest`. See [SIENNA.md](SIENNA.md#known-limits).
