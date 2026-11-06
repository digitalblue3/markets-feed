#!/usr/bin/env python3
"""Fetch live prices for Jamie's markets agent and write data/prices.json.

Crypto: CoinGecko public API (price, 24h/7d/30d change, volume, market cap, BTC dominance).
Shares and FX: Yahoo Finance chart API (latest regular price, extended-hours price, last 30 daily closes),
with Stooq as a fallback for the latest close.
Standard library only. Anything that fails keeps its last good value from the previous file, marked stale.
"""
import json, os, time, urllib.request, urllib.parse
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (markets-feed; GitHub Actions)", "Accept": "application/json,text/csv,*/*"}
OUT = "data/prices.json"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat() if ts else None


def get(url, tries=3, raw=False):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8")
                return body if raw else json.loads(body)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def crypto(ids_by_sym, errors):
    out = {}
    ids = ",".join(sorted(set(ids_by_sym.values()) | {"artificial-superintelligence-alliance"}))
    url = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&per_page=250&ids="
           + urllib.parse.quote(ids) + "&price_change_percentage=24h,7d,30d")
    try:
        rows = {r["id"]: r for r in get(url)}
    except Exception as e:  # noqa: BLE001
        errors.append(f"coingecko markets: {e}")
        return out, None
    for sym, cid in ids_by_sym.items():
        r = rows.get(cid) or (rows.get("artificial-superintelligence-alliance") if sym == "FET" else None)
        if not r or r.get("current_price") is None:
            errors.append(f"{sym}: not in CoinGecko response")
            continue
        out[sym] = {
            "price": r["current_price"],
            "chg24": r.get("price_change_percentage_24h_in_currency"),
            "chg7": r.get("price_change_percentage_7d_in_currency"),
            "chg30": r.get("price_change_percentage_30d_in_currency"),
            "vol": r.get("total_volume"),
            "mcap": r.get("market_cap"),
            "at": r.get("last_updated"),
            "src": "coingecko",
        }
    dom = None
    try:
        dom = round(get("https://api.coingecko.com/api/v3/global")["data"]["market_cap_percentage"]["btc"], 2)
    except Exception as e:  # noqa: BLE001
        errors.append(f"coingecko global: {e}")
    return out, dom


def yahoo(ticker):
    q = urllib.parse.quote(ticker)
    daily = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{q}?interval=1d&range=1mo")
    res = daily["chart"]["result"][0]
    meta = res["meta"]
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    hist = [{"date": datetime.fromtimestamp(t, timezone.utc).date().isoformat(), "close": round(c, 4)}
            for t, c in zip(ts, closes) if c is not None]
    d = {
        "price": meta.get("regularMarketPrice"),
        "at": iso(meta.get("regularMarketTime")),
        "prevClose": meta.get("chartPreviousClose") if len(hist) < 2 else hist[-2]["close"],
        "ccy": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "closes": hist[-30:],
        "src": "yahoo",
    }
    if d["price"] and d["prevClose"]:
        d["chg"] = round((d["price"] / d["prevClose"] - 1) * 100, 2)
    # Latest tick including pre-market and after-hours.
    try:
        intra = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{q}?interval=5m&range=1d&includePrePost=true")
        r = intra["chart"]["result"][0]
        its = r.get("timestamp") or []
        ic = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        pts = [(t, c) for t, c in zip(its, ic) if c is not None]
        if pts:
            t, c = pts[-1]
            reg = r["meta"].get("currentTradingPeriod", {}).get("regular", {})
            session = "regular"
            if reg.get("start") and t < reg["start"]:
                session = "pre"
            elif reg.get("end") and t >= reg["end"]:
                session = "post"
            if session != "regular":
                d["ext"] = {"price": round(c, 4), "at": iso(t), "session": session}
    except Exception:  # noqa: BLE001
        pass
    return d


def stooq(ticker):
    s = ticker.lower().replace(".l", ".uk") if ticker.endswith(".L") else ticker.lower() + ".us"
    csv = get(f"https://stooq.com/q/l/?s={s}&f=sd2t2ohlcv&h&e=csv", raw=True).strip().splitlines()
    head, row = csv[0].split(","), csv[1].split(",")
    r = dict(zip(head, row))
    if r.get("Close") in (None, "", "N/D"):
        raise RuntimeError("no data")
    return {"price": float(r["Close"]), "at": f"{r['Date']}T{r['Time']}", "src": "stooq"}


def main():
    cfg = json.load(open("symbols.json"))
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
        except Exception:  # noqa: BLE001
            prev = {}
    errors = []
    coins, dom = crypto(cfg["crypto"], errors)
    for sym in cfg["crypto"]:
        if sym not in coins and sym in prev.get("crypto", {}):
            coins[sym] = dict(prev["crypto"][sym], stale=True)
    stocks = {}
    for sym, t in {**cfg["stocks"], **cfg.get("fx", {})}.items():
        try:
            stocks[sym] = yahoo(t)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{sym} yahoo: {e}")
            try:
                stocks[sym] = stooq(t)
            except Exception as e2:  # noqa: BLE001
                errors.append(f"{sym} stooq: {e2}")
                old = {**prev.get("stocks", {}), **prev.get("fx", {})}.get(sym)
                if old:
                    stocks[sym] = dict(old, stale=True)
    fx = {k: stocks.pop(k) for k in list(stocks) if k in cfg.get("fx", {})}
    out = {"at": now_iso(), "btcDom": dom if dom is not None else prev.get("btcDom"),
           "crypto": coins, "stocks": stocks, "fx": fx, "errors": errors}
    os.makedirs("data", exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(f"{len(coins)} coins, {len(stocks)} shares, {len(fx)} fx, {len(errors)} errors")
    for e in errors:
        print("  ", e)


if __name__ == "__main__":
    main()
