# Claude CLI — powered by cswap

Claude CLI is a native account-management UI over
[upstream claude-swap (`cswap`)](https://github.com/realiti4/claude-swap).
It is separate from **Codex Desktop/CLI**. Claude Code talks directly to Anthropic;
there is no Claude proxy, desktop integration or Claude-as-a-Codex-lane feature.

The native interface shows each account's five-hour and weekly limits, the selected
login, Switch and Exclude/Enable buttons, and automatic-switching controls. Its
headline is the selected account's weekly allowance. Unknown readings stay unknown.

## How it works

The command adapter runs cswap's public commands. `list --json` supplies account
metadata and usage. `switch SLOT --json` changes the selected login, and the adapter
checks that cswap confirms the exact requested slot. `add`, `enable`, `disable` and
`alias` are delegated to cswap as well. The UI reads only the adapter's status file.
Credential storage, refresh and locking belong to cswap, not this UI.

Automatic switching is off initially. When enabled, the guard runs `cswap auto
--once --json` each minute; cswap owns quota thresholds, cooldowns, hysteresis and
account selection. Choose **Most quota left** or **Reset soonest**. Do not run
cswap's own auto loop or menu-bar auto-switcher at the same time.

On macOS, an existing Claude Code process can retain a cached login. Reopen Claude
Code to apply a switch immediately. The UI confirms cswap's selected login, not
that every already-running process has adopted it. API-key accounts are excluded
from upstream automatic rotation by default; the adapter does not enable them.

## Setup, when authorized

```sh
codexpool claude install --dry-run
codexpool claude install
# Sign in through Claude Code, then save its current login:
codexpool claude add
codexpool claude status --live
codexpool claude switch 2
codexpool claude auto on
```

`install` uses uv and Python 3.13 to install upstream source pinned at revision
`3a4e5c14873eb5b32f182d55c68da98ac8c0db45` (upstream package `0.27.0b1`).
An existing cswap is reused, not silently upgraded. Public JSON schema version 1
is required. No third-party source is vendored or relicensed here; upstream is MIT.

The CLI also offers `auto off`, `strategy best|consume-first`, `threshold PERCENT`,
`enable SLOT`, `disable SLOT` and `label SLOT NAME`. Run plain `claude` to work.

## Existing proxy installations

There is no automatic credential migration. First preview retirement with
`codexpool claude retire-proxy`; only `--yes` stops the old proxy and removes its
launcher. It retains old account data. Restart pooled CLI sessions with plain
`claude`, then set up cswap accounts through normal Claude sign-ins. Installation
refuses while the legacy proxy is still installed. Its existing protective guard
continues until explicit retirement, so copying new code does not remove credit
protection from a running old proxy.

This source change does not modify a live install. Sandbox tests and native
snapshots validate the adapter and UI; a live account-switch acceptance test still
requires the owner's permission.

Historical proxy/desktop/lane documentation and tests remain for reference under
`docs/` and `legacy_tests/`. They are not supported features of this product.
