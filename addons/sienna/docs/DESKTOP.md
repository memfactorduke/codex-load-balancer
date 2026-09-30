# Pooled desktop backend

The CLI is `codexpool sienna desktop`; `codexpool claude desktop` remains an alias.
Existing `codexpool claude` commands continue to work. The desktop binds only to the
existing Claude pool. No named-pool registry or state-file migration is introduced.

The menu bar app and the Settings window give it a control (see "Menu bar and Settings" below).
Live desktop testing and metering verification are still required before release.

V1 manages the desktop only when its recorded Pool entry is the **only** third-party
configuration. An empty library is eligible for first setup. Any other listed or
unlisted configuration file blocks `pooled`, even when it is not applied or is named
Pool. No foreign entry is opened, inherited, applied or restored. Status and doctor
say: **another third-party configuration exists; codexpool leaves the desktop to you**.

The manual alternative is Claude's **Developer → Configure Third-Party Inference**:
choose gateway `http://127.0.0.1:8321` (or the configured Claude pool port), placeholder
key `codexpool`, turn model discovery off, and list the full model IDs printed by the
refusal. Use high maximum effort for each model. Manage that setup in Claude itself.

## Commands

- `status [--json]`: inspect configured/running state; exits zero even with unreadable configuration. It may update the diagnostic log-offset cache; it never changes app configuration.
- `pooled [--dry-run]`: merge the Pool entry and select 3p while Claude is stopped.
- `claudeai [--dry-run]`: select 1p first, then unapply Pool, keeping it saved. This changes mode; it does not sign out stored MCP/import credentials.
- `remove [--yes] [--delete-edited] [--dry-run]`: without `--yes`, show the plan. Select 1p before unapplying Pool. Preserve a later user selection and its mode. Delete only an unchanged, unrenamed generated Pool entry; keep edited entries and their permissions for reuse by UUID. `--delete-edited` is a compatibility no-op. Never restore a previous provider. Keep explicit 1p even when the library becomes empty.
- `rollback`: recover a pending transaction, retaining user-added permission restrictions.
- `reveal`: reveal the configuration library. No new file backups are created.
- `relaunch`: confirm, quit normally, wait for exit, and open the validated absolute bundle path. No kill and no `open -b`.

`pooled`, `claudeai`, and `remove` accept `--relaunch`. A noninteractive caller must
pass `--yes` **after its own confirmation dialog**. Without confirmation the backend
refuses before quitting. Without `--relaunch`, a running app prevents a mutation.
The GUI's approved action therefore runs, for example,
`codexpool sienna desktop pooled --relaunch --yes`.

Pooled options persist: `--models ID[,ID…]`, `--effort low|medium|high`, `--1m`,
`--no-tool-search`, `--import`, `--no-import`. Import starts off. Enabling it says the
wizard stores its own sign-in in the pooled profile. `--reclaim` replaces edited owned
fields through the key undo log. In-app option edits are adopted; an explicit flag
wins for that option. Other user keys stay untouched. There is no inheritance and no
`--fresh-policy` flag. An edited API key or a gateway URL with userinfo, a non-root
path, query or fragment must be removed in the app before reclaim: none enters the
journal. These checks do not relax the refused-key or filesystem rules.

There is no credits override. Every eligible account needs a successful poll within
15 minutes and no older than the newest account addition. Credits off passes the
existing guard predicate. A last-resort policy with a positive cap is also acceptable
with fresh, known credit state. Unknown, stale and mismatched state refuses.
`added_at` records when an account joined the pool. Signing the same account in
again preserves it; removing and adding that account again refreshes it. A login
refresh alone therefore does not move the cutoff for every account's credits poll.

## Files and recovery

`addons/sienna/desktop.py` contains the backend, loaded by the Sienna add-on.
Paths derive from the CLI's HOME, including the test helper's fake HOME. Only the
third-party profile and codexpool state are mutable; the normal profile is never
opened, resolved or statted. Status may stat the normal-mode **log** for its mtime,
without reading it.

Directory descriptors are pinned with `O_DIRECTORY|O_NOFOLLOW`. Directories must
be user-owned and not group/other writable. The install root may be a symlink to
a user-owned directory with those permissions; profile and state descendants may
not be symlinks. Files must be owned regular files with one link. Replacements use
exclusive 0600 temporary files and file/directory fsync. Tests inject their fake
HOME in process; setting `CODEXPOOL_HOME` cannot disable the production HOME check.

`desktop-txn.json` version 2 is a key-level undo log, not a file backup. It records
presence and before/after values for changed entry keys, Pool’s membership in
`_meta.json`, `appliedId`, and `deploymentMode`. Membership edits preserve every
other entry and its metadata. `desktop.json` retains the UUID, generated options,
including after remove so a retained entry is reused. It contains no original-provider
record or inherited-policy claim. Every profile write reads the current object and changes only recorded
keys. Ordinary preferences such as `mcpServers` and `globalShortcut` never enter
state or backups.

A durable journal precedes each write, recording which steps may have executed.
Recovery finishes a fully applied transaction despite unrelated preference edits.
Otherwise it restores a key only if it still equals the value codexpool wrote;
later changes are left alone and named without exposing values. It never creates
an absent, untouched entry or metadata file. Recovery keeps an edited or renamed
entry listed under its current name, even if removing generated keys leaves an empty
object. Undo groups keys by file: going to 1p writes mode first; going to 3p writes it
last, after Pool is restored. Each profile file is written at most once per transaction
or recovery attempt. A missing old mode is restored as explicit 1p for safety. Legacy journals
are discarded without replaying or opening their file backups. Existing legacy
backup folders are left untouched, not read, copied or required for recovery. Doctor
warns that each `state/desktop-backup-*` folder may hold secrets and tells the owner
to review and delete that folder in Finder when no longer needed.

All preflight refusals and planning happen before quit. A pending transaction with
a running app refuses before quit and gives the manual recovery command. The
Claude guard lock covers recovery and writes, never quit/open or readiness waits.
The second lock section compares both the contents and filesystem revisions of
`desktop.json` and the journal with the plan, and rechecks the single-entry rule.
A competing command causes refusal; recovery cannot undo that command's journal
or ownership record. A declined quit is attempted once. Failures after a successful
quit restore this command's keys and reopen; subprocess errors are reported cleanly.
Every write to `claude-status.json`, including readiness updates, holds the Claude
lock. Quit, open and readiness sleeps remain outside it.

Claude/sienna uninstall uses a separate, best-effort file cleanup. It never needs app
discovery, a supported app version or a visible chooser, and never launches or quits
the app. It switches to 1p before unapplying its own entry, keeps user edits, and
retains a removed ownership record for future reuse. It reports unsafe/unwritable
leftovers and continues uninstall rather than trapping it. A running app needs the
owner to reopen it after uninstall. Doctor retains its Desktop section when an
ownership record, journal or legacy backup exists after uninstall.

LaunchServices selects the app when several copies exist. Start time uses macOS
`proc_pidinfo` microseconds; if unavailable, second-resolution `ps` evidence cannot
prove ordering within that second. Restart state compares only codexpool’s recorded
key-write time, never app preference mtimes. App log evidence covers `main.log`,
`main.old.log` and other `main*.log`/`main.log.*` rotations. The diagnostic
`state/desktop-log-cache.json` stores inode/offset/prefix-hash cursors and redacted
evidence under its own lock; unchanged logs are not rescanned. It resets for a new
app start or pool address. No raw log lines or credentials are cached. Gate
first-client logging still uses two fixed client classes. Its evidence and doctor's
10-minute threshold are keyed to the **pool** process start, not every app relaunch;
unknown pool start means unknown evidence, and old-process markers do not count.

## UI contract: `claude-status.json` → `pool.desktop`

The guard refreshes this block; commands patch it after commit. The UI reads only this file.

| Fields | Rendering rule |
| --- | --- |
| `configured_mode` | `pooled`, `claudeai`, `other`, `none`, `unknown`; selects the control. `other` selects neither; `none` offers setup; `unknown` disables it. |
| `chooser_disabled`, `applied_name` | Chooser state is inspected only for the recorded Pool entry. Other providers are not read; `chooser_disabled` stays false for them. |
| `app_running`, `app_started_at`, `app_bundle_ok`, `app_version` | App presence and validated process identity. A wrong bundle is a doctor failure. |
| `running_mode`, `running_host` | `pooled`, `3p`, `other`, `claudeai`, `fallback`, `unknown`, or null when stopped. Only `pooled` supports “On the pool.” It requires fresh log lines showing both 3p activation and the pool's exact address. `3p` alone awaits the first request. Hosts are stripped of credentials, query and fragment. |
| `restart_required` | Show “Reopen Claude…” after confirmation. Configured and running state must remain distinct. |
| `ours`, `current`, `owned_drift`, `port_ok` | Show edited/outdated configuration and a Doctor action; do not automatically reclaim fields. |
| `credits_ok`, `credits_problems` | True/false/null plus actionable reasons. False while configured pooled warrants a warning; null is unknown. |
| `cpa_compatible`, `cpa_compatibility_reason` | True only with a recorded wire PASS for the build reported by the running Claude pool. False disables pooled desktop setup; the reason names the CPA version and remedy. |
| `cpa_build_id`, `cpa_version`, `cpa_wire_result`, `cpa_failing_paths` | Exact CPA build ID, upstream version, `PASS`/`FAIL`/`UNKNOWN`, and failing JSON/header paths. With the pool down, these describe the linked build but compatibility remains false. |
| `accepted_credits` | List of `{label, cap}` for fresh enabled-credit last-resort accounts. Show the capped-paid-use warning. |
| `txn_pending`, `txn_classes` | Disable switches for pending recovery; classes map role to `untouched`, `written`, or `foreign`. Offer Doctor/rollback guidance. |
| `pool_seen_desktop_at` | First-admitted-request evidence since the pool process started (`pool_started_at`). Add the traffic-evidence suffix only when present. It is not proof of a completed answer. |
| `old_3p_logs`, `checked_at`, `last_backup` | Older log-folder notice, freshness; `last_backup` is retained for compatibility and always null. |
| `app_start_resolution` | Added: process-start resolution in seconds (`0.000001` from macOS, `1.0` for the `ps` fallback). |

Diagnostics also include `errors`, `conflicts`, `placeholder_ok`, `models_ok`,
`mode_explicit`, `verified_with`, `seen`, `policy_inherited`,
`wire_fixtures_synthesised`, `legacy_backups`, `pool_started_at`, and the three `*_rejections_24h` counts. `owned` contains
only the safe generated rendering, never the on-disk entry's edited credential (the UI
reads one thing from it: `claudeAiImport.enabled`, whether the history import is unlocked).
`policy_inherited` is retained for UI compatibility and always null; `last_backup` is also always null.
The Doctor JSON uses the existing section/check format under **Desktop**.

## Menu bar and Settings

Both read only `pool.desktop`; every action is a `codexpool` command run in the background,
after a confirmation, with `--relaunch --yes` (codexpool quits and reopens Claude itself).
The GUI never passes `--reclaim` or a credits override; those stay terminal choices.

The **Claude tab** of the popover gains **Desktop · Pooled | Claude.ai** under Claude Code
route. The selected segment is `configured_mode`; the caption under it is the truth about the
running app: "On the pool · Chat, Cowork and Code · history in Claude-3p" only with
`running_mode: pooled` (plus " · answering" once the pool has seen a request from it),
"Configured for the pool · reopen Claude to switch" with `restart_required` (and a
**Reopen Claude…** link), "In pooled mode · the pool's address is confirmed on the first
request" for `3p`, "Configured for the pool · nothing seen from the app yet" for `unknown`,
"On its own claude.ai account" only with `running_mode: claudeai`. `other` selects neither
segment ("Another configuration is applied in the app", "· it hides the Claude.ai sign-in"
with **Run Doctor** when `chooser_disabled`); `none` selects Claude.ai with "Not set up" and
**Set Up…**; `unknown` and `txn_pending` grey the control ("Can't read the app's settings",
"A change was interrupted") with **Run Doctor**. Under a pooled desktop, warning lines:
"Credits on at claude.ai for Max B · the pooled app can spend them" (orange, from
`credits_problems`), "No fresh credits reading for …", "Paid use accepted on Max 20x, up to
$150 …" (`accepted_credits`), "The "Pool" configuration was edited in the app"
(`owned_drift`), "… points at another port", "… is out of date". The control is not drawn
without the block (an older guard) or when the backend reports `Claude app not found`.

Choosing the other segment asks first ("Switch the desktop app to the pool?" / "Go back to
Claude.ai?", saying what moves and what stays), then runs `codexpool claude desktop
pooled|claudeai --relaunch --yes`; meanwhile the control is greyed and the caption reads
"Switching Claude to the pool…", then the command's closing line ("Claude opened on the
pool") for 8 s. What codexpool would refuse is said before Claude is quit for nothing, in
its own words: credits on at claude.ai (which accounts, and the fix), an interrupted change
(`rollback`), an edited "Pool" entry (`--reclaim`), a pool that is down; the alert offers
Run Doctor. When the Claude pool is down and the desktop is pooled, the banner says so and
offers **Back to Claude.ai**. The menu bar item's Claude tooltip gains "· desktop pooled" or
"· desktop switch pending".

**Settings → Overview (Claude) → Using it** has the same control as a **Desktop** row, with
**Reopen Claude…**, **Check Health** or **Set Up…** beside it as the state asks, the warnings
as rows, a "What changes when the app is pooled" disclosure (kept: Chat, local Cowork, Code,
projects, artifacts, scheduled tasks, memory; not while pooled: mobile and web sync, cloud
Cowork, Remote Control, Claude in Chrome, voice, Design, Security and Tag, exact token
counts; Code transcripts are shared with Claude Code in the terminal), and, while pooled,
**Import claude.ai history…**: off by default; the confirm says the wizard stores its own
sign-in in the pooled profile, then `codexpool claude desktop pooled --import --relaunch
--yes` unlocks the app's Settings → Import & export → Import…. The Setup assistant's Done
step offers **Set Up Pooled Desktop…** once the credits check passes, or says why not
("Turn credits off at claude.ai for Max B first"). Health shows the doctor's Desktop section
like the others.

## Tests and release gate

Run `python3 -m unittest discover -s addons/sienna/tests -p 'test_desktop*.py'`.
The Python tests use `tests/_helpers.py`'s fake HOME. App, process, pool and launchd
operations are mocked. The audit-hook test also wraps stat/lstat and resolves
file-descriptor-relative opens to prove normal-profile isolation.

The Go wrapper defaults to the scratchpad `cpa-src` sibling of this checkout's
parent, or `CODEXPOOL_CPA_SOURCE`. Set `CODEXPOOL_GO` if Go is not on PATH and
`GOMODCACHE` to an existing offline module cache if needed. It copies CPA into the
fake home, uses fabricated credentials and a recording RoundTripper, and never
opens a listener or contacts an upstream. Missing source/toolchain/cache skips explicitly only in local runs. `CI` or
`CODEXPOOL_CI` makes missing prerequisites fail. Missing wire fixtures always fail,
including the Go test itself; build-time validation never skips or downloads a
substitute source.

Builds copy the wire test into CPA's executor package and run it before compilation.
Its failure does not block building or installing the pool for Claude Code. Each completed
build records `build_id`, `version`, `result` (`PASS` or `FAIL`), `failing_paths`, `reason`,
and `checked_at` in `state/cpa-desktop-wire/<build-id>.json`. Cached builds retain their
result; rebuilding a legacy cache without a record runs the test. The build hash covers
the wire source and all fixture names/bytes; BUILD-INFO records their hashes. The five
fixtures are explicitly synthesised, pending E2 captures.

`codexpool sienna desktop pooled` requires a recorded PASS for the **running** Claude
pool's exact build, not merely the version selected by the build symlink. Missing or
failed evidence refuses pooled desktop setup, including dry runs. Status exposes the
fields above; doctor warns when desktop is unused and errors when configured pooled.
An identity-header failure names the CPA version and changed header paths: use a CPA
version that passes, or rebuild with `build/cpa-native-desktop-3p.patch` and pass the test.
The lane's separate CPA compatibility check is unchanged.

**Session-ID exception (coordinator decision, 2026-09-28):** the supplied CPA does
not include `claude-desktop-3p` in its native entrypoints. Its
`ClaudeAgentSessionUUIDForRequest` discards unconfirmed callers' Claude session
signals and derives a stable conversation ID. Both Execute and ExecuteStream may
therefore replace `X-Claude-Code-Session-Id`, but only with the same derived ID
used in the JSON string `metadata.user_id`'s `session_id` field for that request.
The capture test independently parses that outgoing field, checks equality with
the outgoing header and the expected derived ID, and fails on a mismatch or a
missing ID. This is a narrow addition to design §1.7/§8.2's allowed differences;
all other header and body checks remain enforced. No local request rewrite is added.

[The upstream patch proposal](../gate/patches/cpa-native-desktop-3p.patch) adds the engine
to CPA's native entrypoints; [its rationale](../gate/patches/cpa-native-desktop-3p-rationale.md)
describes the review and validation needed. It is **unapplied** and is not part of
the build process. E2 §8.3 and live metering verification remain undone.
