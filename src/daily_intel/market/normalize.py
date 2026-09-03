from __future__ import annotations

import re

import pandas as pd
from rapidfuzz.fuzz import ratio


def _clean_url(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    return ""


def _title_key(title: str) -> str:
    return re.sub(r"[\s:：，,。．·\-—_]+", "", str(title or "")).casefold()


def _subject(title: str) -> str:
    text = str(title or "").strip()
    for sep in ("：", ":", "丨", "|"):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text[:4]


def _similar_title(left: str, right: str) -> bool:
    a, b = _title_key(left), _title_key(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 10 and shorter in longer:
        return True
    if _title_key(_subject(left)) != _title_key(_subject(right)):
        return False
    return ratio(a, b) >= 72


def _collapse_news(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per story and prefer the copy that already has a URL."""
    if frame.empty:
        return frame.reset_index(drop=True)
    ranked = frame.copy()
    ranked["url"] = ranked["url"].map(_clean_url)
    ranked["_has_url"] = ranked["url"].map(bool)
    ranked = ranked.sort_values("_has_url", ascending=False, kind="stable").reset_index(drop=True)
    keep: list[int] = []
    kept_titles: list[str] = []
    for index, title in ranked["title"].items():
        text = str(title or "")
        if any(_similar_title(text, previous) for previous in kept_titles):
            continue
        keep.append(int(index))
        kept_titles.append(text)
    return ranked.loc[keep].drop(columns=["_has_url"]).reset_index(drop=True)


def combine_news_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    parts = [normalize_news(frame) for frame in frames if frame is not None and not frame.empty]
    if not parts:
        return pd.DataFrame(columns=["title", "summary", "published_at", "url"])
    return _collapse_news(pd.concat(parts, ignore_index=True))


def normalize_news(raw: pd.DataFrame) -> pd.DataFrame:
    columns=["title","summary","published_at","url"]
    if raw.empty: return pd.DataFrame(columns=columns)
    if "title" in raw.columns:
        out = pd.DataFrame({
            "title": raw["title"].astype(str),
            "summary": raw["summary"].astype(str) if "summary" in raw.columns else "",
            "published_at": raw["published_at"].astype(str) if "published_at" in raw.columns else "",
            "url": raw["url"] if "url" in raw.columns else "",
        })
    elif "标题" in raw:
        out = pd.DataFrame({
            "title": raw["标题"].astype(str),
            "summary": raw.get("内容", pd.Series("", index=raw.index)).astype(str),
            "published_at": raw.get("发布时间", pd.Series("", index=raw.index)).astype(str),
            "url": raw.get("链接", pd.Series("", index=raw.index)),
        })
    elif "内容" in raw and "时间" in raw:
        content = raw["内容"].astype(str)
        title = content.str.extract(r"^【([^】]+)】", expand=False).fillna("")
        title = title.where(title.ne(""), content.str.slice(0, 42))
        out = pd.DataFrame({
            "title": title,
            "summary": content.str.replace(r"^【[^】]+】", "", regex=True).str.strip(),
            "published_at": raw["时间"].astype(str),
            "url": "",
        })
    else:
        raise ValueError(f"不认识的资讯字段: {list(raw.columns)}")
    out["url"] = out["url"].map(_clean_url)
    return _collapse_news(out)


def clean_text(value: str, limit: int = 150) -> str:
    value=re.sub(r"\s+"," ",str(value)).strip(); return value if len(value)<=limit else value[:limit-1]+"…"
