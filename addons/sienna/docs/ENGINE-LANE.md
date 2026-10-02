# Engine members: Claude Code as a read-only lane

Part of the sienna add-on. The core lane system is described in the core's `docs/LANES.md`; this is the engine
provider (`sienna`) that the add-on registers with it.

The `sienna` provider runs Claude Code as a local engine behind the lane bridge. The picker label defaults to
**Claude**. Each turn starts a fresh process in the thread's Claude session; the existing Claude pool supplies
its account and handles account failover. Seat requests do not enter this engine path.

**Implementation available; live E4 acceptance is still pending.** No Claude Code version is accepted by default.
A normal apply preserves an existing acceptance only while the engine configuration is identical. An unknown
version refuses before spawning, with `engine_untested` and the acceptance command below.

```json
{
  "lanes": {
    "sienna": {
      "display": "Claude",
      "role": "Read the workspace, review changes and propose precise diffs.",
      "effort": "medium",
      "members": [
        {"provider": "sienna", "model": "claude-opus-5-5", "name": "Opus 5.5",
         "max_turns": 60, "turn_timeout": 1200}
      ]
    }
  }
}
```

The model names above are lane definitions; use the model you have available through the Claude pool.
`context_1m` is optional. `pool` is reserved and must be absent/null; `ultracode` must be false. An engine member
cannot set `base_url`, environment variables, a permission mode or accepted versions through `lanes.json`.

### Enabling and accepting an engine

Install Claude Code and the Claude pool first. With the owner present and a credits-off Claude account available:

```sh
subpool lane apply --dry-run
subpool lane apply
subpool lane apply --accept-engine --dry-run
subpool lane apply --accept-engine
subpool doctor
```

The acceptance command spends one Claude pool turn per engine. It refuses without an installed engine and current
pool launcher; dry-run never executes a probe. The probe uses the same restricted launch arguments as a lane turn,
`--max-turns 1`, and a fresh empty directory under `state/engine-probe`. Only this internal probe bypasses the
workspace trust check; nothing is added to Codex's trust configuration. It requires exactly Read, Grep and Glob,
permission mode `default`, `apiKeySource: none`, no MCP servers or plugins, a successful result with no permission
denials, an isolated transcript, and unchanged normal-profile mtimes. Settings warnings also refuse acceptance.
The exact version and init expectations are recorded only after success. Re-run acceptance after a Claude Code
update. The lane sets `DISABLE_AUTOUPDATER=1`; another terminal can still update the installed executable.

### What it can do

Lane turns can answer, plan and review using **Read, Grep and Glob in a Codex-trusted workspace**. They cannot edit,
run commands, access the network, invoke skills or spawn subagents. The role file and generated AGENTS block tell
the parent to request proposed diffs or commands as text. There is no mid-turn approval channel. Trust comes from
Codex's on-disk project table; turn metadata and prompt text cannot grant permission. Protected application and
credential directories remain excluded even if a trust entry names them.

The isolated profile is `~/.subpool/lanes/<lane>-home`, with a private temporary directory. The launcher requires
the Claude pool and exits 75 if it is down: it never silently routes an engine turn direct. Only the fixed engine
environment is passed. The placeholder bearer belongs to this isolated profile; the user's normal Claude login
is never imported. Lane turns have no claude.ai connectors, Chrome integration or Remote Control.

### Sessions, cancellation and checkpoints

A Codex thread maps to one session record and an exclusive file lock. A second turn waits up to 30 seconds, then
returns `503 session_busy`. Rewinds and workspace changes start new Claude sessions and import visible history;
forks get their own sessions. A retry runs the read-only turn again and may spend another request.

The bridge holds HTTP 200 until input delivery and exact init verification. Separate bounded readers and the SSE
writer keep cancellation independent of output pressure. The watchdog checks the client every 250 ms and sends
keepalives after 15 seconds of silence. Cancellation sends INT, then TERM after 5 seconds, then KILL after another
5 seconds. An unreaped direct child reserves its PID and process group; after reaping, the bridge signals a group
only if `ps` matches both the recorded start time and `pgid == pid`. Startup recovery applies that same identity
check to stale running ledger entries and marks them interrupted. Shutdown cancels all active groups together.

Engine compaction runs **no Claude process**. It seals a v3 checkpoint containing thread, session, last turn and
up to 60,000 characters of visible history. The seal authenticates the digest; it does not encrypt it. Later turns
verify every checkpoint. A matching session/last-turn pair recovers the cursor even if Codex pruned the visible
item IDs. A missing session record causes a new session with the digest imported as text. Older checkpoints with
no digest explicitly say that earlier history was compacted and is unavailable. Tampering or a different thread
returns `400 bad_checkpoint`.

To continue the isolated conversation interactively, the current command spelling is:

```sh
subpool claude lane-resume <session-uuid>
```

This implements the planned `sienna resume` behavior until the terminal launcher is renamed. It resolves the
session record, takes the same thread lock, rechecks the record and workspace, and holds the lock until the
terminal child exits. It uses the lane profile and required-pool environment. Unlike a read-only lane turn,
this is an ordinary interactive session: the user can approve actions there. It still has no normal-profile
login or connectors. Codex turns meanwhile wait or receive `session_busy`.

### Engine tests and CPA compatibility

Run `subpool lane test sienna` from inside a Codex-trusted git repository. It spends engine and seat quota.
The engine task reads fixture files, returns a random check value and a proposed diff, verifies that no file
changed, checks the child model and isolated transcript, and does not require `apply_patch`. `--compaction`
uses the read-only marker task and checks for a Codex compaction. Unit tests use only `tests/fixtures/stub_claude.py`.

`tests/test_cpa_passthrough.py` contains the strict request diff and the binary capture harness. It starts a
separate CPA in a throwaway HOME with fabricated credentials and a loopback stub upstream; it never uses the
installed pool's auth directory. Its allowed changes are only credential Authorization, the OAuth beta addition,
account/device identity fields, and the billing-header signature. Any other difference fails with JSON paths,
including changes to tools, thinking signatures, cache markers, TTLs, model, sampling or message content.
Transport headers such as Host and Content-Length are excluded from the comparison.

E4 must supply sanitized, shape-preserving captures for `say-ok`, `tools`, `resumed-thinking`, `cache-max`, `helper`
and a subagent request if one is emitted. Put each `{ "headers": {...}, "body": {...} }` fixture beside
`manifest.json` in a private fixture directory. The manifest has `cpa_sha256`, `claude_version`, `codex_revision`,
`models` (upstream model IDs), `subagent` (`"captured"` or `"not emitted in v0"`), and `fixtures` (case name to JSON
filename). The harness requires the exact binary hash; it does not represent synthetic input as a live capture.

```sh
CODEXPOOL_CPA_BINARY=/path/to/test-build/cli-proxy-api \
CODEXPOOL_CPA_FIXTURES=/path/to/sanitized-captures \
python3 -m unittest discover -s tests -p test_cpa_passthrough.py
```

To record compatibility for doctor, run `subpool doctor --cpa-passthrough /path/to/sanitized-captures`.
This starts only the isolated test instance of the installed Claude build and stores the verdict in
`state/engine-cpa-check.json`. Ordinary doctor calls never execute a capture test; they require a successful
record matching the build hash, accepted engine version and current exception list. Unexpected JSON paths are
shown as errors. After reviewing a specific difference, the owner may put its exact path in the top-level
`cpa_known_normalisations` list in `lanes/bridge.json` and re-run the check. Apply preserves this list and doctor
always displays it as a warning. There are no automatic exceptions or silent payload repairs.

`tests/cpa/failed_chunk_test.go` exercises CPA's actual `BuildOpenAIResponsesStreamFailedChunk` for post-init
engine errors and rate limits (§10 rows 4 and 6 of the engine design). The Python runner copies that standalone
CPA implementation and the Go test into a temporary directory, with module downloads disabled and no source-tree
edits. Set `CODEXPOOL_CPA_SOURCE` to the CPA checkout and optionally `CODEXPOOL_GO` to a Go executable. It also
recognizes a sibling `scratchpad/cpa-src` checkout. Tests skip cleanly when their source/compiler or capture/binary
prerequisites are absent; a skip is not compatibility evidence.

### Live E4 checks still required

- **[?] Acceptance on the installed Claude Code version:** exact three-tool init, mode, credential source,
  settings behavior, transcript location and normal-profile isolation (E4.1; design §§4, 10.6, 15).
- **[?] CPA passthrough:** the exact installed build changes only the documented credential-bound fields across
  all captured request shapes (E4.2; §12). Normalization paths still run for native clients; their being no-ops
  must be measured. No captured pass is bundled with this implementation.
- **[?] Metadata headers on the wire:** thread/turn labels and subagent metadata survive CPA (E4.1; §5a).
- **[?] Subscription metering:** `sdk-cli` requests consume plan usage without overage (E4.2; §§12, 15). Keep
  Anthropic's usage credits disabled; local observation cannot itself guarantee billing behavior.
- **[I] UI and policy behavior:** final-answer phase rendering, context-meter arithmetic and terminal-error
  presentation; restricted reads through symlinks; stop/retry, switch/fork/rewind, images, checkpoint recovery,
  role dispatch, terminal locking and version refusal (E4.3–8; §§6–8, 10–13, 15).

Do not treat an offline test pass or accepted init alone as completion of E4. Record the source revisions, exact
engine version, time to first token, turn duration, pool request counts, and cache read/creation totals per step.
