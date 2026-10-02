# Security policy

subpool holds working ChatGPT logins for every seat you add, so security reports get priority.

## Report a vulnerability privately

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/memfactorduke/codex-load-balancer/security/advisories/new)**
(the repository's **Security** tab, then **Report a vulnerability**).

If that page shows no report form, open a
[security contact request](https://github.com/memfactorduke/codex-load-balancer/issues/new?template=security_contact.yml):
a public issue that says only that you have something to report, with no details. The maintainer replies there
with a private way to send it.

Please don't put any detail of a security problem in a public issue, pull request or discussion. In the
private report, include:

- what an attacker can do, and from where (a web page, another program running as you, another user on the
  same Mac, the local network);
- steps to reproduce, and the subpool version (`subpool version`, or the commit) and CLIProxyAPI build
  (`subpool doctor` shows it);
- your macOS version.

Leave real tokens, emails, account ids and seat names out of the report: redact them, or reproduce with a
throwaway account.

The maintainer aims to acknowledge a report within 7 days, agree on a fix and a disclosure date with you, and
credit you in the advisory unless you would rather not be named.

## Supported versions

Security fixes go into the latest release. Upgrade by running the installer again.

## Threat model in brief

The full version is in the README's [Security model](README.md#security-model).

- **Loopback only.** The pool (CLIProxyAPI) listens on `127.0.0.1`. Remote management and its web control
  panel are off. Nothing on the network can reach it.
- **An origin gate.** The pool accepts local requests without a client key, because the Codex app can only send
  its own ChatGPT login. The gate, compiled into the pool, refuses any request whose `Host` is not a loopback
  name (DNS rebinding) or that carries browser provenance (an `Origin` other than the Codex app's own, or a
  `Sec-Fetch-Site` other than `none`), so web pages can't use the pool. It runs before CLIProxyAPI's logging,
  CORS, auth and routes, WebSocket upgrades included. Every build passes an 11-case gate self-test before it is
  used, and `subpool doctor` probes the running gate.
- **Seat tokens stay put.** Each seat's login lives in `~/.subpool/auth/` (directory mode 700, files mode
  0600), and only the pool reads its tokens and refreshes them. subpool never copies, prints or sends a token:
  usage and reset calls go through the pool, which inserts the token itself. Lane provider keys live in
  `~/.subpool/lanes/secrets/` (mode 700) and are never printed either.
- **The management key is in the Keychain**, as `codexpool-management-key`. It is written with `security -i`
  so it never appears in the process list, and subpool's calls to the pool bypass any HTTP proxy. The menu
  bar app, the Settings window and the Setup assistant never touch the Keychain.
- **No telemetry.** Model traffic and usage and reset calls go to chatgpt.com through the pool, and
  `subpool login` signs in with OpenAI. Install and upgrades download from go.dev, GitHub, the Go module proxy
  (proxy.golang.org, checked against sum.golang.org) and PyPI (PyObjC), plus astral.sh only when `install.sh`
  installs uv. Lane subagents, if you set lanes up, talk to the providers you chose. Nothing else leaves your
  Mac.
- **Stock upstream.** The pool is built from CLIProxyAPI's release source plus the gate file and the one-line
  hook in `cmd/server/main.go` that installs it, and nothing else sits in the seat request path.

What subpool trusts: any non-browser program running as you can send model requests through the pool, just
as it could read `~/.codex/auth.json`. Other user accounts on the same Mac can reach loopback too.

## In scope

- Getting around the origin gate, or reaching the pool from a web page, another machine or a DNS rebinding
  attack.
- Anything that exposes a seat token, the management key or a lane key: in output, logs, files with loose
  permissions, process arguments or crash reports.
- Anything that makes `install.sh`, `subpool install` or `subpool upgrade` run code other than what they
  say they fetch, or that weakens those downloads.
- The menu bar app, the Settings window or the Setup assistant running a command the user did not ask for.
- Anything that makes `subpool reset` do more than redeem a banked reset the account already holds.

## Out of scope

- Bugs in CLIProxyAPI itself: report them [upstream](https://github.com/router-for-me/CLIProxyAPI). Tell us
  too if subpool's configuration makes one reachable or worse.
- Problems with OpenAI's services or the Codex app.
- Attacks that need code already running as your user, which the model above trusts.
- Whether pooling seats is allowed by the terms that apply to your accounts: that is not a security question.
