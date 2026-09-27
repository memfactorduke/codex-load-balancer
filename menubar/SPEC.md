# Codex Pool menu bar app: spec

A native macOS menu bar app for codexpool, in CodexBar's design language. Python 3.11+ with PyObjC
(`pyobjc-core`, `pyobjc-framework-Cocoa`), one file: `menubar/codexpool_menubar.py`. No Xcode needed. Its
Settings window and Setup assistant are a second file, `menubar/codexpool_settings.py`, run as their own process
(see [Settings window and Setup assistant](#settings-window-and-setup-assistant) below).
`codexpool install` puts PyObjC into the interpreter named by `menubar_python` in `~/.codexpool/settings.json`
(default: codexpool's own venv, `~/.codexpool/.venv`) and loads the LaunchAgent.

## Hard rules
- **Reads only** `~/.codexpool/state/status.json` (written every 60 s by `codexpool guard`),
  `~/.codexpool/state/history.jsonl`, and `settings.json` (only to find the Python that runs codexpool).
  **No Keychain access, no network, no management API.** A locked Keychain would pop password dialogs. The one
  file it writes is the marker `state/setup-shown` (first run, below).
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
2. **Problem banner** when the pool is down (Restart Pool), not reporting (Run Doctor) or empty (Add Account…,
   which opens the Setup assistant's Add accounts step).
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
     Blocked seats say "Re-login needed" and show the error on an extra line. A seat the pool still serves after
     OpenAI ended its sign-in (`sign_in_ended` in its status.json row, state `active`/`ready`) says "Re-login soon"
     in orange, with "OpenAI ended this sign-in" on an extra line; it serves until its access token runs out.
   - Tooltip: the blocked detail and "n banked resets, soonest expires Oct 3. Click to use one."
   - Clicking a row opens an `NSMenu` (seat file name `name` is what gets passed to codexpool):
     - **Use reset now… (n banked, expires Oct 3)** when the seat has a banked reset. Asks first ("Use a reset on
       Work B?"), then runs `codexpool reset <seat> --yes`, which redeems one banked **free** reset (the
       soonest-expiring) and never buys one.
     - Re-login… first when the seat is blocked or says "Re-login soon" (Terminal, `codexpool login <label> --no-open --priority <n>`,
       with a hint to sign in as the seat's email in a private window; the command also puts the link on the
       clipboard).
     - Enable (spends credits)… for a parked seat, with a confirmation; Enable for a disabled one; else Disable.
     - Make first (priority above the current first; disabled for reserve seats and for the seat already first).
     - Re-login… (when not first).
   - After a successful action the app runs one `codexpool guard` pass so the popover shows the effect at once.
6. **Footer rows** (SF Symbols): Status… (`terminal`, Terminal `codexpool status --live`), Doctor (`stethoscope`),
   Pool log (`doc.text`, `codexpool logs -f`), Docs (`book`, opens `~/.codexpool/README.md`, else the README on
   GitHub), Refresh (`arrow.clockwise`, runs a guard pass); a hairline; then Add a ChatGPT account…
   (`person.crop.circle.badge.plus`, the Setup assistant's Add accounts step), Settings… (`gearshape`, with a
   `⌘,` hint at the right; ⌘, works while the popover is open) and Quit, with the CLIProxyAPI version at the right.
   Both start `codexpool_settings.py` (below) with the app's own interpreter (`sys.executable`, which has
   PyObjC), non-blocking and reaped like every helper; without that file, Add a ChatGPT account… falls back to the
   Terminal sign-in.
7. **First run.** At the first report from a running pool (not missing, stale or down), if `status.json` lists no
   Codex seats (lane credentials have another `provider`), the app opens the Setup assistant
   (`--pane setup-welcome`). Either way it writes `state/setup-shown` then, and never opens the assistant on its
   own again.

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

## Settings window and Setup assistant
`menubar/codexpool_settings.py`: a System Settings-style window (sidebar + content pane) and a three-step Setup
assistant, in PyObjC/AppKit with native controls laid out with `NSStackView` and `NSGridView`. Same rules as the
menu bar app: no Keychain, no network, no management API; the main thread never waits.

**Process.** `<menubar python> codexpool_settings.py [--pane NAME]`, NAME one of `overview`, `seats`, `lanes`,
`general`, `health`, `about`, `setup-welcome`, `setup-accounts`, `setup-done`. Regular activation policy while
open, so it has a Dock icon (the capsule mark, drawn at runtime) and a main menu (About, Settings… ⌘,, Setup
Assistant…, Edit for text fields, View ⌘1–⌘6 for the panes, Window, Help). Single instance: it holds an
exclusive `flock` on `~/.codexpool/state/settings.pid` (which holds its pid); a second launch posts the
distributed notification `com.codexpool.settings.show` with the pane (and leaves it in `state/settings-request` for
a first instance still starting up, which reads it once it listens), brings the first forward and exits. Closing
the last window quits it. Its output (a traceback, say) goes to `~/.codexpool/logs/settings.log`, whether the menu
bar app or `codexpool gui` started it.

**Data.** `status.json` and `history.jsonl` (the pace line) through the menu bar app's own code (`DataSource`, `build_model`: the same parsing and the
same stale/down handling), imported from `codexpool_menubar.py`, polled every 10 s in the default run loop mode (so
no rebuild while a pop-up menu is open). Everything else comes from codexpool commands, run in the background exactly
like the menu bar app's actions (`bin/codexpool` with the `python` from settings.json, stdout and stderr captured, a
new session so Stop can end the whole group). After a change it runs one `codexpool guard` pass and re-reads
`status.json`. A pane is rebuilt when its data changes, keeping the scroll position, but never while a text field is
being edited.

**Look.** 820 pt wide, resizable in height. A 220 pt sidebar (`NSVisualEffectView`, sidebar material) with the app
icon, the wordmark and one line of status ("54% left · Work B", green or red like the menu bar), then the panes as
rows with white SF Symbols on coloured rounded squares, the selection in the accent colour. The content pane has the
pane's title in the 52 pt toolbar band and scrolls grouped sections: rounded boxes (10 pt radius, a faint fill and
hairline) whose rows are separated by inset hairlines, a title with a one-line explanation on the left and the
control on the right. Colours and meters are the menu bar app's (`C`, `draw_bar`, pills), so green, orange, red and
the drain direction mean the same thing everywhere. Light and dark both follow the system. VoiceOver reads the
custom-drawn parts: the headline and its pill, each seat's meters, the pills and the step dots are static text; a
row in the Seats list is a radio button and an About link row is a link, each labelled and pressable.

**Panes** (the command each control runs):

| Pane | Shows | Controls |
|---|---|---|
| Overview | The headline (big number, left or used, `pool.display`/`pool.headline` as in the popover, its bar, the Regular/Reserve pill, the per-seat breakdown as its tooltip); new threads → seat, regular seats ready, reserve, next back; a link to a seat with a banked reset; the pace line; every seat with a state dot, plan badge (size ×), Reserve tag, banked resets, weekly and 5-hour bars with reset times; last updated. Down, not reporting and no seats get an explanation and one button. | Restart Pool… (`codexpool restart`), Check Health, Add a ChatGPT Account… |
| Seats | The seats in fill order (select one), then its settings | Name (`codexpool label SEAT NEW`, on Return or leaving the field), Size (`codexpool weight SEAT N`), Priority field + stepper (`codexpool priority SEAT N`, a burst of clicks commits once), Reserve switch (`codexpool reserve SEAT [--off]`), In rotation switch (`codexpool enable\|disable SEAT`; a parked seat asks first, since enabling it spends credits), Redeem Reset… (`codexpool reset SEAT --yes` after a sheet saying it spends 1 of n banked free resets and never buys one), Sign In Again… (the assistant, with the seat's label and priority), Remove… (`codexpool remove SEAT --yes` after a destructive confirmation), Add Account…. SEAT is the seat file name. The outcome of the last command shows inline in the group. |
| Lanes | `codexpool lane list --json`: each lane (name, effort, role) and its members in order with provider, model, state and last test. No lanes: what a lane is, and a link to docs/LANES.md. | Test… (confirm: spends lane quota, can take minutes; then `codexpool lane test LANE` streams into a sheet with Stop), Apply… (confirm, `codexpool lane apply`), Open Docs |
| General | The menu bar display | Numbers show Left/Used (`codexpool set display left\|used`), Headline covers All seats/Regular seats (`codexpool set headline all\|regular`), Restart… (confirm, `codexpool restart`), Open Logs (`~/.codexpool/logs/`), Reopen Codex… (confirm; quits every `com.openai.codex` app, waits up to 20 s, then `open -b com.openai.codex`), the Setup assistant |
| Health | `codexpool doctor --json`: a summary (everything good / n problems, n warnings) and each section's checks with ✓ ! ✗ and the fix hint | Run Again, Copy Report (the doctor's text form, nothing redacted: it stays local), Run in Terminal when there is no report |
| About | The icon, wordmark, `codexpool version`, the CLIProxyAPI version, the pitch; links (website, source, docs, report an issue); the license in one sentence and the disclaimer | |

**Setup assistant** (its own 640 × 560 window, step dots in the title bar; a pane name starting with `setup-` opens
it): 1 **Welcome**: what codexpool does and a checklist from `codexpool doctor --json` (pool running, Codex app
pointed at the pool, menu bar running; a failing check shows its fix). 2 **Add accounts**: the seats found, then a
name field and Get Sign-In Link, which runs `codexpool login NAME --no-open --no-copy` (plus `--priority N` for
Sign In Again) in the background and takes the first `https://` URL it prints. It puts that URL on the clipboard
itself (NSPasteboard; `--no-copy` keeps the CLI's own copy out of it) and shows a green check and "Copied" after
the buttons while the pasteboard's change count says the link is still there. Then: Open in Browser (`open URL`),
Open in Private Chrome Window (only when Chrome is installed: `open -na "Google Chrome" --args --incognito URL`),
Copy Link (copies it again, "Link copied" in place of the countdown for 1.5 s, or "Couldn’t copy the link" when the pasteboard refused it; "Copied" shows only for a copy that worked), the hint "To add a different
account than the one your browser is signed in to, use a private window.", a 5-minute countdown (at zero the login is stopped and Try Again offered) and Cancel. Once CLIProxyAPI prints
"Authentication saved to", the countdown and Cancel go away ("Signed in. Adding Work C…"): codexpool may still be
naming the seat and is never stopped then. Success is exit 0 and a line starting with `seat `: it shows the plan
and, after a guard pass, the size the pool gave it (`Business 5×`), with Mark as Reserve (`codexpool reserve SEAT`)
and Add Another…. If the seat file was already in the pool (the browser signed in to an account that is already a
seat), nothing was added: it says so, calls the seat by its name (`login LABEL` never renames a seat that is
already in the pool) and says to close every private window before opening the link in a new one. If the login
printed that it could not point Codex at the pool ("warning: could not point Codex at the pool", or
"openai_base_url was changed by hand"), the card says so with the fix, `codexpool install`, and so does Done,
which then reads "One more step" instead of "You're all set". Closing the window stops a login that is still
waiting for the browser. One that is finishing keeps running (its result shows when the assistant opens again),
and quitting, or closing the last window, waits for it (up to 45 s). A request from another launch (the
installer, the menu bar's first run) only brings an open assistant forward when it would send it back to Welcome
or away from a sign-in in progress.
3 **Done**: Quit and Reopen Codex… (confirm), and where Settings live.

**Commands the GUI codes against.** Existing: `status --json` (same shape as status.json), `login LABEL --no-open
--no-copy [--priority N]`, `label`, `weight`, `priority`, `reserve [--off]`, `enable`, `disable`, `reset --yes`, `remove
--yes`, `restart`, `lane test LANE`, `lane apply`, `guard`. New (stage 2): `doctor --json` → `{"ok": bool,
"problems": int, "warnings": int, "sections": [{"title": str, "checks": [{"status": "ok"|"warn"|"fail", "text": str,
"fix": str|null}]}]}`; `lane list --json` → `{"lanes": [{"name": str, "display": str (the lane's name in the Codex
model picker), "effort": str, "role": str, "members": [{"id": str, "provider": str, "model": str, "name": str, "state":
str, "last_test": {"ok": bool, "when": str, "reason": str}|null}]}]}`; `set KEY VALUE` (keys `display`,
`headline`); `version` (prints `codexpool X.Y.Z`). Until a command exists, argparse's answer (exit 2, "invalid
choice" or "unrecognized arguments") shows as "This needs a newer codexpool. Run the installer again to update it,
then try again." in place of that data.

**Snapshot mode**, for QA and the docs, shows no UI and runs no command (a guard in the code refuses to):
```
codexpool_settings.py --snapshot OUT.png --pane NAME --appearance light|dark --status PATH
                      [--doctor PATH] [--lanes PATH] [--history PATH] [--now ISO-8601] [--height PT]
```
NAME also takes `setup-signin` (a waiting sign-in with a made-up link) and `setup-added`. It builds the real
window offscreen, draws it as the key window (a snapshot-only subclass), caches its frame view at 2× and sets it on
a soft backdrop with a shadow, like the popover shots. `--now` defaults to the status file's `generated_at`, so
demo data reads as fresh; the height fits the content unless `--height` is given. The Overview's pace line
comes from history.jsonl: live from `~/.codexpool/state/history.jsonl`, in a snapshot only from `--history`
(none by default, so a snapshot never depends on the live file). Fixtures: `docs/images/demo/`
(`status-*.json`, `doctor.json`, `lanes.json`, all made up); `docs/images/demo/render.py` renders the
`settings-*.png` and `setup-*.png` in `docs/images/` with the rest.

## Running
The LaunchAgent (label `menubar_label` from settings.json, default `com.codexpool.menubar`, template
`launchd/menubar.plist.template`) runs `<menubar_python> ~/.codexpool/menubar/codexpool_menubar.py` with
RunAtLoad and KeepAlive (restart unless it quit cleanly). Log: `~/.codexpool/logs/menubar.log`. Restart it after
editing: `launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar`.
