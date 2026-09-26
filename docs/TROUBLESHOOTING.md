# Troubleshooting

Start with:

```sh
codexpool doctor
```

It checks the pool process, the running build and its gate, the management key, the Codex config, every seat,
the guard, the menu bar app, the last 24 hours of pool errors and, if you use them, your lanes. Every failed
check prints the fix on the line below it, and a healthy setup ends with `OK`.

Logs, all in `~/.codexpool/logs/`:

| File | What | Tail it with |
|---|---|---|
| `main.log` | the pool (CLIProxyAPI): requests, seat cooldowns, config reloads, gate rejections | `codexpool logs -f` |
| `guard.log` | guard decisions: parks, heals, resets, every notification | `codexpool logs --guard -f` |
| `launchd.log`, `guard.launchd.log` | crashes and output of the pool and guard processes | `tail -f` |
| `menubar.log` | the menu bar app | `tail -f` |
| `bridge.log` | the lane bridge, if a lane uses it: one line per request (status, model, seconds, notes), never content | `tail -f` |

The launchd labels below are the defaults; yours are in `~/.codexpool/settings.json`.

**To take the pool out of the path right away**, remove the `openai_base_url` line from `~/.codex/config.toml`
(or run `codexpool uninstall --yes`) and reopen the Codex app. It then talks to OpenAI directly with its own
login.

## Codex

### Codex stopped working right after the install

Install points Codex at the pool, and the pool has no seats until you add them, so every request fails in
between. Add a seat: in the Setup assistant (Add a ChatGPT account… in the menu bar, or
`codexpool gui setup-welcome`), with `codexpool setup`, or by hand with `codexpool login "<Label>" --priority <n>`.
Then quit (⌘Q) and reopen Codex. To go back instead, run `codexpool uninstall --yes` and reopen Codex.

### Codex can't reach the model / every request fails at once

The pool is down or unreachable.

```sh
codexpool doctor
codexpool restart
codexpool logs -n 100
```

If `doctor` says the launchd job isn't loaded, run the `launchctl bootstrap` line it prints, or re-run
`codexpool install`. If the pool crashes on start, `~/.codexpool/logs/launchd.log` says why; a broken
`config.yaml` is the usual cause.

### My threads disappeared from the sidebar

`~/.codex/config.toml` has a `model_provider` line. A custom provider hides every thread made with the built-in
one. Remove the line, or run `codexpool install --fix-config` (uninstall puts it back), then reopen Codex. The
threads were never deleted.

### New models never appear in the picker

`~/.codex/config.toml` has `model_catalog_json`, which freezes the picker. Remove it or run
`codexpool install --fix-config`. Also check `oauth-excluded-models` in `~/.codexpool/config.yaml`.

### I changed the Codex config but nothing changed

The Codex app reads `config.toml` at start. Quit it fully (⌘Q) and reopen it.

### The Codex app's usage meter doesn't match the menu bar

Expected. The app's meter shows only the account the app itself is signed in to. The menu bar shows the pool.

## Seats

### A seat says Blocked or "Re-login needed"

Its login was rejected (`unauthorized`, `invalid_grant`) or its plan lapsed (`payment_required`). The guard
already retries a refresh every 15 minutes and notifies you after three failures. By hand:

```sh
codexpool refresh "Work B"
codexpool login "Work B" --priority 300 --no-open     # if it stays blocked; open the link in a private window
```

Pass the seat's current `--priority` again: the new login rewrites the file that holds it. The menu bar's
**Re-login…** does this for you.

### doctor reports `refresh_token_reused`

Something else is using a copy of a seat's login. ChatGPT refresh tokens rotate, so two holders of one login sign
each other out. Find what copied it (another proxy, a backup restore, a synced folder), stop it, then re-login the
affected seat. Never copy files out of `~/.codexpool/auth/`.

### A seat is Parked

The credit guard took it out of rotation: it reached 100% but would have kept answering by spending credits. It
comes back by itself when its limit resets, and you got a notification saying when. To use it anyway (it will
spend credits until the reset):

```sh
codexpool enable "Work A"
```

To stop this happening, turn off auto top-up for that account in ChatGPT's billing settings.

### Everything is red, or every seat is Out

Red means the reserve is serving and the regular seats are spent. Grey with a triangle and "All out" means every
seat is spent. The popover's "Next back" line says when the first seat returns. If a seat has a banked reset, the
popover says "Reset available"; click that seat and choose **Use reset now…**.

### A seat I didn't touch shows as Off

Something disabled it: you, the menu bar, or an interrupted `selftest` (the guard undoes those on its next pass).
If you got the notification "codexpool guard state was unreadable", the guard lost its park records and a parked
seat can show as Off. Turn it back on with `codexpool enable <seat>`.

### New threads go to the wrong seat

Fill order is priority, highest first. `codexpool status` shows each seat's priority. Seats with the same priority
are used in file-name order, and `doctor` warns about them. Fix it with `codexpool priority <seat> <n>`, or
**Make first** in the menu bar. Only priority decides the order: `codexpool reserve <seat>` moves a reserve that
would be used before a regular seat to the end, but a priority you set by hand afterwards wins.

### After a restart, a thread moved to another seat

The pool keeps session affinity in memory, so a restart places open threads again. That is an ordinary seat
change and the thread continues.

### `codexpool status` shows a seat twice, or login warns "is in the pool 2 times"

The same account and workspace was signed in twice under different file names. Remove the stale one:

```sh
codexpool remove <seat file or label> --yes
```

### "matches several seats" / "matches no seats"

`<seat>` matched more than one seat or none. Use the full label in quotes (`"Work A"`), the seat file name, or
the email.

## Signing in

### The sign-in opened in the wrong account

Your browser is signed in to another account. Use `--no-open` and open the printed link in a private window:

```sh
codexpool login "Work B" --priority 300 --no-open
```

Pick the right workspace on the ChatGPT page; each workspace is a separate seat.

### "login did not complete; nothing changed"

The sign-in was cancelled, timed out, or the link expired (it lasts a few minutes). Run the command again; the
output above the message shows what CLIProxyAPI's login flow reported. If the browser flow keeps failing, try
`--device`, which works only when device-code sign-in is enabled in the account's ChatGPT security settings.

### The priority I set was not applied

`login` sets the priority once the pool has loaded the new file. If that takes more than 20 seconds it prints a
warning with the command to run, for example `codexpool priority "Work B" 300`.

## Resets

### "has no banked resets"

The seat has no free reset credits right now. The count in the menu bar comes from the guard's last check (every
10 minutes), so it can be a few minutes behind.

### "This seat's usage does not need a reset right now; the credit was not used."

ChatGPT refused because the seat isn't at a limit. Nothing was spent. Use the reset when the seat is out.

### "That reset was already used." / "No resets are available."

The credit was redeemed elsewhere (for example in another app or on another Mac) or expired between the check and
the click. The next guard pass updates the count.

### The seat still shows Out after a reset

The reset clears the pool's cooldown and refreshes the seat's numbers, then the menu bar runs a guard pass. Click
**Refresh** in the popover, or run `codexpool status --live`. Each attempt is recorded in
`~/.codexpool/state/resets.jsonl` and `guard.log`.

## The menu bar

### The menu bar item is missing

- **Hidden.** On a Mac with a notch, or with many menu bar items, macOS hides items that don't fit. Remove or
  shrink others, or ⌘-drag the item closer to the clock.
- **Not running.** `launchctl print gui/$(id -u)/com.codexpool.menubar` shows whether it is loaded and its pid.
  Start it with `launchctl kickstart gui/$(id -u)/com.codexpool.menubar`. If you used **Quit**, it stays stopped
  until your next login.
- **Crashing.** Read `~/.codexpool/logs/menubar.log`. `No module named 'AppKit'` means PyObjC is missing from its
  interpreter: re-run `codexpool install`, which installs it.

### The item is grey with a warning triangle

The pool is down, or the guard hasn't written `status.json` for more than 3 minutes (the popover says which).
Right after the Mac wakes, the app waits 2 minutes for the guard to catch up.

```sh
codexpool doctor
launchctl print gui/$(id -u)/com.codexpool.guard
codexpool logs --guard -n 50
```

### A menu action failed

The popover header shows the error for a few seconds. Run the same command in Terminal to see all of it, for
example `codexpool reset "Work B"`. The app runs `~/.codexpool/bin/codexpool` with the `python` from
`settings.json` (else `~/.codexpool/.venv/bin/python`); if that interpreter is gone, re-run `codexpool install`.

### Notifications never appear

codexpool sends them with `osascript`, which macOS lists as **Script Editor**. Allow it in System Settings →
Notifications → Script Editor. Every notification is also written to `~/.codexpool/logs/guard.log`.

## Install, upgrade, uninstall

### "You have not agreed to the Xcode license agreements"

The Mac's `/usr/bin/python3` (and `git`) belong to Xcode, and Xcode's license hasn't been accepted. Run
`sudo xcodebuild -license accept`, or start the installer with another Python 3.9+, for example
`/opt/homebrew/bin/python3 ./bin/codexpool install`. After install nothing depends on `/usr/bin/python3`: the
`codexpool` command, the guard and the menu bar app name their interpreter.

### "not installed yet (… missing)"

`login`, `status` and `doctor` need an installed pool. Run `./bin/codexpool install` from your checkout first.

### "cannot reach go.dev (…)" during install

The message shows the actual error for each host. Install downloads from go.dev, proxy.golang.org,
api.github.com and codeload.github.com, so allow those through any firewall or proxy.

`CERTIFICATE_VERIFY_FAILED` means that Python can't check HTTPS certificates, which is usual for Python from
python.org until you run **Install Certificates.command** in `/Applications/Python 3.x`. Run that, or start the
installer with Homebrew Python or uv instead:

```sh
uv run --managed-python --no-project --python 3.13 python ./bin/codexpool install
```

### The pool, guard or menu bar won't stay loaded

On macOS 13 and later, launch agents can be switched off in System Settings → General → Login Items &
Extensions, where codexpool's three show under the names of their programs (for example `python3.13` and
`cli-proxy-api`). Turn them back on, then run `codexpool install`, which loads whatever isn't loaded. If one
still stops, its log says why: `~/.codexpool/logs/launchd.log` (pool), `guard.launchd.log` (guard) or
`menubar.log`.

### "no Python 3.11+ and no uv on this Mac"

Install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`) or Python (`brew install python@3.13`) and re-run
`./bin/codexpool install`. With uv, the installer fetches Python 3.13 by itself.

### "Codex app not found"

Install the Codex desktop app from OpenAI. codexpool finds it by its bundle id (`com.openai.codex`) through
Spotlight, then as `ChatGPT.app` or `Codex.app` in `/Applications` or `~/Applications`, and uses the `codex` CLI
inside it: `Contents/Resources/codex-cli/bin/codex` since version 26.924, `Contents/Resources/codex` before. If
Spotlight indexing is off and the app lives somewhere else, set `"codex_bin"` in `~/.codexpool/settings.json` to
that CLI inside the app, or to a `codex` CLI of your own.

### "something else is listening on 127.0.0.1:8319"

Find it with `lsof -nP -iTCP:8319 -sTCP:LISTEN` and stop it, or choose another port. On a fresh install, create
the settings file before running `install`:

```sh
mkdir -p ~/.codexpool && echo '{"port": 8329}' > ~/.codexpool/settings.json
```

The folder then isn't empty, so `git clone … ~/.codexpool` would fail; clone anywhere else and run the installer
from there. On an existing install, change `port` in both `settings.json` and `~/.codexpool/config.yaml`
(install refuses to continue while they differ), then run `codexpool install` and `codexpool restart`.

### `codexpool: command not found`

`~/.local/bin` is not on your `PATH`:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

Open a new terminal. Until then, `~/.local/bin/codexpool` works.

### "unknown key(s)" or "invalid …" in settings.json

Every command refuses to run with a broken `settings.json`, on purpose. The message names the key. Valid keys are
listed in [examples/settings.json](../examples/settings.json); keys starting with `_` are ignored.

### "management key … is not in the Keychain" / "the Keychain would not release"

Missing key: re-run `codexpool install`, which mints a new one into the Keychain and `config.yaml`. Locked
Keychain: unlock it (`security unlock-keychain`) and try again; the key itself is fine.

### "install stopped: …"

The message says which step failed and why. Fix that and re-run `codexpool install`; every step checks first, so
it continues where it stopped.

### An upgrade failed

`codexpool upgrade` switches back to the previous build on any failure, and says so. Two messages mean upstream
changed in a way the gate doesn't handle yet, and nothing was changed:

- "gate anchor not found exactly once in cmd/server/main.go"
- "upstream now sets its own engine configurator"

Stay on your current build until codexpool is updated for that release. To go back to an older build by hand:
`codexpool upgrade <older version>` (builds are kept in `~/.codexpool/bin/versions/`).

### doctor: "origin gate: … expected 200 and 403"

The running process isn't a working gated build. Switch back to and restart the gated build with
`codexpool upgrade <current version> --force` (the fix doctor prints), then run `codexpool doctor` again.

### doctor: "N browser/non-loopback requests blocked by the gate"

Something on this Mac tried to use the pool from a web page or a non-loopback name, and the gate refused. That is
the gate doing its job. See what it was with `grep "codexpool gate" ~/.codexpool/logs/main.log`.

## Self-test

### `selftest` prints FAIL

The two seats didn't carry the thread across, or a turn failed. The output shows each turn and any pool log
lines about encrypted content. Check both seats are healthy (`codexpool status --live`) and run it again. If the
same pair fails twice, keep those two seats apart in the fill order and open an issue with the output (no tokens
appear in it).

### `selftest` prints INCONCLUSIVE

The test thread produced no encrypted reasoning, or no compaction with `--compact`, so there was nothing to
carry across. Run it with a model that reasons (`--model`) at `--effort high` (the default) or above.

### "another codexpool operation (guard pass or selftest) is running"

A self-test holds the guard's lock while it runs (1 to 3 minutes). Wait and retry.

## Lanes

Start with `codexpool lane`, which shows each lane member's state and last test, and the Lanes section of
`codexpool doctor`. [LANES.md](LANES.md) explains what `codexpool lane apply` generates. To try one member on
its own: `codexpool lane test <lane> --member <id>` (it spends a little quota).

### A lane subagent fails with "failed to parse function arguments"

xAI writes whole numbers in `"type":"number"` fields as floats (`30000.0`), and Codex rejects floats for integer
arguments such as `yield_time_ms` and `timeout_ms`, so every tool call fails. The lane's payload rules retype
those fields, and they are missing or out of date, usually because the lanes block in `~/.codexpool/config.yaml`
was edited or removed. `codexpool doctor` says whether the block matches `lanes.json`. Rewrite it:

```sh
codexpool lane apply
codexpool lane test <lane> --member <id>
```

### A lane subagent's requests are about 150k tokens

Its role file lacks `apps = false` under `[features]`, so Codex sends every ChatGPT connector's tool schema
inline, about 130k tokens per request (lane models get no deferred tool search). With it, a child request is
about 28k. `codexpool lane apply` rewrites `~/.codex/agents/<lane>.toml`. If apply stops because that file has
no `# Generated by codexpool` first line, the file is yours: move it away and apply again.

### The bridge answers 401

- **`bad bridge key`**: the key in the pool's `meta-api-key` entries doesn't match
  `~/.codexpool/lanes/secrets/bridge.key`, because the file was replaced or the lanes block edited.
  `codexpool lane apply` writes the block from the file again.
- **`upstream_unauthorized`** ("Lane provider rejected the key"): the provider refused your key. Store a new
  one with `codexpool lane key <keyname>`, then run `codexpool lane apply`, which restarts the bridge when a key
  file changed. If the provider said 403 for a free OpenCode
  model, that is expected: free models work only in OpenCode's own client. Contributor models also need training
  consent on your OpenCode workspace and are available only in some regions.

### The bridge answers 429 (`rate_limit_exceeded`)

The provider's quota or rate limit. The bridge passes it on with a `resets_at` (the provider's `Retry-After`,
else 5 minutes), and the pool cools that member until then and serves the lane from the next member. Nothing
to fix; `~/.codexpool/logs/bridge.log` shows the 429s. When every member of a lane is out, its subagents fail
until one comes back; the main agent is told to do the work itself then.

### The bridge answers 502 `upstream_blocked`

The provider sent back an HTML page instead of an API answer, almost always its CDN refusing the request. The
bridge already sends its own User-Agent, since the default Python one is blocked. Retry later. If it keeps
happening, check whether a VPN or proxy on this Mac is in the way.

### "This lane cannot read an encrypted message from another agent"

A bridge member received an encrypted handoff from the parent agent. The pool sends lane subagents plaintext
handoffs only while `optimize-multi-agent-v2: true` stays under `codex:` in `~/.codexpool/config.yaml`, as
install writes it. Put the line back, then start a new subagent.

### "A compaction checkpoint in this thread failed verification"

The bridge signs its compaction checkpoints with `~/.codexpool/lanes/secrets/seal.key`, and that key changed
since the checkpoint was made. The old thread can't continue on a bridge member; start a new subagent.

### A spawn fails with an unknown `agent_type`

Codex loads role files when a thread starts. After `codexpool lane apply`, start a new thread or restart the
app. If it still fails, check that `~/.codex/agents/<lane>.toml` exists (`codexpool doctor` checks it) and that
the `agent_type` is the lane's name.

### The model picker lists a lane's members

The picker should show one entry per lane, named `<Lane> lane (<member>, then <member>)` after the members'
display names in `lanes.json`. Entries named `<Lane> lane: <member> only` are member aliases (`<lane>-<id>`),
which the pool offers only while `codexpool lane test` runs. A test that was killed before it could withdraw
them leaves them behind. `codexpool doctor` then warns that `/v1/models` still lists the member aliases from a
lane test, and says the lanes block in `config.yaml` no longer matches `lanes.json`. Withdraw them:

```sh
codexpool lane apply
```

### `lane apply` stops: "oauth-model-alias", "meta-api-key" or "payload" outside the lanes block

Your `config.yaml` has its own top-level section that the lanes block also needs: `payload:` always,
`oauth-model-alias:` when a lane has an xAI member, `meta-api-key:` when a lane has a bridge member. YAML
allows each key once and apply won't merge them, and `lanes.json` can't hold such entries. Remove or comment out
your own entries, then run `codexpool lane apply` again. An `xai:` list under `oauth-excluded-models:` that
apply didn't write stops it the same way.

### `lane apply` stops: "the pool already serves a model named ..."

A lane name is a model the pool already offers, such as a seat model. The lane would take that model's
requests, so apply refuses it. `lane test` stops the same way ("cannot offer the member aliases for the test")
when a member alias (`<lane>-<id>`) is such a model. Rename the lane, or set another `id` on the member, in
`~/.codexpool/lanes.json`. Names must also be unique across lanes; `lanes.json` validation says which clash.

### `lane apply` stops: a provider key is missing

Every bridge member needs its provider's key file. Store it with the command the error names, for example
`codexpool lane key opencode-go`, then apply again.

### The bridge is not running or `/healthz` fails

`codexpool doctor` says which. `~/.codexpool/logs/bridge.log` shows why it stopped; a missing or empty key file
stops it at start. `codexpool lane apply` re-renders its launchd job and restarts it when its code, `bridge.json`
or a key file changed. To restart it by hand: `launchctl kickstart -k gui/$(id -u)/com.codexpool.bridge`
(`com.codexpool.bridge` is the default `bridge_label`).
