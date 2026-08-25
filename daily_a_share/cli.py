"""Compatibility entry point; the implementation lives in daily_intel.app."""

from daily_intel.app.cli import build_parser, main, run_doctor

__all__ = ["build_parser", "main", "run_doctor"]
