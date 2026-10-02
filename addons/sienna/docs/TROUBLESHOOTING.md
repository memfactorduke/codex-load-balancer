# Troubleshooting the Claude pool

Part of the sienna add-on. The core's `docs/TROUBLESHOOTING.md` covers the Codex pool, seats, the menu bar,
install and lanes.

For the [Claude pool](SIENNA.md). `subpool doctor` has three Claude sections once it is installed (Claude pool,
Claude accounts, Claude Code) and a fourth for its recent errors. Its log is `~/.subpool/claude/logs/main.log`
(`subpool claude logs -f`); the guard's Claude decisions are in `guard.log` as `guard: claude: …` lines.

**Do this from a direct session.** If Claude Code is what you are using to fix the Claude pool, start it with
`CLAUDEPOOL=off claude` (or plain `claude` without the shim) first: a restart or rebuild would otherwise cut it off.

### Claude Code doesn't go through the pool

In Claude Code, `/status` shows the base URL when it does. If it doesn't:

- You started plain `claude` without the shim. Use `claude-pool`, or `subpool claude shim install` and its `PATH`
  line.
- The route is direct: `subpool claude route` prints it; `subpool claude route pool` sends new sessions through
  the pool again.
- `CLAUDEPOOL=off` is set in your environment.
- The launcher printed `claude-pool: the Claude pool does not answer on 127.0.0.1:8321 …`: see the next entry.

A session keeps the route it started with. `/exit` and `claude-pool --resume` moves one onto the pool.

### "the Claude pool does not answer on 127.0.0.1:8321"

The launcher gave the pool 300 ms and started Claude Code direct. `subpool claude status` and `subpool doctor`
say whether the Claude pool's launchd job runs; `subpool claude logs -n 50` shows why it stopped.
`subpool claude install` puts back whatever is missing.

### "Claude Code (claude) is not in ~/.local/bin or on PATH"

The launcher found no Claude Code to start. Install it (its installer puts it in `~/.local/bin/claude`). The
launcher never starts itself or the shim.

### Connectors, artifacts or Claude in Chrome are missing in a pooled session

They need Claude Code's claude.ai login, which the pool leaves alone. In `/status`, check that the login is your
claude.ai account and not an API key or token, and that none of `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` or an
`apiKeyHelper` is set, in your shell or in `~/.claude/settings.json`: any of them replaces the login with a gateway
credential. subpool never sets them. **Remote Control** is different: Claude Code turns it off for any base URL
that isn't Anthropic's, so use a direct session for it.

### doctor: "ANTHROPIC_… set in launchd's environment"

Something ran `launchctl setenv` for that variable, so every app started from the Dock or Finder gets it (the
Claude desktop app included). Remove it with the `launchctl unsetenv …` line doctor prints. subpool sets
`ANTHROPIC_BASE_URL` only for the process `claude-pool` starts.

### doctor: "state/claude-status.json not written yet"

Right after `subpool claude install`, that is expected: the guard's next pass (within a minute) writes it. If it
stays missing, `subpool logs --guard` shows why the Claude pass fails.

### doctor: "usage polling is passive"

Anthropic refused the pool's usage call. The guard now reads the rate-limit headers of each account's last served
request instead: the serving account is current, idle accounts are as of their last request, and credit amounts are
unknown, so a `last-resort` account spends nothing. The guard tries the call again every 6 hours. A CLIProxyAPI
upgrade of the Claude pool may help (set `claude_cpa`, then `subpool claude install`).

### A Claude account is Parked

The guard took it out of rotation, and its row says why:

- **credits off: parked at its plan limit.** It reached a 5-hour, weekly or scoped limit, and its next requests would
  spend usage credits. It comes back at its reset.
- **last resort: waits until every account is out.** Its policy is `last-resort`, and another account can still
  serve.
- **last resort: no current credit reading, so no spending.** The guard can't see its credits right now (passive
  polling, or a stale reading), so it doesn't let it spend.
- **credit cap reached.** It spent its cap this month. `subpool claude credits SEAT last-resort --cap USD` raises it.
- **billed to credits below its limit.** See the next entry.

`subpool claude enable SEAT` overrides a credits-off policy park for its remaining duration; the account may
spend credits during that override. It refuses to override a last-resort policy park.

### "Claude account billed to credits below its limit"

The account's used credits went up while its plan windows were below their limits, and its credit policy is
`off`. The guard parked it for an hour (longer if it happens again within a day). The usual cause is fast mode
(`/fast`), which bills usage credits even with plan quota left: turn it off, or let that account pay for it with
`subpool claude credits SEAT last-resort --cap USD`. If you were not using fast mode, some of its requests were
billed to credits instead of the plan: look at the account's usage at claude.ai (Settings → Usage) and at the
Claude pool's log around that time before you enable it again, and if it keeps happening, turn credits off for that
account at claude.ai.

A `last-resort` account is not parked for this: it may spend credits, so you get a "Claude account spending usage
credits" notice instead, and the guard parks it at its cap.

### All Claude accounts are out

Claude Code shows its usual limit message once every account is at a limit; the menu bar says which one comes back
first. A `last-resort` account serves on credits only if its credits are on at claude.ai and it has a cap left. For
the time being, `CLAUDEPOOL=off claude` works on Claude Code's own account, if it isn't one of the pooled accounts
at its limit.

### A Claude account says Blocked or "Anthropic ended this sign-in"

Its login in the pool stopped working (Anthropic ended the sign-in, or a refresh failed). Sign it in again with the
same label, which keeps its place and settings:

```sh
subpool claude login "<Label>" --no-open
```

Open the link where you are signed in to that Claude account. It doesn't touch Claude Code's own login.

### The Claude sign-in opened in the wrong account

Anthropic's sign-in page uses whichever claude.ai account the browser is signed in to. Use `--no-open` and open the
link in a browser profile or private window signed in to the account you want. The success line names the account;
if it is the wrong one, `subpool claude remove SEAT --yes` takes it out again; then sign in the right one.

### doctor: "N thinking-signature rejection(s)"

After an account change, Anthropic refused a thinking block from an earlier turn. Claude Code retried without the
earlier thinking, so the conversation went on without its earlier reasoning. Check that pair of accounts with
`subpool claude selftest A B` (with a little quota to spare), and move the Claude pool to a newer CLIProxyAPI
release if it keeps happening.

### doctor: "N requests from other clients or browsers blocked by the gate"

Something on this Mac that isn't Claude Code tried the Claude pool's port (a script, another tool pointed at 8321, or
a web page), and the gate refused it. `grep "codexpool gate" ~/.subpool/claude/logs/main.log` shows what.

### "claude install stopped: the Claude pool's gate does not keep other clients out"

The build behind `bin/claude-current` doesn't enforce the claude profile, so install stopped the pool again. Remove
the link and let install build it from the current gate: `rm ~/.subpool/bin/claude-current`, then
`subpool claude install`.

### "claude install stopped: … has no claude gate profile"

`~/.subpool/build/codexpool_gate.go` is from before 1.3.0. Update subpool first (the one-line installer, or
`./bin/subpool install` from a 1.3.0 or later checkout), then run `subpool claude install` again.

### "claude install stopped: cannot ask GitHub for the newest CLIProxyAPI release"

The first build needs to know the newest release. Retry when GitHub answers, or set `claude_cpa` in
`~/.subpool/settings.json` to a version (7.3.18 or later) and run install again.

### "claude_port (must differ from port and bridge_port)"

`claude_port` in `settings.json` is the same port as the Codex pool or the lane bridge. Pick another, or remove the
key: left out, it takes 8321 or, if that is taken by those, the next free port.

### `/usage` in Claude Code doesn't match the menu bar

`/usage` shows the plan of Claude Code's own login and counts pooled use as if it were that account's. The menu bar
and `subpool claude status` show the pool.
