from __future__ import annotations

import re
from dataclasses import dataclass, field

from daily_intel.core.models import (
    AnalysisDraft,
    ClaimAudit,
    ClaimCheck,
    ClaimKind,
    ClaimStatus,
    ComparisonRow,
    Document,
    VerificationResult,
)

SENTENCE_SPLIT = re.compile(r"(?<=[。！？])\s*|(?<=[!?])\s+|(?<=\.)\s+(?=[A-Z\u4e00-\u9fff])")
NUMBER_RE = re.compile(r"(?<![A-Za-z])(\d+(?:\.\d+)?)")
PERCENT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(个百分点|percent(?:age)?\s*points?|\bpp\b|%|％)",
    re.IGNORECASE,
)
DELTA_CLAIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:到|至|→|->)\s*(\d+(?:\.\d+)?)\s*%"
    r".{0,40}?(提升|提高|增加|增长|降低|下降|减少)\s*"
    r"(\d+(?:\.\d+)?)\s*(个百分点|percent(?:age)?\s*points?|\bpp\b|%|％)?",
    re.IGNORECASE | re.DOTALL,
)
COMPARISON_TRIGGER = re.compile(
    r"领先|最高|最低|首次|第一次|第一|优于|好于|差于|提升|提高|增加|增长|"
    r"降低|下降|减少|最大|最小|最好|最差|"
    r"\blead(?:s|ing)?\b|\bhighest\b|\blowest\b|\bfirst(?:-|\s)?ever\b|"
    r"\bimprove[ds]?\b|\bincreas(?:e|ed|es)\b|\bdecreas(?:e|ed|es)\b|"
    r"\bbetter\b|\bworse\b|\bstate[- ]of[- ]the[- ]art\b|\bsota\b",
    re.IGNORECASE,
)
FIRST_CLAIM_RE = re.compile(r"首次|第一次|first(?:-|\s)?ever|\bnovel\b", re.IGNORECASE)
LEADING_CLAIM_RE = re.compile(
    r"领先|最高|最低|最好|最差|优于|好于|\blead(?:s|ing)?\b|\bhighest\b|\blowest\b|"
    r"\bbetter than\b|\bstate[- ]of[- ]the[- ]art\b|\bsota\b",
    re.IGNORECASE,
)
LOWER_BETTER = re.compile(
    r"误差|错误率|error(?:\s*rate)?|latency|loss|perplexity|时延|延迟|失败率",
    re.IGNORECASE,
)
PROPOSE_ONLY_RE = re.compile(
    r"提出一种|we propose|this paper presents|本文提出",
    re.IGNORECASE,
)
METRIC_ALIASES = {
    "f1": "f1",
    "f1-score": "f1",
    "precision": "precision",
    "recall": "recall",
    "accuracy": "accuracy",
    "acc": "accuracy",
    "auc": "auc",
    "bleu": "bleu",
    "rouge": "rouge",
    "emap": "emap",
}


def split_sentences(value: str) -> list[str]:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return []
    protected: list[str] = []
    def protect(match):
        protected.append(match.group())
        return f"\ue000{len(protected)-1}\ue001"
    text = re.sub(r"https?://[^\s。！？]+|\b(?:e\.g\.|i\.e\.|Mr\.|Dr\.|U\.S\.)", protect, text)
    parts = [item.strip() for item in SENTENCE_SPLIT.split(text) if item.strip()]
    parts = [re.sub(r"\ue000(\d+)\ue001", lambda m: protected[int(m[1])], item) for item in parts]
    return parts or [text]


def extract_claims(draft: AnalysisDraft) -> list[ClaimAudit]:
    """Enumerate auditable assertions from published fields. Model lists are ignored."""
    claims: list[ClaimAudit] = []
    if draft.headline.strip():
        claims.append(_claim("headline", "headline", draft.headline))
    if draft.scan_sentence.strip():
        claims.append(_claim("scan_sentence", "scan_sentence", draft.scan_sentence))
    for index, sentence in enumerate(split_sentences(draft.plain_takeaway)):
        claims.append(_claim(f"plain_takeaway.{index}", f"plain_takeaway[{index}]", sentence))
    for index, fact in enumerate(draft.key_facts):
        if fact.strip():
            claims.append(_claim(f"key_facts.{index}", f"key_facts[{index}]", fact))
    for name in ("technical_mechanism", "novelty", "maturity"):
        value = getattr(draft, name)
        if str(value).strip():
            claims.append(_claim(name, name, str(value)))
    if draft.outlook_6_24m.strip():
        claims.append(_claim(
            "outlook_6_24m", "outlook_6_24m", draft.outlook_6_24m, kind=ClaimKind.OUTLOOK,
        ))
    for index, impact in enumerate(draft.industry_impacts):
        text = f"{impact.segment} {impact.horizon} {impact.direction}: {impact.rationale}"
        claims.append(_claim(
            f"industry_impacts.{index}", f"industry_impacts[{index}]", text,
            kind=ClaimKind.OUTLOOK,
        ))
    return claims


def covering_claim_checks(draft: AnalysisDraft) -> list[ClaimCheck]:
    """Build synthetic verifier responses for deterministic fixtures only."""
    checks = []
    for index, item in enumerate(extract_claims(draft)):
        evidence = draft.evidence[index % len(draft.evidence)] if draft.evidence else None
        checks.append(ClaimCheck(
            claim_id=item.claim_id, field_path=item.field_path, text=item.text,
            status="supported", document_id=evidence.document_id if evidence else "",
            quote=evidence.quote if evidence else "", locator=evidence.locator if evidence else "",
        ))
    return checks


def apply_claim_checks(
    claims: list[ClaimAudit],
    verification: VerificationResult,
) -> list[ClaimAudit]:
    checks = list(verification.claim_checks)
    ids = [item.claim_id for item in checks if item.claim_id]
    duplicate_ids = {claim_id for claim_id in ids if ids.count(claim_id) > 1}
    used: set[int] = set()
    audited: list[ClaimAudit] = []
    for claim in claims:
        match, index = _match_check(claim, checks, used, duplicate_ids)
        if match is None:
            fallback = _legacy_unsupported(claim, verification.unsupported_claims)
            audited.append(claim.model_copy(update={
                "status": fallback,
                "notes": ("断言编号重复" if claim.claim_id in duplicate_ids else "审核结果缺失") if fallback == ClaimStatus.UNAUDITED else "列入 unsupported_claims",
            }))
            continue
        used.add(index)
        if match.field_path and not _path_compatible(claim.field_path, match.field_path):
            audited.append(claim.model_copy(update={
                "status": ClaimStatus.UNAUDITED, "notes": "字段路径失效",
            }))
            continue
        if match.text and not _same_assertion(claim.text, match.text):
            audited.append(claim.model_copy(update={
                "status": ClaimStatus.UNAUDITED, "notes": "审核文本与草稿不一致",
            }))
            continue
        audited.append(claim.model_copy(update={
            "status": ClaimStatus(match.status),
            "document_id": match.document_id,
            "locator": match.locator,
            "quote": match.quote,
            "notes": match.notes,
        }))
    return audited


def check_numeric_claims(
    claims: list[ClaimAudit],
    documents: list[Document],
    comparison_rows: list[ComparisonRow] | None = None,
) -> list[ClaimAudit]:
    corpus = "\n".join(_document_text(item) for item in documents)
    tables = [table for document in documents for table in parse_metric_tables(_document_text(document))]
    extra_rows = [row for row in (comparison_rows or []) if _valid_comparison_row(row, documents)]
    updated: list[ClaimAudit] = []
    for claim in claims:
        notes: list[str] = []
        status = claim.status
        if not COMPARISON_TRIGGER.search(claim.text) and not FIRST_CLAIM_RE.search(claim.text):
            updated.append(claim)
            continue
        percent_issue = _percent_point_confusion(claim.text)
        if percent_issue:
            status = ClaimStatus.CONFLICTING
            notes.append(percent_issue)
        if FIRST_CLAIM_RE.search(claim.text) and not _first_time_supported(claim.text, corpus):
            status = ClaimStatus.UNSUPPORTED
            notes.append("来源仅描述提出方法，不足以证明首次")
        if LEADING_CLAIM_RE.search(claim.text):
            lead_issue = _leading_conflict(claim.text, tables, extra_rows, corpus)
            if lead_issue:
                status = ClaimStatus.CONFLICTING if "矛盾" in lead_issue or "高于" in lead_issue else ClaimStatus.UNVERIFIABLE
                notes.append(lead_issue)
        if status != claim.status:
            updated.append(claim.model_copy(update={
                "status": status,
                "notes": "；".join([claim.notes, *notes]).strip("；"),
            }))
        else:
            updated.append(claim)
    return updated


def blocking_claim_issues(claims: list[ClaimAudit]) -> list[str]:
    issues: list[str] = []
    statuses = {item.status for item in claims}
    if ClaimStatus.UNAUDITED in statuses:
        issues.append("unaudited_claims")
    if ClaimStatus.UNSUPPORTED in statuses:
        issues.append("unsupported_claims")
    if ClaimStatus.CONFLICTING in statuses:
        issues.append("conflicting_claims")
    if ClaimStatus.UNVERIFIABLE in statuses:
        issues.append("unverifiable_claims")
    return issues


def supported_text(claims: list[ClaimAudit], field_prefix: str) -> list[str]:
    return [
        item.text for item in claims
        if item.field_path.startswith(field_prefix) and item.status == ClaimStatus.SUPPORTED
    ]


def field_is_supported(claims: list[ClaimAudit], field_path: str) -> bool:
    matched = [item for item in claims if item.field_path == field_path or item.claim_id == field_path]
    return bool(matched) and all(item.status == ClaimStatus.SUPPORTED for item in matched)


@dataclass(frozen=True, slots=True)
class MetricTable:
    condition: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, dict[str, float]], ...] = field(default_factory=tuple)


def parse_metric_tables(text: str) -> list[MetricTable]:
    tables: list[MetricTable] = []
    tables.extend(_parse_markdown_tables(text))
    tables.extend(_parse_aligned_tables(text))
    return tables


def _claim(
    claim_id: str, field_path: str, text: str, *, kind: ClaimKind = ClaimKind.FACT,
) -> ClaimAudit:
    return ClaimAudit(
        claim_id=claim_id, field_path=field_path, text=text.strip(), kind=kind,
    )


def _match_check(
    claim: ClaimAudit,
    checks: list[ClaimCheck],
    used: set[int],
    duplicate_ids: set[str],
) -> tuple[ClaimCheck | None, int]:
    if claim.claim_id in duplicate_ids:
        return None, -1
    for index, check in enumerate(checks):
        if index in used:
            continue
        if check.claim_id and check.claim_id == claim.claim_id:
            return check, index
    return None, -1


def _path_compatible(left: str, right: str) -> bool:
    return left == right


def _same_assertion(left: str, right: str) -> bool:
    return re.sub(r"\s+", "", left) == re.sub(r"\s+", "", right)


def _legacy_unsupported(claim: ClaimAudit, unsupported: list[str]) -> ClaimStatus:
    compact = re.sub(r"\s+", "", claim.text)
    for item in unsupported:
        other = re.sub(r"\s+", "", item)
        if compact and (compact in other or other in compact):
            return ClaimStatus.UNSUPPORTED
    return ClaimStatus.UNAUDITED


def _document_text(document: Document) -> str:
    return "\n".join(part for part in (document.title, document.summary, document.content) if part)


def _percent_point_confusion(text: str) -> str:
    match = DELTA_CLAIM_RE.search(text)
    if not match:
        return ""
    start = float(match.group(1))
    end = float(match.group(2))
    delta = float(match.group(4))
    unit = (match.group(5) or "%").lower()
    point_delta = abs(end - start)
    if start == 0:
        return ""
    relative = abs(end - start) / abs(start) * 100.0
    uses_points = "百分点" in unit or unit.strip().lower() in {"pp", "percentage point", "percentage points"}
    if abs(point_delta - delta) <= 0.15 and not uses_points and abs(relative - delta) > 1.0:
        direction = "百分点" if end >= start else "个百分点"
        return (
            f"{start}% 到 {end}% 是 {point_delta:.1f} {direction}，"
            f"约 {relative:.1f}% 相对变化，不能写成 {delta}%"
        )
    if LOWER_BETTER.search(text) and match.group(3) in {"提升", "提高", "增加", "增长"} and end > start:
        return "该指标通常越低越好，不能把数值升高表述为提升"
    if LOWER_BETTER.search(text) and match.group(3) in {"降低", "下降", "减少"} and end < start:
        return ""
    return ""


def _first_time_supported(claim: str, corpus: str) -> bool:
    if not FIRST_CLAIM_RE.search(corpus):
        return False
    if PROPOSE_ONLY_RE.search(corpus) and not re.search(
        r"首次|第一次|first(?:-|\s)?ever|to our knowledge first", corpus, re.IGNORECASE,
    ):
        return False
    return bool(re.search(r"首次|第一次|first(?:-|\s)?ever", corpus, re.IGNORECASE))


def _leading_conflict(
    claim: str,
    tables: list[MetricTable],
    extra_rows: list[ComparisonRow],
    corpus: str,
) -> str:
    metric = _metric_in_text(claim)
    subject = _subject_in_text(claim, tables, extra_rows)
    if not metric:
        # Never compare arbitrary numbers across a document (URLs, dates, currencies).
        # A bounded source statement can retain the independent verifier's verdict.
        clean_claim = re.sub(r"https?://\S+", "", claim)
        if _scoped_money_supported(clean_claim, corpus):
            return ""
        return "无法确认比较对象、指标、单位与测试范围，不能得出整体领先结论"
    values: list[tuple[str, float, str]] = []
    for table in tables:
        if metric not in { _norm_metric(header) for header in table.headers }:
            continue
        if not table.headers:
            return "表头或测试条件无法确定，不能排序"
        for name, metrics in table.rows:
            if metric in metrics:
                values.append((name, metrics[metric], table.condition))
    if not values:
        relevant_rows = [row for row in extra_rows if _norm_metric(row.metric) == metric and row.value is not None]
        if len({row.unit for row in relevant_rows}) > 1:
            return "比较单位不一致，不能直接排序"
        values.extend((row.subject, row.value, row.condition) for row in relevant_rows)
    if metric in {"f1", "precision", "recall", "accuracy", "auc"} and any(v < 0 or v > 1 for _, v, _ in values):
        return "指标单位或归一化范围不明确，不能直接排序"
    if len(values) < 2:
        return "比较证据缺少对手行，不能得出总体领先结论"
    conditions = {item[2] for item in values}
    if len(conditions) > 1 and not _claim_has_condition(claim, conditions):
        return "不同任务或测试条件的数值不能直接排序"
    matched_conditions = {c for c in conditions if c and c.casefold() in claim.casefold()}
    if matched_conditions:
        values = [v for v in values if v[2] in matched_conditions]
    if len(values) < 2:
        return "指定测试条件缺少对手行，不能排序"
    higher_better = not LOWER_BETTER.search(claim + metric)
    ranked = sorted(values, key=lambda item: item[1], reverse=higher_better)
    best_name, best_value, _ = ranked[0]
    if subject:
        subject_values = [item for item in values if _names_match(item[0], subject)]
        if not subject_values:
            return "领先结论缺少对应比较行"
        subject_best = max(item[1] for item in subject_values) if higher_better else min(item[1] for item in subject_values)
        if (higher_better and subject_best + 1e-9 < best_value) or (
            not higher_better and subject_best - 1e-9 > best_value
        ):
            return (
                f"作者叙述与表格矛盾：{subject} 的 {metric} 为 {subject_best:g}，"
                f"{best_name} 为 {best_value:g}，不能称领先"
            )
    elif best_value and "最高" in claim:
        mentioned = [float(item) for item in NUMBER_RE.findall(claim)]
        if mentioned and max(mentioned) + 1e-9 < best_value:
            return f"材料中存在更高数值 {best_value:g}，不能使用不限定范围的最高结论"
    if not subject:
        return "无法绑定比较主体，不能得出整体领先结论"
    return ""


def _metric_in_text(text: str) -> str:
    compact = text.casefold()
    for alias, canonical in METRIC_ALIASES.items():
        if alias in compact:
            return canonical
    if "f1" in compact:
        return "f1"
    return ""


def _subject_in_text(
    text: str, tables: list[MetricTable], extra_rows: list[ComparisonRow],
) -> str:
    names: list[str] = []
    for table in tables:
        names.extend(name for name, _ in table.rows)
    names.extend(row.subject for row in extra_rows)
    names = sorted({item for item in names if item}, key=len, reverse=True)
    for name in names:
        if name and name.casefold() in text.casefold():
            return name
    return ""


def _claim_has_condition(claim: str, conditions: set[str]) -> bool:
    compact = claim.casefold()
    return any(condition and condition.casefold() in compact for condition in conditions)


def _names_match(left: str, right: str) -> bool:
    return re.sub(r"\s+", "", left).casefold() == re.sub(r"\s+", "", right).casefold()


def _norm_metric(value: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return METRIC_ALIASES.get(compact, compact)


def _inline_metric_values(text: str, metric: str) -> list[tuple[str, float]]:
    pattern = re.compile(
        rf"([A-Za-z][A-Za-z0-9\-_.]{{1,40}}).{{0,40}}{re.escape(metric)}.{{0,12}}"
        rf"(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    return [(match.group(1), float(match.group(2))) for match in pattern.finditer(text)]


def _parse_markdown_tables(text: str) -> list[MetricTable]:
    tables: list[MetricTable] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines) - 2:
        header = _split_row(lines[index])
        divider = lines[index + 1].strip()
        if len(header) >= 2 and re.match(r"^\|?\s*:?-{3,}", divider):
            condition = _nearby_condition(lines, index)
            headers = tuple(_norm_metric(item) if i else item.strip() for i, item in enumerate(header))
            metric_headers = [_norm_metric(item) for item in header[1:]]
            if not any(item for item in metric_headers):
                index += 1
                continue
            rows: list[tuple[str, dict[str, float]]] = []
            cursor = index + 2
            while cursor < len(lines):
                cells = _split_row(lines[cursor])
                if len(cells) < 2:
                    break
                subject = cells[0].strip()
                metrics: dict[str, float] = {}
                for name, raw in zip(metric_headers, cells[1:]):
                    number = _parse_number(raw)
                    if name and number is not None:
                        metrics[name] = number
                if subject and metrics:
                    rows.append((subject, metrics))
                cursor += 1
            if rows and all(headers[1:]):
                tables.append(MetricTable(condition, tuple(header), tuple(rows)))
            elif rows and not all(headers[1:]):
                tables.append(MetricTable(condition, tuple(), tuple(rows)))
            index = cursor
            continue
        index += 1
    return tables


def _parse_aligned_tables(text: str) -> list[MetricTable]:
    tables: list[MetricTable] = []
    lines = [line.rstrip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        header_cells = re.split(r"\s{2,}|\t", line.strip())
        if len(header_cells) < 3:
            continue
        metrics = [_norm_metric(item) for item in header_cells[1:]]
        if sum(1 for item in metrics if item in METRIC_ALIASES.values() or item in {"f1", "precision", "recall", "accuracy"}) < 2:
            continue
        rows: list[tuple[str, dict[str, float]]] = []
        cursor = index + 1
        while cursor < len(lines):
            cells = re.split(r"\s{2,}|\t", lines[cursor].strip())
            if len(cells) < 2:
                break
            numbers = [_parse_number(item) for item in cells[1:]]
            if any(item is None for item in numbers):
                break
            metrics_map = {
                name: value for name, value in zip(metrics, numbers)
                if name and value is not None
            }
            if metrics_map:
                rows.append((cells[0].strip(), metrics_map))
            cursor += 1
        if rows:
            condition = _nearby_condition(lines, index)
            if any(not item for item in metrics):
                tables.append(MetricTable(condition, tuple(), tuple(rows)))
            else:
                tables.append(MetricTable(condition, tuple(header_cells), tuple(rows)))
    return tables


def _nearby_condition(lines: list[str], header_index: int) -> str:
    for offset in range(1, 4):
        index = header_index - offset
        if index < 0:
            break
        line = lines[index].strip()
        if line:
            return line[:160]
    return ""


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [item.strip() for item in stripped.split("|")]


def _parse_number(value: str) -> float | None:
    match = NUMBER_RE.search(value.replace(",", ""))
    if not match:
        return None
    try:
        number = float(match.group(1))
        return number / 100.0 if "%" in value or "％" in value else number
    except ValueError:
        return None


def _valid_comparison_row(row: ComparisonRow, documents: list[Document]) -> bool:
    document = next((d for d in documents if d.id == row.document_id), None)
    if document is None or not row.quote or row.quote not in (document.content or document.summary):
        return False
    # Require the source span to bind every comparison dimension explicitly.
    if not row.subject or not row.metric or row.value is None or not row.condition or not row.unit:
        return False
    quote = re.sub(r"https?://\S+", "", row.quote).casefold()
    return (all(part.casefold() in quote for part in (row.subject, row.metric, row.condition, row.unit))
            and any(abs(float(n) - row.value) < 1e-9 for n in NUMBER_RE.findall(quote)))


def _scoped_money_supported(claim: str, corpus: str) -> bool:
    money = re.compile(r"(?:\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(美元|人民币|欧元))")
    amounts = [(float(m[1] or m[2]), m[3] or "美元") for m in money.finditer(claim)]
    if len(amounts) != 1:
        return False
    # Scope must include a named object, a price metric and a time condition.
    entities = re.findall(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9-]{2,}(?![A-Za-z])", claim)
    periods = re.findall(r"过去一年|过去一季度|今年|去年|20\d{2}年", claim)
    if not entities or not periods or not re.search(r"价格|成本|price|cost", claim, re.I):
        return False
    for sentence in split_sentences(re.sub(r"https?://\S+", "", corpus)):
        source_amounts = [(float(m[1] or m[2]), m[3] or "美元") for m in money.finditer(sentence)]
        if amounts != source_amounts or not LEADING_CLAIM_RE.search(sentence):
            continue
        if all(e.casefold() in sentence.casefold() for e in entities) and all(t in sentence for t in periods):
            return True
    return False
