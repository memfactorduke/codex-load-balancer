"""Native view of cswap's public status. No network or credential reads here."""
from pathlib import Path


def create(legacy, mb):
    class CswapUI(legacy.PoolUI):
        product_scope = 'CLI'
        status_file = Path.home() / '.codexpool/state/claude-cli-status.json'
        history_file = Path.home() / '.codexpool/state/claude-cli-history.jsonl'
        scope_words = {'all': 'selected account', 'regular': 'selected account'}
        show_chart = False
        order_label = 'cswap accounts'
        order_tip = 'Saved Claude CLI logins; choose an account to switch.'
        setup_pane = 'seats'
        lane_providers = ()
        lane_provider_ids = ()
        switcher_blurb = 'Claude CLI account switching, powered by cswap'
        add_account_title = 'Add current Claude CLI login…'
        serving_tip = 'Selected Claude CLI login'
        welcome_suffix = ' Claude CLI accounts are managed separately with cswap.'
        footer_items = (('terminal', 'Status…', 'status'), ('arrow.clockwise', 'Refresh', 'refresh'),
                        ('book', 'cswap documentation', 'docs'))
        docs_url = 'https://github.com/realiti4/claude-swap'
        docs_path = Path(__file__).with_name('README.md')

        @staticmethod
        def installed(raw, problem, age=None):
            return True  # Always offer the separate CLI product, including first setup.

        @staticmethod
        def parse_pool(pool):
            return dict(pool)

        @staticmethod
        def parse_seat(row, seat, now=None):
            seat.plan = ''
            seat.selected = row.get('selected') is True
            seat.reserve_held = row.get('reserve_held') is True
            seat.scoped = []
            for w in row.get('scoped', []):
                seat.scoped.append(legacy.Scoped(w.get('name', 'Model'), w.get('used'), mb.parse_time(w.get('reset_at'))))

        @staticmethod
        def project_model(model, raw):
            pool = raw.get('pool', {})
            model.headline = pool.get('used_pct') if pool.get('selected_usage_known') else None
            model.serving = next((s for s in model.seats if getattr(s, 'selected', False)), None)
            return model

        @staticmethod
        def state_label(seat, default):
            if getattr(seat, 'selected', False):
                return 'Selected'
            if getattr(seat, 'reserve_held', False) and seat.state != 'disabled':
                return 'Held for later'
            return default

        @staticmethod
        def subtitle(m, updated):
            return updated + (' · Selected ' + m.serving.label if m.serving else '')

        @staticmethod
        def headline_tip(m):
            return 'Weekly quota for the selected Claude CLI account. Each account’s limits are listed separately.'

        @staticmethod
        def tip_suffix(m):
            return ' · Claude CLI only · powered by cswap'

        @staticmethod
        def version_text(m):
            return 'cswap · Claude CLI'

        @staticmethod
        def seat_lines(*args):
            return []

        @staticmethod
        def banner_copy(m):
            if not (m.extra or {}).get('installed'):
                return ('Set up Claude CLI', 'Use cswap to switch Claude Code accounts. Codex is separate.',
                        ('Open Settings', 'settings'))
            return ('Claude CLI status unavailable', 'Refresh to read the latest cswap account status.', ('Refresh', 'refresh'))

        @staticmethod
        def draw_hero(lay, y):
            m = lay.m
            f = mb.font(13, mb.NSFontWeightSemibold)
            selected = m.serving.label if m.serving else 'No account selected'
            lay.text(selected, mb.PAD, y, f, mb.C.label(), width=mb.INNER)
            y += mb.line_height(f) + 5
            text = 'Selected Claude CLI login · ' + ('auto-switch on' if (m.extra or {}).get('auto_switch') else 'manual switching')
            lay.text(text, mb.PAD, y, mb.font(11), mb.C.secondary(), width=mb.INNER)
            return y + 20

        @staticmethod
        def hero_lines(m):
            return []

        @staticmethod
        def footer_rows(lay, y, row_h, f):
            return y

        @staticmethod
        def account_menu(seat, add):
            add('Selected' if getattr(seat, 'selected', False) else 'Switch to this account',
                'checkmark.circle' if getattr(seat, 'selected', False) else 'arrow.left.arrow.right',
                None if getattr(seat, 'selected', False) else 'switch')
            add('Enable automatic rotation' if seat.state == 'disabled' else 'Exclude from automatic rotation',
                'pause.circle', 'enable' if seat.state == 'disabled' else 'disable')
            add('Use as a regular account' if seat.reserve else 'Use as reserve',
                'shield', 'unreserve' if seat.reserve else 'reserve')

        @staticmethod
        def seat_action(verb, seat, app):
            if verb not in ('switch', 'enable', 'disable', 'reserve', 'unreserve') or seat is None:
                return True
            app.say('Updating Claude CLI…')
            args = ['claude', 'reserve', seat.name, 'off' if verb == 'unreserve' else 'on'] if verb in ('reserve', 'unreserve') else ['claude', verb, seat.name]
            mb.run_codexpool(args, lambda code, err: app.after_action(
                'Claude CLI updated; reopen Claude Code to apply a switch immediately', verb, code, err))
            return True

        @staticmethod
        def run_action(app, action):
            if action == 'refresh':
                app.say('Refreshing cswap…')
                mb.run_codexpool(['claude', 'status', '--live'], app.refreshed)
                return True
            if action == 'addaccount':
                app.popover.performClose_(None)
                mb.open_settings('seats', 'claude')
                return True
            return False

        def settings_loaded(self, settings):
            self.st = settings

        def execute(self, pane, args):
            pane.run(['claude'] + args, 'Updating Claude CLI…',
                     'Claude CLI updated. Reopen Claude Code to apply a login switch immediately.')

        def settings_page(self, pane, page):
            st, m, k = self.st, pane.store.model('claude'), pane.keep
            extra = m.extra or {}
            busy = bool(pane.note and pane.note[0] == 'busy')
            note = st.note_row(pane.note_now())
            out = [st.section(st.group([st.form_row('Claude CLI · powered by cswap',
                    'Switch Claude Code logins directly. Start Claude Code with plain claude. '
                    'Codex Desktop/CLI and its accounts are separate.', ())]), 'Claude CLI')]
            if note is not None:
                out.append(st.group([note]))
            if not extra.get('installed'):
                out.append(st.section(st.group([st.form_row('Install cswap',
                    'Installs a pinned upstream release. Account import and auto-switching are separate steps.',
                    st.button('Install cswap…', lambda _: pane.app.ask('Install cswap?',
                        'Install the upstream Claude CLI account switcher. No accounts will be imported.',
                        'Install', lambda: self.execute(pane, ['install'])), k, enabled=not busy))]), 'Setup'))
                return out
            if page != 'switching':
                rows = []
                for seat in m.seats:
                    selected = getattr(seat, 'selected', False)
                    week = mb.fmt_pct(m.shown(seat.week.used if seat.week else None))
                    short = mb.fmt_pct(m.shown(seat.short.used if seat.short else None))
                    desc = ('Selected · ' if selected else '') + f'Week {week} left · 5h {short} left'
                    if seat.reserve:
                        desc += ' · Reserve'
                        if getattr(seat, 'reserve_held', False):
                            desc += ' (held for later)'
                    if seat.detail:
                        desc += ' · ' + seat.detail.replace('_', ' ')
                    buttons = [st.button('Selected' if selected else 'Switch',
                        lambda _, n=seat.name: self.execute(pane, ['switch', n]), k,
                        enabled=not selected and not busy)]
                    verb = 'enable' if seat.state == 'disabled' else 'disable'
                    buttons.append(st.button('Enable' if verb == 'enable' else 'Exclude',
                        lambda _, n=seat.name, v=verb: self.execute(pane, [v, n]), k, enabled=not busy))
                    buttons.append(st.button('Make regular' if seat.reserve else 'Reserve',
                        lambda _, n=seat.name, mode='off' if seat.reserve else 'on': self.execute(pane, ['reserve', n, mode]), k, enabled=not busy))
                    buttons.append(st.button('Remove…',
                        lambda _, n=seat.name: pane.app.ask('Remove this saved account?',
                            'cswap will remove this account from its saved accounts. This does not delete the Claude account.',
                            'Remove', lambda: self.execute(pane, ['remove', n, '--yes'])), k, enabled=not busy))
                    rows.append(st.form_row(seat.label, desc, buttons))
                if not rows:
                    rows.append(st.form_row('No accounts saved', 'Sign in with Claude Code, then add its current login below.', ()))
                out.append(st.section(st.group(rows), 'Accounts'))
                out.append(st.group([st.form_row('Reserve accounts',
                    'Held out of automatic rotation until every enabled regular account reaches the switching threshold. '
                    'You can still switch to a reserve manually.', ())]))
                out.append(st.section(st.group([st.form_row('Add the current Claude Code login',
                    'First sign in to the account in Claude Code. cswap saves that login itself; '
                    'old proxy accounts are not imported.', st.button('Add current login…',
                    lambda _: pane.app.ask('Add the current Claude Code login?',
                        'cswap will save the login currently used by Claude Code.', 'Add',
                        lambda: self.execute(pane, ['add'])), k, enabled=not busy))]), 'Add account'))
            if page in ('switching', 'overview'):
                on = extra.get('auto_switch') is True
                rows = [st.form_row('Automatic switching',
                    'Checks every minute using cswap’s rotation rules. Run only one auto-switcher: '
                    'turn off cswap auto and its menu-bar auto-switcher before enabling this.',
                    st.button('Turn off' if on else 'Turn on…', lambda _: self.execute(pane, ['auto', 'off']) if on else
                        pane.app.ask('Enable automatic account switching?',
                            'cswap may change the Claude Code login while you work. Confirm that no other cswap '
                            'auto-switcher is running.', 'Enable', lambda: self.execute(pane, ['auto', 'on'])), k, enabled=not busy))]
                strategy = extra.get('strategy', 'best')
                rows.append(st.form_row('Choose the next account',
                    'Most quota left switches near the limit. Reset soonest uses quota before it expires.',
                    [st.button(('✓ ' if strategy == value else '') + title,
                        lambda _, v=value: self.execute(pane, ['strategy', v]), k, enabled=not busy)
                     for value, title in (('best', 'Most quota left'), ('consume-first', 'Reset soonest'))]))
                rows.append(st.form_row('Switch before the limit',
                    'Current threshold: %s%% used. cswap handles cooldowns and avoids repeated switching.' % extra.get('threshold', 90),
                    [st.button(str(n) + '%', lambda _, v=n: self.execute(pane, ['threshold', str(v)]), k, enabled=not busy)
                     for n in (80, 90, 95)]))
                out.append(st.section(st.group(rows), 'Automatic switching'))
            return out

        def setup_accounts(self, assistant):
            st = self.st
            return [assistant.heading('Claude CLI uses cswap',
                        'Manage Claude CLI separately from Codex Desktop/CLI.'),
                    st.button('Open Claude CLI accounts',
                        lambda _: (assistant.win.close(), assistant.app.set_pool('claude'),
                                   assistant.app.open_pane('seats')), assistant.keep),
                    st.button('Back to Codex setup', lambda _: assistant.set_pool('codex'), assistant.keep)]

        def overview_sections(self, pane):
            return self.settings_page(pane, 'overview')

    ui = CswapUI()
    ui.module = legacy
    return ui
