# Codex Pool menu bar app: spec

A native macOS menu bar app for codexpool, in CodexBar's design language. Python 3.11+ with PyObjC
(`pyobjc-core`, `pyobjc-framework-Cocoa`), one file: `menubar/codexpool_menubar.py`. No Xcode needed.
`codexpool install` puts PyObjC into the interpreter named by `menubar_python` in `~/.codexpool/settings.json`
(default: codexpool's own venv, `~/.codexpool/.venv`) and loads the LaunchAgent.

## Hard rules
- **Reads only** `~/.codexpool/state/status.json` (written every 60 s by `codexpool guard`),
  `~/.codexpool/state/history.jsonl`, and `settings.json` (only to find the Python that runs codexpool).
  **No Keychain access, no network, no management API.** A locked Keychain would pop password dialogs.
- A missing, stale (> 3 min) or partial status file renders a clear "pool not reporting" state, never a crash.
  A half-written file keeps the last good copy. Right after the Mac wakes, an old file gets 2 minutes' grace.
- Actions shell out to codexpool without blocking: `bin/codexpool` run with the `python` from settings.json
  (else codexpool's venv), never through `~/.local/bin` or the script's `#!/usr/bin/env python3`, because under
  launchd `PATH` is `/usr/bin:/bin` and `python3` there is the system one. Commands that need a terminal open
  one (a `.command` file, so no Apple Events permission is needed) and show the short `codexpool` form when
  `~/.local/bin/codexpool` is the wrapper `codexpool install` wrote.
- Never block the main thread: file reads are tiny, subprocesses are reaped on a background thread.
- Refresh: stat the status file every 10 s; redraw only when it changed, when the state crosses a threshold
  (for example it goes stale), or every 30 s while the popover is open (countdowns).
- **Two presentation modes, both from `status.json`** (the guard copies them from `settings.json`; the app never
  reads them from settings): `pool.headline` = `all` (default: every seat that is not off, reserve included) or
  `regular` (reserve seats left out), and `pool.display` = `left` (default) or `used`. `pool.used_pct` is the
  headline's used %; `used_pct_all` and `used_pct_regular` carry both. A file without `headline` (an older guard)
  is read as `all`, from `used_pct_all`. In `left` mode every number is 100 minus the used % and every bar's length
  is what is left, so bars drain; bar colours always come from the used %, so both modes colour alike (green
  above 30 % left = under 70 % used, orange at 30 % left or less, red at 10 % left or less = 90 % used or more).
  `used` mode with `headline: regular` is the original presentation, unchanged.

## Menu bar item (next to the clock)
- `NSStatusItem`, autosave name `CodexPool`, fixed width (fits ` 100%`, so neighbours never shift).
- Image: a coloured (non-template) capsule meter, 18 × 12 pt: two stacked rounded bars. Top = the headline
  (`pool.used_pct`), red while a reserve seat serves (like the title), else by threshold; bottom = the serving
  seat's week, by threshold. Both drain in `left` mode and fill in `used` mode.
- Title: the headline percentage as left (or used), e.g. `54%`, menu bar font size, regular weight, monospaced
  digits:
  - **green** while a regular seat serves,
  - **red** while a reserve seat serves (`pool.reserve_in_use`): the regular seats are spent,
  - grey, with a warning triangle in place of the meter, when the pool is down, not reporting, or every seat is out.
- The headline is the weight-averaged weekly use of every seat that is not off, reserve seats included
  (`headline: all`), or of the regular seats only (`headline: regular`); weights from `seats.json`, Plus = 1,
  Pro = 20. The colour rule does not depend on it.
- Tooltip, worded like the hero's caption: "Codex Pool: 54% left this week · all seats · serving Work B" ("… ·
  serving the reserve seat Pro 20x"). `used` mode with `headline: regular` keeps the original "Codex Pool: 67% of
  the regular seats used this week, serving Work B" and "regular seats used up, serving the reserve seat Pro 20x".

## Popover (click the item)
Transient `NSPopover`, 340 pt wide, 16 pt padding. No `NSVisualEffectView`: the popover draws its own material
(Liquid Glass on macOS 26) and the content is transparent over it. System fonts and semantic colours, so light
and dark both work; coloured text uses darker variants in light mode for contrast. Sections are separated by
hairlines. If the popover would not fit on the screen, only the seat list scrolls.

1. **Header**: "Codex Pool" (15 pt semibold); subtitle (11 pt, secondary) "Updated 1m ago · Serving Team".
   Right: a pill `Regular` (green) / `Reserve` (red) / `All out` / `Down` / `Stale` / `No seats` / `No data` (grey).
   After an action the subtitle shows its feedback for 8 s ("Resetting Work B…").
2. **Problem banner** when the pool is down (Restart Pool), not reporting (Run Doctor) or empty (Add Seat…).
3. **Hero**: big `54%` (28 pt semibold, coloured like the menu bar title) + "left this week · all seats" ("used
   this week", "· regular seats" in the other modes), a 6 pt bar (red while a reserve seat serves, like the
   number; else by threshold, grey when nothing serves), then:
   - "3 of 4 regular seats ready · reserve 66% left" ("reserve at 34%" in `used` mode)
   - "Next back: Work B in 3h 56m" when a seat is out (emphasised when everything regular is out)
   - **Resets** (blue): "Reset available for Work B · click the seat" when an out seat has a banked free reset
   - **Pace** from history: "At this pace the pool lasts ~27h" (`headline: all`, also while the reserve serves) or
     "At this pace the regular seats last ~9h" (`headline: regular`, only while a regular seat serves), or
     "Steady". The slope is taken on the headline's used %, ignores drops (weekly resets) and needs ≥ 30 min of
     rising samples in the last 6 h.
   - **Tooltip** over the number and its bar (no highlight, no click): one line per seat in fill order, name ·
     size · figure · state · countdown: "Work A · 5× · 0% left · back in 2d 7h" ("100% used" in `used` mode),
     "Work B · 5× · 56% left · serving · resets in 6d 13h", "Pro 20x · 20× reserve · 66% left · resets in 3d 8h";
     with `headline: regular` a reserve seat reads "Pro 20x · reserve, not counted · 66% left". Disabled seats:
     "Personal · off, not counted"; no weekly figure: "· usage unknown, not counted". Last line: "Weighted by size:
     54% left · all seats".
4. **Chart**, titled "Quota left" (`left` mode) or "Usage" (`used` mode): the headline over 24 h or 7 d (toggle),
   44 pt tall, fixed 0–100 % scale, 1.5 pt line over a soft gradient; green where a regular seat served, red
   where the reserve did, grey when nothing serves now. It plots what the hero shows: history's `all` or `used`
   (regular seats) series, as left or as used, so in `left` mode it falls over time and jumps up at a reset. A
   dashed stub carries the last value to "now". Fewer than 3 samples: "Collecting history…".
5. **Seats**, in fill order (highest priority first). One row per seat:
   - Line 1: **name**, plan badge in small text (`Team 1×`, `Business 5×`, `Pro 20×`), `· Reserve` tag, and at
     the right the state: capsules `Serving` (green; red for a reserve) / `Out` / `Parked` / `Blocked`, plain
     text `Ready` / `Off`. A seat with banked resets shows `↺ n` before it, blue when the seat is out.
   - Bars: the weekly bar; seats with a 5-hour window get a thin 5 h bar under it. Green < 70 % used, orange
     70–90 %, red ≥ 90 % (the same colours in `left` mode, where the bars drain); grey for seats that cannot serve.
   - Line 3: "51% left" / "49% used" (or "Week 36% left · 5h 0% left" / "Week 64% · 5h 100%", the binding window
     emphasised) left; "Resets in 3d 20h", or
     "Back in 3h 56m" for out and parked seats, right, plus "· reset available" when a reset would bring it back.
     Blocked seats say "Re-login needed" and show the error on an extra line.
   - Tooltip: the blocked detail and "n banked resets, soonest expires Oct 3. Click to use one."
   - Clicking a row opens an `NSMenu` (seat file name `name` is what gets passed to codexpool):
     - **Use reset now… (n banked, expires Oct 3)** when the seat has a banked reset. Asks first ("Use a reset on
       Work B?"), then runs `codexpool reset <seat> --yes`, which redeems one banked **free** reset (the
       soonest-expiring) and never buys one.
     - Re-login… first when the seat is blocked (Terminal, `codexpool login <label> --no-open --priority <n>`,
       with a hint to sign in as the seat's email in a private window).
     - Enable (spends credits)… for a parked seat, with a confirmation; Enable for a disabled one; else Disable.
     - Make first (priority above the current first; disabled for reserve seats and for the seat already first).
     - Re-login… (when not blocked).
   - After a successful action the app runs one `codexpool guard` pass so the popover shows the effect at once.
6. **Footer rows** (SF Symbols): Status… (`terminal`, Terminal `codexpool status --live`), Doctor (`stethoscope`),
   Pool log (`doc.text`, `codexpool logs -f`), Docs (`book`, opens `~/.codexpool/README.md`, else the README on
   GitHub), Refresh (`arrow.clockwise`, runs a guard pass), then Quit, with the CLIProxyAPI version at the right.

## Pinning next to the clock
At startup the app sets its bundle identifier to `com.codexpool.menubar` (PyObjC: patch
`NSBundle.mainBundle().infoDictionary()`), uses `NSApplicationActivationPolicyAccessory` (no Dock icon) and the
autosave name `CodexPool`. On first run only it writes `NSStatusItem Preferred Position CodexPool = 1` into that
defaults domain, so macOS places it rightmost among third-party items. After a ⌘-drag macOS stores the new
position under the same key, and the app leaves it alone.

## Verification mode
```
codexpool_menubar.py --snapshot OUT.png [--appearance light|dark] [--status PATH] [--history PATH]
                     [--now ISO-8601] [--range 24h|7d] [--max-height PT] [--hover KIND:VALUE]
```
Renders the popover (`OUT.png`) and the menu bar item on a menu-bar-like strip (`OUT-menubar.png`), both at 2×,
and prints the hovered region's tooltip, if any (`--hover tip:headline` prints the hero's breakdown),
without showing UI (offscreen `NSView` caching on an opaque stand-in for the popover material, because vibrancy
needs a window). `--history` defaults to `history.jsonl` beside `--status`. Used by humans and agents to check the
design; the screenshots in `docs/images/` are made this way from synthetic data.

## Running
The LaunchAgent (label `menubar_label` from settings.json, default `com.codexpool.menubar`, template
`launchd/menubar.plist.template`) runs `<menubar_python> ~/.codexpool/menubar/codexpool_menubar.py` with
RunAtLoad and KeepAlive (restart unless it quit cleanly). Log: `~/.codexpool/logs/menubar.log`. Restart it after
editing: `launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar`.
