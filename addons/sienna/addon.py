"""Sienna's registration boundary. Loading only binds code; it performs no runtime writes."""
cp = None


def load(core):
    global cp
    cp = core
    from . import pool, guard, selftest, desktop, desktop_build, gate_build, lane_engine
    pool.sienna_guard, pool.sienna_selftest = guard, selftest
    guard.sienna_selftest = selftest
    selftest.sienna_guard = guard

    class Sienna:
        id, version, core_min = 'sienna', '1.3.0', '1.3.0'
        install_hint = 'codexpool sienna install'
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
            return {'sienna': lane_engine.Provider()}

        @property
        def bridge_extension(self):
            return cp.pathlib.Path(lane_engine.__file__).with_name('bridge_engine.py')

        @property
        def menubar_extension(self):
            """The menu bar and Settings extension (a PoolUI: menubar_ext.py; the apps load it themselves)."""
            return cp.pathlib.Path(lane_engine.__file__).with_name('menubar_ext.py')

        def install_bridge_config(self, config):
            return lane_engine.install_bridge_config(config)

        def add_doctor_parser(self, parser):
            lane_engine.add_doctor_parser(parser)

        def before_doctor(self, args):
            lane_engine.before_doctor(args)

        def gate(self):
            return gate_build.GateContribution()

        def pools(self):
            return [pool.claude_pool_instance()]

        def seat_pools(self):
            return {'claude': pool.claude_seat_pool()}

        def add_parser(self, sub, core):
            pool.add_claude_parser(sub)

        def guard_passes(self):
            return [('claude guard', guard.claude_guard_pass, pool.claude_seat_pool())] if pool.claude_installed() else []

        def status(self, live):
            if not pool.claude_installed():
                return None
            view = pool.claude_status_view(live)
            def show():
                print()
                pool.print_claude_status(*view)
            return 'claude', view[0], show

        def doctor(self, rep):
            if pool.claude_installed():
                pool.doctor_claude_checks(rep)
            elif desktop.desktop_recorded():
                desktop.doctor(cp, rep)

        def uninstall_plan(self):
            if pool.claude_installed() or any(cp.script_is_ours(p, m) for p, m in (
                    (pool.CLAUDE_LAUNCHER, pool.CLAUDE_LAUNCHER_MARK), (pool.CLAUDE_SHIM, pool.CLAUDE_SHIM_MARK))):
                return pool.claude_uninstall_plan()
            return []

        def keep_note(self):
            if self.uninstall_plan():
                return ("with the Claude pool's auth-claude/ and config-claude.yaml (codexpool install and codexpool claude "
                        'install bring it all back)')
            return None

    addon = Sienna()
    addon.pool, addon.guard, addon.selftest = pool, guard, selftest
    addon.desktop, addon.desktop_build = desktop, desktop_build
    addon.gate_build = gate_build
    addon.lane_engine = lane_engine
    return addon
