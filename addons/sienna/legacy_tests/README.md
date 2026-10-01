# Retired proxy tests

These tests describe the retired Claude proxy, desktop integration and Codex engine
lane. They are retained with the old implementation for migration/reference, not
counted as validation of the cswap product. Several require the former runtime
registration and are not runnable against the replacement add-on registration.

The active suite is `../tests/`: it tests cswap's public command boundary, UI data,
account identity checks and opt-in switching. Core tests continue unchanged.
