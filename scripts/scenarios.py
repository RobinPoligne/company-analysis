"""Build bear/base/bull DCF scenarios from each company's own history.

All the rules live in config.json under "scenario_rules".
"""
import copy
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "site" / "data" / "companies"


def history_growth(revenue):
    """Average yearly revenue growth over the last (up to) 3 years."""
    years = sorted(revenue)[-4:]
    if len(years) < 2:
        return None, 0
    a, b = revenue[years[0]], revenue[years[-1]]
    if not a or not b or a <= 0 or b <= 0:
        return None, 0
    n = len(years) - 1
    return (b / a) ** (1 / n) - 1, n


def company_config(ticker, config):
    """Return (config copy with this company's scenarios, explanation)."""
    raw = json.loads((DATA / f"{ticker}.json").read_text())
    r = config["scenario_rules"]
    cfg = copy.deepcopy(config)
    d = cfg["dcf"]

    if raw.get("sector") in r["no_dcf_sectors"]:
        d["scenarios"] = {}
        return cfg, {"text": "DCF not applied to banks and insurers."}

    hist, n = history_growth(raw["financials"]["revenue"])
    lr, pull = r["long_run_growth"], r["pull_toward_long_run"]
    blended = lr if hist is None else hist * (1 - pull) + lr * pull
    base = max(r["min_start_growth"], min(r["max_start_growth"], blended))
    hold = next(y for level, y in r["hold_years"] if base >= level)
    top = d["forecast_years"] - 1

    def scale(m):
        return base * m if base >= 0 else base / m

    d["scenarios"] = {
        "bear": {"starting_fcf_growth": round(scale(r["bear_multiple"]), 4), "high_growth_years": min(top, max(1, hold - 1))},
        "base": {"starting_fcf_growth": round(base, 4), "high_growth_years": min(top, hold)},
        "bull": {"starting_fcf_growth": round(scale(r["bull_multiple"]), 4), "high_growth_years": min(top, hold + 1)},
    }
    d["score_scenario"] = "base"

    if hist is None:
        text = f"No usable revenue history, so base growth is the long-run rate of {lr * 100:.0f}%."
    else:
        text = (f"Base growth: revenue grew {hist * 100:.0f}% a year over the last {n} years, "
                f"moved {pull * 100:.0f}% of the way toward {lr * 100:.0f}%"
                + (f", then limited to the range {r['min_start_growth'] * 100:.0f}% to {r['max_start_growth'] * 100:.0f}%" if blended != base else "")
                + f". Bear is {r['bear_multiple']}x the base and bull {r['bull_multiple']}x, "
                  f"with {hold} years of high growth in the base case.")
    return cfg, {"text": text, "history_growth": None if hist is None else round(hist, 4), "base_start_growth": round(base, 4)}
