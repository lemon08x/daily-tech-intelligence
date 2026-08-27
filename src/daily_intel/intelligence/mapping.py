from __future__ import annotations

import contextlib
import io
import re
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from daily_intel.core.models import CompanyHypothesis, CompanyMapping, Evidence, MappingStatus
from daily_intel.infrastructure.http import install_proxy_fallback


class CompanyMapper:
    def __init__(self, snapshot: pd.DataFrame, now: datetime, offline: bool = False) -> None:
        self.now = now
        self.offline = offline
        install_proxy_fallback()
        self.company_by_code = {
            str(row["code"]): str(row["name"])
            for _, row in snapshot[["code", "name"]].dropna().drop_duplicates("code").iterrows()
        }
        self._industry_cache: dict[str, str] = {}

    def resolve(self, hypotheses: list[CompanyHypothesis]) -> list[CompanyMapping]:
        mappings: list[CompanyMapping] = []
        for hypothesis in hypotheses[:3]:
            actual_name = self.company_by_code.get(hypothesis.code)
            if actual_name is None or actual_name != hypothesis.name:
                continue
            announcement_evidence = [] if self.offline else self._verify_cninfo(hypothesis)
            industry = "" if self.offline else self._industry_cninfo(hypothesis.code)
            evidence = list(announcement_evidence)
            if industry:
                evidence.append(
                    Evidence(
                        document_id=f"cninfo-industry:{hypothesis.code}",
                        url="https://webapi.cninfo.com.cn/#/apiDoc",
                        quote=f"{hypothesis.code} {actual_name} 巨潮行业分类：{industry}",
                        locator="巨潮资讯行业分类",
                    )
                )
            mappings.append(
                CompanyMapping(
                    code=hypothesis.code, name=actual_name, industry=industry,
                    rationale=hypothesis.rationale,
                    # Industry classification alone cannot prove an event-specific relationship.
                    status=MappingStatus.VERIFIED if announcement_evidence else MappingStatus.UNVERIFIED,
                    confidence=min(hypothesis.confidence, 0.9 if announcement_evidence else 0.45),
                    evidence=evidence,
                )
            )
        return mappings

    def _industry_cninfo(self, code: str) -> str:
        if code in self._industry_cache:
            return self._industry_cache[code]
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                frame = ak.stock_industry_change_cninfo(
                    symbol=code, start_date="19900101", end_date=self.now.date().strftime("%Y%m%d")
                )
            if frame.empty:
                return ""
            row = frame.iloc[-1]
            parts = [
                str(row.get(column, "")).strip()
                for column in ("行业门类", "行业次类", "行业大类", "行业中类")
                if pd.notna(row.get(column)) and str(row.get(column, "")).strip()
            ]
            value = " / ".join(dict.fromkeys(parts))
            self._industry_cache[code] = value
            return value
        except Exception:
            return ""

    def _verify_cninfo(self, hypothesis: CompanyHypothesis) -> list[Evidence]:
        start = (self.now.date() - timedelta(days=365)).strftime("%Y%m%d")
        end = self.now.date().strftime("%Y%m%d")
        keywords = [item.strip() for item in hypothesis.keywords if item.strip()][:2]
        for keyword in keywords:
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    frame = ak.stock_zh_a_disclosure_report_cninfo(
                        symbol=hypothesis.code, keyword=keyword, start_date=start, end_date=end
                    )
                if frame.empty:
                    continue
                matches = frame.head(2)
                return [
                    Evidence(
                        document_id=f"cninfo:{hypothesis.code}:{index}",
                        url=str(row["公告链接"]), quote=re.sub(r"<[^>]+>", "", str(row["公告标题"])),
                        locator=f"巨潮公告 {row['公告时间']}",
                    )
                    for index, row in matches.iterrows()
                ]
            except Exception:
                continue
        return []
