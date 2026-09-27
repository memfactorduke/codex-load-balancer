# The menu bar app and the Settings window

codexpool's Mac apps are written in Python with PyObjC, no Xcode: the menu bar item next to the clock
([`menubar/codexpool_menubar.py`](../menubar/codexpool_menubar.py)), and the [Settings window](#the-settings-window)
and [Setup assistant](#the-setup-assistant) it opens
([`menubar/codexpool_settings.py`](../menubar/codexpool_settings.py)). `codexpool install` sets them up and launchd
keeps the menu bar item running. The design spec, for anyone changing them, is
[`menubar/SPEC.md`](../menubar/SPEC.md).

- [What it can touch](#what-it-can-touch)
- [The item next to the clock](#the-item-next-to-the-clock), [the popover](#the-popover) and
  [seat actions](#seat-actions)
- [The Settings window](#the-settings-window): [Balancing](#balancing), [editing lanes](#editing-lanes); and
  [the Setup assistant](#the-setup-assistant)
- [Running it](#running-it), [snapshots](#snapshots), [SwiftBar or xbar instead](#swiftbar-or-xbar-instead)

<p>
  <img src="images/popover-light.png" width="340" alt="The popover in light mode: 54% left this week across all seats, five seats, Work B serving">
  <img src="images/popover-dark.png" width="340" alt="The same popover in dark mode">
</p>

## What it can touch

It reads `~/.codexpool/state/status.json` (the guard rewrites it every 60 seconds), `state/history.jsonl` for the
chart, and `settings.json` only to find the Python that runs codexpool. The `headline` and `display` settings reach
it through `status.json`, where the guard copies them. It never touches the Keychain, never
calls the network and never talks to the pool's management API. Actions run the `codexpool` command in the
background, or open Terminal for commands that need one. The Settings window and the Setup assistant follow the
same rules; they also read what `codexpool doctor --json`, `codexpool lane list --json`,
`codexpool lane providers --json`, `codexpool lane models PROVIDER --json` and `codexpool version` print. A
provider key you paste into the Lanes pane goes to `codexpool lane key NAME -` on its standard input: never on a
command line, never in a file.

## The item next to the clock

![The item in its three states, light and dark](images/menubar-strip.png)

- **The number** is how much of this week's quota is left across all your seats, weighted by seat size. With
  `"display": "used"` it shows how much is used instead, and with `"headline": "regular"` it leaves the reserve out.
  The README explains [how it is computed](../README.md#the-headline-number-weights-and-the-reserve).
- **Green** while a regular seat serves, **red** while the reserve serves (the regular seats are spent), **grey
  with a warning triangle** when the pool is down, the guard has not reported for 3 minutes, or every seat is out.
- **The meter** left of the number has two bars: the number itself on top (red, like the number, while the
  reserve serves), the serving seat's week below. They drain as the quota is used (with `"display": "used"` they
  fill).

The item has a fixed width, so its neighbours never shift as the number changes. On first run it asks macOS
for the rightmost spot among third-party items; ⌘-drag it anywhere and it stays there.

## The popover

Click the item. Click it again, or anywhere outside the popover (another app, the desktop), or press Escape to
close it; it also closes when you switch to another app.

1. **Header**: "Codex Pool", when the status was last updated and which seat is serving. The pill on the right
   says `Regular` (green), `Reserve` (red), or `All out`, `Down`, `Stale`, `No seats`, `No data` (grey). After
   you use an action, the subtitle shows its progress and result for a few seconds.
2. **Problem banner** when something needs you: pool down (**Restart Pool**), not reporting (**Run Doctor**) or
   no seats yet (**Add Account…**, which opens the [Setup assistant](#the-setup-assistant) at its Add accounts
   step).
3. **Headline**: the big percentage ("54% left this week · all seats") and its bar, then
   - "3 of 4 regular seats ready · reserve 66% left",
   - "Next back: Work A in 2d 7h" when a seat is out (in bold when every regular seat is out),
   - "Reset available for Work A · click the seat" in blue when a seat that can't serve has a banked reset,
   - the pace from recent history: "At this pace the pool lasts ~5d" ("the regular seats last" with
     `"headline": "regular"`), or "Steady". Weekly resets are ignored when working out the slope.

   Hover over the number or its bar for the breakdown, one line per seat in fill order, then the result:

   ```
   Work A · 5× · 0% left · back in 2d 7h
   Work B · 5× · 56% left · serving · resets in 6d 13h
   Pro 20x · 20× reserve · 66% left · resets in 3d 8h
   Weighted by size: 54% left · all seats
   ```

   With `"headline": "regular"` the reserve's line reads "Pro 20x · reserve, not counted · 66% left".
4. **Chart** ("Quota left", or "Usage" with `"display": "used"`): the headline over 24 hours or 7 days (toggle),
   green where a regular seat served and red where the reserve did. It plots the same figure as the number, so
   what is left falls as you work and jumps up at a weekly reset. It needs a few samples first and says
   "Collecting history…" until then.
5. **Seats**, in fill order. Each row has the seat's name, its plan and weight (`Business 5×`), a `· Reserve` tag,
   and its state: `Serving`, `Ready`, `Out`, `Parked`, `Blocked` or `Off`. Below that is the weekly bar (and a thin
   5-hour bar for seats that have one), how much is left ("56% left", or "Week 24% left · 5h 100% left"), and when
   it resets or comes back. Bars drain as a seat is used: green above 30% left, orange at 30% or less, red at 10%
   or less, and grey for a seat that can't serve. A seat that needs a new sign-in says "Re-login needed" and shows
   the error. "Re-login soon" (orange) is a seat whose sign-in OpenAI ended but that still serves until its access
   runs out, within a day: sign in again before then.
6. **Footer**: Status… (Terminal, `codexpool status --live`), Doctor, Pool log (`codexpool logs -f`), Docs (the
   README), Refresh (runs one guard pass now); then **Add a ChatGPT account…** (the Setup assistant's Add
   accounts step), **Settings…** (⌘,, which also works while the popover is open), Quit, and the running
   CLIProxyAPI version.

If the popover is taller than the screen, only the seat list scrolls.

**When the reserve takes over** everything turns red: the pill, the headline number and its bar, the meter's top
bar and the reserve's `Serving` capsule. The chart shows red stretches where the reserve served. In this example
Work A and Work B are out, Team has hit its 5-hour limit, Personal is parked by the credit guard, and Work B has a
banked reset that would bring it back now.

<img src="images/popover-reserve-light.png" width="340" alt="The popover while the reserve serves: red 34% left, every regular seat out or parked, Pro 20x serving">

**Used instead of left.** With `"display": "used"` in `settings.json` every number counts up from 0% and the
bars fill: the same pool reads 46% used, the seats "100% used" and "Week 76% · 5h 0%", and the bars turn orange at
70% used and red at 90%. Add `"headline": "regular"` to leave the reserve out of the number as well.

<img src="images/popover-used-light.png" width="340" alt="The same popover with display set to used: 46% used this week across all seats, bars filling">

## Seat actions

Click a seat row for its menu. The first line shows the seat's label and account.

| Item | When | What it runs |
|---|---|---|
| **Use reset now… (n banked, expires Oct 3)** | the seat has banked free resets | asks first, then `codexpool reset <seat> --yes` |
| **Re-login…** | always (near the top when the seat is blocked or says "Re-login soon") | Terminal: `codexpool login <Label> --no-open --priority <current>` |
| **Enable (spends credits)…** | the credit guard parked the seat | asks first, then `codexpool enable <seat>` |
| **Enable** / **Disable** | the seat is off / on | `codexpool enable` / `codexpool disable` |
| **Make first** | any regular seat that isn't already first | `codexpool priority <seat> <top + 10>` |

**Resets.** A seat with banked resets shows `↺ n` next to its state, blue when the seat can't serve (that is when
a reset helps). Hovering shows how many are banked and when the soonest expires. **Use reset now…** asks "Use a
reset on Work B?" and explains that it uses one of the banked free resets and never buys one. After you confirm,
the seat's weekly and 5-hour limits go back to full and the pool can use it right away. If the seat didn't need a
reset, ChatGPT declines and the credit is kept; the header shows the reason.

**Re-login** opens Terminal with the sign-in link printed rather than opened, and reminds you which account to
use: open the link in a private window and sign in to that account. The link is on the clipboard too, so you can
paste it straight into the private window. It passes the seat's current priority back, because a new login
rewrites the seat file that holds it.

**Make first** is disabled for reserve seats: moving the reserve first would drain it before the regular seats.

After any action the app runs one guard pass, so the popover shows the effect at once.

## The Settings window

A window in the style of System Settings: a sidebar with the pool's number and serving seat, and seven panes. It
shows the same data as the popover, in the same colours, and every change it makes is a `codexpool` command run
in the background, the same one you could type in a terminal.

![The Settings window, Seats pane: the five seats in fill order with Work A selected, and its Name and Size, its place in the fill order with a link to Balancing, its In rotation switch, its banked reset, and buttons to sign in again or remove it](images/settings-seats-light.png)

**Open it** with **Settings…** (⌘,) in the popover, or with `codexpool gui` from a terminal
(`codexpool gui seats` opens it on a pane). Only one runs at a time: opening it again brings the open window
forward on the pane you asked for. While it is open it has a Dock icon and a menu bar of its own, with ⌘1 to ⌘7
for the panes. Closing the window quits it.

| Pane | Shows | You can |
|---|---|---|
| **Overview** | The headline number and its bar, the seat new threads go to, how many regular seats are ready, the reserve, the next seat back, a banked reset you could use, the pace, and every seat with its plan, size, weekly and 5-hour bars and reset times | When the pool is down, not reporting or has no seats: Restart Pool…, Check Health or Add a ChatGPT Account… |
| **Seats** | The seats in fill order; pick one for its settings and its place in the fill order | Rename it (`codexpool label`), set its Size (`weight`), switch In rotation (`enable`, `disable`; a parked seat asks first, since it would spend credits), Redeem Reset… (`reset --yes`, after saying it spends one banked free reset and never buys one), Sign In Again… (the Setup assistant, keeping the seat's name and priority), Remove… (`remove --yes`, after a confirmation), Add Account…; its Fill order row opens Balancing |
| **Balancing** | How the pool picks a seat, the fill order (with each seat's week left and reset) and the reserve | Your order or Soonest reset first (`codexpool set balancing priority\|reset`), move seats up or down (`codexpool order`), Use last (reserve) per seat (`reserve`, `--off`). [More below](#balancing). |
| **Lanes** | Each [lane](LANES.md): its name in the model picker, effort, role, members in order with their state and last test, and the lane's own last test; then the provider sign-ins and keys | New Lane… and Edit… (a sheet, below), Test… (confirms first, since it spends lane quota and can take minutes; the output streams into a sheet with Stop), Delete… (`lane remove`, after a confirmation), Apply… (`lane apply`), Open Docs, and per provider Sign In… / Add Key… / Replace Key…. [More below](#editing-lanes). |
| **General** | The menu bar settings | Numbers show Left or Used (`codexpool set display`), Headline covers All seats or Regular seats (`codexpool set headline`), Restart… the pool, Open Logs, Reopen Codex… (quits the Codex app and opens it again), open the Setup assistant |
| **Health** | `codexpool doctor`'s report: a summary, then each check with ✓, ! or ✗ and how to fix it | Run Again, Copy Report (the full text, which stays on your Mac until you paste it) |
| **About** | The codexpool and CLIProxyAPI versions, links to the website, source, docs and issues, the license and the disclaimer | |

A change takes effect at once: the window runs one guard pass after it and shows the result inline. If a command
the window needs is missing (an older `codexpool`), the pane says "This needs a newer codexpool" instead: run the
installer again. The window's output goes to `~/.codexpool/logs/settings.log`.

### Balancing

![The Balancing pane: Your order selected; the seat order Work A, Work B, Team, Personal, each with its week left and reset and up and down arrows, then the Pro 20x reserve, always last; and a Use last (reserve) checkbox for every seat](images/settings-balancing-light.png)

**How the pool picks a seat.** *Your order* uses the first seat until it runs out, then the next, which is best
for prompt caching (the default, `codexpool set balancing priority`). *Soonest reset first* uses the seat whose
weekly quota resets soonest, so quota that would expire unused gets used first (`codexpool set balancing reset`).
Either way new threads follow the order, while threads already running stay on their seat until it runs out.

**Seat order.** In *Your order* the arrows move a seat up or down; a burst of clicks is saved once, as one
`codexpool order` with every regular seat in the new order. In *Soonest reset first* the list is read-only: it
shows the order the guard computed, re-sorted every minute by weekly reset, with seats that have no usage data
after the others in your order. Switching back to *Your order* brings your own order back. The reserve is always
last.

**Reserve.** Tick *Use last (reserve)* for the seat the pool should keep until every other seat is out
(`codexpool reserve`, `--off` to clear it). A big seat, such as a Pro 20× plan, makes a good reserve. The menu bar
turns red while it serves.

### Editing lanes

![The lane editor on the bulk lane: its picker label, effort and role, and its two members in fallback order, each with up and down arrows and a remove button, then Add Model…, Cancel and Save](images/settings-lanes-edit-light.png)

**New Lane…** (at the top of the Lanes pane, and in its empty state) and **Edit…** on a lane open the lane editor:
the name (new lanes only), the picker label (its name in the Codex model picker; empty means the lane name with a
capital first letter), the effort, the role (what the lane is for; the main agent reads it) and the members in
fallback order, with arrows to reorder them and − to remove one. **Add Model…** picks a provider, lists its models
with `codexpool lane models` (or type a model id by hand) and takes a display name; a provider that isn't ready
offers **Sign In to xAI…** or **Add Key…** right there. A model the lane already has is refused, and so is a second
new Responses endpoint before the first is saved. **Save** runs one command, `codexpool lane add` for a new lane or
`codexpool lane edit` with the changes, which writes `lanes.json` and applies it; if it fails, the sheet stays open
and shows what codexpool said. When the lane still needs a key (a new responses member has a key of its own,
named after the lane), the sheet asks for it right there and saves once it is in. **Cancel** discards the changes. Start a new Codex thread to use a
changed lane.

**Credentials**, at the bottom of the pane, lists each provider a lane uses or that is ready, with its state:
**Sign In…** for xAI runs `codexpool lane login xai --no-open` and shows the link with Open in Browser, Open in
Private Chrome Window and Copy Link, like the Setup assistant (the link goes on the clipboard at once).
**Add Key…** and **Replace Key…** take the key in a secure field and pipe it to `codexpool lane key NAME -`; the
field is cleared as soon as it is sent, or when you cancel. Apply the lanes after a new key so the bridge reads it.

## The Setup assistant

A three-step window for adding ChatGPT accounts. It opens:

- by itself at the end of a first install with the one-liner, when it runs in Terminal on the Mac itself;
- once by itself from the menu bar app, the first time it sees the pool running with no seats (it records that in
  `state/setup-shown` and never opens on its own again);
- from **Add a ChatGPT account…** in the popover (at step 2), **Setup Assistant…** in the Settings window's
  menu, the General pane, or `codexpool gui setup-welcome`.

1. **Welcome**: what codexpool does, and a checklist from `codexpool doctor` (pool running, Codex app pointed at
   the pool, menu bar running), with the fix for any check that fails.
2. **Add accounts**: the seats already in the pool, then a name field and **Get Sign-In Link**, which runs
   `codexpool login <name> --no-open --no-copy` in the background. The link goes on the clipboard as soon as it
   appears ("Copied" shows next to **Copy Link** while it is still there). Open it in the browser or in a private
   Chrome window (when Chrome is installed), or paste it; to add an account other than the one your browser is
   signed in to, use a private window. **Copy Link** copies it again. The link is good for 5 minutes, with Try
   Again after that. Once you have signed in, the
   assistant shows the seat's plan and size, and offers **Mark as Reserve** and **Add Another…**. If the browser
   signed in to an account that is already a seat, nothing is added: it says so and gives that seat its old name
   back.
3. **Done**: **Quit and Reopen Codex…**, so the app goes through the pool, and where to find Settings later.

`codexpool setup` is the same flow in a terminal. The README shows the Add accounts step
([Install](../README.md#install)).

## Running it

The LaunchAgent (label `menubar_label` in `settings.json`, default `com.codexpool.menubar`) runs
`<menubar_python> ~/.codexpool/menubar/codexpool_menubar.py` at login and restarts it if it crashes. **Quit** in
the popover stops it until your next login.

```sh
launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar    # restart (after editing or upgrading)
launchctl print gui/$(id -u)/com.codexpool.menubar           # is it loaded and running?
tail -f ~/.codexpool/logs/menubar.log                        # its log
```

The interpreter needs `pyobjc-core` and `pyobjc-framework-Cocoa`; the installer puts them into codexpool's venv.
To start it again after **Quit** without logging out:

```sh
launchctl kickstart gui/$(id -u)/com.codexpool.menubar
```

If the item doesn't appear, see [Troubleshooting](TROUBLESHOOTING.md#the-menu-bar-item-is-missing).

The Settings window is not a LaunchAgent: the menu bar app, `codexpool gui` or the installer start it when you
open it, with the same interpreter (`menubar_python`), and it quits when you close its last window.

## Snapshots

The app can render its popover and item to PNG without showing any UI, which is how the screenshots here were
made (from synthetic data in `docs/images/demo/`):

```sh
~/.codexpool/.venv/bin/python ~/.codexpool/menubar/codexpool_menubar.py \
    --snapshot /tmp/popover.png --appearance dark
```

Options: `--status PATH` and `--history PATH` (default: the live files), `--now ISO-8601`, `--range 24h|7d`,
`--max-height PT` (simulates a short screen), `--hover seat:<seat file>` or `--hover action:doctor`
(`--hover tip:headline` prints the headline's breakdown tooltip, which can't be drawn offscreen). It writes
`OUT.png` (the popover) and `OUT-menubar.png` (the item), both at 2×. The headline and display modes come from the
status file, as in the running app; `docs/images/demo/status-used.json` is the demo pool with `"display": "used"`.

The Settings window and the Setup assistant do the same, and run no command while they do:

```sh
~/.codexpool/.venv/bin/python ~/.codexpool/menubar/codexpool_settings.py \
    --snapshot /tmp/seats.png --pane seats --appearance light --status docs/images/demo/status-regular.json
```

`--pane` takes any pane, a Setup assistant step (`setup-welcome`, `setup-accounts`, `setup-signin`,
`setup-added`, `setup-again`, `setup-done`), or one of the Lanes pane's sheets over the pane: `lanes-edit` (the
lane editor on the first lane), `lanes-new`, `lanes-model` and `lanes-model-key` (Add Model on a provider that is
ready, and on one that needs a key), `lanes-key` (Add Key) and `lanes-signin` (the xAI sign-in, with a made-up
link). Add `--doctor PATH`, `--lanes PATH`, `--providers PATH` and `--models PATH` (the JSON that
`codexpool doctor --json`, `codexpool lane list --json`, `codexpool lane providers --json` and
`codexpool lane models PROVIDER --json` print; demo files are in `docs/images/demo/`), `--history PATH` for the
pace line, `--now ISO-8601` and `--height PT`. Without `--status` it renders the live `status.json`;
`docs/images/demo/status-reset.json` is the demo pool with `"balancing": "reset"`.

To rebuild every image in `docs/images/`:

```sh
~/.codexpool/.venv/bin/python docs/images/demo/render.py
```

## SwiftBar or xbar instead

`codexpool menubar` prints plugin output for SwiftBar or xbar: the serving seat and its usage (left or used, per
`display`), a line per seat
with enable/disable/re-login, and the usual Status, Doctor, Pool log and Restart items. The native app is the
supported path and the only one with the reset action.
