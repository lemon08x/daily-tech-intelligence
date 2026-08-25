"""验证候选引文是否为文档内容的逐字子串（忽略空白差异）。

用法: python scripts/check_quotes.py <request.json> <quotes.json>
quotes.json 为字符串数组。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def norm(value: str) -> str:
    return re.sub(r"\s+", "", value)


def main() -> None:
    request_path = Path(sys.argv[1])
    quotes_path = Path(sys.argv[2])
    data = json.loads(request_path.read_text(encoding="utf-8"))
    payload = json.loads(data["user"])
    quotes = json.loads(quotes_path.read_text(encoding="utf-8"))
    for i, doc in enumerate(payload.get("documents", [])):
        haystack = norm(doc.get("content") or doc.get("title", ""))
        print(f"doc[{i}] {doc['document_id']} len={len(haystack)}")
        for q in quotes:
            ok = norm(q) in haystack
            print(f"  {'OK ' if ok else 'FAIL'} {q[:80]}")


if __name__ == "__main__":
    main()
