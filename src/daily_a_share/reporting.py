"""Compatibility exports for formatting helpers; publication lives in daily_intel."""

import json

from daily_intel.publication.reporting import format_money, format_number, publish


def records(frame):
    clean = frame.replace({float("nan"): None})
    return json.loads(clean.to_json(orient="records", force_ascii=False))


__all__ = ["format_money", "format_number", "publish", "records"]
