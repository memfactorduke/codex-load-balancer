# The menu bar app

A native macOS menu bar item for codexpool, written in Python with PyObjC (one file,
[`menubar/codexpool_menubar.py`](../menubar/codexpool_menubar.py); no Xcode). `codexpool install` sets it up and
launchd keeps it running. The design spec, for anyone changing it, is [`menubar/SPEC.md`](../menubar/SPEC.md).

<p>
  <img src="images/popover-light.png" width="340" alt="The popover in light mode: 54% left this week across all seats, five seats, Work B serving">
  <img src="images/popover-dark.png" width="340" alt="The same popover in dark mode">
</p>

## What it can touch

It reads `~/.codexpool/state/status.json` (the guard rewrites it every 60 seconds), `state/history.jsonl` for the
chart, and `settings.json` only to find the Python that runs codexpool. The `headline` and `display` settings reach
it through `status.json`, where the guard copies them. It never touches the Keychain, never
calls the network and never talks to the pool's management API. Actions run the `codexpool` command in the
background, or open Terminal for commands that need one.

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

Click the item.

1. **Header**: "Codex Pool", when the status was last updated and which seat is serving. The pill on the right
   says `Regular` (green), `Reserve` (red), or `All out`, `Down`, `Stale`, `No seats`, `No data` (grey). After
   you use an action, the subtitle shows its progress and result for a few seconds.
2. **Problem banner** when something needs you: pool down (**Restart Pool**), not reporting (**Run Doctor**) or
   no seats yet (**Add Seat…**, which opens Terminal with `codexpool login`).
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
   the error.
6. **Footer**: Status… (Terminal, `codexpool status --live`), Doctor, Pool log (`codexpool logs -f`), Docs (the
   README), Refresh (runs one guard pass now), Quit, and the running CLIProxyAPI version.

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
| **Re-login…** | always (near the top when the seat is blocked) | Terminal: `codexpool login <Label> --no-open --priority <current>` |
| **Enable (spends credits)…** | the credit guard parked the seat | asks first, then `codexpool enable <seat>` |
| **Enable** / **Disable** | the seat is off / on | `codexpool enable` / `codexpool disable` |
| **Make first** | any regular seat that isn't already first | `codexpool priority <seat> <top + 10>` |

**Resets.** A seat with banked resets shows `↺ n` next to its state, blue when the seat can't serve (that is when
a reset helps). Hovering shows how many are banked and when the soonest expires. **Use reset now…** asks "Use a
reset on Work B?" and explains that it uses one of the banked free resets and never buys one. After you confirm,
the seat's weekly and 5-hour limits go back to full and the pool can use it right away. If the seat didn't need a
reset, ChatGPT declines and the credit is kept; the header shows the reason.

**Re-login** opens Terminal with the sign-in link printed rather than opened, and reminds you which account to
use: open the link in a private window and sign in to that account. It passes the seat's current priority back,
because a new login rewrites the seat file that holds it.

**Make first** is disabled for reserve seats: moving the reserve first would drain it before the regular seats.

After any action the app runs one guard pass, so the popover shows the effect at once.

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
To rebuild every image in `docs/images/`:

```sh
~/.codexpool/.venv/bin/python docs/images/demo/render.py
```

## SwiftBar or xbar instead

`codexpool menubar` prints plugin output for SwiftBar or xbar: the serving seat and its usage (left or used, per
`display`), a line per seat
with enable/disable/re-login, and the usual Status, Doctor, Pool log and Restart items. The native app is the
supported path and the only one with the reset action.
