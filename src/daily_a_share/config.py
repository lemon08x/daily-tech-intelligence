from pathlib import Path

from daily_intel.core.settings import load_settings


load_config = load_settings


def resolve_project_path(config, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(config["_project_root"]) / path


__all__ = ["load_config", "resolve_project_path"]
