"""Shared production-run filters for analyzer health reporting."""

PRODUCTION_RUN_TYPES = ("incremental", "full_resolution")

# Synthetic probe phases are allowed to fail by design. They prove phase
# failures are non-fatal, but they should not keep production health degraded.
PROBE_PHASE_NAMES = ("forced_failure",)


def production_run_types() -> list[str]:
    return list(PRODUCTION_RUN_TYPES)


def probe_phase_names() -> list[str]:
    return list(PROBE_PHASE_NAMES)


def is_production_run_type(run_type: str | None) -> bool:
    return run_type in PRODUCTION_RUN_TYPES


def is_probe_phase_name(phase: str | None) -> bool:
    return phase in PROBE_PHASE_NAMES
