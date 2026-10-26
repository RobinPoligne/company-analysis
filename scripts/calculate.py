"""Calculate ratios, DCF value and the transparent score for one company."""
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
    "risk": ["beta"],
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


def run_dcf(fcf0, wacc, g_term, years, g_start, net_debt, shares):
    """Growth fades in a straight line from g_start (year 1) to g_term (last year)."""
    if wacc <= g_term:
        raise ValueError("WACC must be higher than terminal growth")
    pv = 0.0
    fcf = fcf0
    for i in range(1, years + 1):
        g = g_start + (g_term - g_start) * (i - 1) / (years - 1)
        fcf *= 1 + g
        pv += fcf / (1 + wacc) ** i
    terminal_value = fcf * (1 + g_term) / (wacc - g_term)
    pv += terminal_value / (1 + wacc) ** years
    equity_value = pv - net_debt
    return pv, equity_value, equity_value / shares


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

    # ---- DCF ----
    d = config["dcf"]
    fcf = val("free_cash_flow")
    shares = price.get("shares_outstanding")
    last_price = price.get("last")
    net_debt = (val("total_debt") or 0) - (val("cash") or 0)
    dcf_out = None
    upside = None

    if fcf and fcf > 0 and shares and last_price:
        args = (d["forecast_years"], d["starting_fcf_growth"], net_debt, shares)
        ev, eq_value, per_share = run_dcf(fcf, d["wacc"], d["terminal_growth"], *args)
        upside = per_share / last_price - 1

        wacc_values = [d["wacc"] + x for x in (-0.02, -0.01, 0, 0.01, 0.02)]
        growth_values = [d["terminal_growth"] + x for x in (-0.01, -0.005, 0, 0.005, 0.01)]
        grid = [
            [run_dcf(fcf, w, g, *args)[2] for w in wacc_values]
            for g in growth_values
        ]
        dcf_out = {
            "assumptions": d,
            "base_fcf": fcf,
            "net_debt": net_debt,
            "enterprise_value": ev,
            "equity_value": eq_value,
            "fair_value_per_share": per_share,
            "current_price": last_price,
            "upside": upside,
            "sensitivity": {
                "wacc_values": wacc_values,
                "terminal_growth_values": growth_values,
                "grid": grid,
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
        "beta": price.get("beta"),
    }

    # ---- Score ----
    components = {}
    for name, keys in COMPONENTS.items():
        items = []
        for key in keys:
            lo, hi = config["ranges"][key]
            value = metrics[key]
            items.append({
                "metric": key,
                "value": value,
                "score": score_metric(value, lo, hi),
                "range": [lo, hi],
            })
        scores = [i["score"] for i in items if i["score"] is not None]
        components[name] = {
            "score": sum(scores) / len(scores) if scores else None,
            "weight": config["weights"][name],
            "metrics": items,
        }

    usable = {n: c for n, c in components.items() if c["score"] is not None}
    if not usable:
        raise ValueError("No score component could be calculated")
    total_weight = sum(c["weight"] for c in usable.values())
    for c in usable.values():
        c["contribution"] = c["score"] * c["weight"] / total_weight
    total = sum(c["contribution"] for c in usable.values())
    rating = next(label for label, threshold in config["ratings"] if total >= threshold)

    return clean({
        "ticker": ticker,
        "name": raw["name"],
        "fiscal_year": year,
        "price_as_of": price.get("as_of"),
        "metrics": metrics,
        "dcf": dcf_out,
        "score": {"total": total, "rating": rating, "components": components},
        "warnings": warnings,
    })


def pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


if __name__ == "__main__":
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "NVDA"
    config = json.loads((SCRIPTS_DIR / "config.json").read_text())
    result = analyse(ticker, config)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"{ticker}.json"
    out_file.write_text(json.dumps(result, indent=2))

    m = result["metrics"]
    print(f"Saved {out_file}")
    print(f"\n{result['name']} ({ticker}), fiscal year {result['fiscal_year']}")
    print(f"Revenue growth: {pct(m['revenue_growth'])}   Earnings growth: {pct(m['earnings_growth'])}")
    print(f"Operating margin: {pct(m['operating_margin'])}   Net margin: {pct(m['net_margin'])}")
    print(f"Current ratio: {m['current_ratio']}   Debt/equity: {m['debt_to_equity']}")
    print(f"P/E: {m['pe_ratio']}   Beta: {m['beta']}")
    if result["dcf"]:
        dcf = result["dcf"]
        print(f"DCF fair value: ${dcf['fair_value_per_share']:.2f} vs price "
              f"${dcf['current_price']:.2f} (upside {pct(dcf['upside'])})")
    print(f"\nScore: {result['score']['total']:.1f} / 10  ->  {result['score']['rating']}")
    for name, c in result["score"]["components"].items():
        s = "n/a" if c["score"] is None else f"{c['score']:.1f}"
        print(f"  {name:<18} {s:>5}   (weight {c['weight']})")
    for w in result["warnings"]:
        print(f"WARNING: {w}")