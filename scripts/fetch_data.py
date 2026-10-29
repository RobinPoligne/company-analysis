import datetime
import json
import warnings
from pathlib import Path

import yfinance as yf

warnings.filterwarnings("ignore")

TICKER = "NVDA"
OUT_DIR = Path(__file__).resolve().parent.parent / "site" / "data" / "companies"


def row(df, name):
    """Return {year: value} for one statement row, oldest first ({} if missing)."""
    if df is None or df.empty or name not in df.index:
        print(f"  warning: '{name}' not found")
        return {}
    series = df.loc[name].dropna()
    return {str(col.year): float(val) for col, val in sorted(series.items())}


def ttm_fcf(t):
    """Sum of the last four quarters of free cash flow ({} if unavailable)."""
    df = t.quarterly_cashflow
    if df is None or df.empty or "Free Cash Flow" not in df.index:
        print("  warning: quarterly 'Free Cash Flow' not found")
        return {}
    series = df.loc["Free Cash Flow"].dropna().sort_index()
    if len(series) < 4:
        print(f"  warning: only {len(series)} quarters of free cash flow found")
        return {}
    last4 = series.iloc[-4:]
    return {
        "free_cash_flow": float(last4.sum()),
        "through": last4.index[-1].strftime("%Y-%m-%d"),
    }


def short_prices(t):
    """Recent prices for the 1D, 5D and 1M chart views (1D uses 5-minute points)."""
    out = {}
    for key, (period, interval) in {"1d": ("1d", "5m"), "5d": ("5d", "30m"), "1m": ("1mo", "1d")}.items():
        try:
            s = t.history(period=period, interval=interval)["Close"].dropna()
            out[key] = [[d.strftime("%Y-%m-%d %H:%M"), round(float(p), 2)] for d, p in s.items()]
        except Exception:
            out[key] = []
    return out


def fetch(ticker):
    t = yf.Ticker(ticker)
    info = t.info
    prices = t.history(period="5y", interval="1wk")["Close"].dropna()

    return {
        "ticker": ticker,
        "name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "price": {
            "last": info.get("currentPrice"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "high_52w": info.get("fiftyTwoWeekHigh"),
            "low_52w": info.get("fiftyTwoWeekLow"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "beta": info.get("beta"),
            "as_of": datetime.date.today().isoformat(),
        },
        "stats": {
            "forward_pe": info.get("forwardPE"),
            "ps_ratio": info.get("priceToSalesTrailing12Months"),
            "pb_ratio": info.get("priceToBook"),
            "enterprise_value": info.get("enterpriseValue"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
            "peg": info.get("trailingPegRatio"),
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),
            "gross_margin": info.get("grossMargins"),
            "eps": info.get("trailingEps"),
            "forward_eps": info.get("forwardEps"),
            "avg_50": info.get("fiftyDayAverage"),
            "avg_200": info.get("twoHundredDayAverage"),
            "target_mean": info.get("targetMeanPrice"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "rating": info.get("recommendationKey"),
            "target_low": info.get("targetLowPrice"),
            "target_high": info.get("targetHighPrice"),
            "prev_close": info.get("previousClose"),
            "day_low": info.get("dayLow"),
            "day_high": info.get("dayHigh"),
            "volume": info.get("volume"),
        },
        "profile": {
            "description": info.get("longBusinessSummary"),
            "employees": info.get("fullTimeEmployees"),
            "website": info.get("website"),
        },
        "financials": {
            "revenue": row(t.financials, "Total Revenue"),
            "net_income": row(t.financials, "Net Income"),
            "operating_income": row(t.financials, "Operating Income"),
            "free_cash_flow": row(t.cashflow, "Free Cash Flow"),
            "total_debt": row(t.balance_sheet, "Total Debt"),
            "equity": row(t.balance_sheet, "Stockholders Equity"),
            "cash": row(t.balance_sheet, "Cash Cash Equivalents And Short Term Investments"),
            "current_assets": row(t.balance_sheet, "Current Assets"),
            "current_liabilities": row(t.balance_sheet, "Current Liabilities"),
        },
        "ttm": ttm_fcf(t),
        "prices_short": short_prices(t),
        "prices_weekly": [
            [d.strftime("%Y-%m-%d"), round(float(p), 2)] for d, p in prices.items()
        ],
    }


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch(TICKER)
    out_file = OUT_DIR / f"{TICKER}.json"
    out_file.write_text(json.dumps(data, indent=2))
    print(f"Saved {out_file}")
    print(f"Name: {data['name']}")
    print(f"Years of revenue data: {list(data['financials']['revenue'])}")
    print(f"Weekly price points: {len(data['prices_weekly'])}")
    print(f"Trailing 12-month FCF: {data['ttm']}")
