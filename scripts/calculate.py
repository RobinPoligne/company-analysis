"""Calculate ratios, DCF scenarios, reverse DCF and the transparent score."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
DATA_DIR = ROOT / "site" / "data" / "companies"
OUT_DIR = ROOT / "site" / "data" / "analysis"

# Which metrics make up each score component
COMPONENTS = {
    "growth": ["revenue_growth", "earnings_growth"],
    "profitability": ["operating_margin", "net_margin"],
    "financial_health": ["current_ratio", "debt_to_equity"],
    "valuation": ["pe_ratio", "dcf_upside"],
    "risk": ["beta", "volatility", "max_drawdown"],
}


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(series, year, prev):
    a, b = series.get(year), series.get(prev)
    if a is None or b is None or b <= 0:
        return None
    return a / b - 1


def score_metric(value, lo, hi):
    """Map a value to 0-10: lo scores 0, hi scores 10 (works if hi < lo)."""
    if value is None:
        return None
    s = (value - lo) / (hi - lo) * 10
    return max(0.0, min(10.0, s))


def price_risk(prices):
    """Annualised volatility of weekly returns and max drawdown (as a positive
    fraction, e.g. 0.66 = a 66% fall from peak) from weekly closing prices."""
    closes = [p for _, p in prices if p]
    if len(closes) < 52:
        return None, None
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    volatility = (variance ** 0.5) * (52 ** 0.5)
    peak = closes[0]
    max_drawdown = 0.0
    for c in closes:
        peak = max(peak, c)
        max_drawdown = max(max_drawdown, 1 - c / peak)
    return volatility, max_drawdown


def run_dcf(fcf0, wacc, g_term, years, g_start, hold_years, net_debt, shares):
    """Growth stays at g_start for hold_years, then fades in a straight line
    to g_term by the last forecast year."""
    if years < 2:
        raise ValueError("forecast_years must be at least 2")
    if not 0 <= hold_years < years:
        raise ValueError("high_growth_years must be between 0 and forecast_years - 1")
    if wacc <= g_term:
        raise ValueError("Discount rate must be higher than terminal growth")
    pv = 0.0
    fcf = fcf0
    for i in range(1, years + 1):
        if i <= hold_years:
            g = g_start
        else:
            g = g_start + (g_term - g_start) * (i - hold_years) / (years - hold_years)
        fcf *= 1 + g
        pv += fcf / (1 + wacc) ** i
    terminal_value = fcf * (1 + g_term) / (wacc - g_term)
    pv += terminal_value / (1 + wacc) ** years
    equity_value = pv - net_debt
    return pv, equity_value, equity_value / shares


def discount_rate(d, beta, warnings):
    """Return (rate, details). CAPM from beta, kept inside a min/max band."""
    fixed = d["fixed_wacc"]
    if d["wacc_method"] != "capm":
        return fixed, {"method": "fixed", "wacc": fixed}
    if beta is None:
        warnings.append("Beta missing: used the fixed discount rate")
        return fixed, {"method": "fixed (beta missing)", "wacc": fixed}
    beta_used = 0.67 * beta + 0.33 if d["use_adjusted_beta"] else beta
    cost_of_equity = d["risk_free_rate"] + beta_used * d["equity_risk_premium"]
    wacc = max(d["wacc_min"], min(d["wacc_max"], cost_of_equity))
    return wacc, {
        "method": "capm",
        "raw_beta": beta,
        "beta_used": beta_used,
        "cost_of_equity": cost_of_equity,
        "wacc": wacc,
        "clamped": wacc != cost_of_equity,
    }


def implied_growth(fcf0, wacc, g_term, years, hold_years, net_debt, shares, price):
    """Reverse DCF: starting growth at which the fair value equals the price."""
    def value(g):
        return run_dcf(fcf0, wacc, g_term, years, g, hold_years, net_debt, shares)[2]

    lo, hi = -0.10, 1.00
    if value(lo) > price or value(hi) < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if value(mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clean(obj):
    """Round all floats to 4 decimals so the JSON stays small and tidy."""
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def analyse(ticker, config):
    raw = json.loads((DATA_DIR / f"{ticker}.json").read_text())
    fin = raw["financials"]
    price = raw["price"]
    warnings = []

    year = sorted(fin["revenue"])[-1]
    prev = str(int(year) - 1)

    def val(name):
        return fin[name].get(year)

    revenue = val("revenue")
    equity = val("equity")

    debt_to_equity = safe_div(val("total_debt"), equity)
    if equity is None or equity <= 0:
        debt_to_equity = None
        warnings.append("Equity missing or not positive: debt/equity not meaningful")

    pe = price.get("pe_ratio")
    if pe is None or pe <= 0:
        pe = None
        warnings.append("P/E missing or negative: not scored")

    volatility, max_drawdown = price_risk(raw.get("prices_weekly", []))
    if volatility is None:
        warnings.append("Not enough price history for volatility and drawdown")

    # ---- Starting free cash flow: trailing 12 months if we have it ----
    ttm = raw.get("ttm") or {}
    if ttm.get("free_cash_flow") and ttm["free_cash_flow"] > 0:
        fcf = ttm["free_cash_flow"]
        fcf_source = f"trailing 4 quarters to {ttm.get('through')}"
    else:
        fcf = val("free_cash_flow")
        fcf_source = f"fiscal year {year}"
        warnings.append("Trailing free cash flow unavailable: used latest annual figure")

    # ---- DCF (bear / base / bull) plus reverse DCF ----
    d = config["dcf"]
    shares = price.get("shares_outstanding")
    last_price = price.get("last")
    net_debt = (val("total_debt") or 0) - (val("cash") or 0)
    dcf_out = None
    upside = None

    if fcf and fcf > 0 and shares and last_price:
        years = d["forecast_years"]
        wacc, rate_info = discount_rate(d, price.get("beta"), warnings)
        g_term = d["terminal_growth"]
        wacc_values = [wacc + x for x in (-0.02, -0.01, 0, 0.01, 0.02)]
        growth_values = [g_term + x for x in (-0.01, -0.005, 0, 0.005, 0.01)]

        scenarios = {}
        for name, s in d["scenarios"].items():
            g0, hold = s["starting_fcf_growth"], s["high_growth_years"]
            ev, eq_value, per_share = run_dcf(
                fcf, wacc, g_term, years, g0, hold, net_debt, shares
            )
            grid = [
                [run_dcf(fcf, w, g, years, g0, hold, net_debt, shares)[2]
                 for w in wacc_values]
                for g in growth_values
            ]
            scenarios[name] = {
                "starting_fcf_growth": g0,
                "high_growth_years": hold,
                "enterprise_value": ev,
                "equity_value": eq_value,
                "fair_value_per_share": per_share,
                "upside": per_share / last_price - 1,
                "sensitivity": {
                    "wacc_values": wacc_values,
                    "terminal_growth_values": growth_values,
                    "grid": grid,
                },
            }

        score_name = d["score_scenario"]
        upside = scenarios[score_name]["upside"]
        hold_for_reverse = d["scenarios"][score_name]["high_growth_years"]
        implied = implied_growth(
            fcf, wacc, g_term, years, hold_for_reverse, net_debt, shares, last_price
        )
        dcf_out = {
            "assumptions": {
                "discount_rate": rate_info,
                "terminal_growth": g_term,
                "forecast_years": years,
            },
            "base_fcf": fcf,
            "base_fcf_source": fcf_source,
            "net_debt": net_debt,
            "current_price": last_price,
            "score_scenario": score_name,
            "scenarios": scenarios,
            "reverse_dcf": {
                "implied_starting_fcf_growth": implied,
                "high_growth_years": hold_for_reverse,
            },
        }
    else:
        warnings.append("DCF skipped: needs positive FCF, shares outstanding and price")

    # ---- Metrics ----
    metrics = {
        "revenue_growth": growth(fin["revenue"], year, prev),
        "earnings_growth": growth(fin["net_income"], year, prev),
        "operating_margin": safe_div(val("operating_income"), revenue),
        "net_margin": safe_div(val("net_income"), revenue),
        "fcf_margin": safe_div(fcf, revenue),
        "current_ratio": safe_div(val("current_assets"), val("current_liabilities")),
        "debt_to_equity": debt_to_equity,
        "pe_ratio": pe,
        "dcf_upside": upside,