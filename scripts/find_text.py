"""在文档内容中定位关键词附近原文，用于核对精确字符。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

content = Path(sys.argv[1]).read_text(encoding="utf-8")
for keyword in sys.argv[2:]:
    matches = list(re.finditer(re.escape(keyword), content))
    if not matches:
        print(f"[{keyword}] NOT FOUND")
        continue
    m = matches[0]
    print(f"[{keyword}] {repr(content[max(0, m.start() - 60):m.end() + 120])}")
    print("---")
