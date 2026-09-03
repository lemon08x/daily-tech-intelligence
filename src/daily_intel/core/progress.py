from __future__ import annotations

import sys
import unicodedata


def _console_text(message: str, encoding: str) -> str:
    # Cf includes U+200B; GBK and other legacy consoles cannot encode it.
    stripped = "".join(ch for ch in message if unicodedata.category(ch) != "Cf")
    try:
        return stripped.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except LookupError:
        return stripped


def progress(message: str) -> None:
    """Print a live status line. Launch scripts tee this to the console.

    Never raise UnicodeEncodeError: a GBK Windows console aborting on one
    title must not kill the daily run.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    text = _console_text(message, encoding)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"), flush=True)
