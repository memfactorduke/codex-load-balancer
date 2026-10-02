"""Sienna's registration boundary. Loading only binds code; it performs no runtime writes."""
cp = None


def load(core):
    global cp
    cp = core
    from . import pool, guard, selftest, desktop, desktop_build, gate_build, lane_engine, cswap_backend
    pool.sienna_guard, pool.sienna_selftest = guard, selftest
    guard.sienna_selftest = selftest
    selftest.sienna_guard = guard

    class Sienna:
        id, version, core_min = 'sienna', '1.4.0', '1.4.0'
        install_hint = 'subpool claude install'
        ui_pool_ids = ('claude',)  # the menu bar and Settings pool id (menubar_ext.POOL)
        uninstall_title = 'the Claude pool'

        def settings_problems(self, settings):
            value = settings['claude_cpa']
            if value is not None and pool.parse_cpa_version(value) is None:
                return ['claude_cpa (null, or a CLIProxyAPI version 7.3.18 or later)']
            return []

        def on_set(self, key, old, new):
            return ('The guard applies it to the Claude pool on its next pass (within a minute); new '
                    'conversations follow it, running ones stay on their account.') if old != new else ''

        def lane_providers(self):
            return {}

        @property
        def bridge_extension(self):
            return cp.pathlib.Path(lane_engine.__file__).with_name('bridge_engine.py')

        @property
        def menubar_extension(self):
            """The menu bar and Settings extension (a PoolUI: menubar_ext.py; the apps load it themselves)."""
            return cp.pathlib.Path(lane_engine.__file__).with_name('menubar_ext.py')

        def install_bridge_config(self, config):
            return None

        def add_doctor_parser(self, parser):
            pass

        def before_doctor(self, args):
            pass

        def gate(self):
            return gate_build.GateContribution()

        def pools(self):
            return []

        def seat_pools(self):
            # Protect an existing proxy until explicit retirement; never create one.
            return {'claude': pool.claude_seat_pool()} if pool.claude_installed() else {}

        def add_parser(self, sub, core):
            cswap_backend.add_parser(sub)

        def guard_passes(self):
            if pool.claude_installed():
                # An upgrade must not remove credit protection from a still-running proxy.
                return [('Legacy Claude guard', guard.claude_guard_pass, pool.claude_seat_pool())]
            if cswap_backend.options().get('enabled') is not True:
                return []
            return [('Claude CLI status', cswap_backend.guard,
                     cswap_backend.guard_descriptor(pool.claude_seat_pool()))]

        def status(self, live):
            if cswap_backend.options().get('enabled') is not True:
                return None
            view = cswap_backend.read_live() if live and cswap_backend.executable() else cswap_backend.saved()
            return 'claude', view, lambda: print('Claude CLI: powered by cswap; use subpool claude status')

        def doctor(self, rep):
            if cswap_backend.options().get('enabled') is not True and not pool.claude_installed():
                return
            rep.section('Claude CLI')
            rep.check(bool(cswap_backend.executable()), 'cswap installed',
                          'subpool claude install', warn=True)
            if pool.claude_installed():
                rep.check(False, 'Legacy Claude proxy still installed; CLI switching uses cswap instead. '
                              'Migration has not been performed.', warn=True)

        def uninstall_plan(self):
            if pool.claude_installed() or any(cp.script_is_ours(p, m) for p, m in (
                    (pool.CLAUDE_LAUNCHER, pool.CLAUDE_LAUNCHER_MARK), (pool.CLAUDE_SHIM, pool.CLAUDE_SHIM_MARK))):
                return pool.claude_uninstall_plan()
            return []

        def keep_note(self):
            if self.uninstall_plan():
                return ("with the Claude pool's auth-claude/ and config-claude.yaml (subpool install and subpool claude "
                        'install bring it all back)')
            return None

    addon = Sienna()
    addon.pool, addon.guard, addon.selftest = pool, guard, selftest
    addon.desktop, addon.desktop_build = desktop, desktop_build
    addon.gate_build = gate_build
    addon.lane_engine = lane_engine
    addon.cswap = cswap_backend
    return addon
