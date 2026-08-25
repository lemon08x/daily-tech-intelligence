from daily_intel.app.orchestrator import run_application


def run_pipeline(config, offline: bool = False, no_ai: bool = False, require_ai: bool = False):
    return run_application(config, offline=offline, no_ai=no_ai, require_ai=require_ai)


__all__ = ["run_pipeline"]
