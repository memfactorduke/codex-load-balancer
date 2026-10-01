# Claude CLI: cswap integration

Read the core AGENTS.md and this add-on's README before changes.

The supported Claude product is a native UI over upstream cswap. It manages Claude
Code CLI logins only. Codex Desktop/CLI remains a separate product and backend.

## Active implementation

- `cswap_backend.py`: pinned upstream dependency, public JSON projection and CLI commands.
- `cswap_ui.py`: file-only native UI; commands execute in background workers.
- `cswap_patch.py`: checked source extension to cswap's own profile and public JSON
  code. cswap alone accesses its credentials; only public subscription fields leave
  its process. Keep exact source validation, identity checks, caching and unknown sizes.
- `addon.py`: registers the Claude CLI family and a separate guard lock.
- `menubar_ext.py`: reusable drawing primitives plus the active UI loader. Its old
  proxy/desktop functions are not the supported UI.

Delegate credential operations, refresh, account choice, cooldowns and switching to
cswap. Never read or copy Claude credentials in this integration, never import the
old proxy's tokens into cswap, and never put Claude behind the Codex pool. Validate
schema versions and exact numeric account IDs on switch responses. Unknown usage
stays unknown. The headline describes the selected account, not a pool average.

Automatic switching starts off. Only an explicit `claude auto on` enables it; avoid
running another cswap auto-switcher alongside it. Commands and status refreshes share
a Claude-only lock. No Claude desktop controls or Claude Codex-lane provider may be
registered.

## Validation

Run `python3 -m unittest discover -s addons/sienna/tests` and the core suite. Render
changed native panes in both appearances using synthetic `docs/images/demo/claude-cli-status.json`.
Do not click live UI buttons to test. Do not install, import accounts or switch the
owner's live login without authorization.

The code in the remaining legacy modules exists for explicit retirement of an old
installation and historical reference. `docs/LEGACY-AGENTS.md` and `legacy_tests/`
describe that retired product, not the current contract. Do not use their tests as
proof that cswap integration works. Old proxy credentials remain untouched during
retirement. A real account-switch test is a separate, owner-authorized acceptance
step; sandbox tests are not evidence of a successful live switch.
