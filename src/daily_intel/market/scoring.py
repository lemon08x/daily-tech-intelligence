from __future__ import annotations

from typing import Any

import pandas as pd


def market_breadth(snapshot:pd.DataFrame)->dict[str,Any]:
    changes=snapshot["pct_change"].dropna();total=len(changes);adv=int(changes.gt(0).sum());dec=int(changes.lt(0).sum());ratio=adv/total if total else 0;median=float(changes.median()) if total else 0
    mood="偏强" if ratio>=.62 and median>.5 else "回暖" if ratio>=.53 else "分化" if ratio>=.45 else "偏弱"
    return {"total":int(total),"advancing":adv,"declining":dec,"flat":int(total-adv-dec),"advance_ratio":ratio,"median_change":median,"limit_up_like":int(changes.ge(9.5).sum()),"limit_down_like":int(changes.le(-9.5).sum()),"amount_cny":float(snapshot["amount_cny"].sum(skipna=True)),"mood":mood}
