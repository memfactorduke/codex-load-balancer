# Local add-on interface (steps 1–2)

Step 1 adds discovery and extension points. Existing pool code, runtime paths, settings keys, gate profiles,
launchers and app code remain in place. There is no state migration. With no add-ons, existing help and version
output remain unchanged. The management commands are intentionally absent from root help during this step;
`codexpool addon --help` describes them.

## Bootstrap and manifest

An add-on is a trusted local Python package with `addon.json` and `addon.py`. It must use the standard library,
Python 3.9 syntax, and perform no external I/O at import or in `load(cp)`. Fault isolation catches ordinary
exceptions and `SystemExit`; it is not a security sandbox and does not stop hostile code or an infinite loop.

Discovery scans `CODE_DIR/addons/<id>/` before `ROOT/addons/<id>/`. IDs match `[a-z][a-z0-9]{1,15}`. The checkout
wins even when broken: an installed copy must not silently mask a bad checkout. Loaded add-ons are ordered by ID.

`addon.json` is read before settings, without executing Python:

```json
{
  "id": "example",
  "version": "1.0.0",
  "core_min": "1.3.0",
  "title": "Example pool",
  "settings_prefixes": ["example_", "legacyexample_"],
  "settings": {"example_mode": "off"},
  "settable": {"example_mode": ["off", "on"]},
  "code_files": ["addon.py", "pool.py"]
}
```

`id`, `version`, and `core_min` are required. Versions use `major.minor.patch`. Other fields default to the ID
namespace, empty settings/choices, and `code_files: ["addon.py"]`. Every setting belongs to an explicitly
reserved prefix; prefixes can retain old settings names without renaming state. Settings cannot replace core
settings, and namespaces cannot replace another add-on's reservation. `code_files` names relative files or
directories; every declared file must exist. Nested symlinks, parent traversal and absolute paths are refused.
`addon.json` is always copied. Undeclared files and bytecode are not installed.
The optional `auto_ports` list names declared port settings whose omitted default advances to the next unused
port. Explicitly supplied values are never changed. It is evaluated before executable loading, so path/port
constants see the final defaults. Without this field, port collisions remain validation errors.

The durable namespace registry is `state/addons.json`: an object mapping IDs to prefix lists. Installation
records reservations before publishing code. Removal retains them. Missing/incompatible/broken add-ons' reserved
settings are ignored with a diagnostic, while unknown unreserved keys still fail as before. A healthy add-on's
unknown settings still fail. Discovery does not write the registry. A never-installed, absent add-on without a
reservation cannot claim settings: repair its manifest/namespace reservation before using those keys.

Executable initialization runs at the end of the CLI module, after settings, pool types, functions and parsers
exist. `addon.py` exports `load(cp) -> object`; `cp` is the complete core module. Relative package imports work.
The returned object's `id`, `version`, and `core_min` must match the manifest. If it also exposes `settings`,
`settable`, or `code_files`, they must match the manifest. Metadata has one authoritative source.

Within one CLI process, `load_addon(path, manifest)` reuses the same `LoadedAddon` and package modules when
the resolved path, manifest contents, and SHA-256 hashes of every declared payload file are unchanged.
Missing files still fail validation before a cache hit. Changes, or `reload=True`, load fresh source (including
relative imports, without trusting timestamp-based bytecode caches). A failed import restores the old modules.
Replacing an active add-on updates `ADDONS`, rebuilds lane providers and GUI/gate registrations, and refreshes
existing root parser trees. Guard and bridge-extension hooks read the current `ADDONS` on each invocation.
Already parsed command namespaces are snapshots; parse again after a reload.

`prepare_addon()` explicitly reloads install/remove validation code in a separate package namespace. This
lets validation inspect another copy without replacing active modules, registrations, or lazy relative imports.

## State and failures

| Condition | Behavior |
|---|---|
| No directories or reservations | `ADDONS == []`; no add-on output or hooks |
| Reserved namespace, code absent | Settings remain reserved; warning in `ADDON_ERRORS`; repair/install works |
| Incompatible core minimum | No Python executes; settings remain reserved; warning |
| Invalid manifest, partial files, failed import/load | Add-on unavailable; any established namespace stays reserved |
| Failed settings validator | Add-on disabled; core settings and commands remain available |
| Failed runtime hook | That hook is disabled for this process; other hooks and the Codex core continue |
| Unknown explicit pool ID | `ValueError`; never resolves to the Codex pool |

`ADDONS` holds `LoadedAddon` objects. `ADDON_ERRORS` maps ID to diagnostic text. `ADDON_MANIFESTS`,
`ADDON_PATHS`, and `ADDON_NAMESPACES` retain metadata independently of executable loading.
`ADDON_FAILED_HOOKS` records `(id, hook)` failures. `addon_call(addon, hook, *args, default=None,
transform=None)` isolates invocation and collection conversion; `addon_operation(addon, hook, operation,
default=None)` isolates multi-step contributions. User interrupts are not swallowed.

Parser contributions are built separately and committed only after successful completion and collision checks.
Status data must serialize to JSON and cannot replace a core field. Doctor sections are buffered. Guard hooks
are evaluated only after the existing core passes and must use their own pool/lock. Failures appear on stderr,
in guard logs, and as doctor warnings; diagnostics do not alter the core status JSON.

## Executable hooks

All hooks and optional attributes below may be omitted. Existing built-in pool behavior remains alongside them.

| Name/signature | Result or behavior |
|---|---|
| `settings_problems(settings)` | Iterable of errors; may derive only its declared settings on a private copy. Add-on ports/labels must be valid and distinct across pools. |
| `on_set(key, old, new)` | Text printed after saving an owned settable setting |
| `pools()` | Iterable of `cp.PoolInstance` descriptors for name lookup |
| `seat_pools()` | Mapping of names to `cp.SeatPool`; also supplies login directories |
| `add_parser(sub, cp)` | Add command parsers and callbacks; collisions with core commands or aliases are refused |
| `guard_passes()` | Iterable of `(name, callback, SeatPool)`; callback takes no arguments |
| `status(live)` | `None` or `(json_key, data, print_fn)`; `print_fn()` prints the text view |
| `doctor(report)` | Append sections/checks through `cp.DoctorReport` |
| `uninstall_plan()` | Iterable of `(description, callback)`; callbacks take no arguments |
| `keep_note()` | Text describing retained runtime files |
| `gate()` | Contribution with `profiles`, `sources`, `hash_inputs`, optional `cache_valid(dest)`, `pre_build(tree, go, env)` and `post_build(dest, version, stamp, result)` |
| `lane_providers()` | Provider mapping registered alongside built-ins; detailed provider callback dispatch belongs to the lane extraction step |
| `bridge_extension` | Absolute path emitted in bridge config only when its provider has routes; bridge loading/dispatch belongs to the bridge extraction step |
| `menubar_extension` | Optional path; adds the add-on's `ui_pool_ids` (default: its ID) to the CLI's `gui --pool` choices. The apps load the add-on's `menubar_ext.py` themselves (below) |
| `ui_pool_ids` | Optional tuple of the pool ids the add-on's `PoolUI` answers to (the `--pool` values); defaults to `(id,)` |
| `install_hint` | Text printed after installing code |

Gate `profiles` maps profile names to `cases(code, ws_code, auth, evil, rebind) -> [(name, got, want)]`.
Sources and hash inputs must exist within the add-on. Duplicate profiles/source filenames are refused. Every
source and input contributes to the build hash and `BUILD-INFO`. `cache_valid` can require add-on evidence before
a cached binary is reused; absent hooks accept the cache. The value returned by `pre_build` is passed to that
contribution's `post_build` after the binary passes its gate self-test. The latter writes add-on evidence before
the build can be selected. Hooks run through the same failure isolation as other gate operations. A gate failure
may not publish or reuse an incomplete build; it does not restart a running pool. The Go profile registration
mechanism is a later step.

Full lane-provider callback dispatch (including headers and member IDs), the generic bridge route/error contract,
normalized UI fields and public asset cleanup remain in their respective extraction steps.

## Pool descriptors (step 2)

`pool_instance(id)` and `seat_pool(id)` resolve built-in factories, then add-on contributions, and raise
`ValueError` for an unknown explicit ID (including an empty string). `seat_pool` also accepts an argparse or
simple namespace; only a missing `pool` attribute means the core pool. Factories construct fresh descriptors
so current paths, settings and callbacks are used. Construction does no I/O. Additional provider values and
callbacks now live in their add-on; runtime paths and user-facing output are unchanged.

`SeatPool` keeps its existing positional arguments. Optional keyword fields describe wording (`title`, `vendor`,
`noun_title`, `sizes_text`, `moves_text`, `reserve_tail`, `sign_in_ended_detail`, `sign_in_soon_detail`), state
paths (`history_file`, `selftest_journal`, `status_file`), the log matcher (`refresh_failed_line`), the balancing
log prefix (`log_tag`) and whether metadata commands immediately refresh status (`refresh_after_meta`). Defaults
retain the core pool's behavior; an additional pool supplies its own callbacks and paths.

| Callback | Contract |
|---|---|
| `claims_context()` | Once per `load_seats` call, return a context shared by its rows. Default: an empty dict. A provider can snapshot cached plans once, preserving one consistent listing. |
| `claims(path, name, context)` | Return non-secret identity/plan facts. `path` can be `None`; `name` is the management record's name or ID. |
| `default_weight(stored_value, claims)` | Return the row's weight, retaining the provider's override-validation rules. |
| `enrich_row(row, claims, record)` | Optional; mutate the assembled row using the complete original claims and raw management record. Neither input is filtered or copied. Do not put secrets into rows. |
| `login_flow(no_open, device)` | Return CLIProxyAPI's login flags. |
| `new_seat_fields()` | Return additional fresh metadata; a `None` value deletes an old field. |
| `enable_refusal(pool, seat, guard_entry)` | Optional; return a refusal string or `None`, before any enable/override mutation. |
| `on_remove(pool, seat)` | Run after CPA deletes the login; perform provider-specific metadata/guard cleanup and print the result. |
| `refresh_status()` | Refresh this pool's status; return whether it was written. |
| `seat_state(seat, guard, now, pool_only=False)` | Return `(state, detail, until)`; healing uses this callback. |
| `history_extra(status)` | Optional; return additional history-sample fields. |

Row enrichment preserves the distinction between unknown exclusions (`None`) and known empty exclusions (`[]`).
An additional pool can read manual exclusions from claims and model-specific quotas from the management
record. Those facts reach credit protection unchanged, including when CPA unregisters a disabled account's
models. Tests exercise manual exclusions, disabled-seat recovery and scoped versus unrelated-model overage
through the real loader and this callback.

`PoolInstance` also retains its positional arguments. Its keyword fields are `config_template` (path),
`config_default_port` (the template's port before rendering), `probe_cases(pool)` (returns gate probe triples),
`rebuild_hint`, `next_rebuild`, `build_subject`, `gate_title` and `gate_fix` (diagnostic text). Config rendering
and doctor use these fields, independent of the descriptor's name or gate profile. Core guard ownership is
reserved by `CORE_POOL_IDS` as well as its lock path; this is an identity restriction, not provider dispatch.

## Management and installation

- `codexpool addon list`: loaded versions and unavailable add-ons, with reasons.
- `codexpool addon install PATH`: validate metadata/code, stage declared files, retain namespace reservations,
  then replace `ROOT/addons/<id>/`; print `install_hint`. It installs code only.
- `codexpool addon remove ID`: load the installed copy and refuse if `uninstall_plan()` is nonempty or cannot be
  evaluated. Remove only code; preserve namespaces and runtime files.
- `codexpool version`: core version, followed by `+ <id> <version>` for loaded add-ons.
- `codexpool doctor`: additional sections/checks and an Add-ons summary when applicable.

Writers serialize through `state/addon-code.lock`. Discovery holds a shared read lock across both bootstrap
phases when the lock exists; read-only commands never create it. With add-ons present, `copy_code()` stages all
core/add-on files, publishes complete add-on directories and the CLI under that lock, and rolls back a failed
rename batch. The CLI is published last. If rollback itself fails, recovery files are retained and their path
is reported. This is exception recovery, not a crash/power-loss journal. No config, auth, build link or process
is part of code publication. Code/config coordination for the future bridge extraction still belongs to that step.

## Extraction and test suites (step 3)

The additional pool's lifecycle, guard, status, doctor and selftest code belongs to its add-on. The core's built-in
registries contain only its own pool. Desktop and engine consumers remain in place until their extraction steps;
their temporary dependency accessor fails explicitly when the supplying add-on is unavailable.

An add-on may expose `uninstall_title` to preserve its existing plan prefix; otherwise the prefix is
`add-on <id>`. Nonempty `keep_note()` results describe the retained runtime data after the core's keep line.

`tests/_helpers.py` merges each available `addons/*/tests/settings.json` fragment into the throwaway home's
settings before loading the CLI. A `"free-port"` value allocates another unused loopback port. Add-on test helpers
chain to that helper and reuse its fake home and stubs. Tests for still-core components that require an absent
add-on skip until those components are extracted; independent core checks continue to run.

Run `tests/run_all.sh` for the core suite and each present add-on suite in separate interpreters, with a combined
test count and failure status. `PYTHON=/path/to/python tests/run_all.sh` selects an interpreter.

## Menu bar and Settings (step 7)

The menu bar app and the Settings window keep the generic two-pool plumbing (a second `DataSource`, the strip with
both numbers, the switcher tiles, the pool switcher, the `pool` parameter on `build_model`/`parse_seat`, and
`--pool-status PATH` / `--pool-history PATH` for snapshots). Everything an add-on's pool says or does in them comes
from one object, its **PoolUI**: `addons/<id>/menubar_ext.py` exports `load(mb) -> PoolUI`, called by
`menubar/codexpool_menubar.py` once it is defined (PyObjC is up; `mb` is that module), and the Settings window calls
`PoolUI.settings_loaded(st)` with its own module. The apps scan `<code dir>/addons/*/menubar_ext.py` (the checkout,
or `~/.codexpool/addons/` once installed; `CODEXPOOL_ADDONS` overrides the directory). A load that raises is noted on
stderr and skipped: the app runs with the Codex pool alone, and with no add-on it shows the Codex-only UI (no
switcher, one number in the item). One add-on pool at most: the item has two halves.

The core reads these members by name (`mb.POOL_UI[id]`), never a literal of the add-on's:

| Member | Read by |
|---|---|
| `id`, `aliases`, `title`, `noun`, `account_title`, `status_file`, `history_file`, `docs_path`, `docs_url` | `POOLS`, `--pool`, `DataSource`, `Model.name/noun`, Docs and the sign-in flow |
| `colors` (text light/dark, fill light/dark), `mark` (bundle id, icon files), `mark_scale`, `mark_px`, `mark_alpha(rgba, w, h, filename)`, `draw_glyph(path, cx, cy, size, weight)`, `draw_settings_glyph(path, s)` | `C.pool_text/pool_fill`, `MARK_APPS`/`MARK_SCALE`/`build_mark`, `draw_pool_glyph`, the Settings switcher |
| `scope_words`, `plan_names`, `sign_in_ended_text`, `sign_in_site`, `alarm_word`, `history_alarm_key`, `week_key`, `short_key`, `chart_area`, `compact_usage`, `command_prefix`, `add_account_title`, `switcher_blurb`, `serving_tip`, `session_word` | `Model.scope`, `parse_seat`, `load_history`, the pill, the rows, the footer, every `codexpool <prefix> …` |
| `installed(raw, problem, age)`, `parse_seat(d, seat, now)`, `parse_pool(pool)` | `DataSource.is_installed`, `Seat.scoped/extra/spending` (the normalised fields: the extra limits, the current alarm), `Model.extra` |
| `seat_right_text`, `seat_lines`, `seat_tip`, `hero_lines`, `banner_copy`, `footer_rows(lay, y, row_h, f)`, `version_text`, `tip_suffix`, `spending_line` | the popover's rows, hero, banner, footer and the strip tooltip |
| `seat_rotation_item`, `seat_menu_extra`, `seat_action(verb, seat, app)`, `enable_parked_text`, `handle_region(kind, value, app)` | the account menu and the popover's own controls |
| `settings_loaded(st)`, `lane_providers`, `lane_provider_ids`, `lane_copy(pid)`, `balancing_key`, `modes()`, `mode_done`, `order_footer`, `reserve_footer`, `seats_hint`, `setup_state`, `empty_state(app, keep, where)`, `overview_sections(pane)`, `hero_facts`, `hero_extra`, `overview_footer`, `seat_detail_views`, `seat_details(pane, seat, m)`, `remove_text`, `balancing_sections(pane, m, busy)` | the Settings panes on the add-on pool's side, and the Lanes pane's engine copy |
| `welcome_suffix`, `meter_tip_suffix`, `setup_heading`, `same_account_text`, `login_args(name, priority)`, `install_command`, `installed_note`, `setup_install_card(assistant)`, `setup_done_rows(assistant)` | the Setup assistant |
| `snapshot_panes`, `snapshot_pane(pane)`, `snapshot_login(pane, setup)`, `snapshot_prepare(controller, pane)` | `--snapshot` states of its own |

`Seat.extra` and `Model.extra` are the add-on's own objects (its credits, its route and desktop block): the core
never reads into them. The pane's `ext` dict is the add-on's state on its pool's side, cleared when the switcher
moves. Tests of the add-on's UI live in `addons/<id>/tests/` and reach the module through
`mb.POOL_UI[id].module`.
