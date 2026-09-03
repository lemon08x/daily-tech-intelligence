from __future__ import annotations

import sys
from io import BytesIO, TextIOWrapper

import pytest

from daily_intel.app.cli import main
from daily_intel.core.progress import progress


ZWSP_TITLE = "当前：深研 general · The EU’s AI Drive Undermines Its \u200bOwn Chip Strategy"


def test_progress_does_not_raise_when_stdout_is_gbk(monkeypatch) -> None:
    buf = BytesIO()
    stream = TextIOWrapper(buf, encoding="gbk", errors="strict", line_buffering=True)
    monkeypatch.setattr(sys, "stdout", stream)
    progress(ZWSP_TITLE)
    stream.flush()
    output = buf.getvalue().decode("gbk")
    assert "深研 general" in output
    assert "Own Chip Strategy" in output
    assert "\u200b" not in output


def test_progress_replaces_characters_gbk_cannot_encode(monkeypatch) -> None:
    buf = BytesIO()
    stream = TextIOWrapper(buf, encoding="gbk", errors="strict", line_buffering=True)
    monkeypatch.setattr(sys, "stdout", stream)
    progress("当前：深研 · 🚀")
    stream.flush()
    output = buf.getvalue().decode("gbk")
    assert "深研" in output
    assert "🚀" not in output


def test_cli_does_not_treat_unicode_encode_error_as_handled_failure(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise UnicodeEncodeError("gbk", "\u200b", 0, 1, "illegal multibyte sequence")

    monkeypatch.setattr("daily_intel.app.cli.load_settings", boom)
    with pytest.raises(UnicodeEncodeError):
        main(["run", "--config", "config/settings.yaml"])
