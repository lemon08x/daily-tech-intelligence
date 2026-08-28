from __future__ import annotations


def progress(message: str) -> None:
    """Print a live status line. Launch scripts tee this to the console."""
    print(message, flush=True)
