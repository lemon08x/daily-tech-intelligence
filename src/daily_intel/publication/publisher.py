from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from daily_intel.core.models import Analysis
from daily_intel.publication.reporting import publish


class FileDigestPublisher:
    """Default file publisher; replace this adapter to change presentation or delivery."""

    def publish(
        self,
        context: dict[str, Any],
        analyses: list[Analysis],
        metadata: dict[str, Any],
        output_dir: Path,
        now: datetime,
    ) -> dict[str, Path]:
        return publish(context, analyses, metadata, output_dir, now)
