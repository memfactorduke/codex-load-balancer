"""Checked source extension for cswap's public subscription metadata.

Only cswap's own process uses its existing OAuth helpers. subpool never receives
credentials. Exact upstream hashes prevent applying this to an unfamiliar build.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

HASHES = {
    'oauth.py': 'a3c1a4b3f84ff60ab6f018b6596a79e239a735cd4c61e6cfdcd02de9aebaed40',
    'switcher.py': 'b70747a3c6f9221ddec3e31eb9b0ad93462b3d5e6aa0c1613fe64fe9e2ea5126',
}
PROFILE_ANCHOR = '        "organizationUuid": org_uuid if isinstance(org_uuid, str) else None,\n    }\n\n\n\n'
PROFILE_FIELDS = '''        # subpool subscription metadata: public fields only.
        "subscription": {
            "organizationType": organization.get("organization_type") if isinstance(organization, dict) else None,
            "rateLimitTier": organization.get("rate_limit_tier") if isinstance(organization, dict) else None,
            "seatTier": organization.get("seat_tier") if isinstance(organization, dict) else None,
            "hasPro": account.get("has_claude_pro") is True,
            "hasMax": account.get("has_claude_max") is True,
        },
'''
LIST_ANCHOR = '    def _build_list_payload(\n'
ROW_ANCHOR = '        payload = {\n            "schemaVersion": SCHEMA_VERSION,\n            "activeAccountNumber": active_num,\n'
SUBSCRIPTION_METHOD = '''    def _subpool_subscription(self, account_num, creds):
        """Public plan projection inside cswap; no refresh, switch or credential write."""
        import hashlib
        import tempfile
        from datetime import datetime, timezone

        identity = self.account_identity(account_num)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = self.backup_dir / "cache" / ("subpool-plan-" + key + ".json")
        now = time.time()
        try:
            cache = json.loads(path.read_text())
            if isinstance(cache, dict) and now < cache.get("retryAt", 0):
                return cache.get("subscription")
        except (OSError, ValueError, TypeError):
            pass
        subscription = None
        token = oauth.extract_access_token(creds)
        resolved = oauth.fetch_oauth_profile(token) if token else None
        if resolved and self._resolved_matches_slot_identity(account_num, resolved) is True:
            source = resolved.get("subscription")
            if isinstance(source, dict):
                subscription = {
                    k: v for k, v in source.items()
                    if (k in ("organizationType", "rateLimitTier", "seatTier")
                        and isinstance(v, str) and len(v) <= 128)
                    or (k in ("hasPro", "hasMax") and isinstance(v, bool))
                }
                subscription["fetchedAt"] = datetime.fromtimestamp(now, timezone.utc).isoformat()
        # Six-hour successful cache; failures retry after fifteen minutes.
        # This cache contains only public subscription fields, never credentials.
        cache = {"retryAt": now + (21600 if subscription else 900), "subscription": subscription}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
                tmp = output.name
                json.dump(cache, output)
            os.replace(tmp, path)
        except OSError:
            if "tmp" in locals():
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return subscription

'''


def patched_sources(original):
    """Pure, fail-closed render. A repeated install accepts only our exact result."""
    result = {}
    for name, expected in HASHES.items():
        source = original[name]
        if hashlib.sha256(source.encode()).hexdigest() != expected:
            raise ValueError('cswap plan detection needs the pinned upstream source; unexpected ' + name)
        if name == 'oauth.py':
            if source.count(PROFILE_ANCHOR) != 1:
                raise ValueError('cswap profile anchor changed.')
            value = source.replace(PROFILE_ANCHOR, PROFILE_ANCHOR.replace('    }\n', PROFILE_FIELDS + '    }\n', 1))
        else:
            if source.count(LIST_ANCHOR) != 1 or source.count(ROW_ANCHOR) != 1:
                raise ValueError('cswap list anchor changed.')
            value = source.replace(LIST_ANCHOR, SUBSCRIPTION_METHOD + LIST_ANCHOR)
            value = value.replace(ROW_ANCHOR,
                '            accounts[-1]["subscription"] = self._subpool_subscription(str(num), creds)\n' + ROW_ANCHOR)
        compile(value, name, 'exec')
        result[name] = value
    return result


def install(binary):
    if not binary:
        raise ValueError('Install cswap before enabling automatic plan detection.')
    first = Path(binary).read_text().splitlines()[0]
    if not first.startswith('#!/'):
        raise ValueError('Cannot locate cswap’s Python interpreter.')
    python = first[2:]
    lookup = subprocess.run([python, '-c',
        'import importlib.util; print(importlib.util.find_spec("claude_swap").submodule_search_locations[0])'],
        capture_output=True, text=True, check=True)
    root = Path(lookup.stdout.strip())
    originals = {}
    for name in HASHES:
        path = root / name
        backup = path.with_suffix('.py.subpool-original')
        originals[name] = backup.read_text() if backup.exists() else path.read_text()
    rendered = patched_sources(originals)
    # Validate all files before writing any. Preserve an exact source-only rollback.
    for name, value in rendered.items():
        path = root / name
        if path.read_text() not in (originals[name], value):
            raise ValueError('cswap source changed after plan detection was installed: ' + name)
    for name, value in rendered.items():
        path = root / name
        if path.read_text() == value:
            continue
        backup = path.with_suffix('.py.subpool-original')
        if not backup.exists():
            backup.write_text(originals[name])
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as output:
            output.write(value)
            temporary = output.name
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
