from __future__ import annotations

import re

import pandas as pd


def combine_news_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    parts = [normalize_news(frame) for frame in frames if frame is not None and not frame.empty]
    if not parts:
        return pd.DataFrame(columns=["title", "summary", "published_at", "url"])
    return pd.concat(parts, ignore_index=True).drop_duplicates(subset=["title"], keep="first")


def normalize_news(raw: pd.DataFrame) -> pd.DataFrame:
    columns=["title","summary","published_at","url"]
    if raw.empty: return pd.DataFrame(columns=columns)
    if "title" in raw.columns:
        return pd.DataFrame({
            "title": raw["title"].astype(str),
            "summary": raw["summary"].astype(str) if "summary" in raw.columns else "",
            "published_at": raw["published_at"].astype(str) if "published_at" in raw.columns else "",
            "url": raw["url"].astype(str) if "url" in raw.columns else "",
        })
    if "标题" in raw:
        return pd.DataFrame({"title":raw["标题"].astype(str),"summary":raw.get("内容",pd.Series("",index=raw.index)).astype(str),"published_at":raw.get("发布时间",pd.Series("",index=raw.index)).astype(str),"url":raw.get("链接",pd.Series("",index=raw.index)).astype(str)})
    if "内容" in raw and "时间" in raw:
        content=raw["内容"].astype(str); title=content.str.extract(r"^【([^】]+)】",expand=False).fillna(""); title=title.where(title.ne(""),content.str.slice(0,42))
        return pd.DataFrame({"title":title,"summary":content.str.replace(r"^【[^】]+】","",regex=True).str.strip(),"published_at":raw["时间"].astype(str),"url":""})
    raise ValueError(f"不认识的资讯字段: {list(raw.columns)}")


def clean_text(value: str, limit: int = 150) -> str:
    value=re.sub(r"\s+"," ",str(value)).strip(); return value if len(value)<=limit else value[:limit-1]+"…"
