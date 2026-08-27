from __future__ import annotations

import re
import numpy as np
import pandas as pd


def _numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce") if column in frame else pd.Series(default, index=frame.index, dtype="float64")


def normalize_snapshot(raw: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=raw.index)
    if "code" in raw.columns:
        result["raw_code"] = raw["code"].astype(str).str.lower()
        result["name"] = raw["name"].astype(str).str.strip()
        mapping = {"price":"zxj","pct_change":"zdf","turnover_rate":"hsl","volume_ratio":"lb","pe_ttm":"pe_ttm","pb":"pn","market_cap_100m":"zsz","float_cap_100m":"ltsz","amount_10k":"turnover","momentum_5d":"zdf_d5","momentum_10d":"zdf_d10","momentum_20d":"zdf_d20","momentum_60d":"zdf_d60","momentum_ytd":"zdf_y"}
        for target, source in mapping.items():
            result[target] = _numeric(raw, source)
        result["amount_cny"] = result.pop("amount_10k") * 10_000
        result["market_cap_cny"] = result.pop("market_cap_100m") * 100_000_000
        result["float_cap_cny"] = result.pop("float_cap_100m") * 100_000_000
    elif "代码" in raw.columns:
        result["raw_code"] = raw["代码"].astype(str).str.lower()
        result["name"] = raw["名称"].astype(str).str.strip()
        result["price"], result["pct_change"], result["amount_cny"] = _numeric(raw,"最新价"), _numeric(raw,"涨跌幅"), _numeric(raw,"成交额")
        for column in ("turnover_rate","volume_ratio","pe_ttm","pb","market_cap_cny","float_cap_cny","momentum_5d","momentum_10d","momentum_20d","momentum_60d","momentum_ytd"):
            result[column] = np.nan
    else:
        raise ValueError(f"不认识的行情字段: {list(raw.columns)}")
    result["code"] = result["raw_code"].str.replace(r"^(sh|sz|bj)", "", regex=True)
    result["market"] = result["raw_code"].str.extract(r"^(sh|sz|bj)", expand=False)
    ordered = ["raw_code","code","market","name"] + [c for c in result.columns if c not in {"raw_code","code","market","name"}]
    return result[ordered].reset_index(drop=True)


def normalize_industries(raw: pd.DataFrame) -> pd.DataFrame:
    columns = ["name","pct_change","amount_cny","leader","leader_pct"]
    if raw.empty: return pd.DataFrame(columns=columns)
    if "板块" in raw.columns:
        out = pd.DataFrame({"name":raw["板块"].astype(str),"pct_change":_numeric(raw,"涨跌幅"),"amount_cny":_numeric(raw,"总成交额"),"leader":raw.get("股票名称",pd.Series("",index=raw.index)).astype(str),"leader_pct":_numeric(raw,"个股-涨跌幅")})
    elif "板块名称" in raw.columns:
        out = pd.DataFrame({"name":raw["板块名称"].astype(str),"pct_change":_numeric(raw,"涨跌幅"),"amount_cny":np.nan,"leader":raw.get("领涨股票",pd.Series("",index=raw.index)).astype(str),"leader_pct":_numeric(raw,"领涨股票-涨跌幅")})
    else: raise ValueError(f"不认识的行业字段: {list(raw.columns)}")
    return out.sort_values("pct_change",ascending=False,na_position="last").reset_index(drop=True)


def normalize_indices(raw: pd.DataFrame) -> pd.DataFrame:
    columns=["code","name","price","pct_change","amount_cny"]
    if raw.empty or "代码" not in raw: return pd.DataFrame(columns=columns)
    out=pd.DataFrame({"code":raw["代码"].astype(str).str.lower(),"name":raw["名称"].astype(str),"price":_numeric(raw,"最新价"),"pct_change":_numeric(raw,"涨跌幅"),"amount_cny":_numeric(raw,"成交额")})
    return out[out["code"].isin({"sh000001","sz399001","sz399006","sh000300","sh000905"})].reset_index(drop=True)


def normalize_global_quotes(raw: pd.DataFrame) -> pd.DataFrame:
    columns = ["code", "name", "price", "pct_change"]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    name_col = next((item for item in ("名称", "name") if item in raw.columns), None)
    price_col = next((item for item in ("最新价", "price") if item in raw.columns), None)
    pct_col = next((item for item in ("涨跌幅", "pct_change", "zdf") if item in raw.columns), None)
    if not name_col or not price_col or not pct_col:
        return pd.DataFrame(columns=columns)
    code_col = next((item for item in ("代码", "code") if item in raw.columns), None)
    out = pd.DataFrame({
        "code": raw[code_col].astype(str).str.lower().str.strip() if code_col else "",
        "name": raw[name_col].astype(str).str.strip(),
        "price": _numeric(raw, price_col),
        "pct_change": _numeric(raw, pct_col),
    })
    out = out[out["name"].ne("") & out["pct_change"].notna()]
    return out.drop_duplicates(subset=["name"]).reset_index(drop=True)


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
