"""No I/O on load; proves executable initialization sees a complete core module."""

def load(cp):
    assert cp.SETTINGS and cp.SeatPool and cp.PoolInstance and cp.build_parser

    class Stub:
        id, version, core_min = 'stub', '1.0.0', '1.3.0'
        install_hint = 'codexpool stub --help'

        def settings_problems(self, settings):
            return []

        def on_set(self, key, old, new):
            return 'Fixture setting saved.'

        def add_parser(self, sub, core):
            assert core is cp
            sub.add_parser('stub', help='fixture add-on').set_defaults(fn=lambda args: None)

        def guard_passes(self):
            return []

        def status(self, live):
            return 'stub', {'live': live}, lambda: print('Fixture status')

        def doctor(self, rep):
            rep.section('Fixture pool')
            rep.check(True, 'fixture ready')

        def uninstall_plan(self):
            return []

        def keep_note(self):
            return 'Keep fixture runtime state.'

    return Stub()
