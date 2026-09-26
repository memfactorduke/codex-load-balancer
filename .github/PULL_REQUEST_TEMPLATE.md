## What this changes

<!-- What changes for someone using codexpool, and why. Link the issue if there is one. -->

## Checklist

- [ ] I read the [invariants in AGENTS.md](https://github.com/memfactorduke/codex-load-balancer/blob/main/AGENTS.md#invariants-never-break-these) and this change keeps all of them, or the description says which one it changes and why.
- [ ] Python compiles: `python3 -m py_compile bin/codexpool lanes/bridge.py menubar/*.py`, and `bin/codexpool` and `lanes/bridge.py` still parse as Python 3.9.
- [ ] `bash -n install.sh` passes, and `bash install.sh --dry-run` still works (if `install.sh` changed).
- [ ] Tests pass (`python3 -m unittest discover -s tests`), and I added tests where it made sense.
- [ ] Where it applies: `./bin/codexpool install` on a real install, then `codexpool doctor` ends with `OK`.
- [ ] Both pre-commit scans from AGENTS.md print nothing (no tokens, hashes, home-directory paths or emails).
- [ ] No personal data: no emails, account ids, real seat names, company names, `/Users/<name>` paths or personal launchd labels.
- [ ] For UI changes: screenshots in light and dark, rendered from `docs/images/demo/`, attached below.
- [ ] Docs updated where needed: README, settings table and `examples/settings.json` for a new setting, AGENTS.md, CHANGELOG.md under "Unreleased".

## Screenshots

<!-- For UI changes: before and after, light and dark. -->

## How I tested it

<!-- Commands you ran and what you saw. -->
