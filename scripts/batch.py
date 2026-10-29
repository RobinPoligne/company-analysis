"""Fetch and analyse many companies at once.

python scripts/batch.py                S&P 500, skipping companies already done
python scripts/batch.py --limit 5      only the first 5 (good for a test run)
python scripts/batch.py --refresh      redo everything
python scripts/batch.py AAPL MSFT      only these tickers
"""
import argparse
import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

import calculate
import fetch_data
import scenarios

ROOT = Path(__file__).resolve().parent.parent
CO = ROOT / "site" / "data" / "companies"
AN = ROOT / "site" / "data" / "analysis"
IDX = ROOT / "site" / "data" / "index.json"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def sp500():
    html = requests.get(WIKI, headers={"User-Agent": "Mozilla/5.0 (personal research project)"}, timeout=30).text
    df = pd.read_html(io.StringIO(html))[0]
    return [s.replace(".", "-") for s in df["Symbol"].astype(str)]


def dump(path, obj):
    path.write_text(json.dumps(obj, separators=(",", ":")))


def analyse_one(t, config):
    cfg, basis = scenarios.company_config(t, config)
    result = calculate.analyse(t, cfg)
    if result.get("dcf"):
        result["dcf"]["growth_basis"] = basis
    elif basis.get("text", "").startswith("DCF not applied"):
        result["warnings"] = [basis["text"] if w.startswith("DCF skipped") else w for w in result["warnings"]]
    return result


def process(t, config):
    for attempt in range(3):
        try:
            data = fetch_data.fetch(t)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    if not data.get("name") or not data["financials"]["revenue"]:
        raise ValueError("no usable financial data")
    dump(CO / f"{t}.json", data)
    dump(AN / f"{t}.json", analyse_one(t, config))


def build_index():
    rows = []
    for f in sorted(AN.glob("*.json")):
        if not (CO / f.name).exists():
            continue
        a = json.loads(f.read_text())
        c = json.loads((CO / f.name).read_text())
        d = a.get("dcf")
        rows.append({
            "t": a["ticker"], "n": a["name"], "s": c.get("sector"),
            "p": c["price"].get("last"), "m": c["price"].get("market_cap"), "pe": c["price"].get("pe_ratio"),
            "sc": a["score"]["total"], "r": a["score"]["rating"],
            "u": d["scenarios"][d["score_scenario"]]["upside"] if d else None,
        })
    dump(IDX, rows)
    print(f"Index written: {len(rows)} companies")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--recalc", action="store_true", help="redo the calculations from saved data, no downloads")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between companies")
    args = ap.parse_args()

    config = json.loads((Path(__file__).resolve().parent / "config.json").read_text())
    CO.mkdir(parents=True, exist_ok=True)
    AN.mkdir(parents=True, exist_ok=True)
    if args.recalc:
        names = [x.upper() for x in args.tickers] or sorted(p.stem for p in CO.glob("*.json"))
        for t in names:
            try:
                dump(AN / f"{t}.json", analyse_one(t, config))
            except Exception as e:
                print(t, "failed:", str(e)[:80])
        build_index()
        return
    tickers = [x.upper() for x in args.tickers] or sp500()
    if args.limit:
        tickers = tickers[: args.limit]

    skipped = []
    for i, t in enumerate(tickers, 1):
        if not args.refresh and (AN / f"{t}.json").exists():
            continue
        try:
            process(t, config)
            print(f"[{i}/{len(tickers)}] {t} done")
        except Exception as e:
            skipped.append(t)
            print(f"[{i}/{len(tickers)}] {t} skipped: {str(e)[:80]}")
        time.sleep(args.pause)
    build_index()
    if skipped:
        print("Skipped:", ", ".join(skipped))


if __name__ == "__main__":
    main()
