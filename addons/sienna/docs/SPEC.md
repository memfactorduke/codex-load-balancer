# The Claude pool in the menu bar app: spec

Part of the sienna add-on: what `menubar_ext.py` adds to the core apps (spec: the core's `menubar/SPEC.md`).

## Current Claude CLI view

The supported UI is `cswap_ui.py`, a file-only view of `claude-cli-status.json`.
Account rows retain detected plan badges from the core parser (Max 5×, Max 20×,
Team Premium 6.25×, or Plan unknown). The selected available account appears first,
then ready regular accounts, then ready reserves, then unavailable accounts, keeping
slot order within each group. Settings' account cards use the same display grouping
and plan badges. cswap's account selection is unchanged. `cswap_patch.py` projects
identity-checked OAuth profile metadata through cswap's public JSON; the adapter
normalizes it to `plan`, `weight`, `capacity_known` and `plan_detected_at`. The
headline remains the selected account's measured weekly usage.
The shared Compact / Full toggle defaults to Compact and remembers the choice. Compact account rows
show the binding limit first and all limits on hover; Full restores the quota bars.

The remaining sections describe the retained legacy proxy view.

## Two pools (1.3.0)
The Claude pool is installed when `state/claude-status.json` exists and its `pool.installed` is true (a file without
the key, from an older writer, counts while it is fresh; a half-written or unreadable one counts, and shows "not
reporting"). `codexpool claude uninstall` leaves `"installed": false` or removes the file. Without it the item and the popover are the Codex pool's alone, as
below. With it, one item shows both pools and the popover has a Codex | Claude switcher (see the two sections after
the popover). The same model code reads both files (`DataSource(..., pool='claude')`, `build_model(...,
pool_name='claude')`); a stale, half-written, down or empty Claude file is handled exactly like a Codex one.

**Pool colours.** Each pool has its own colour for its number while it serves normally: **Codex blue** and **Claude
coral**. The number turns **red** while that pool's reserve serves, while every seat is out (`allout`) and while a
Claude account spends usage credits as the last resort; it turns **grey** (a warning triangle in place of its glyph or
meter) when the pool is down, not reporting or has no seats. Bars keep the green/orange/red "how much is left" colours
everywhere. The pool colour also draws the pool's glyph, its chart (the line and the use bars) and the selected
tile's hairline; the chart turns red for the samples where the number was red (the reserve serving, every seat out, or credits spent: the
`reserve` and `credits` flags of each history sample, and `all` at 100 %).

| Role | Light | Dark | Contrast (light menu bar #EDEDF2 / popover #F6F6F8; dark #212126 / #2A2A2E) |
|---|---|---|---|
| Codex text (`C.codex_text`) | `#0A6FB5` | `#6CC4FF` | 4.6 / 4.9; 8.4 / 7.5 |
| Codex fill (`C.codex`) | `#1A7BC7` | `#6CC4FF` | glyph, chart, stroke |
| Claude text (`C.claude_text`) | `#AA5034` | `#EE8E6F` | 4.6 / 5.0; 6.7 / 6.0 |
| Claude fill (`C.claude`) | `#C15F3C` | `#EE8E6F` | glyph, chart, stroke |

The blue leans azure so it stays apart from the system link blue of "Reset available" (`C.blue_text`); the coral is a
muted terracotta, so next to the saturated red of the reserve state (and with its own glyph beside the number as a
reference swatch) the change to red is unmistakable. Light shades are darker for text contrast; the fills can be
lighter because glyphs and lines need 3:1, not 4.5:1.


### With the Claude pool: one item, two pools
- The item becomes one strip, `⬡ 54%   ✳ 71%`: per pool its mark in an 11 pt box, 4 pt, then its number in the menu
  bar font with monospaced digits, 10 pt between the halves, centred in a fixed width that fits `100%` twice
  (`strip_length()`), so neighbours never shift. The mark is the pool's real logo, an alpha mask made at runtime from
  the app installed on this Mac (Codex from the ChatGPT app's `icon-codex-light.png`, alpha from chroma so the `>_`
  prompt stays cut out, the inside of the cloud's outline filled solid so its glossy highlight is not a hole; Claude
  from the Claude app's own `TrayIconTemplate`, thickened by a pixel so the thin rays cover one at menu bar size),
  trimmed, checked and cached per (app path, version), re-checked every 10 minutes (a build that failed is tried
  again then); nothing is bundled. It is fitted aspect-correct, on the pixel grid, and optically sized: the solid
  cloud fills the box, the spark overhangs it a little (1.1×), so the two weigh the same next to the numbers.
  Without the app, with a mask that fails the check, or in snapshots (`--marks drawn`, the default), a plain drawn
  glyph stands in: Codex a rounded hexagon outline, Claude an eight-ray asterisk. The Settings switcher shows the
  same masks as template images, with the same fallback. The mark is always in the pool's colour; the number
  follows the colour rules above. A pool
  that is down, not reporting or empty swaps its glyph for a grey warning triangle and greys its number (`—` without
  one); the other half is unaffected. No meter in this mode.
- Tooltip, one line per pool: "Codex 54% left this week · serving Work B" / "Claude 71% left this week · serving Max
  A" ("· serving the reserve account Max 20x", "· Max B on credits, $112 of $150", "every account is out", "the pool
  is down", "not reporting", "no accounts yet"). VoiceOver reads "Codex 54% left, Claude 45% left, reserve".
- Click: the app hit-tests the click's x against the middle of the gap between the halves; the left half opens the
  popover on Codex, the right half on Claude. With the popover open, a click on the other half switches to it and a
  click on the same half closes it. Keyboard or VoiceOver activation (no mouse event) opens the pool in the worse
  state: down or not reporting > all out > spending credits > reserve > regular > empty (Codex on a tie).


### The switcher and the Claude tab (Claude pool installed)
- **Switcher**, in place of the header: two tiles, 149 pt each with a 10 pt gap, 66 pt tall, 10 pt radius. Each: the
  pool's glyph (or the warning triangle) and name (12.5 pt semibold), the state at the right (10.5 pt medium:
  `Regular` in secondary, `Reserve` / `All out` / `Credits` in red, `Down` / `Stale` grey); the number (21 pt
  semibold, monospaced, coloured like that half of the menu bar) with "left" / "used"; a 4 pt bar (the headline's
  colours). Without a number (no accounts, no data) the state takes the number's place. The selected tile has a quiet
  fill and a 1 pt hairline in its pool's colour, the other a separator hairline; hovering it highlights it, clicking
  it switches. Its tooltip is that pool's tooltip line. The subtitle under the tiles ("Updated just now · Serving Max
  A") follows the selected tile; the sections below are the selected pool's, laid out as above. Banners name the pool
  ("Codex pool is down").
- **The Claude tab** reads `claude-status.json`: status.json's shape (the Interface contract in the build spec), with
  per seat `five_hour{used,reset_at}` (shown like a Codex seat's 5-hour window), `week`, `scoped[{name,used,
  reset_at}]` (the Fable weekly cap) and `credits{enabled,used,limit,policy,cap}` (`policy` `off` | `last-resort`;
  amounts in dollars; optional `currency`, default USD; optional `spending`; optional `mismatch`: the guard found
  usage credits on at claude.ai for an account whose policy is off, which only turning them off there fixes, since
  a proxy can't stop every paid request; a file without it has none to warn about), and in `pool`: `installed`,
  `route` (`pool` | `direct`), `claude_code` (the Claude Code version) and `desktop` (the pooled desktop app, the
  block in docs/DESKTOP.md: `configured_mode`, `chooser_disabled`, `applied_name`, `running_mode`, `running_host`,
  `app_running`, `app_started_at`, `app_bundle_ok`, `restart_required`, `owned_drift`, `current`, `port_ok`,
  `credits_ok`, `credits_problems`, `accepted_credits`, `txn_pending`, `txn_classes`, `pool_seen_desktop_at`,
  `app_version`, `base_url`, `old_3p_logs`, `checked_at`, `last_backup`, `errors`, and `owned.claudeAiImport.enabled`;
  `parse_desktop` never raises on odd values). It says "accounts" where Codex says "seats".
  - Banners: down ("Claude pool is down": running sessions can't reach it, new claude-pool sessions start direct;
    Run Doctor), not reporting (Run Doctor), no accounts (Add Account…), and, while an account spends credits as the
    last resort, an orange "Spending usage credits" with the account and "$112 of its $150 cap" (no button).
  - Hero: as for Codex ("2 of 3 regular accounts ready · reserve 80% left"), plus the serving account's "5-hour
    window 38% left on Max A · resets in 2h 10m" and one line per scoped cap, "Fable weekly 64% left on Max A".
  - Accounts, in fill order: plan badges from the plan string and the weight (`Pro 1×`, `Max 5×`, `Max 20×`,
    `Team 5×`); bars for the week (5 pt), the 5-hour window (3 pt) and each scoped cap (3 pt); line 3 "Week 45% · 5h
    38% · Fable 64% left" (in `left` mode one "left" at the end, dropped when the line would not fit), the limit that
    binds in the label colour, and on the right the binding limit's reset ("5h resets in 2h 9m", "Resets in 5d 6h")
    or "Back in 1h 11m". States as for Codex (`Serving` is red for the reserve and for an account on credits). Extra
    lines, each with a small symbol: why a parked account waits (orange ⏸, the guard's `detail`), "Anthropic ended
    this sign-in" (Re-login soon), and the credits: "Credits: last resort · $31 of $150 cap" ("Credits: last resort,
    cap $150" before any spend, "… · off at claude.ai" while claude.ai has its credits off, "Credits · $31 of $150
    cap" under a parked line that already says last resort), "Spending credits · $112 of $150 cap" (orange, filled
    card), or "Credits on at claude.ai · not used by the pool" for an account whose credits are on at claude.ai with
    policy off; the tooltip explains the policy. When the guard flags that as `credits.mismatch`, the line is the
    warning "Turn credits off at claude.ai (Settings → Usage)" instead (orange ⚠ with grey text, like Re-login
    soon; under a parked account's ⏸ line too), and the row's tooltip is the sentence in full: "Usage credits are
    on at claude.ai for Max 20x: turn them off there (Settings → Usage). codexpool can't stop every paid request."
    It changes nothing in the menu bar, the tiles or the headline: their red and their warning triangle say what the
    pool is doing now (the reserve serving, credits being spent, a pool down or not reporting), and a standing
    account setting that stays wrong for days would drown those; the row, Settings and Doctor (an error there)
    carry it. Settings' account rows use the same words (`credits_line`); the Seats pane's Usage credits row has
    the sentence in full, with the month's spend.
  - An account's menu: Re-login… (`codexpool claude login LABEL --no-open --priority N` in Terminal), Enable (spends
    credits)… for an account parked with credits off (asks first; `codexpool claude enable` overrides that park
    until the limit resets), Enable / Disable, Make first (`codexpool claude priority SEAT N`, same rules), and Open
    claude.ai usage page (the browser's own account). No resets. An account parked by its last-resort policy gets no
    Enable, since the CLI refuses to override that park: in its place a disabled line,
    **Serves once every other account is out**, whose tooltip says why (`last_resort_tip`: codexpool brings it back
    once every other account's plan quota is spent, the reserve's included; change its credit policy in
    Settings → Balancing). Settings' In rotation switch for it is off and disabled, with the same reason as the row's
    subtitle (`rotation_row`).
  - Footer: Status… (`codexpool claude status --live`), Doctor (`codexpool doctor`, which covers both pools), Pool
    log (`codexpool claude logs -f`), Docs (`~/.codexpool/addons/sienna/docs/SIENNA.md`, else on GitHub), Refresh, then **Claude
    Code route** with a two-segment control, Pool | Direct (`pool.route`). Choosing the other asks first ("Send new
    Claude Code sessions direct?"), then runs `codexpool claude route pool|direct`; running sessions stay where they
    are. Then Add a Claude account… (the Setup assistant's Add accounts step), Settings…, Quit, and "CLIProxyAPI
    7.3.18 · Claude Code 2.1.283". With the Claude pool installed, Settings… and Add a … account… start the Settings
    window with `--pool` set to the tab's pool (`--pane setup-accounts --pool claude` from the Claude tab), so it
    opens on that pool's side whichever side it showed last.
  - **Desktop** (`pool.desktop`, the pooled Claude desktop app; docs/DESKTOP.md, DESKTOP_3P.md §7): under the route,
    a second two-segment control, **Desktop · Pooled | Claude.ai** (`desktop_row`), drawn only with the block and
    the app on this Mac (`desktop_shown`: not with an older guard's file, nor with the backend's "Claude app not
    found"). The selected segment is `configured_mode` (the files); the 11 pt caption under it, word-wrapped, is
    the truth about the running app, from `running_mode` (the app's own log, never the files), per `desktop_view`:
    pooled + running `pooled` "On the pool · Chat, Cowork and Code · history in Claude-3p" (+ " · answering" with
    `pool_seen_desktop_at`); `3p` "In pooled mode · the pool's address is confirmed on the first request"; `other`
    "Running on another address, not the pool" (Run Doctor); `fallback` "Claude fell back to standard mode" (Run
    Doctor); `restart_required` "Configured for the pool · reopen Claude to switch" (Reopen Claude…); `unknown`
    "Configured for the pool · nothing seen from the app yet"; not running "Configured for the pool · opens on the
    pool next time". claudeai: "On its own claude.ai account" only with running `claudeai`; "Configured for
    Claude.ai · reopen Claude to switch" (Reopen Claude…); "… · nothing seen from the app yet"; not running
    "Configured for Claude.ai"; a running process still in pooled mode: "Configured for Claude.ai · the running app
    is still in pooled mode" (Reopen Claude…). `other`: neither segment, "Another configuration is applied in the
    app", with " · it hides the Claude.ai sign-in" and Run Doctor when `chooser_disabled`. `none`: Claude.ai
    selected, "Not set up" and **Set Up…** (the Pooled confirm). `unknown`: greyed, "Can't read the app's
    settings", Run Doctor. `txn_pending`: greyed, "A change was interrupted", Run Doctor. `app_bundle_ok` false:
    "A process named Claude runs from another path", Run Doctor. The action is a link at the right end of the
    caption's last line when it fits, else on its own line. Under a pooled desktop, extra lines with a small
    symbol (13 pt line): orange ⚠ "Credits on at claude.ai for Max B · the pooled app can spend them"
    (`credits_ok` false, the accounts from `credits_problems`), "No fresh credits reading for Pro C · wait for the
    guard's next poll", "No account can serve the desktop app"; grey ⓘ "Paid use accepted on Max 20x, up to $150
    · the pooled app can spend credits" (`accepted_credits`), "The "Pool" configuration was edited in the app"
    (`owned_drift`), "… points at another port" (`port_ok`), "… is out of date" (`current`); these three add Run
    Doctor. Segment tooltips say what each mode is.
    Choosing the other segment (or Set Up…) asks first (`desktop_confirm`): "Switch the desktop app to the pool?"
    / "Go back to Claude.ai?" with what moves and what stays, [Switch] / [Go Back] [Cancel]; Reopen Claude…: "Reopen
    Claude now?" [Reopen]. Then `codexpool claude desktop pooled|claudeai --relaunch --yes` (`relaunch --yes` for
    the reopen) runs in the background (`desktop_command`: never `--reclaim` or a credits override); the control is
    greyed with the caption "Switching Claude to the pool…" / "Sending Claude back to Claude.ai…" / "Reopening
    Claude…" (`desktop_busy`), then the command's closing line, its last stdout line ("Claude opened on the pool",
    "Claude opened; could not confirm the mode yet …"), is the subtitle for 8 s, or its ✗ line on failure
    (`desktop_closing`); a guard pass follows a success. What codexpool would refuse is said first
    (`desktop_refusal`, an alert with Run Doctor / OK, before Claude is quit for nothing): an interrupted change
    (`rollback`), credits on at claude.ai or no fresh reading (the backend's lines, one per account), an edited
    "Pool" entry (`--reclaim` is a terminal choice), the Claude pool down (for Pooled), the applied entry hiding
    the Claude.ai sign-in (for Claude.ai). `credits_ok` null (unknown) is left to the command.
  - The down banner, when the desktop is pooled: "Claude pool is down · The desktop app is pooled and can't answer
    until the pool is back; running Claude Code sessions can't reach it either." with **Run Doctor** and **Back to
    Claude.ai** (the confirm above; a banner may carry a row of buttons). The Claude half of the menu bar item's
    tooltip and the tiles' tooltips gain " · desktop pooled" (pooled, no restart pending) or " · desktop switch
    pending" (`restart_required`), and `menubar_signature` carries it so the tooltip refreshes.

## Verification mode

`--pool-status` installs the Claude pool (`addons/sienna/menubar_ext.py`) for the snapshot (both numbers in the strip, the
switcher in the popover) and `--pool claude` picks the tab; `--pool-history` defaults to the `claude-history-*.jsonl` named
like it. Without `--pool-status` a snapshot never reads the live Claude file.

Claude fixtures come from `docs/images/demo/make_claude_data.py` (made-up accounts Max A, Max B, Pro C and the Max 20x
reserve): `claude-status-{regular,reserve,lastresort,down,direct,empty}.json` and
`claude-history-{regular,reserve,lastresort}.jsonl` (`direct` is the regular scenario with `pool.route` set to direct; give
it `--pool-history docs/images/demo/claude-history-regular.jsonl`). `--pane overview-desktop` snapshots the Claude Overview
with the Desktop row's "What changes" open.

## The Lanes pane: the Claude engine

The engine provider `sienna` (`kind` `engine`, `needs` `engine`; ENGINE-LANE.md) is titled **Claude**: its members read `Claude · claude-opus-5-5 · read-only` (`member_line`), their pill is the engine's state (`LANE_STATE`: `engine ok` Engine OK, `untested engine` Not accepted, `no engine`, `no launcher`, `pool down`, `no profile`; the member row's tooltip is the fix, `ENGINE_HINT`), and its Credentials row says "Not accepted yet · read-only, through the Claude pool · used by sienna" (`engine_state_text`; `infer_ready` reads `engine ok` from the members when `lane providers` is missing). Credentials: for Claude **Accept Engine…** (**Accept Again…** once accepted; enabled only while a lane has a Claude member): a confirm (one read-only probe turn through the Claude pool, one request spent, redo after a Claude Code update), then `codexpool lane apply --accept-engine` streams into the sheet, and providers and lanes are re-read.

**Add Model** on the Claude engine, which has no catalog: "Type a Claude model id the pool serves, e.g. claude-opus-5-5."; typing an id always works. Claude says "Claude Code, read-only, through the Claude pool. Accept the engine once the lane is saved." and, while not accepted, a box saying that Credentials → Accept Engine… does it after Save (accepting needs a lane with the member).
