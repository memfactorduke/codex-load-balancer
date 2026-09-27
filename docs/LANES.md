# Subagent lanes

A lane is a named model tier from another provider that the Codex main agent can spawn as a native subagent.
The main agent keeps running on your ChatGPT seats. A lane subagent runs on, for example, xAI's Grok 4.7 Fast,
and the pool serves it from OpenCode's Muse Spark 1.3 instead when Grok is unavailable or used up.

Lanes are optional. Without `~/.codexpool/lanes.json`, nothing on this page runs and nothing in the pool changes.

- [What a lane is](#what-a-lane-is) and [when to use one](#when-to-use-one)
- [How it works](#how-it-works) and [providers](#providers)
- [Set up a lane](#set-up-a-lane), [commands](#commands) and [lanes.json](#lanesjson)
- [What `lane apply` generates](#what-lane-apply-generates)
- [Fallback and stickiness](#fallback-and-stickiness), [known limits](#known-limits), [privacy](#privacy)
- [Troubleshooting](#troubleshooting)
- [Paste this to your agent](#paste-this-to-your-agent)

## What a lane is

- **A name**, such as `bulk`. It is the `agent_type` the main agent passes to `spawn_agent`, the name of the
  Codex role file `~/.codex/agents/bulk.toml`, and the model alias the pool serves.
- **Members**, in fallback order. Each is one provider and one model. The pool serves the lane from the first
  member while it can, then from the next.
- **A role**: one sentence on what the lane is for. The main agent reads it to decide when to use the lane.
- **An effort**: the reasoning effort every member runs at (`low`, `medium`, `high` or `xhigh`).
- **A picker label** (optional): the lane's entry in the Codex model picker. Without one, it is the lane's name
  with a capital first letter, such as "Bulk"; the members stay under the hood.

You describe lanes in `~/.codexpool/lanes.json`. `codexpool lane apply` generates everything else from it.

## When to use one

Use a lane for work that spends many tokens but doesn't need the most capable model: codebase sweeps, bulk
edits, log and data digging, first drafts, and second opinions from a different model family. A lane
subagent's tokens count against the lane provider's quota or bill, not a seat's. The main agent's own turns,
including the spawn and reading the result, still run on a seat.

Don't use a lane for work that needs top capability, for tasks that need ChatGPT connectors (lane subagents
have none), or for anything you wouldn't send to that provider (see [Privacy](#privacy)).

## How it works

```
 Codex main agent                    runs on a ChatGPT seat, as always
        │   spawn_agent(agent_type "bulk") loads ~/.codex/agents/bulk.toml: model = "bulk", apps off
        ▼
 pool (CLIProxyAPI, 127.0.0.1:8319)  alias "bulk" = the lane's members, fill-first by priority, sticky
        │
        ├─ member 1, native provider ─────────────────────────────▶ xAI: grok-4.7-build-fast
        │    payload rules: lane effort, integer arguments
        │
        └─ member 2, bridge member ──▶ lane bridge (127.0.0.1:8320) ──▶ OpenCode Go: muse-spark-1.3-contributor
             meta-api-key entry          lanes/bridge.py: provider key,
                                         request adaptation, compaction
```

The main agent spawns a subagent with `agent_type "bulk"`. Codex loads the role file of that name, which sets
the model to `bulk`, the lane's effort, instructions for a third-party model, and no connectors. The child's
requests go to the pool like any other, and the pool serves the alias `bulk` from the lane's members:

- **Native members** are providers CLIProxyAPI talks to itself. Today that is xAI, with one OAuth login.
  codexpool adds lane-scoped payload rules that set the effort and fix a type mismatch between xAI and Codex.
- **Bridge members** are providers that speak the OpenAI Responses API but not Codex's dialect of it, such as
  OpenCode. The pool sends their requests to a small local bridge, `lanes/bridge.py`, as an ordinary upstream.
  The bridge holds the provider key, adapts the request and forwards it.

Seat traffic never touches any of this. The payload rules name lane aliases only, and the bridge sits behind
the pool, so it only sees requests the pool has already routed to a lane member. A lane never falls back to a
seat, and a seat never serves a lane. `lane apply` refuses a lane name that the pool already serves (a seat
model, for instance), and `lane test` refuses a member alias the same way before it offers one, so a lane can't
take over another model's requests.

The pool offers one model per lane, under the lane's name, so the Codex model picker shows one entry per lane,
such as "Bulk", or the name you give the lane with `display` in `lanes.json`. To test one member at a time,
`lane test` also offers each member under a member alias of its own (`bulk-grok`, `bulk-muse`), but only while
it runs.

## Providers

| Provider | How the pool reaches it | Credential | Status |
|---|---|---|---|
| `xai` | natively (CLIProxyAPI's xAI provider) | `codexpool lane login xai` | tested with `grok-4.7-build-fast` |
| `opencode-go` | the bridge, `https://opencode.ai/zen/go/v1` | `codexpool lane key opencode-go` | tested with `muse-spark-1.3-contributor` |
| `opencode-zen` | the bridge, `https://opencode.ai/zen/v1` | `codexpool lane key opencode-zen` | same bridge path, untested |
| `responses` | the bridge, any OpenAI Responses API endpoint | `codexpool lane key <lane>-<id>` | generic, untested |
| Anthropic, Gemini, OpenRouter, others | | | not supported yet |

- **xAI.** One login serves every lane with an xAI member, at most one per lane. Fast tiers are separate model
  ids (`grok-4.7-build-fast`), so pick the tier by model id. The context window comes from the pool's xAI model
  list (500000 tokens for `grok-4.7-build-fast`). Once any lane has an xAI member, `lane apply` hides every
  other xAI model from the pool, so Codex is offered only the lane models.
- **OpenCode Go.** Contributor models such as `muse-spark-1.3-contributor` need training consent on your
  OpenCode workspace and are available only in some regions. OpenCode has no fast tier.
- **OpenCode Zen** uses the same bridge path as Go but is untested. Free models, including the
  `-contributor-free` ones, answer 403 outside OpenCode's own client, so they can't be lane members.
- **`responses`** is for any endpoint that implements `POST <base_url>/responses` with streaming. Add it with
  `codexpool lane add <name> --member responses:<model> --base-url https://... [--session-header <name>]`
  (the header only if the provider needs a per-thread session id), or in `lanes.json` with `base_url` and
  `session_header`. Its key is named `<lane>-<id>`. The bridge's adaptations were written for OpenCode; another
  endpoint may need more.
- **Not supported yet**: Anthropic, Gemini, OpenRouter and other APIs. They would need their own adapters.

Bridge members default to a context window of 272000 tokens; set `context` in `lanes.json` for a model that has
more or less.

## Set up a lane

You need a working install (`codexpool doctor` ends with `OK`) and an account with each provider you add.

**1. Sign in or store the keys.**

```sh
codexpool lane login xai            # opens the xAI sign-in; --no-open prints the link instead
codexpool lane key opencode-go      # asks for the key without echoing it
```

`lane login xai` runs CLIProxyAPI's own xAI login, like `codexpool login` does for seats, and prints the name
of the credential file it saved in `~/.codexpool/auth/`. That credential serves lane aliases only.

`lane key` writes `~/.codexpool/lanes/secrets/<keyname>.key` (mode 600, in a folder with mode 700) and never
prints the key. It also takes the key from a file (`codexpool lane key opencode-go ~/key.txt`; delete the file
afterwards) or on stdin. Key names are `opencode-go`, `opencode-zen`, and `<lane>-<id>` for a `responses`
member. To replace a key, run the command again, then `codexpool lane apply`, which restarts the bridge when a
key file changed.

**2. Add the lane.** Preview it with `--dry-run`, then run the same command without it.

```sh
codexpool lane add bulk \
  --member "xai:grok-4.7-build-fast:Grok 4.7 Fast" \
  --member "opencode-go:muse-spark-1.3-contributor:Muse Spark 1.3 contributor" \
  --role "A capable, fast model and a strong second opinion for token-heavy work: codebase sweeps, bulk edits, log and data digging, first drafts, second opinions." \
  --effort xhigh \
  --dry-run
```

Each `--member` is `PROVIDER:MODEL[:DISPLAY NAME]`, in fallback order; it splits at the first two colons, so a
model id that contains `:` goes into `lanes.json` by hand. A member's id defaults to the first word of its model
id (`grok`, `muse`). `--effort` defaults to `xhigh`. Give every lane a `--role`: it is what the main agent
reads. `--display` sets the lane's entry in the Codex model picker, up to 40 characters, for example
`--display "Bulk: Grok, then Muse"`; the picker cuts long names short, and without it the entry is the
lane name with a capital first letter, "Bulk". `--base-url` and `--session-header` apply to the lane's
`responses` members. `lane add` writes `~/.codexpool/lanes.json` and then runs `lane apply`.

For anything `lane add` doesn't take (a member's `context` or `id`), edit `~/.codexpool/lanes.json` and run
`codexpool lane apply --dry-run`, then `codexpool lane apply`.
[examples/lanes.json](../examples/lanes.json) is a complete file.

**3. Test it.** This runs real subagents through Codex, so it spends a little of the lane providers' quota and
a little seat quota.

```sh
codexpool lane test bulk                  # each member on its own, then the lane
codexpool lane test bulk --member muse    # one member
codexpool lane test bulk --compaction     # also forces a compaction on each member
```

For each member, and then for the lane itself, the test asks a throwaway Codex thread to spawn the subagent in
a temporary folder. The subagent must change a file and create another with `apply_patch`, then show both.
The test passes when the files are right, the child's session shows it ran on the expected model, and no tool
call failed with "failed to parse function arguments". `--compaction` also has the child read eight large
files, one at a time, which forces a compaction, and checks that a compaction happened and that the child still
reports the marker hidden in each file. The test prints a PASS/FAIL table with the seconds and a reason, and
saves the results to `~/.codexpool/state/lane-tests.json`.

To pin one member, the test offers the member aliases (`bulk-grok`, `bulk-muse`) for as long as it runs: it
adds them to the lanes block in `config.yaml`, waits up to 15 seconds for the pool to list them, and spawns each
member through a temporary role file whose model is the member alias. When the test finishes, fails or is
interrupted, it deletes the temporary roles and withdraws the aliases, so the model picker is back to one entry
per lane. If a crashed test leaves the aliases behind, `codexpool doctor` warns and `codexpool lane apply`
removes them.

**4. Start a new Codex thread**, or restart the app. Codex reads role files and `AGENTS.md` when a thread
starts.

**5. Check.** `codexpool doctor` gains a Lanes section and must end with `OK`. `codexpool lane` lists every
lane with its members' states. `codexpool status` prints one line per lane under its table:

```
lane bulk: grok ● ready → muse ● bridge ok
```

**Using a lane.** Ask in plain words: "Use the bulk lane to find every caller of `parse_config` and list
them." The main agent may also pick a lane by itself when a task fits the lane's role. It is told to check what
a lane returns before relying on it.

**Changing or removing a lane.** Edit `lanes.json` and run `codexpool lane apply`, or run
`codexpool lane remove bulk`, which takes the lane out of `lanes.json` and applies. Keys stay in
`lanes/secrets/` either way.

## Commands

| Command | What it does |
|---|---|
| `codexpool lane` (same as `lane list`) | Every lane with its effort and its name in the Codex model picker, then its members in order: id, provider, model, state and last test. State for xAI is the xAI login's state: `ready`, `cooldown`, `exhausted`, `blocked` (sign in again), `disabled`, `missing` (no login), or `unknown` when the pool doesn't answer. For a bridge member: `bridge ok` when the bridge is up, lists the model, and the key file exists; else `no key`, `bridge down` or `not in bridge` (run `lane apply`). |
| `lane add NAME --member PROVIDER:MODEL[:DISPLAY NAME] ... [--role TEXT] [--effort E] [--display TEXT] [--base-url URL] [--session-header NAME] [--dry-run]` | Adds a lane to `lanes.json` (creating the file), then applies. `--display` sets the lane's name in the Codex model picker. `--base-url` and `--session-header` are for `responses` members. |
| `lane remove NAME [--dry-run]` | Removes a lane from `lanes.json`, then applies. |
| `lane apply [--dry-run]` | Renders and writes everything below. `--dry-run` prints every change as a unified diff and writes nothing. |
| `lane key KEYNAME [FILE]` | Stores a provider key from FILE, stdin, or a prompt without echo. Never prints it. |
| `lane login xai [--no-open]` | Signs in to xAI. `--no-open` prints the link instead of opening it. |
| `lane test [LANE] [--member ID] [--compaction]` | Real subagent test through Codex, per member and then per lane. Offers the member aliases (`<lane>-<id>`) while it runs and withdraws them afterwards. Spends a little quota. |

The rest of codexpool knows about lanes too:

- `codexpool install` keeps the bridge's launchd job current whenever `lanes/bridge.json` exists.
- `codexpool uninstall` also stops the bridge and removes the generated role files and the lanes block in
  `~/.codex/AGENTS.md`. It keeps `lanes.json`, `lanes/` and the keys, so `codexpool install` followed by
  `codexpool lane apply` brings everything back.
- `codexpool doctor` checks, when `lanes.json` exists: that it is valid; that `lane apply` would not stop (for
  example on a lane name the pool already serves); that the lanes block in `config.yaml`, the role files and the
  `AGENTS.md` block match what `lane apply` would write; that the xAI login exists and isn't disabled (a
  cooldown is only a warning); that the bridge's launchd job is loaded, answers `/healthz` and lists every
  bridge model; that every key file exists with mode 600; and that the pool lists every lane alias. It warns
  when the pool still lists a member alias, which a lane test that crashed can leave behind;
  `codexpool lane apply` withdraws it.
- `codexpool status` adds the per-lane line shown above. Its `--json` output and the menu bar don't change.

## lanes.json

`~/.codexpool/lanes.json` holds your lanes. It is never committed (the `.gitignore` lists it, for checkouts in
`~/.codexpool`).

```json
{
  "lanes": {
    "bulk": {
      "role": "A capable, fast model and a strong second opinion for any token-heavy work that does not need the most capable model: codebase sweeps, bulk edits, log and data digging, first drafts, second opinions.",
      "effort": "xhigh",
      "members": [
        {"id": "grok", "provider": "xai", "model": "grok-4.7-build-fast", "name": "Grok 4.7 Fast"},
        {"id": "muse", "provider": "opencode-go", "model": "muse-spark-1.3-contributor", "name": "Muse Spark 1.3 contributor", "context": 1048576}
      ]
    }
  }
}
```

**Lanes.** Each key under `lanes` is a lane name: a lowercase letter, then up to 30 lowercase letters, digits
or `-` (`^[a-z][a-z0-9-]{0,30}$`). `default` and names starting with `zz-codexpool-test-` (lane test's
temporary roles) are not allowed.

| Key | Required | Meaning |
|---|---|---|
| `role` | yes | What the lane is for, one sentence. It goes into the role file's description and the `AGENTS.md` block. |
| `effort` | no, `xhigh` | Reasoning effort for every member: `low`, `medium`, `high` or `xhigh`. |
| `display` | no | The lane's name in the Codex model picker, one line of 1 to 40 characters (the picker cuts longer names short). Default: the lane name with a capital first letter (`Bulk`). |
| `members` | yes | The members, in fallback order. At least one. |

**Members.**

| Key | Required | Meaning |
|---|---|---|
| `provider` | yes | `xai`, `opencode-go`, `opencode-zen` or `responses`. At most one `xai` member per lane. |
| `model` | yes | The provider's model id (letters, digits and `._:/-`). |
| `id` | no | Up to 16 lowercase letters and digits, unique in the lane. Default: the first `-` or `.` separated word of the model id, lowercased, with a digit added if two members would clash. |
| `name` | no | Display name, used in the role file, the `AGENTS.md` block and the member's own entry while `lane test` runs. Default: the model id. |
| `context` | no | Context window in tokens, a positive integer. Default: from the pool's model list for xAI, 272000 for bridge members. |
| `base_url` | `responses` only | The endpoint's base URL, https; the bridge posts to `<base_url>/responses`. |
| `session_header` | no, `responses` only | A header the provider needs with a per-thread session id. |

An unknown key or a bad value is an error with a `codexpool: lanes.json: ...` message, and nothing is applied.
Keys starting with `_` are ignored, for comments.

Every lane name and every member alias (`<lane>-<id>`, below) must be unique across all lanes: lane `bulk`
with a member `fast` and a lane `bulk-fast` is an error. A `responses` member's key name, `<lane>-<id>`, can't
be `opencode-go` or `opencode-zen`, which belong to those providers. When the pool answers, `lane apply` also
refuses a lane name the pool already serves from outside the lanes block, and `lane test` refuses such a member
alias before it offers one.

**Names codexpool derives**, for the example:

| What | Pattern | Example |
|---|---|---|
| Lane alias | `<lane>` | `bulk` |
| Lane display name, its entry in the Codex model picker (unless the lane sets `display`) | `<Lane>`: the lane name with a capital first letter | `Bulk` |
| Member alias, which pins one member; offered only while `lane test` runs | `<lane>-<id>` | `bulk-grok`, `bulk-muse` |
| Member display name | `<Lane> lane: <name> only` | `Bulk lane: Grok 4.7 Fast only`, `Bulk lane: Muse Spark 1.3 contributor only` |
| Bridge model id | `lane-<lane>-<id>` | `lane-bulk-muse` |
| Bridge base URL of a member | `http://127.0.0.1:<bridge_port>/lane/<lane>/<id>/v1` | `http://127.0.0.1:8320/lane/bulk/muse/v1` |
| Lane context window | the smallest of its members' | 500000 |

The bridge's port and launchd label are the `bridge_port` (8320) and `bridge_label` (`com.codexpool.bridge`)
settings in `~/.codexpool/settings.json`.

## What `lane apply` generates

`lane apply` renders four things from `lanes.json` and writes them in this order. The output is deterministic,
so `codexpool doctor` can compare what is on disk with a fresh render. Don't edit the generated
parts by hand: the next apply overwrites them, and until then doctor reports them as out of sync.

When it is done, apply prints a summary and reminds you to start a new Codex thread (or restart the app) so
Codex sees role and `AGENTS.md` changes.

### 1. The lanes block in `~/.codexpool/config.yaml`

A marked block at the end of the pool's config. For the example it reads (the bridge key is elided here):

```yaml
# >>> codexpool lanes (generated by `codexpool lane apply` from lanes.json; edit lanes.json, not this block) >>>
oauth-model-alias:
  xai:
    - { name: "grok-4.7-build-fast", alias: "bulk", fork: false, display-name: "Bulk" }
meta-api-key:
  # The local lane bridge (lanes/bridge.py) holds the provider keys; this key only admits the pool.
  - api-key: "<bridge key>"
    priority: -10
    base-url: "http://127.0.0.1:8320/lane/bulk/muse/v1"
    models:
      - { name: "lane-bulk-muse", alias: "bulk", display-name: "Bulk", max-context-length: 500000 }
payload:
  override:
    - models: [ { name: "bulk", protocol: "codex" } ]
      params: { "reasoning.effort": "xhigh" }
    - models: [ { name: "bulk", protocol: "meta" } ]
      params: { "reasoning.effort": "xhigh" }
    # xAI writes 30000.0 for "type":"number" fields, and Codex rejects floats for these integer arguments.
    # Retype them to "integer" only where a field of that name is currently "number" (top level and in namespaces).
    - models: [ { name: "bulk", protocol: "codex" } ]
      params:
        'tools.#(parameters.properties.yield_time_ms.type=="number")#.parameters.properties.yield_time_ms.type': "integer"
        'tools.#(type=="namespace")#.tools.#(parameters.properties.yield_time_ms.type=="number")#.parameters.properties.yield_time_ms.type': "integer"
        # ... the same pair for max_output_tokens, session_id and timeout_ms
  filter:
    - models: [ { name: "bulk", protocol: "codex" } ]
      params: [ "service_tier" ]
# <<< codexpool lanes <<<
```

- **`oauth-model-alias: xai:`** exposes the xAI model under the lane alias instead of its own id, with the
  lane's display name, the same one the lane's bridge entries carry: when two providers offer one alias, the
  model picker shows the name of whichever registered last, so with different names the lane's label would
  change with provider load order.
- **`meta-api-key:`** has one entry per bridge member: its base URL on the bridge, its priority, and the bridge
  key. That key only admits the pool to the bridge; provider keys never enter `config.yaml`. Its model entry
  offers the bridge model under the lane alias, with the lane's display name and context window.
- **`payload: override:`** sets `reasoning.effort` to the lane's effort on every lane request, whatever the
  spawn asked for. Rules for xAI members use the protocol `codex`, rules for bridge members `meta`, and each
  rule names the lane alias (and the member aliases while `lane test` runs), so seat models never match (apply
  refuses lane names the pool already serves).
- **The integer fix.** xAI writes `30000.0` for JSON-schema `"type":"number"` fields. Codex's `exec_command`,
  `write_stdin` and `wait_agent` parse `yield_time_ms`, `max_output_tokens`, `session_id` and `timeout_ms` as
  integers and reject floats; without the fix, one test child failed 104 of its 104 tool calls. The rule
  retypes those four fields to `"integer"` in the tool schemas sent to xAI, only where they are currently
  `"number"`, at the top level and inside namespaces. The pool runs payload rules before it flattens namespaces.
- **`payload: filter:`** drops `service_tier` from xAI requests. It names an OpenAI service tier; on xAI a fast
  tier is a separate model id instead.
- **`oauth-excluded-models: xai:`**, present when a lane has an xAI member, lists every other xAI model from the
  pool's model list, so the xAI login offers only lane models. If your `config.yaml` already has a top-level
  `oauth-excluded-models:`, the list goes in a marked sub-block right under it
  (`# >>> codexpool lanes (xai): only the lane models are offered >>>`); otherwise it goes inside the lanes
  block. If the pool can't be reached, apply keeps the existing list and warns.

Empty sections are left out: no xAI member means no alias section and no `codex` rules, no bridge member means
no `meta-api-key`. Several lanes share the same sections.

While `lane test` runs, the block also offers each member under its member alias: a second entry for the xAI
member under `oauth-model-alias` (alias `bulk-grok`, display name "Bulk lane: Grok 4.7 Fast only"), a second
model entry for each bridge member (alias `bulk-muse`, display name "Bulk lane: Muse Spark 1.3 contributor
only", with the member's own context window, 1048576), and the member aliases next to the lane alias in each
payload rule. The test withdraws them when it ends. `lane apply` writes the block without them, and doctor
compares against that.

Apply refuses to guess. It stops with an error, and changes nothing, when `config.yaml` has a top-level section
outside the lanes block that the block also needs: `payload:` always, `oauth-model-alias:` when a lane has an
xAI member, `meta-api-key:` when a lane has a bridge member. It stops the same way on an `xai:` list under
`oauth-excluded-models:` that it didn't write. `lanes.json` can't hold such entries; remove or comment them
out, then apply again.

Before writing, apply saves the current file to `~/.codexpool/state/config.yaml.before-lane-apply`. It writes
atomically and keeps the mode at 600. The pool reloads the file by itself; apply then waits up to 15 seconds
for `/v1/models` to list every lane alias, and warns if they don't appear.

### 2. The bridge

Only when some member is a bridge member. Apply:

- writes `~/.codexpool/lanes/bridge.json` (mode 600) with the upstreams and models the lanes use;
- creates `~/.codexpool/lanes/secrets/` (mode 700) and a random `bridge.key` in it if there is none;
- stops with an error naming `codexpool lane key <keyname> <file>` if a provider key is missing;
- installs the launchd job `com.codexpool.bridge` (your `bridge_label`) from `launchd/bridge.plist.template`,
  restarts it when its code, `bridge.json` or a key file changed, and waits up to 10 seconds for `/healthz` to
  list every bridge model;
- records the job in `state/install.json`, so `install` and `uninstall` manage it with the other agents.

With no bridge members left, apply stops the job, removes its plist and deletes `lanes/bridge.json`, so
`install` doesn't bring the bridge back. The keys stay.

The bridge (`lanes/bridge.py`, standard-library Python) adapts each request:

- It adds the provider key, the provider's session header with one id per thread (`x-opencode-session` for
  OpenCode), and its own User-Agent, because the provider's CDN blocks Python's default one.
- It flattens namespaced tools into plain functions and restores the names in the response. It inlines `$ref`
  schemas, turns custom (freeform) tools into functions and back, and drops hosted tools the provider lacks,
  such as web search and image generation.
- It turns agent messages and Codex app notifications into user messages.
- It writes integer-valued float arguments (`30000.0`) as integers, the same fix the payload rule makes for xAI.
- It drops OpenAI-only fields and sends `store: false`, which asks the provider not to store the response.
- It answers compaction requests itself (below).
- It reports a provider's 429 as a quota error with a `resets_at` (from `Retry-After`, else 5 minutes), which
  the pool treats as a cooldown for that member. An HTML error page, usually a CDN block, becomes
  `502 upstream_blocked`; a rejected key becomes `401 upstream_unauthorized`.

It listens on `127.0.0.1:<bridge_port>` only. Every request except `GET /healthz`, which lists model ids and
nothing else, needs the bridge key and is refused for a non-loopback `Host` or any `Origin`, like the pool's
gate. It logs one line per request to `~/.codexpool/logs/bridge.log` (status, model, seconds, notes) and never
logs prompts, outputs or keys.

**Compaction checkpoints.** Codex (tested with 0.155) compacts a long thread by sending a normal `/responses`
request whose last input item is a `compaction_trigger`. For xAI members the pool handles it. For bridge
members the bridge asks the model for a checkpoint summary of the conversation and returns it as a compaction
item. The item is sealed: compressed, and signed with HMAC-SHA256 under `lanes/secrets/seal.key`, which the
bridge creates on first start. On later requests the bridge checks the seal and puts the summary back in place
of the item. A checkpoint whose seal doesn't verify stops the request (the seal key was replaced; start a new
subagent), and a checkpoint from another provider is dropped. The seal proves where a checkpoint came from; it
doesn't hide the summary, which sits in Codex's local session files like the rest of the thread.

### 3. Role files in `~/.codex/agents/`

One `<lane>.toml` per lane. For the example:

```toml
# Generated by codexpool (`codexpool lane apply`) from ~/.codexpool/lanes.json. Edit the lane there, not this file.
name = "bulk"
description = "Bulk lane: Grok 4.7 Fast at xhigh; Muse Spark 1.3 contributor (xhigh) takes over when Grok 4.7 Fast is unavailable or used up. A capable, fast model and a strong second opinion for any token-heavy work that does not need the most capable model: codebase sweeps, bulk edits, log and data digging, first drafts, second opinions."
model = "bulk"
model_reasoning_effort = "xhigh"
developer_instructions = """
You are a subagent running on a third-party model reached through codexpool. Do only the assigned task.
Run commands with the shell tool. Edit files by running apply_patch through the shell:
apply_patch <<'EOF'
*** Begin Patch
*** Update File: path/to/file
@@
-old line
+new line
*** End Patch
EOF
To create a file use "*** Add File: path/to/file" followed directly by the content lines, each prefixed by "+" (no @@ line in an Add File section). There is no separate apply_patch tool.
Do not spawn subagents. Finish with a short report: files changed, commands run and their results, anything uncertain.
"""

# Connector (apps) tool schemas cost ~130k tokens per request on third-party models (they get no deferred tool
# search), and a lane subagent does not need them.
[features]
apps = false
```

- **`model`** is the lane alias, so the pool picks the member.
- **`developer_instructions`**: Codex gives lane models no `apply_patch` tool, so the child edits files by
  running `apply_patch` through the shell, which Codex intercepts. The instructions show it how, tell it not to
  spawn subagents of its own, and ask for a short report.
- **`apps = false`**: the pool advertises lane models without tool search or code mode, so Codex would send
  every ChatGPT connector's tool schema inline, about 130k input tokens on each child request. With apps off, a
  child request dropped from about 156k tokens to about 28k in testing. Role files apply to the child only; the
  main agent keeps its connectors.

With several members the description reads "X at E; then Y, then Z (E) when the earlier ones are unavailable or
used up."

Apply writes a role file only when it is absent or its first line starts with `# Generated by codexpool`. It
stops, naming the file, if a role file of that name is yours. It removes role files of lanes you have removed,
again only those that still carry the marker.

### 4. The lanes block in `~/.codex/AGENTS.md`

The main agent reads `~/.codex/AGENTS.md` at the start of every thread. Apply keeps a marked block there, between
`<!-- codexpool:lanes BEGIN ... -->` and `<!-- codexpool:lanes END -->`, and leaves the rest of the file alone:

```markdown
## Subagent lanes

codexpool serves these third-party models as native subagents. This list is current and replaces any older notes about
alternative model providers.

- `bulk`: Grok 4.7 Fast at xhigh reasoning; when it is unavailable or used up, the pool serves it from Muse Spark 1.3
  contributor at xhigh instead. A capable, fast model and a strong second opinion for any token-heavy work that does
  not need the most capable model: codebase sweeps, bulk edits, log and data digging, first drafts, second opinions.
  Use the default model when a task needs top capability. Spawn it with spawn_agent, agent_type "bulk", fork_turns
  "none", and a self-contained task. Check what it returns before relying on it. If a spawn fails, do the work
  yourself rather than retrying in a loop.
```

The block is replaced on every apply, appended if it is missing, and removed when no lanes are left.

## Fallback and stickiness

The pool chooses among a lane's members the way it chooses seats: fill-first by credential priority, higher
first, with session affinity.

- **Priorities.** The xAI login keeps its own priority, 0. Each bridge member is a credential of its own (it has
  its own base URL on the bridge) and gets a priority from its position relative to the xAI member: 10 per
  position above 0 if it comes before, 10 per position below 0 if it comes after. In the example, `grok` is at 0
  and `muse` at -10. In a lane without an xAI member, the first of n members gets 10·n and each next one 10
  less.
- **Failover.** A member that answers 429 (xAI's own quota errors, or a provider limit the bridge reports) is
  cooled until its `resets_at`, and the pool replays the same request on the next member, so the child
  continues without noticing. This was tested live: with the xAI login disabled, a `bulk` child ran on the
  OpenCode member; re-enabled, the next one ran on xAI again.
- **Stickiness.** A child thread stays on the member that serves it and moves only when that member can't
  serve. New subagents go to the first member that is available.
- **All out.** When every member is cooling down, the child's request fails. The `AGENTS.md` block tells the
  main agent to do the work itself rather than retry. A lane never falls back to a seat.

A move between providers has a cost; see the next section.

## Known limits

- **Bridge members reason from scratch each turn.** The pool strips encrypted reasoning that isn't OpenAI's, so
  a bridge member never gets its own earlier reasoning back. It re-reasons, which costs tokens and time.
- **A thread that changes provider loses that provider's state.** The new member can't use the old one's
  compaction checkpoint or its reasoning. After a compaction, it continues from what came after the checkpoint.
- **No connectors.** Lane subagents run with apps off, so they can't use ChatGPT connectors. Give them tasks that
  need only the shell and files.
- **Fast tiers.** On xAI a fast tier is a separate model id, such as `grok-4.7-build-fast`; choose it in
  `lanes.json`. OpenCode has no fast tier. Codex's own service tier setting doesn't apply to lanes.
- **Free OpenCode models** answer 403 outside OpenCode's own client, so they can't be lane members.
- **Contributor models** need training consent on your OpenCode workspace, are available only in some regions,
  and their traffic may be used for training (see [Privacy](#privacy)).
- **Tested combinations.** xAI and OpenCode Go were tested with the models above, with Codex 0.155. OpenCode
  Zen and `responses` members use the same bridge path but are untested. A Codex update that changes tool
  schemas or request shapes can break a lane until codexpool is updated; it can't break seat traffic.

## Privacy

- **Lane traffic leaves for the lane's provider.** Everything a lane subagent sees goes to that provider: its
  task, the files it reads and the output of the commands it runs. Decide per provider what you are willing to
  send.
- **Contributor and free OpenCode models** may be used for training: Meta may train on their prompts and
  completions. Pick another model for work you don't want used that way.
- **Keys stay local.** Provider keys live only in `~/.codexpool/lanes/secrets/` (files 600, folder 700) and in
  the bridge's memory. They never enter `config.yaml`, and codexpool never prints them. The xAI login lives in
  `~/.codexpool/auth/` with the seat logins and is held by the pool alone; never copy it.
- **The bridge logs no content.** One line per request: status, model, seconds and notes.
- **Loopback only.** The bridge listens on `127.0.0.1`. Everything but `GET /healthz` (model ids only) needs its
  key and refuses browser origins.

Each provider's own terms and privacy policy apply to its traffic. codexpool is not affiliated with xAI,
OpenCode or Meta.

## Troubleshooting

Start with `codexpool lane` (each member's state and last test) and `codexpool doctor` (its Lanes section names
the fix for every failed check). Then look at:

- `~/.codexpool/logs/bridge.log` for bridge members: one line per request, such as `429 lane-bulk-muse 1.2s
  rate_limit_exceeded`;
- `codexpool logs -n 100` for the pool;
- the child's session file in `~/.codex/sessions/` for what the subagent did;
- `codexpool lane test <lane> --member <id>` to try one member on its own (it spends a little quota).

| Symptom | Fix |
|---|---|
| A lane child fails with "failed to parse function arguments" | `codexpool lane apply` ([details](TROUBLESHOOTING.md#a-lane-subagent-fails-with-failed-to-parse-function-arguments)) |
| A lane child's requests are about 150k tokens | `codexpool lane apply` rewrites the role file with `apps = false` ([details](TROUBLESHOOTING.md#a-lane-subagents-requests-are-about-150k-tokens)) |
| The bridge answers 401 | bad bridge key: `codexpool lane apply`; provider refused the key: `codexpool lane key` ([details](TROUBLESHOOTING.md#the-bridge-answers-401)) |
| The bridge answers 429 | provider quota; the pool cools the member until `resets_at` ([details](TROUBLESHOOTING.md#the-bridge-answers-429-rate_limit_exceeded)) |
| The bridge answers 502 `upstream_blocked` | the provider's CDN refused the request; retry later ([details](TROUBLESHOOTING.md#the-bridge-answers-502-upstream_blocked)) |
| "cannot read an encrypted message from another agent" | keep `optimize-multi-agent-v2: true` in `config.yaml` ([details](TROUBLESHOOTING.md#this-lane-cannot-read-an-encrypted-message-from-another-agent)) |
| A spawn says the `agent_type` is unknown | start a new Codex thread ([details](TROUBLESHOOTING.md#a-spawn-fails-with-an-unknown-agent_type)) |
| The model picker lists a lane's members ("Bulk lane: Grok 4.7 Fast only") | a lane test was cut off before it withdrew them: `codexpool lane apply` ([details](TROUBLESHOOTING.md#the-model-picker-lists-a-lanes-members)) |

All lane entries are in [TROUBLESHOOTING.md](TROUBLESHOOTING.md#lanes).

## Paste this to your agent

To have your own coding agent set up lanes, give it this prompt:

```text
Set up codexpool subagent lanes on this Mac.

First read ~/.codexpool/docs/LANES.md (docs/LANES.md in the codexpool checkout) and ~/.codexpool/AGENTS.md,
and follow AGENTS.md: a live pool carries all my Codex traffic, yours included.

1. Ask me which lane providers I have accounts with (xAI, OpenCode Go, OpenCode Zen, or another OpenAI
   Responses API endpoint), which models I want from each, and what role each lane should play, for example a
   fast lane for sweeps, bulk edits and second opinions. Propose the lanes: a name, the members in fallback
   order, a one-sentence role and an effort. Wait for my OK.
2. Credentials. For xAI, run `codexpool lane login xai` and wait while I sign in. For a key, ask me to run
   `codexpool lane key <keyname>` in my own terminal (it asks without echo), or, if I give you a file path,
   run `codexpool lane key <keyname> <file>` without opening the file. Never print, echo, log, copy or paste
   a key, and never read anything in ~/.codexpool/lanes/secrets/ or ~/.codexpool/auth/.
3. For each lane, run `codexpool lane add <name> --member PROVIDER:MODEL[:DISPLAY NAME] ... --role "..."
   --effort <effort> --dry-run`, show me the output, and once I agree run it again without --dry-run. For
   settings `lane add` does not take, edit ~/.codexpool/lanes.json, then run `codexpool lane apply --dry-run`
   and `codexpool lane apply`. Never hand-edit the generated blocks in ~/.codexpool/config.yaml and
   ~/.codex/AGENTS.md or the role files in ~/.codex/agents/.
4. Ask me before running `codexpool lane test`: it spends a little provider quota and seat quota. Then run
   `codexpool lane test` and `codexpool doctor`.
5. Report the lanes you set up, the lane test table (PASS or FAIL for each member and lane, with the reason),
   and whether doctor ended with OK. If something failed, say what you checked and what you suggest instead of
   retrying in a loop. Remind me to start a new Codex thread so Codex sees the lanes.
```
