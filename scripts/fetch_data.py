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