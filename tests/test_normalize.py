import pandas as pd

from daily_a_share.normalize import normalize_snapshot


def test_normalize_tencent_units_and_code() -> None:
    raw = pd.DataFrame(
        [{
            "code": "sh600000", "name": "浦发银行", "zxj": "10.5", "zdf": "1.2",
            "hsl": "2", "lb": "1.3", "pe_ttm": "6", "pn": "0.6", "zsz": "1000",
            "ltsz": "900", "turnover": "12345", "zdf_d5": "2", "zdf_d10": "3",
            "zdf_d20": "4", "zdf_d60": "8", "zdf_y": "9",
        }]
    )
    result = normalize_snapshot(raw).iloc[0]
    assert result["code"] == "600000"
    assert result["market"] == "sh"
    assert result["amount_cny"] == 123_450_000
    assert result["market_cap_cny"] == 100_000_000_000


def test_normalize_sina_keeps_required_fields() -> None:
    raw = pd.DataFrame(
        [{"代码": "sz000001", "名称": "平安银行", "最新价": 11, "涨跌幅": -0.5, "成交额": 2e8}]
    )
    result = normalize_snapshot(raw).iloc[0]
    assert result["code"] == "000001"
    assert result["amount_cny"] == 2e8
    assert pd.isna(result["momentum_60d"])
