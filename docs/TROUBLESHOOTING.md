# Troubleshooting

Start with:

```sh
subpool doctor
```

It checks the pool process, the running build and its gate, the management key, the Codex config, every seat,
the guard, the menu bar app, the last 24 hours of pool errors and, if you use them, your lanes and any
[add-on](ADDONS.md). Every failed
check prints the fix on the line below it, and a healthy setup ends with `OK`.

Logs, all in `~/.subpool/logs/`:

| File | What | Tail it with |
|---|---|---|
| `main.log` | the pool (CLIProxyAPI): requests, seat cooldowns, config reloads, gate rejections | `subpool logs -f` |
| `guard.log` | guard decisions: parks, heals, resets, every notification | `subpool logs --guard -f` |
| `launchd.log`, `guard.launchd.log` | crashes and output of the pool and guard processes | `tail -f` |
| `menubar.log` | the menu bar app | `tail -f` |
| `bridge.log` | the lane bridge, if a lane uses it: one line per request (status, model, seconds, notes), never content | `tail -f` |

An add-on's pool logs to its own folder; see the add-on's documentation.

The launchd labels below are the defaults; yours are in `~/.subpool/settings.json`.

**To take the pool out of the path right away**, remove the `openai_base_url` line from `~/.codex/config.toml`
(or run `subpool uninstall --yes`) and reopen the Codex app. It then talks to OpenAI directly with its own
login.

## Codex

### Codex still talks to OpenAI directly

Codex goes through the pool once `~/.codex/config.toml` has `openai_base_url = "http://127.0.0.1:8319/v1"` and the
Codex app has been reopened since. `subpool doctor` shows the line under "Codex app".

- **No seat yet** (doctor warns "Codex still talks to OpenAI directly until you add the first seat"). Expected
  after a first install: install leaves the Codex config alone until the pool has a seat, so Codex keeps working
  on its own login. Add a seat in the Setup assistant (Add a ChatGPT account… in the menu bar, or
  `subpool gui setup-welcome`), with `subpool setup`, or with `subpool login "<Label>"`. The first sign-in
  sets the line and prints "Codex now uses the pool; quit and reopen the Codex app".
- **Seats, but the app hasn't been reopened.** It reads `config.toml` at start. Quit it fully (⌘Q) and open it
  again; your next prompt then shows up in `subpool logs -n 20`.
- **Seats, and the line is missing or different** (doctor reports `openai_base_url = (unset)` or another value as
  a problem). The sign-in could not write the file (it printed "could not point Codex at the pool"), the line was
  changed by hand before the first sign-in (it printed "openai_base_url was changed by hand since install", and
  left it), or it was edited or removed since. Run `subpool install`, which sets it now that the pool has a
  seat, and reopen Codex.
- **The `codex` CLI only**, with `CODEX_HOME` set: install configures `~/.codex/config.toml`, the file the app
  reads. Add the same line to `$CODEX_HOME/config.toml` yourself.

After `subpool uninstall`, Codex talking to OpenAI directly is the point.

### Every Codex request fails and the pool has no seats

Codex points at the pool, but the pool has no seat to serve it: an install from before 1.1.0 switched Codex over
straight away. Run `subpool install`: with no seat in the pool it puts `openai_base_url` back as it was before
install, so Codex goes back to its own login, and the first seat you add points it at the pool again. Then quit
(⌘Q) and reopen Codex. Or add a seat right away (the Setup assistant, `subpool setup` or
`subpool login "<Label>"`) and reopen Codex. Removing the last seat with `subpool remove` puts the line back by
itself.

### Codex can't reach the model / every request fails at once

The pool is down or unreachable.

```sh
subpool doctor
subpool restart
subpool logs -n 100
```

If `doctor` says the launchd job isn't loaded, run the `launchctl bootstrap` line it prints, or re-run
`subpool install`. If the pool crashes on start, `~/.subpool/logs/launchd.log` says why; a broken
`config.yaml` is the usual cause.

### My threads disappeared from the sidebar

`~/.codex/config.toml` has a `model_provider` line. A custom provider hides every thread made with the built-in
one. Remove the line, or run `subpool install --fix-config` (uninstall puts it back), then reopen Codex. The
threads were never deleted.

### New models never appear in the picker

`~/.codex/config.toml` has `model_catalog_json`, which freezes the picker. Remove it or run
`subpool install --fix-config`. Also check `oauth-excluded-models` in `~/.subpool/config.yaml`.

### I changed the Codex config but nothing changed

The Codex app reads `config.toml` at start. Quit it fully (⌘Q) and reopen it.

### The Codex app's usage meter doesn't match the menu bar

Expected. The app's meter shows only the account the app itself is signed in to. The menu bar shows the pool.

## Seats

### A seat says Blocked or "Re-login needed"

Its login was rejected (`unauthorized`, `invalid_grant`) or its plan lapsed (`payment_required`). The guard
already retries a refresh every 15 minutes and notifies you after three failures. By hand:

```sh
subpool refresh "Work B"
subpool login "Work B" --priority 300 --no-open     # if it stays blocked; paste the link into a private window
```

Pass the seat's current `--priority` again: the new login rewrites the file that holds it. With `--no-open`, `login`
puts the link on the clipboard. The menu bar's **Re-login…** does all this for you.

When the seat says **OpenAI ended this sign-in**, refreshing cannot help: OpenAI refused the seat's refresh
token for good (`refresh_token_invalidated`, "Your session has ended", `refresh_token_reused` or
`invalid_grant`), for instance after you signed out of all devices or changed the password. The guard notifies
you the first time it sees that, from the pool's log or its own refresh, and stops refreshing the seat. When the
pool log showed it, the seat usually still serves on its access token for up to a day: it says **Re-login soon**
until then, and blocked after. Sign in again with the `subpool login` line from the notification (or
**Re-login…** in the menu bar); the seat is back on the guard's next pass after the sign-in.

### doctor reports `refresh_token_reused`

Something else is using a copy of a seat's login. ChatGPT refresh tokens rotate, so two holders of one login sign
each other out. Find what copied it (another proxy, a backup restore, a synced folder), stop it, then re-login the
affected seat. Never copy files out of `~/.subpool/auth/`.

### A seat is Parked

The credit guard took it out of rotation: it reached 100% but would have kept answering by spending credits. It
comes back by itself when its limit resets, and you got a notification saying when. To use it anyway (it will
spend credits until the reset):

```sh
subpool enable "Work A"
```

To stop this happening, turn off auto top-up for that account in ChatGPT's billing settings.

### Everything is red, or every seat is Out

Red means the reserve is serving and the regular seats are spent. Grey with a triangle and "All out" means every
seat is spent. The popover's "Next back" line says when the first seat returns. If a seat has a banked reset, the
popover says "Reset available"; click that seat and choose **Use reset now…**.

### A seat I didn't touch shows as Off

Something disabled it: you, the menu bar, or an interrupted `selftest` (the guard undoes those on its next pass).
If you got the notification "subpool guard state was unreadable", the guard lost its park records and a parked
seat can show as Off. Turn it back on with `subpool enable <seat>`.

### New threads go to the wrong seat

Fill order is priority, highest first. `subpool status` shows each seat's priority, and its first line says
how the order is kept: `fill: your order` or `fill: soonest reset first`. Seats with the same priority are used in
file-name order, and `doctor` warns about them. Fix it with `subpool order <seat> <seat> ...` (or
`subpool priority <seat> <n>` for one seat), the Balancing pane of the Settings window, or **Make first** in the
menu bar. Only priority decides the order: `subpool reserve <seat>` moves a reserve that would be used before a
regular seat to the end, but a priority you set by hand afterwards wins.

Threads that are already running stay on their seat either way (session affinity); only new threads follow a new
order.

### The fill order changes by itself, or my order does not stick

That is `"balancing": "reset"`: every minute the guard puts the regular seats in the order of their weekly resets,
soonest first, and each seat's note in `subpool status` says when it resets. `subpool logs --guard` shows a
`guard: balancing: new fill order ...` line for each change. Your own order is kept for later: `subpool order`
(or `priority`) changes only that saved order while balancing is `reset`, and `subpool set balancing priority`
brings it back on the guard's next pass. A seat that is out,
parked, blocked or off is not ordered until it serves again, and a seat without usage data yet goes after the
others.

### After a restart, a thread moved to another seat

The pool keeps session affinity in memory, so a restart places open threads again. That is an ordinary seat
change and the thread continues.

### `subpool status` shows a seat twice, or login warns "is in the pool 2 times"

The same account and workspace was signed in twice under different file names. Remove the stale one:

```sh
subpool remove <seat file or label> --yes
```

### "matches several seats" / "matches no seats"

`<seat>` matched more than one seat or none. Use the full label in quotes (`"Work A"`), the seat file name, or
the email.

## Signing in

### The sign-in opened in the wrong account

Your browser is signed in to another account. Use `--no-open` and paste the link, which is also on the
clipboard, into a private window:

```sh
subpool login "Work B" --priority 300 --no-open
```

Pick the right workspace on the ChatGPT page; each workspace is a separate seat.

### After signing in, the browser can't connect to localhost:1455

The sign-in ends at `http://localhost:1455` on the Mac that runs `subpool login`, where the pool waits for it.
A browser on another computer (you reach the Mac over SSH, or a coding agent runs the login on a Mac you aren't
at) goes to its own localhost instead, and the sign-in never finishes. Either open the link in a browser on that
Mac, in person or through Screen Sharing, or forward the port from your own computer and keep it open while you
sign in (it needs Remote Login on in that Mac's System Settings → General → Sharing):

```sh
ssh -N -L 1455:localhost:1455 <user>@<that Mac's name>.local
```

If the page has already failed, start the forward and reload it while the login is still waiting (5 minutes);
after that, run the login again and open its new link.

### "login did not complete; nothing changed"

The sign-in was cancelled, timed out, or the link expired (it lasts a few minutes). Run the command again; the
output above the message shows what CLIProxyAPI's login flow reported. If the browser flow keeps failing, try
`--device`, which works only when device-code sign-in is enabled in the account's ChatGPT security settings.

### The priority I set was not applied

`login` sets the priority once the pool has loaded the new file. If that takes more than 20 seconds it prints a
warning with the command to run, for example `subpool priority "Work B" 300`. With `"balancing": "reset"` the
guard sets the priorities itself on its next pass (see
[The fill order changes by itself](#the-fill-order-changes-by-itself-or-my-order-does-not-stick)).

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
**Refresh** in the popover, or run `subpool status --live`. Each attempt is recorded in
`~/.subpool/state/resets.jsonl` and `guard.log`.

## The menu bar

### The menu bar item is missing

- **Hidden.** On a Mac with a notch, or with many menu bar items, macOS hides items that don't fit. Remove or
  shrink others, or ⌘-drag the item closer to the clock.
- **Not running.** `launchctl print gui/$(id -u)/com.subpool.menubar` shows whether it is loaded and its pid.
  Start it with `launchctl kickstart gui/$(id -u)/com.subpool.menubar`. If you used **Quit**, it stays stopped
  until your next login.
- **Crashing.** Read `~/.subpool/logs/menubar.log`. `No module named 'AppKit'` means PyObjC is missing from its
  interpreter: re-run `subpool install`, which installs it.

### The item is grey with a warning triangle

The pool is down, or the guard hasn't written `status.json` for more than 3 minutes (the popover says which).
Right after the Mac wakes, the app waits 2 minutes for the guard to catch up.

```sh
subpool doctor
launchctl print gui/$(id -u)/com.subpool.guard
subpool logs --guard -n 50
```

### A menu action failed

The popover header shows the error for a few seconds. Run the same command in Terminal to see all of it, for
example `subpool reset "Work B"`. The app runs `~/.subpool/bin/subpool` with the `python` from
`settings.json` (else `~/.subpool/.venv/bin/python`); if that interpreter is gone, re-run `subpool install`.

### Notifications never appear

subpool sends them with `osascript`, which macOS lists as **Script Editor**. Allow it in System Settings →
Notifications → Script Editor. Every notification is also written to `~/.subpool/logs/guard.log`.

## Install, upgrade, uninstall

### "You have not agreed to the Xcode license agreements"

The Mac's `/usr/bin/python3` (and `git`) belong to Xcode, and Xcode's license hasn't been accepted. Run
`sudo xcodebuild -license accept`, or start the installer with another Python 3.9+, for example
`/opt/homebrew/bin/python3 ./bin/subpool install`. After install nothing depends on `/usr/bin/python3`: the
`subpool` command, the guard and the menu bar app name their interpreter.

### "not installed yet (… missing)"

`login`, `status` and `doctor` need an installed pool. Run `./bin/subpool install` from your checkout first.

### "cannot reach go.dev (…)" during install

The message shows the actual error for each host. Install downloads from go.dev, proxy.golang.org,
api.github.com and codeload.github.com, so allow those through any firewall or proxy.

`CERTIFICATE_VERIFY_FAILED` means that Python can't check HTTPS certificates, which is usual for Python from
python.org until you run **Install Certificates.command** in `/Applications/Python 3.x`. Run that, or start the
installer with Homebrew Python or uv instead:

```sh
uv run --managed-python --no-project --python 3.13 python ./bin/subpool install
```

### The pool, guard or menu bar won't stay loaded

On macOS 13 and later, launch agents can be switched off in System Settings → General → Login Items &
Extensions, where subpool's three show under the names of their programs (for example `python3.13` and
`cli-proxy-api`). Turn them back on, then run `subpool install`, which loads whatever isn't loaded. If one
still stops, its log says why: `~/.subpool/logs/launchd.log` (pool), `guard.launchd.log` (guard) or
`menubar.log`.

### "no Python 3.11+ and no uv on this Mac"

Install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`) or Python (`brew install python@3.13`) and re-run
`./bin/subpool install`. With uv, the installer fetches Python 3.13 by itself.

### "Codex app not found"

Install the Codex desktop app from OpenAI. subpool finds it by its bundle id (`com.openai.codex`) through
Spotlight, then as `ChatGPT.app` or `Codex.app` in `/Applications` or `~/Applications`, and uses the `codex` CLI
inside it: `Contents/Resources/codex-cli/bin/codex` since version 26.924, `Contents/Resources/codex` before. If
Spotlight indexing is off and the app lives somewhere else, set `"codex_bin"` in `~/.subpool/settings.json` to
that CLI inside the app, or to a `codex` CLI of your own.

### "something else is listening on 127.0.0.1:8319"

Find it with `lsof -nP -iTCP:8319 -sTCP:LISTEN` and stop it, or choose another port. On a fresh install, create
the settings file before running `install`:

```sh
mkdir -p ~/.subpool && echo '{"port": 8329}' > ~/.subpool/settings.json
```

The folder then isn't empty, so `git clone … ~/.subpool` would fail; clone anywhere else and run the installer
from there. On an existing install, change `port` in both `settings.json` and `~/.subpool/config.yaml`
(install refuses to continue while they differ), then run `subpool install` and `subpool restart`.

### `subpool: command not found`

`~/.local/bin` is not on your `PATH`:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

Open a new terminal. Until then, `~/.local/bin/subpool` works.

### "unknown key(s)" or "invalid …" in settings.json

Every command refuses to run with a broken `settings.json`, on purpose. The message names the key. Valid keys are
listed in [examples/settings.json](../examples/settings.json); keys starting with `_` are ignored.

### "management key … is not in the Keychain" / "the Keychain would not release"

Missing key: re-run `subpool install`, which mints a new one into the Keychain and `config.yaml`. Locked
Keychain: unlock it (`security unlock-keychain`) and try again; the key itself is fine.

### "install stopped: …"

The message says which step failed and why. Fix that and re-run `subpool install`; every step checks first, so
it continues where it stopped.

### An upgrade failed

`subpool upgrade` switches back to the previous build on any failure, and says so. Two messages mean upstream
changed in a way the gate doesn't handle yet, and nothing was changed:

- "gate anchor not found exactly once in cmd/server/main.go"
- "upstream now sets its own engine configurator"

Stay on your current build until subpool is updated for that release. To go back to an older build by hand:
`subpool upgrade <older version>` (builds are kept in `~/.subpool/bin/versions/`).

### doctor: "origin gate: … expected 200 and 403"

The running process isn't a working gated build. Switch back to and restart the gated build with
`subpool upgrade <current version> --force` (the fix doctor prints), then run `subpool doctor` again.

### doctor: "N browser/non-loopback requests blocked by the gate"

Something on this Mac tried to use the pool from a web page or a non-loopback name, and the gate refused. That is
the gate doing its job. See what it was with `grep "codexpool gate" ~/.subpool/logs/main.log`.

## Self-test

### `selftest` prints FAIL

The two seats didn't carry the thread across, or a turn failed. The output shows each turn and any pool log
lines about encrypted content. Check both seats are healthy (`subpool status --live`) and run it again. If the
same pair fails twice, keep those two seats apart in the fill order and open an issue with the output (no tokens
appear in it).

### `selftest` prints INCONCLUSIVE

The test thread produced no encrypted reasoning, or no compaction with `--compact`, so there was nothing to
carry across. Run it with a model that reasons (`--model`) at `--effort high` (the default) or above.

### "another subpool operation (guard pass or selftest) is running"

A self-test holds the guard's lock while it runs (1 to 3 minutes). Wait and retry.

## Lanes

Start with `subpool lane`, which shows each lane member's state and last test, and the Lanes section of
`subpool doctor`. [LANES.md](LANES.md) explains what `subpool lane apply` generates. To try one member on
its own: `subpool lane test <lane> --member <id>` (it spends a little quota).

### A lane subagent fails with "failed to parse function arguments"

xAI writes whole numbers in `"type":"number"` fields as floats (`30000.0`), and Codex rejects floats for integer
arguments such as `yield_time_ms` and `timeout_ms`, so every tool call fails. The lane's payload rules retype
those fields, and they are missing or out of date, usually because the lanes block in `~/.subpool/config.yaml`
was edited or removed. `subpool doctor` says whether the block matches `lanes.json`. Rewrite it:

```sh
subpool lane apply
subpool lane test <lane> --member <id>
```

### A lane subagent's requests are about 150k tokens

Its role file lacks `apps = false` under `[features]`, so Codex sends every ChatGPT connector's tool schema
inline, about 130k tokens per request (lane models get no deferred tool search). With it, a child request is
about 28k. `subpool lane apply` rewrites `~/.codex/agents/<lane>.toml`. If apply stops because that file has
no `# Generated by codexpool` first line, the file is yours: move it away and apply again.

### The bridge answers 401

- **`bad bridge key`**: the key in the pool's `meta-api-key` entries doesn't match
  `~/.subpool/lanes/secrets/bridge.key`, because the file was replaced or the lanes block edited.
  `subpool lane apply` writes the block from the file again.
- **`upstream_unauthorized`** ("Lane provider rejected the key"): the provider refused your key. Store a new
  one with `subpool lane key <keyname>`, then run `subpool lane apply`, which restarts the bridge when a key
  file changed. If the provider said 403 for a free OpenCode
  model, that is expected: free models work only in OpenCode's own client. Contributor models also need training
  consent on your OpenCode workspace and are available only in some regions.

### The bridge answers 429 (`rate_limit_exceeded`)

The provider's quota or rate limit. The bridge passes it on with a `resets_at` (the provider's `Retry-After`,
else 5 minutes), and the pool cools that member until then and serves the lane from the next member. Nothing
to fix; `~/.subpool/logs/bridge.log` shows the 429s. When every member of a lane is out, its subagents fail
until one comes back; the main agent is told to do the work itself then.

### The bridge answers 502 `upstream_blocked`

The provider sent back an HTML page instead of an API answer, almost always its CDN refusing the request. The
bridge already sends its own User-Agent, since the default Python one is blocked. Retry later. If it keeps
happening, check whether a VPN or proxy on this Mac is in the way.

### "This lane cannot read an encrypted message from another agent"

A bridge member received an encrypted handoff from the parent agent. The pool sends lane subagents plaintext
handoffs only while `optimize-multi-agent-v2: true` stays under `codex:` in `~/.subpool/config.yaml`, as
install writes it. Put the line back, then start a new subagent.

### "A compaction checkpoint in this thread failed verification"

The bridge signs its compaction checkpoints with `~/.subpool/lanes/secrets/seal.key`, and that key changed
since the checkpoint was made. The old thread can't continue on a bridge member; start a new subagent.

### A spawn fails with an unknown `agent_type`

Codex loads role files when a thread starts. After `subpool lane apply`, start a new thread or restart the
app. If it still fails, check that `~/.codex/agents/<lane>.toml` exists (`subpool doctor` checks it) and that
the `agent_type` is the lane's name.

### The model picker lists a lane's members

The picker should show one entry per lane, named by the lane's `display` in `lanes.json`, or else after the lane
itself with a capital first letter, such as "Bulk". Entries named `<Lane> lane: <member> only` are member aliases
(`<lane>-<id>`), which the pool offers only while
`subpool lane test` runs. A test that was killed before it could withdraw them leaves them behind.
`subpool doctor` then warns that `/v1/models` still lists the member aliases from a lane test, and says the
lanes block in `config.yaml` no longer matches `lanes.json`. Withdraw them:

```sh
subpool lane apply
```

### A lane's name is cut short in the model picker

A lane's entry in the picker is its name with a capital first letter ("Bulk"), unless you gave it one of your own
with `"display"`, and the picker shows only the start of a long one. Shorten it: set `"display"` on the lane in
`~/.subpool/lanes.json` (up to 40 characters; remove it for the default) and run `subpool lane apply`, or pass
`--display "<name>"` when you add it with `subpool lane add`. `subpool lane` shows the name each lane has in
the picker. If the picker still shows the old name, start a new thread or reopen the Codex app.

### `lane apply` stops: "oauth-model-alias", "meta-api-key" or "payload" outside the lanes block

Your `config.yaml` has its own top-level section that the lanes block also needs: `payload:` always,
`oauth-model-alias:` when a lane has an xAI member, `meta-api-key:` when a lane has a bridge member. YAML
allows each key once and apply won't merge them, and `lanes.json` can't hold such entries. Remove or comment out
your own entries, then run `subpool lane apply` again. An `xai:` list under `oauth-excluded-models:` that
apply didn't write stops it the same way.

### `lane apply` stops: "the pool already serves a model named ..."

A lane name is a model the pool already offers, such as a seat model. The lane would take that model's
requests, so apply refuses it. `lane test` stops the same way ("cannot offer the member aliases for the test")
when a member alias (`<lane>-<id>`) is such a model. Rename the lane, or set another `id` on the member, in
`~/.subpool/lanes.json`. Names must also be unique across lanes; `lanes.json` validation says which clash.

### `lane apply` stops: a provider key is missing

Every bridge member needs its provider's key file. Store it with the command the error names, for example
`subpool lane key opencode-go`, then apply again.

### `lane models` fails, or the Add Model sheet has no model list

`subpool lane models <provider>` says why. For `xai` the list comes from the pool: it must be running
(`subpool doctor`). For OpenCode it comes from the provider's `/models`: "answered HTTP 401" means the key was
refused (`subpool lane key <keyname> -` to replace it), "cannot reach" a network problem, and "a redirect" that
the provider sent the request elsewhere, which subpool does not follow so the key goes nowhere else. For a
`responses` endpoint pass `--base-url` and `--key-name`. You can always type a model id by hand; `lane add` and
`lane edit` check it when they save.

### `lane edit` says a member does not exist

Members are named by id, as `subpool lane` lists them (`grok`, `muse`), not by model. `lane edit` applies its
flags in the order given, so a member added with `--add-member` can be moved or removed only after it, in the
same call.

### The bridge is not running or `/healthz` fails

`subpool doctor` says which. `~/.subpool/logs/bridge.log` shows why it stopped; a missing or empty key file
stops it at start. `subpool lane apply` re-renders its launchd job and restarts it when its code, `bridge.json`
or a key file changed. To restart it by hand: `launchctl kickstart -k gui/$(id -u)/com.subpool.bridge`
(`com.subpool.bridge` is the default `bridge_label`).

