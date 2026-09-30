# Corrected desktop wire proposal

The earlier malformed-environment findings are superseded by
[the corrected capture matrix](desktop-wire-findings.html).

The smaller unapplied `cpa-desktop-identity.patch` preserves the corrected native
desktop entrypoint, caller session and capability betas on both tested CPA versions.
The fuller supplied patch has the same measured result; its sampling and transport
extensions show no additional benefit on these captures.

Credential rebinding, including an inserted billing cch signature, is allowed.
Generated request IDs remain explicit strict-policy transport differences.
Both patches still fail the existing desktop token-count gate rejection test;
resolve that policy and obtain token-count coverage before adoption.
Gateway-mode captures do not establish native OAuth-mode CLI wire equivalence.
