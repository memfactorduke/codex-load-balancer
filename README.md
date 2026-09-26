# Codex Load Balancer

**codexpool** pools every ChatGPT seat you have behind one Codex desktop app, on macOS.

![The Codex Pool menu bar item and its popover: 54% of the weekly quota left across all seats, five seats in priority order, one of them out with a free reset available](docs/images/hero.png)

You keep one Codex app and one thread history. When the seat in use runs out, the next request goes to the
next seat, in the middle of a turn if need be, and you never switch accounts. A menu bar item shows how much of
the week's quota you have left, which seat is serving, and when the next one comes back.

- [How it works](#how-it-works)
- [Requirements](#requirements) and [Quickstart](#quickstart)
- [Everyday use](#everyday-use)
- [The headline number, weights and the reserve](#the-headline-number-weights-and-the-reserve)
- [Resets](#resets)
- [How a seat change works](#how-a-seat-change-works)
- [Security model](#security-model)
- [Terms of service and risk](#terms-of-service-and-risk)
- [Configuration](#configuration), [Upgrade](#upgrade), [Uninstall](#uninstall)
- [FAQ](#faq), [Troubleshooting](docs/TROUBLESHOOTING.md), [Credits](#credits)

More: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (why it is built this way),
[docs/MENUBAR.md](docs/MENUBAR.md) (the menu bar app), [AGENTS.md](AGENTS.md) (for coding agents).

## How it works

```
 Codex app / codex CLI        stock, still signed in to its own account
        │   ~/.codex/config.toml:  openai_base_url = "http://127.0.0.1:8319/v1"
        ▼
 CLIProxyAPI on 127.0.0.1     built from its upstream release + one file, the origin gate
        │   one OAuth login per seat · fill-first by priority · a thread stays on its seat
        ▼
 chatgpt.com/backend-api/codex, as whichever seat is serving
```

The only thing in the request path is [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI), an
open-source proxy that tracks Codex releases closely. codexpool builds it from the upstream release source and
adds a single file, [`build/codexpool_gate.go`](build/codexpool_gate.go), which refuses browser and non-loopback
requests. Codex reaches it through its built-in `openai` provider with one line of config, so every existing
thread stays visible and nothing in Codex is patched.

Beside the request path, three pieces run as your user under launchd:

| Piece | What it does |
|---|---|
| **guard** (`codexpool guard`, every 60 s) | Polls each seat's usage and banked resets, parks a seat that would start spending credits, retries seats blocked by auth errors, notifies you when the pool changes seat, reaches the reserve or runs dry, and writes `state/status.json` plus a usage sample every 10 minutes to `state/history.jsonl`. |
| **menu bar app** (`menubar/`) | A native macOS item next to the clock with a popover for every seat. It only reads those two files: no Keychain, no network. |
| **`codexpool`** | The command you use: install, add seats, status, doctor, resets, upgrades. |

If the guard or the menu bar app stops, requests keep flowing. If the pool stops, launchd restarts it.

### Features

- **One app, many seats.** Fill-first by priority: one seat is used at a time, so resets are staggered and
  prompt caching keeps working.
- **Failover inside a turn.** A seat that runs out is replaced on the same request after a pause of a second or two.
- **Threads survive seat changes.** Encrypted reasoning and native compactions carry across seats; tested live,
  and `codexpool selftest` checks any pair of your seats.
- **One number for the week.** How much of the week's quota is left across all your seats, weighted by seat
  size, green while a regular seat serves and red once the reserve seat has taken over. It can count used instead,
  or leave the reserve out.
- **Resets, tracked and one click away.** Banked free resets show on each seat; the menu bar redeems one when you
  click. codexpool never buys one.
- **Credit guard.** A seat at 100% that would keep answering by spending credits is parked until it resets.
- **Self-healing and notifications.** Auth-blocked seats are refreshed automatically; you hear about seat
  changes, the reserve, an empty pool and a seat that needs a new sign-in.
- **Hardened.** Loopback only, an origin gate, a management key in the Keychain, seat tokens that only the pool
  holds.
- **Stock upstream.** Upgrades build the new release from source, run an 11-case gate self-test, switch,
  health-check, and switch back on failure.
- **One-command install**, with `--dry-run`, and an uninstall that restores your Codex config.

## Requirements

- **A Mac.** Built and tested on Apple Silicon with a current macOS. Intel Macs should work but are untested.
  Linux and Windows are not supported (launchd, the Keychain and AppKit are macOS-only).
- **The Codex desktop app** from OpenAI (`ChatGPT.app` or `Codex.app`, found by its bundle id), signed in.
  codexpool uses the `codex` CLI inside the app for its self-test; a `codex` on your `PATH` also works.
- **Two or more ChatGPT seats with Codex access**, for example Plus, Pro, Business or Team accounts or
  workspaces that you are entitled to use.
- **A working `python3` (3.9 or newer) to start the installer**, plus Python 3.11+ or
  [uv](https://docs.astral.sh/uv/) for codexpool itself. The installer makes its own venv in `~/.codexpool/.venv`
  (with uv it fetches Python 3.13 itself). On a fresh Mac, `/usr/bin/python3` is only a stub that offers to
  install the Xcode Command Line Tools; get a real one from `xcode-select --install`, Homebrew
  (`brew install python@3.13`) or python.org, or use uv alone (see below).
- **git** to clone (it comes with the Command Line Tools), or download the ZIP from GitHub.
- **Internet access during install** (go.dev, proxy.golang.org, api.github.com and codeload.github.com) and about
  1 GB of disk, mostly the Go toolchain used to build CLIProxyAPI. No Xcode, no Homebrew and no Go install needed.

**Before you start**, check that Python and git run:

```sh
python3 --version && git --version
```

If either says "You have not agreed to the Xcode license agreements", run `sudo xcodebuild -license accept`
first. If `python3` offers to install the Command Line Tools, let it (or install Homebrew Python or uv).

## Quickstart

Budget 20 to 30 minutes for five seats, most of it signing in. The install itself takes a minute or two on a
fast connection (measured on an Apple Silicon Mac: 8 s for the Go download, 25 s to build CLIProxyAPI and run
the gate self-test); each seat's sign-in takes a minute or two after that.

**1. Install.**

```sh
git clone https://github.com/memfactorduke/codex-load-balancer.git
cd codex-load-balancer                # from the ZIP download: cd codex-load-balancer-main
./bin/codexpool install --dry-run     # optional: print every step without changing anything
./bin/codexpool install
```

With uv and no usable `python3`, start it with
`uv run --managed-python --no-project --python 3.13 python ./bin/codexpool install`.

The installer checks first and then does seven steps, each safe to re-run:

1. Preflight: macOS, the Codex app, Python 3.11+ or uv, network, a free port (8319). It copies the code to
   `~/.codexpool`, writes `~/.codexpool/settings.json` with the defaults and creates the venv. If it started
   under an older Python, it restarts itself under the new venv and prints the header and the preflight again.
2. Downloads the official Go toolchain for macOS from go.dev and checks its sha256.
3. Builds the latest CLIProxyAPI release from source with the origin gate, runs the gate self-test on a scratch
   port, and points `bin/current` at it.
4. Mints a random management key into the Keychain (`codexpool-management-key`) and writes `config.yaml`
   (loopback, fill-first, 24 h session affinity).
5. Installs the three launchd agents (pool, guard, menu bar) and PyObjC for the menu bar app. macOS shows a
   "Background Items Added" notification; they appear in System Settings → General → Login Items & Extensions
   under names like `python3.13` and `cli-proxy-api`. Leave them on: with the pool switched off, Codex can't
   reach the model.
6. Backs up `~/.codex/config.toml`, records the keys it may change, and sets
   `openai_base_url = "http://127.0.0.1:8319/v1"`. If your config has `model_provider` or `model_catalog_json`,
   it warns; re-run with `--fix-config` to remove them (uninstall puts them back).
7. Writes the `codexpool` command to `~/.local/bin` and runs `codexpool doctor`.

The doctor run at the end reports "0 Codex seats in the pool" as a problem: expected, since you haven't added
any yet. **From here until you add a seat, Codex requests fail.** Add seats now, or run
`codexpool uninstall --yes` to undo the install.

If `~/.local/bin` is not on your `PATH`, the installer says so: add it and open a new terminal.

**2. Add each seat.** Each `login` opens the ChatGPT sign-in; sign in and pick the workspace for that seat.
Higher priority is used first, so give the seat you want drained first the highest number and the reserve the
lowest.

```sh
codexpool login "Work A" --priority 400
codexpool login "Work B" --priority 300 --no-open    # another account: open the printed link in a private window
codexpool login Team     --priority 200 --no-open
codexpool login Personal --priority 100 --no-open
codexpool login "Pro 20x" --priority 10 --no-open
```

Your browser is usually signed in to one account. For every other account use `--no-open` and open the printed
link in a private window; the link expires after a few minutes. `--device` uses a device code instead, which only
works where device-code sign-in is enabled in the account's ChatGPT security settings. The label is optional:
without one, the seat is named from its email and plan (`codexpool label` renames it later). `login` prints the
seat's `plan=`, which sets its default size in step 4.

**3. Quit the Codex app fully (⌘Q) and reopen it**, so it picks up `openai_base_url`. The app still shows its own
account and its own usage meter; that is expected.

**4. Check sizes and set the reserve.** `codexpool status` shows each seat's size in the `size` column. The
default comes from the plan that `login` printed:

| `plan=` | Size |
|---|---|
| `plus` | 1 |
| `prolite`, `self_serve_business_prolite` (shown as `business`) | 5 |
| `pro` | 20 |
| anything else (`team`, `enterprise`, …) | 1 |

```sh
codexpool weight Team 2          # only if a seat's default size is wrong for its quota
codexpool reserve "Pro 20x"      # the fallback seat: red when it serves
```

**5. Check that Codex goes through the pool.** Send Codex one prompt. Then:

- the top seat shows **Serving** in the menu bar, or `active` in `codexpool status`;
- `codexpool logs -n 20` shows the request;
- `codexpool doctor` ends with `OK`.

The menu bar item sits next to the clock. From now on, use Codex as usual.

## Everyday use

| You want to | Run |
|---|---|
| See seats, usage and resets | the menu bar, or `codexpool` (same as `codexpool status`; `--live` polls every seat now, `--json` for scripts) |
| Check everything is healthy | `codexpool doctor` (must end with `OK`) |
| Use a banked free reset | click the seat in the menu bar → **Use reset now…**, or `codexpool reset <seat>` |
| Take a seat out / put it back | `codexpool disable <seat>` / `codexpool enable <seat>` |
| Change the fill order (higher first) | `codexpool priority <seat> <n>` |
| Rename, resize, mark the reserve | `codexpool label <seat> <name>`, `codexpool weight <seat> <n>`, `codexpool reserve <seat>` (`--off` to undo) |
| Fix a seat that says blocked | `codexpool refresh <seat>`, then if needed `codexpool login "<Label>" --priority <n> --no-open` |
| Remove a seat | `codexpool remove <seat> --yes` (deletes its login file; does not sign the account out) |
| Watch requests | `codexpool logs -f` (`--guard` for the guard's log) |
| Restart the pool | `codexpool restart` |
| Test that threads move between two seats | `codexpool selftest <from> <to> --compact` |
| Upgrade CLIProxyAPI | `codexpool upgrade latest` |

`<seat>` is a label (quote names with spaces: `"Work A"`), a seat file name, an email, or any part of one of
those that matches a single seat.

A re-login of an existing seat rewrites its login file, which is where the priority lives, so pass `--priority`
again. The menu bar's **Re-login…** does that for you.

## The headline number, weights and the reserve

![Menu bar item in its three states, light and dark: green 54% left while a regular seat serves, red 34% left while the reserve serves, grey with a warning triangle when the pool is down](docs/images/menubar-strip.png)

The menu bar shows one percentage: how much of this week's quota is **left** across **all** your seats, as an
average weighted by seat size. It counts down as you work and jumps back up when a seat's week resets.

- **Weight** is a seat's size relative to Plus. The default comes from the plan in its login: `plus` 1,
  `prolite` and `self_serve_business_prolite` 5, `pro` 20, anything else (including `team`) 1. `codexpool status`
  shows it in the `size` column and the popover as `5×`. Change it with `codexpool weight <seat> <n>` (any
  positive number).
- **Reserve** seats (`codexpool reserve <seat>`) count like the others. The popover also shows the reserve's own
  figure on the line below the number ("reserve 66% left").
- Seats you turned off are left out. A seat that is out or blocked and reports no numbers counts as spent (0% left).

In the screenshot above: Work A 5× has 0% left, Work B 5× 56%, Team 1× 24%, Personal 1× 91% and the Pro 20× reserve
66%, which gives (0 + 280 + 24 + 91 + 1320) / 32 = **54%** left. Hover over the number in the popover to see this
breakdown, seat by seat. `codexpool status` prints it under its table:

```
Left this week, weighted by size: Work A 5× 0% · Work B 5× 56% · Team 1× 24% ·
    Personal 1× 91% · Pro 20x 20× 66% reserve → 54% left
```

Two settings in `~/.codexpool/settings.json` change what the number means (see [Configuration](#configuration)):

- **`"headline": "regular"`** leaves reserve seats out, so the number covers only the seats meant to be used
  first. In the example that is (0 + 280 + 24 + 91) / 12 = 33% left.
- **`"display": "used"`** counts up instead: every number shows how much is used (46% in the example), and bars
  fill rather than drain. With both settings, the number is the regular seats' weekly use: 67% used.

`codexpool status` shows a change at once, the menu bar after the guard's next pass (within a minute).

The colour tells you what is serving:

- **Green**: a regular seat is serving.
- **Red**: the reserve is serving, which means your regular seats are spent for now.
- **Grey with a warning triangle**: the pool is down, the guard has not reported for 3 minutes, or every seat is out.

The small meter left of the number has two bars: the number itself on top (red, like the number, while the
reserve serves), the serving seat's week below. Both drain as the quota is used (or fill, with
`"display": "used"`).

The reserve flag changes what you see, not the order seats are used in. Order comes only from priority, so give
the reserve the lowest priority.

## Resets

Some ChatGPT accounts receive free usage resets that stay banked on the account until they expire. codexpool
tracks them for every seat and lets you spend one when it helps.

- **Tracked.** The guard checks each seat's banked resets every 10 minutes. `codexpool status` adds
  "1 reset banked" to the seat's note. In the menu bar the seat shows `↺ 1`, blue when the seat can't serve, and
  when an out seat has a reset the popover says "Reset available for Work A · click the seat".
- **One click.** Click the seat and choose **Use reset now… (1 banked, expires Oct 3)**. The app asks first, then
  runs `codexpool reset <seat> --yes`. The seat's weekly and 5-hour limits go back to full and it can serve again
  at once.
- **From a terminal.** `codexpool reset <seat>` shows the seat's current usage, how many resets are banked and
  which one it will use, then asks `[y/N]`. `--yes` skips the question.

What a reset does: it redeems the soonest-expiring banked credit through ChatGPT's reset-credit endpoint, then
clears the pool's cooldown for that seat and refreshes its numbers. **It never buys a reset**: codexpool only
redeems credits the account already holds and has no code for the paid flow. If the seat's usage doesn't need a
reset, ChatGPT declines and the credit is kept. A retry after a network error reuses the same request id, so one
click cannot spend two credits. Every attempt is logged to `~/.codexpool/state/resets.jsonl`.

## How a seat change works

A seat that runs out answers `429 usage_limit_reached` before it produces any output. CLIProxyAPI marks that
seat as cooling down until its reset time and replays the same request on the next seat, so a turn in progress
continues after a pause of a second or two. Codex itself treats a 429 as final, which is why the retry happens
in the pool.

Threads stay on the seat they started on (session affinity, 24 h) and move only when that seat can't serve.
Subagents ride their parent's seat.

**Threads survive the move.** A Codex thread carries encrypted reasoning items and, once compacted, an encrypted
compaction, both issued under the seat that produced them. In live tests another seat accepted both, in both
directions, between a Business workspace seat and a personal Pro seat. Other pairs haven't been tested, so check
yours:

```sh
codexpool selftest "Work A" "Pro 20x" --compact
```

The self-test runs a real throwaway thread on the first seat (with encrypted reasoning, and with `--compact` a
forced native compaction), moves it to the second seat and checks that it continues. `--model` picks the model
for the test turns (`codexpool selftest --help` shows the default). It prints `PASS`, `FAIL` or
`INCONCLUSIVE`, records the result in `state/selftests.json`, spends a little quota, and pauses your other seats
for 1 to 3 minutes, so run it when you are not in the middle of a task. `codexpool doctor` also counts any
cross-seat decryption errors in the pool log.

**Seats with credits.** A seat holding a credit balance does not return 429 at 100%: it keeps answering and
spends credits. The pool can't tell, so the guard parks such a seat (takes it out of rotation until its limit
resets) and notifies you. `codexpool enable <seat>` overrides that until the next reset. Turning off auto top-up
on every seat avoids the question.

## Security model

The pool holds working logins for all your seats, so it is locked down.

**Loopback only.** CLIProxyAPI listens on `127.0.0.1`, remote management is off and its web control panel is
disabled. Nothing on your network can reach it.

**No client key, and an origin gate instead.** The Codex app can only send its own ChatGPT bearer, so the pool
accepts loopback requests without a key (`api-keys: []`); it ignores the app's bearer and substitutes the serving
seat's token. On its own that would let any web page in your browser use the pool, because CLIProxyAPI answers
every origin with CORS `*`, and a few keyless management calls from a page would get loopback banned from the
management API. The origin gate refuses any request whose `Host` is not a loopback name (DNS rebinding) or that
carries browser provenance (an `Origin` other than the Codex app's own `app://-`, or a `Sec-Fetch-Site` other
than `none`). It runs before CLIProxyAPI's logging, CORS, auth and routes, WebSocket upgrades included, so a
rejected request never reaches them. Every build passes an 11-case gate self-test before it is used, and
`codexpool doctor` probes the running gate (a Codex-like request must get 200, a browser-like one 403).

What remains trusted: any non-browser program running as you can send model requests through the pool, just as
it could read `~/.codex/auth.json`. Other user accounts on the same Mac can reach loopback too, so on a shared Mac
they can use the pool as well.

**Management key.** A random key, stored in your login Keychain as `codexpool-management-key` and written there
through `security -i` so it never appears in the process list. CLIProxyAPI replaces it in `config.yaml` with a
hash on first start. codexpool's calls to the pool bypass any HTTP proxy so the key never leaves loopback. The
menu bar app never touches the Keychain.

**Seat logins and token rotation.** Each seat's OAuth login lives in `~/.codexpool/auth/` (directory mode 700),
and only the pool refreshes it. codexpool reads nothing from a seat file except its identity claims (email, plan,
account id) and never prints or sends tokens: usage and reset calls go through the pool, which inserts the token
itself. The rules that keep this working:

1. **Add seats only with `codexpool login`.** Never copy a seat file or `~/.codex/auth.json` anywhere, and never
   point another tool at `~/.codexpool/auth`. ChatGPT refresh tokens rotate on use: when two programs hold the
   same login, one refresh invalidates the other copy and one of them gets signed out (`refresh_token_reused`).
2. **Keep the Codex app signed in.** Its own login is separate from the seat logins, even for the same account,
   and still serves its usage meter, cloud tasks, plugins and sign-in. Signing out revokes it.
3. **Don't add `model_provider` or `model_catalog_json` to `~/.codex/config.toml`.** A custom provider hides every
   existing thread, and a static catalog freezes the model picker. Only `openai_base_url` points at the pool.

**What leaves your Mac.** Model traffic and usage/reset calls go to chatgpt.com through the pool. Install and
upgrades download from go.dev and GitHub. There is no telemetry.

## Terms of service and risk

OpenAI's terms prohibit circumventing usage limits, and its business terms forbid configuring the service to
avoid them. Having one app draw on several seats you pay for may fall under that, and it isn't hidden: every seat
is used from the same IP address and the same Codex installation. There is no known public case of an account
being suspended for pooling its own seats, but that is not a guarantee. Whether to run this is your call, and the
risk is yours.

This project is not affiliated with or endorsed by OpenAI.

## Configuration

**`~/.codexpool/settings.json`**: per-machine settings, written with the defaults by `install`
(see [examples/settings.json](examples/settings.json)). Create it before the first install if you need other
values.

| Key | Default | Meaning |
|---|---|---|
| `pool_label`, `guard_label`, `menubar_label` | `com.codexpool.pool`, `com.codexpool.guard`, `com.codexpool.menubar` | launchd labels of the three agents |
| `port` | `8319` | loopback port of the pool |
| `python` | `null` = `~/.codexpool/.venv/bin/python` | interpreter for the guard and the `codexpool` command (3.11+) |
| `menubar_python` | `null` = same as `python` | interpreter with PyObjC for the menu bar app |
| `codex_bin` | `null` = the `codex` inside the Codex app, else `codex` on `PATH` | the Codex CLI used by `selftest` |
| `headline` | `"all"` | what the [headline number](#the-headline-number-weights-and-the-reserve) covers: `"all"` = every seat that is not off, the reserve included; `"regular"` = the reserve left out |
| `display` | `"left"` | how numbers read, in the menu bar and in `codexpool status`: `"left"` counts down from 100% and bars drain; `"used"` counts up from 0% and bars fill |

A malformed file, an unknown key or a bad value stops every command with a message rather than running with the
wrong port or labels. Keys starting with `_` are ignored, for comments. `CODEXPOOL_SETTINGS` points to a
different file.

**`~/.codex/config.toml`**: install always configures this file, the one the Codex app reads. If you set
`CODEX_HOME` for the `codex` CLI, add the same `openai_base_url` line to `$CODEX_HOME/config.toml` yourself;
install warns when it sees the variable.

**`~/.codexpool/config.yaml`**: the pool's config, generated from [examples/config.yaml](examples/config.yaml).
CLIProxyAPI reloads most keys when the file changes. To hide models from the Codex picker at the source, list
their exact names under `oauth-excluded-models: codex:`.

**`~/.codexpool/seats.json`**: label, weight and reserve flag per seat file
(see [examples/seats.json](examples/seats.json)). `codexpool label`, `weight` and `reserve` write it.

**Files**

```
~/.codexpool/
  bin/codexpool              the CLI and guard (Python, standard library only)
  bin/current -> versions/…  the running CLIProxyAPI build; older builds stay in bin/versions/
  build/codexpool_gate.go    the origin gate, the only change to upstream
  menubar/                   the menu bar app (PyObjC) and its SPEC.md
  launchd/, examples/        templates the installer renders
  settings.json              per-machine settings
  config.yaml                pool config (mode 600)
  seats.json                 labels, weights, reserve
  auth/                      one OAuth login per seat (mode 700)
  state/                     status.json, history.jsonl, guard.json, resets.jsonl, selftests.json,
                             install.json and dated backups of your Codex config
  logs/                      main.log (pool), guard.log (guard decisions and notifications), menubar.log
  toolchain/                 Go, from go.dev
  .venv/                     codexpool's Python
```

## Upgrade

**codexpool itself.** Pull and re-run the installer from your checkout; it copies the new code into
`~/.codexpool` and changes only what differs:

```sh
cd codex-load-balancer && git pull && ./bin/codexpool install
launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar     # restart the menu bar app (your menubar_label)
```

The guard picks up the new code on its next run. If you cloned straight into `~/.codexpool`, `git pull` there
and run `codexpool install`.

If you installed before the `headline` and `display` settings existed, the number in the menu bar changes
meaning: it now shows % left across all seats, where it used to show % used of the regular seats. For the old
number, set `"display": "used"` and `"headline": "regular"` in `~/.codexpool/settings.json`. The install that
brings the change says so.

**CLIProxyAPI.** Upstream ships often. Upgrade when you choose to:

```sh
codexpool upgrade latest        # or a version: codexpool upgrade 7.3.18
```

This downloads the release source from GitHub, adds the gate, builds it with the Go in `toolchain/`, runs the
gate self-test on a scratch port, switches, and checks the new build (version, seat count, live gate probe). On
any failure it switches back to the previous build. The running build reports itself as `X.Y.Z+gate.<hash>`,
where the hash covers the gate, so after a pull that changed the gate, `codexpool upgrade <current version>`
builds a new one. Older builds stay in `bin/versions/`; `codexpool upgrade <older version>` goes back.
`codexpool build <version>` builds without switching.

If upstream moves the line the gate hooks into, the build stops with "gate anchor not found" and nothing
changes; stay on your current build until codexpool is updated.

## Uninstall

```sh
codexpool uninstall          # prints what it would do
codexpool uninstall --yes    # does it
```

It stops and removes the three launchd agents, removes `openai_base_url` (or puts back the value you had before
install; it leaves the line alone if you have changed it since), puts back any `model_provider` or
`model_catalog_json` that `--fix-config` removed, and removes the `codexpool` command. Then quit and reopen
the Codex app, which talks to OpenAI directly again. Your thread history is never touched.

`~/.codexpool` (seat logins, config, history, builds) and the Keychain key stay, so `codexpool install` brings
everything back. To remove those too:

```sh
rm -rf ~/.codexpool && security delete-generic-password -s codexpool-management-key
```

That does not sign the seat accounts out; do that at chatgpt.com if you want to.

## FAQ

**Will my existing Codex threads still be there?**
Yes. Codex keeps using its built-in provider; only `openai_base_url` changes. Threads started before the install
continue through the pool.

**Does the Codex CLI use the pool too?**
Yes. `codex` reads the same `~/.codex/config.toml`, so anything that uses it (the CLI, other agents) goes through
the pool.

**The Codex app's usage meter disagrees with the menu bar. Which is right?**
Both. The app's meter shows only the account the app is signed in to. The menu bar shows the pool.

**Why did my number jump?**
It is an average weighted by seat size, so anything that changes one seat's figure or the set of seats moves it:

- **A seat's weekly reset**, or a banked reset you used: that seat is back to 100% left and the number jumps up
  (down, with `"display": "used"`). Big seats move it most: in the example the Pro 20× seat is 20 of the 32, so
  its reset alone can move the number by up to 62 points.
- **A seat added, removed, turned off or back on**, or a weight changed with `codexpool weight`: the average is
  taken over different seats.
- **A seat that stops reporting numbers** while it is out or blocked counts as spent until it reports again.
- **A setting**: `"headline"` changes which seats count (with `"regular"`, so does `codexpool reserve`), and
  `"display"` shows what is left or what is used.

Hover over the number in the popover, or run `codexpool status`, to see each seat's share.

**What happens when every seat is out?**
Codex requests fail with a usage-limit error until a seat comes back, just as with a single account. The menu bar
turns grey and says which seat comes back next, and you get a notification. A banked reset, if you have one,
brings a seat back at once.

**What if the pool itself is down?**
Codex can't reach the model until it is back, because the pool is in the path. launchd restarts it right away;
`codexpool doctor` says what is wrong. To bypass the pool, run `codexpool uninstall --yes` or remove the
`openai_base_url` line from `~/.codex/config.toml`, then reopen Codex.

**Why fill-first and not round-robin?**
Round-robin burns every seat's 5-hour and weekly windows at the same time and moves threads between accounts on
every request. Fill-first drains one seat at a time, so resets are staggered, prompt caching keeps working, and
seat changes happen a few times a week.

**Can I choose which seat a new thread uses?**
New threads go to the highest-priority seat that can serve. Change the order with `codexpool priority` or
**Make first** in the menu bar, or take a seat out with `codexpool disable`.

**Does it ever change the model I picked?**
No. The pool is configured never to swap in another model when a seat runs out (`switch-preview-model: false`);
it moves to another seat instead. You can hide models from the picker with `oauth-excluded-models`.

**Do I lose anything when the pool restarts?**
CLIProxyAPI keeps session affinity in memory, so after a restart an open thread may land on a different seat.
That is an ordinary seat change and threads survive it.

**How many seats can I add?**
There is no limit in codexpool. Each seat is one `codexpool login`.

**Can I use the same account as the Codex app for a seat?**
Yes, with its own `codexpool login`. That creates a separate login for the pool. Never copy the app's
`~/.codex/auth.json` into the pool.

**Could this get my account suspended?**
See [Terms of service and risk](#terms-of-service-and-risk).

For problems, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). The first step is always `codexpool doctor`.

## Credits

- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (MIT) is the pool: seat logins, token refresh,
  fill-first routing, session affinity and failover. codexpool builds it from source and adds one file.
- [PyObjC](https://github.com/ronaldoussoren/pyobjc) (MIT) makes the native menu bar app possible in Python.
- The menu bar design borrows CodexBar's visual language.
- Go comes from the official builds at [go.dev](https://go.dev/dl/).

## License

[MIT](LICENSE)
