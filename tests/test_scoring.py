import pandas as pd

from daily_a_share.scoring import market_breadth, percentile, screen_and_score, target_score


def test_percentile_direction_and_target_score() -> None:
    values = pd.Series([10.0, 20.0, 30.0])
    assert percentile(values).iloc[-1] == 1.0
    assert percentile(values, higher_is_better=False).iloc[0] == 1.0
    assert target_score(pd.Series([4.0]), target=4.0, spread=7.0).iloc[0] == 1.0


def test_screen_score_and_breadth() -> None:
    rows = []
    for index in range(6):
        rows.append(
            {
                "raw_code": f"sh60000{index}", "code": f"60000{index}", "market": "sh",
                "name": f"样本{index}", "price": 10 + index, "pct_change": index - 2,
                "amount_cny": 2e8 + index * 1e8, "turnover_rate": 2 + index / 2,
                "volume_ratio": 1 + index / 10, "pe_ttm": 10 + index, "pb": 1 + index / 10,
                "market_cap_cny": 1e10 + index * 1e9, "float_cap_cny": 8e9,
                "momentum_5d": index, "momentum_10d": index, "momentum_20d": index * 2,
                "momentum_60d": index * 4, "momentum_ytd": index * 5,
            }
        )
    frame = pd.DataFrame(rows)
    screening = {
        "include_beijing_exchange": False, "exclude_name_keywords": ["ST", "退", "N", "C"],
        "min_price": 3, "max_price": 300, "min_amount_cny": 1e8,
        "min_daily_change": -5, "max_daily_change": 7,
        "min_market_cap_cny": 5e9, "min_turnover_rate": 0.5, "max_turnover_rate": 15,
        "min_pe_ttm": 0, "max_pe_ttm": 100, "max_pb": 15,
        "min_momentum_60d": -15, "max_momentum_60d": 60,
    }
    weights = {"momentum": .3, "value": .2, "liquidity": .15, "activity": .15, "daily_strength": .1, "size": .1}
    result, universe = screen_and_score(frame, screening, weights)
    assert len(result) == 6
    assert universe["eligible"].all()
    assert result["score"].between(0, 100).all()
    breadth = market_breadth(frame)
    assert breadth["total"] == 6
    assert breadth["advancing"] == 3
