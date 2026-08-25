from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(slots=True)
class Dataset:
    key: str
    frame: pd.DataFrame
    source: str
    fetched_at: str
    stale: bool = False
    error: str | None = None


class CsvCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, frame: pd.DataFrame, source: str, fetched_at: str) -> None:
        csv_path, meta_path = self.directory / f"{key}.csv", self.directory / f"{key}.meta.json"
        csv_tmp, meta_tmp = csv_path.with_suffix(".csv.tmp"), meta_path.with_suffix(".json.tmp")
        frame.to_csv(csv_tmp, index=False, encoding="utf-8-sig")
        meta_tmp.write_text(json.dumps({"source": source, "fetched_at": fetched_at}, ensure_ascii=False, indent=2), encoding="utf-8")
        csv_tmp.replace(csv_path)
        meta_tmp.replace(meta_path)

    def load(self, key: str, error: str | None = None) -> Dataset | None:
        csv_path, meta_path = self.directory / f"{key}.csv", self.directory / f"{key}.meta.json"
        if not csv_path.exists() or not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return Dataset(key, pd.read_csv(csv_path), str(meta.get("source", "未知缓存")), str(meta.get("fetched_at", "未知时间")), True, error)
