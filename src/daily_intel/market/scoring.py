from __future__ import annotations

from typing import Any
import numpy as np
import pandas as pd


def percentile(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    return pd.to_numeric(series,errors="coerce").rank(pct=True,ascending=higher_is_better).fillna(.5)


def target_score(series: pd.Series,target:float,spread:float)->pd.Series:
    return (1-(pd.to_numeric(series,errors="coerce")-target).abs()/spread).clip(0,1).fillna(.5)


def _optional_between(series:pd.Series,lower:float|None,upper:float|None)->pd.Series:
    if lower is not None and upper is not None: return series.isna()|series.between(lower,upper)
    if lower is not None: return series.isna()|series.ge(lower)
    if upper is not None: return series.isna()|series.le(upper)
    return pd.Series(True,index=series.index)


def screen_and_score(snapshot:pd.DataFrame,screening:dict[str,Any],weights:dict[str,float])->tuple[pd.DataFrame,pd.DataFrame]:
    frame=snapshot.copy(); eligible=frame["market"].isin(["sh","sz"])
    if screening.get("include_beijing_exchange",False): eligible|=frame["market"].eq("bj")
    eligible&=frame["code"].str.fullmatch(r"\d{6}",na=False)
    for keyword in screening.get("exclude_name_keywords",[]):
        eligible &= ~frame["name"].str.startswith(keyword,na=False) if keyword in {"N","C"} else ~frame["name"].str.contains(keyword,case=False,na=False)
    eligible&=frame["price"].between(screening["min_price"],screening["max_price"])
    eligible&=frame["pct_change"].between(screening["min_daily_change"],screening["max_daily_change"])
    eligible&=frame["amount_cny"].ge(screening["min_amount_cny"])
    eligible&=_optional_between(frame["market_cap_cny"],screening["min_market_cap_cny"],None)
    eligible&=_optional_between(frame["turnover_rate"],screening["min_turnover_rate"],screening["max_turnover_rate"])
    eligible&=_optional_between(frame["pe_ttm"],screening["min_pe_ttm"],screening["max_pe_ttm"])
    eligible&=_optional_between(frame["pb"],0,screening["max_pb"])
    eligible&=_optional_between(frame["momentum_60d"],screening["min_momentum_60d"],screening["max_momentum_60d"])
    candidates=frame[eligible].copy()
    if candidates.empty:return candidates,frame.assign(eligible=eligible)
    components={
        "momentum":.4*percentile(candidates["momentum_20d"])+.6*percentile(candidates["momentum_60d"]),
        "value":.55*percentile(candidates["pe_ttm"],False)+.45*percentile(candidates["pb"],False),
        "liquidity":percentile(np.log1p(candidates["amount_cny"])),
        "activity":.55*target_score(candidates["turnover_rate"],4,7)+.45*target_score(candidates["volume_ratio"],1.5,2),
        "daily_strength":target_score(candidates["pct_change"],2.5,7),
        "size":percentile(np.log1p(candidates["market_cap_cny"])),
    }
    candidates["score"]=0.0
    for name,values in components.items():candidates[f"factor_{name}"]=(values*100).round(1);candidates["score"]+=values*float(weights[name])*100
    candidates["score"]=candidates["score"].round(1);candidates["reasons"]=candidates.apply(explain_row,axis=1)
    return candidates.sort_values(["score","amount_cny"],ascending=False),frame.assign(eligible=eligible)


def explain_row(row:pd.Series)->str:
    reasons=[]
    if row.get("factor_momentum",0)>=70:reasons.append("中期趋势靠前")
    if row.get("factor_value",0)>=70:reasons.append("估值相对占优")
    if row.get("factor_liquidity",0)>=70:reasons.append("成交活跃")
    if row.get("factor_activity",0)>=70:reasons.append("量比与换手适中")
    if row.get("factor_size",0)>=70:reasons.append("市值稳定性较高")
    if not reasons:reasons.append(max((("趋势",row.get("factor_momentum",0)),("估值",row.get("factor_value",0)),("流动性",row.get("factor_liquidity",0))),key=lambda x:x[1])[0]+"因子相对较强")
    return "、".join(reasons[:3])


def market_breadth(snapshot:pd.DataFrame)->dict[str,Any]:
    changes=snapshot["pct_change"].dropna();total=len(changes);adv=int(changes.gt(0).sum());dec=int(changes.lt(0).sum());ratio=adv/total if total else 0;median=float(changes.median()) if total else 0
    mood="偏强" if ratio>=.62 and median>.5 else "回暖" if ratio>=.53 else "分化" if ratio>=.45 else "偏弱"
    return {"total":int(total),"advancing":adv,"declining":dec,"flat":int(total-adv-dec),"advance_ratio":ratio,"median_change":median,"limit_up_like":int(changes.ge(9.5).sum()),"limit_down_like":int(changes.le(-9.5).sum()),"amount_cny":float(snapshot["amount_cny"].sum(skipna=True)),"mood":mood}
