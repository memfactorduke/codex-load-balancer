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
- **How the pool picks a seat, also from `status.json`**: `pool.balancing` = `priority` (default, and what a file
  without it means: the fill order you set) or `reset` (soonest reset first: the guard reorders the regular seats
  on every pass so the one whose weekly quota resets soonest comes first; the reserve stays last). The app only
  shows it (the seat list's header, below); the list is always in the pool's current fill order either way.

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

**Closing, like a menu.** A click anywhere else closes it: in another app's window, on the desktop, in the menu bar
or on another menu bar item; so do Escape, and another app coming forward. The app is an agent (no Dock icon)
and usually not active, so a transient popover alone never hears of clicks in other apps. While the popover
shows (installed right after `showRelativeToRect:`, removed in `popoverWillClose:` and, unless it was opened again
meanwhile, `popoverDidClose:`; the controller keeps the monitor objects and their handlers):
- a **global** `NSEvent` monitor for left, right and other mouse-down closes it. A global monitor only gets events
  sent to other apps, never ours, so clicks in the popover and on the status item still go to their views; the
  handler also ignores a click whose screen location is on the popover's content or the item, just in case, and
  one while a seat menu is up (the menu tracks events itself; a click outside it only dismisses it);
- a **local** key-down monitor closes it on Escape (no modifiers) and swallows that key; every other key goes on
  as before (⌘, opens Settings). It sees keys only while the popover's window is key;
- `applicationDidResignActive:` closes it (⌘-Tab, or a click elsewhere after the app became active).

Clicking the status item still toggles: its mouse-down reaches us, the transient popover may already close on it,
and a click within 0.35 s (`REOPEN_GUARD_S`) of a close is taken as the click that closed it, so it does not open
again at once. Closes for any other reason (outside click, Escape, resigning active) don't arm that guard, so the
next click on the item opens the popover straight away.

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
5. **Seats**, in fill order (highest priority first). The header's right side names the order: "Your order"
   (`pool.balancing` `priority`) or "Soonest reset first" (`reset`), with a tooltip saying how the pool picks a
   seat ("… New threads follow; running threads stay on their seat. The reserve stays last."). One row per seat:
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
     - Make first (priority above the current first; disabled for reserve seats and for the seat already first,
       and for every seat while balancing is `reset`, since the guard sets the order then, with a tooltip pointing
       to Settings → Balancing).
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
needs a window). `--hover tip:order` prints the seat header's tooltip. `--history` defaults to `history.jsonl`
beside `--status`. Used by humans and agents to check the design; the screenshots in `docs/images/` are made this
way from synthetic data (`docs/images/demo/status-reset.json` is the regular scenario with `"balancing": "reset"`).

## Settings window and Setup assistant
`menubar/codexpool_settings.py`: a System Settings-style window (sidebar + content pane) and a three-step Setup
assistant, in PyObjC/AppKit with native controls laid out with `NSStackView` and `NSGridView`. Same rules as the
menu bar app: no Keychain, no network, no management API; the main thread never waits.

**Process.** `<menubar python> codexpool_settings.py [--pane NAME]`, NAME one of `overview`, `seats`, `balancing`,
`lanes`, `general`, `health`, `about`, `setup-welcome`, `setup-accounts`, `setup-done`. Regular activation policy while
open, so it has a Dock icon (the capsule mark, drawn at runtime) and a main menu (About, Settings… ⌘,, Setup
Assistant…, Edit for text fields, View ⌘1–⌘7 for the panes, Window, Help). Single instance: it holds an
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
| Seats | The seats in fill order (select one), then its settings | Name (`codexpool label SEAT NEW`, on Return or leaving the field), Size (`codexpool weight SEAT N`), Fill order (its place, "2nd of 3 regular seats, in your order", or Reserve; the row opens Balancing, where the order and the reserve are set), In rotation switch (`codexpool enable\|disable SEAT`; a parked seat asks first, since enabling it spends credits), Redeem Reset… (`codexpool reset SEAT --yes` after a sheet saying it spends 1 of n banked free resets and never buys one), Sign In Again… (the assistant, with the seat's label and priority), Remove… (`codexpool remove SEAT --yes` after a destructive confirmation), Add Account…. SEAT is the seat file name. The outcome of the last command shows inline in the group. |
| Balancing | How the pool picks a seat (`pool.balancing`), the regular seats in fill order (label, plan badge, state, week left and reset; in "Soonest reset first" the guard's order, read-only, "Updates itself"), the reserve seats last, and a Reserve group (every seat with "Use last (reserve)"). No seats: an Add a ChatGPT Account… empty state. | Two radio buttons, **Your order** and **Soonest reset first** (`codexpool set balancing priority\|reset`); ▲▼ per seat in your order (a burst of clicks commits once: `codexpool order SEAT…`); the Reserve checkboxes (`codexpool reserve SEAT [--off]`). A radio button or checkbox shows the choice just made until status.json agrees (the guard pass after the command, or the command failing, ends that). |
| Lanes | `codexpool lane list --json` and `lane providers --json`: one card per lane (name, “label” in the model picker, effort, role, members in order as name, provider title · model, last test ✓/✗ (a member's failure says just Failed, its reason in the tooltip and on the lane's own test line) and state pill; the lane's last test), then Apply lanes, Documentation and Credentials (each provider in use or ready, and each responses member's own key, with its state; a CLI hint in a detail, "(codexpool lane login xai)", is not shown). No lanes: what a lane is, New Lane… and Read About Lanes. | New Lane…, per card Delete… (set apart from the others; destructive confirm, `codexpool lane remove LANE`), Test… (confirm: spends lane quota, can take minutes; then `codexpool lane test LANE` streams into a sheet with Stop), Edit…; Apply… (confirm, `codexpool lane apply`), Open Docs; per credential Sign In…/Sign In Again… or Add Key…/Replace Key…. |
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

**Lane sheets** (stage 3), each on the Settings window or on the sheet that opened it:
- **Lane editor** (New Lane… and Edit…): Name (new lanes only: lowercase letters, digits and -), Picker label
  (placeholder = the capitalised name; empty means default), an Effort popup (low/medium/high/xhigh), Role (a
  wrapping text view in a rounded box, as tall as its text from 58 to 150 pt, scrolling past that; Return and Tab
  end editing, since a role is one paragraph) and Members in fallback order (provider title · model, ▲▼ and − per
  row), then Add Model…. Save runs exactly one command:
  - `lane add NAME --member=P:M[:NAME]… --effort E [--role=…] [--display=…] [--base-url=…]` for a new lane;
  - `lane edit NAME` for an existing one, with only the changes: `--role=`, `--effort`, `--display=`/`--no-display`,
    then removals, then additions in list order, then `--move-member ID --to POS` from the last place to the first.
    Nothing changed closes the sheet.

  It checks the name, the 40-character label, a non-empty role, at least one member, at most one xAI member and at
  most one new Responses endpoint first. On failure it keeps the sheet open with the command's last lines. When
  lane apply stopped for a missing key ("no API key for NAME", nothing changed), it shows an Add Key… row for each
  such key instead (a new responses member's key is `<lane>-<id>`, known only once the CLI has named it), and saves
  again once the last one is in.
- **Add Model**: a Provider popup (xAI disabled when the lane already has an xAI member), a Model combo box filled by
  `lane models PROVIDER --json` with a spinner (not fetched for a provider that needs a key it doesn't have; typing
  an id always works; a list that failed says "Couldn’t list the models; type an id." with the reason in the
  tooltip), a Display name defaulting to the listed name, and a Base URL for responses. A provider that isn't ready
  shows Sign In to xAI… or Add Key… right there, then relists its models. Add refuses a model the lane already has
  and a second Responses endpoint while one is unsaved (`--base-url` goes to every responses member of one command).
- **Add Key**: an NSSecureTextField whose value is piped to `codexpool lane key NAME -` on stdin (never in argv or a
  file). The field is cleared the moment it is read, and when the sheet is cancelled.
- **xAI sign-in**: `lane login xai --no-open` with `CODEXPOOL_NO_CLIPBOARD=1`. The sheet copies the first `https://`
  link itself and shows it with Open in Browser, Open in Private Chrome Window, Copy Link and "Copied", like the
  Setup assistant. "Authentication saved to" means finishing, and it is never stopped then. Exit 0 means done, which
  refreshes providers and lanes; closing or quitting stops a login that is still waiting.

**Commands (stage 3)**: `set balancing priority|reset`, `order SEAT…`, `lane providers --json`, `lane models PROVIDER
[--base-url URL] [--key-name NAME] --json` (`{"error": str}` on failure), `lane edit …`, `lane key NAME -` (stdin),
`lane login xai --no-open`.

**Snapshot mode**, for QA and the docs, shows no UI and runs no command (a guard in the code refuses to):
```
codexpool_settings.py --snapshot OUT.png --pane NAME --appearance light|dark --status PATH
                      [--doctor PATH] [--lanes PATH] [--providers PATH] [--models PATH] [--history PATH]
                      [--now ISO-8601] [--height PT]
```
NAME also takes `setup-signin` (a waiting sign-in with a made-up link) and `setup-added`, and `lanes-edit`,
`lanes-new`, `lanes-model`, `lanes-model-key`, `lanes-key`, `lanes-signin`: the Lanes pane with that sheet (or
nested sheets) drawn under the toolbar. `--providers` and `--models` stand in for `lane providers --json` and every
`lane models --json`. It builds the real
window offscreen, draws it as the key window (a snapshot-only subclass), caches its frame view at 2× and sets it on
a soft backdrop with a shadow, like the popover shots. `--now` defaults to the status file's `generated_at`, so
demo data reads as fresh; the height fits the content unless `--height` is given. The Overview's pace line
comes from history.jsonl: live from `~/.codexpool/state/history.jsonl`, in a snapshot only from `--history`
(none by default, so a snapshot never depends on the live file). Fixtures: `docs/images/demo/`
(`status-*.json`, among them `status-reset.json` with `"balancing": "reset"`, `doctor.json`, `lanes.json`,
`lane-providers.json`, `lane-models.json`, all made up); `docs/images/demo/render.py` renders the `settings-*.png`
(including `settings-balancing-light.png` and `settings-lanes-edit-light.png`) and `setup-*.png` in `docs/images/`
with the rest.

## Running
The LaunchAgent (label `menubar_label` from settings.json, default `com.codexpool.menubar`, template
`launchd/menubar.plist.template`) runs `<menubar_python> ~/.codexpool/menubar/codexpool_menubar.py` with
RunAtLoad and KeepAlive (restart unless it quit cleanly). Log: `~/.codexpool/logs/menubar.log`. Restart it after
editing: `launchctl kickstart -k gui/$(id -u)/com.codexpool.menubar`.
