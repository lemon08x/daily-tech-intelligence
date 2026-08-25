from __future__ import annotations

import re


def sanitize_run_identifier(value: str, default: str = "default") -> str:
    """Return a filesystem- and cache-safe identifier with a stable fallback."""
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("._-")
    return normalized[:80] or default
