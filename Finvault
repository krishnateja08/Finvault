#!/usr/bin/env python3
"""
FinVault — Static Site Generator
=================================
Run this script to generate a fresh index.html with live market data baked in.
GitHub Actions runs this every hour automatically.

SETUP (run once):
  pip install requests yfinance

USAGE:
  python generate_site.py          # generates index.html in current folder
  python generate_site.py --dry    # prints data but doesn't write file

GITHUB ACTIONS:
  See .github/workflows/update.yml — runs this every hour, commits index.html
"""

import sys, os, datetime, json, time, argparse

# ── auto-install ──────────────────────────────────────────────
def install(pkg):
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for pkg in ["requests", "yfinance"]:
    try: __import__(pkg)
    except ImportError: install(pkg)

import requests
import yfinance as yf

# ─────────────────────────────────────────────────────────────
# CONFIG — tweak as needed
# ─────────────────────────────────────────────────────────────
REPO_RATE     = 6.25   # RBI repo rate % — update when RBI changes it
PREV_REPO     = 6.50   # previous rate  — to detect cutting/hiking cycle
HOME_LOAN     = 8.50   # avg home loan rate %
OUTPUT_FILE   = "index.html"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 FinVault/3.0", "Accept": "application/json"})

# ─────────────────────────────────────────────────────────────
# FETCH HELPERS
# ─────────────────────────────────────────────────────────────
def fetch(url, retries=3, timeout=15):
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < retries - 1:
                time.sleep(2 ** i)
            else:
                raise e

def pct(v): return f"{v:+.2f}%" if v is not None else "N/A"
def usd(v): return f"${v:,.2f}" if v is not None else "N/A"
def inr_fmt(v):
    if v is None: return "N/A"
    if v >= 1e7: return f"₹{v/1e7:.2f} Cr"
    if v >= 1e5: return f"₹{v/1e5:.2f} L"
    return f"₹{v:,.0f}"

# ─────────────────────────────────────────────────────────────
# MARKET DATA FETCHERS
# ─────────────────────────────────────────────────────────────
def fetch_crypto():
    """BTC, ETH prices + Fear & Greed index"""
    data = {}
    try:
        prices = fetch(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,pax-gold,silver"
            "&vs_currencies=usd,inr"
            "&include_24hr_change=true&include_7d_change=true&include_market_cap=true"
        )
        data["btc_usd"]    = prices["bitcoin"]["usd"]
        data["btc_inr"]    = prices["bitcoin"]["inr"]
        data["btc_24h"]    = prices["bitcoin"].get("usd_24h_change", 0) or 0
        data["btc_7d"]     = prices["bitcoin"].get("usd_7d_change", 0) or 0
        data["eth_usd"]    = prices["ethereum"]["usd"]
        data["eth_inr"]    = prices["ethereum"]["inr"]
        data["eth_24h"]    = prices["ethereum"].get("usd_24h_change", 0) or 0
        data["btc_mcap"]   = prices["bitcoin"].get("usd_market_cap", 0) or 0
        data["gold_usd"]   = prices["pax-gold"]["usd"]
        data["gold_inr"]   = prices["pax-gold"]["inr"]
        data["gold_24h"]   = prices["pax-gold"].get("usd_24h_change", 0) or 0
        data["silver_usd"] = prices["silver"]["usd"]
        data["silver_inr"] = prices["silver"]["inr"]
        data["silver_24h"] = prices["silver"].get("usd_24h_change", 0) or 0
    except Exception as e:
        print(f"  ⚠ CoinGecko prices failed: {e}")

    try:
        fg = fetch("https://api.alternative.me/fng/?limit=1")
        data["fear_score"] = int(fg["data"][0]["value"])
        data["fear_label"] = fg["data"][0]["value_classification"]
    except Exception as e:
        print(f"  ⚠ Fear & Greed failed: {e}")
        data["fear_score"] = None
        data["fear_label"] = "Unavailable"

    # Detailed gold/silver data for signals
    for coin_id, key in [("pax-gold", "gold_detail"), ("silver", "silver_detail")]:
        try:
            d = fetch(f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                      "?localization=false&tickers=false&community_data=false&developer_data=false")
            md = d["market_data"]
            data[key] = {
                "usd":      md["current_price"]["usd"],
                "inr":      md["current_price"]["inr"],
                "c24h":     md["price_change_percentage_24h"] or 0,
                "c7d":      md["price_change_percentage_7d"] or 0,
                "c30d":     md["price_change_percentage_30d"] or 0,
                "ath_chg":  md["ath_change_percentage"]["usd"] or 0,
            }
        except Exception as e:
            print(f"  ⚠ {coin_id} detail failed: {e}")
            data[key] = None
    return data

def fetch_stocks():
    """Nifty 50 + S&P 500 via yfinance"""
    data = {}
    for ticker, key in [("^NSEI", "nifty"), ("^GSPC", "sp500"), ("^IXIC", "nasdaq")]:
        try:
            hist = yf.Ticker(ticker).history(period="3mo")
            if hist.empty:
                data[key] = None
                continue
            closes   = hist["Close"]
            price    = float(closes.iloc[-1])
            prev     = float(closes.iloc[-2])
            chg      = (price - prev) / prev * 100
            ma20     = float(closes.iloc[-20:].mean())
            ma50     = float(closes.iloc[-50:].mean()) if len(closes) >= 50 else float(closes.mean())
            delta    = closes.diff()
            gain     = delta.clip(lower=0).rolling(14).mean()
            loss     = (-delta.clip(upper=0)).rolling(14).mean()
            rsi      = float((100 - 100 / (1 + gain / loss)).iloc[-1])
            data[key] = {"price": price, "prev": prev, "chg": chg,
                         "ma20": ma20, "ma50": ma50, "rsi": rsi}
        except Exception as e:
            print(f"  ⚠ {ticker} failed: {e}")
            data[key] = None
    return data

# ─────────────────────────────────────────────────────────────
# SIGNAL GENERATORS
# ─────────────────────────────────────────────────────────────
def sig_gold(d):
    if not d.get("gold_detail"):
        return {"signal":"HOLD","cls":"hold","reasons":["Live data unavailable — using macro defaults"],"metrics":{}}
    g = d["gold_detail"]
    below_ath = abs(g["ath_chg"])
    if below_ath > 20 and g["c30d"] > -5:
        s,c = "🟢 BUY","buy"
        reasons = [f"Gold is {below_ath:.1f}% below ATH — historically strong entry zone",
                   "30-day trend stabilising — not in free-fall",
                   "Ideal: Sovereign Gold Bonds (SGB) or Gold ETF for tax efficiency"]
    elif below_ath < 5:
        s,c = "🔴 WAIT","wait"
        reasons = [f"Gold near all-time high (only {below_ath:.1f}% below) — expensive",
                   "Set a price alert; enter on 10–15% correction"]
    else:
        s,c = "🟡 HOLD","hold"
        reasons = [f"Gold {below_ath:.1f}% below ATH — fair-value zone",
                   "Hold existing; accumulate gradually on dips"]
    if g["c24h"] < -1.5: reasons.append(f"24h dip {g['c24h']:.2f}% — short-term opportunity")
    if g["c7d"] > 5: reasons.append(f"7d rally +{g['c7d']:.1f}% — wait for pullback before adding")
    metrics = {"USD Price": usd(g["usd"]), "INR Price": inr_fmt(g["inr"]),
               "24h Change": pct(g["c24h"]), "30d Change": pct(g["c30d"]),
               "vs ATH": f"{below_ath:.1f}% below"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"CoinGecko (PAXG proxy)","context":"💡 SGB offers 2.5% annual interest on top of gold returns — best option for Indian investors."}

def sig_silver(d):
    if not d.get("silver_detail"):
        return {"signal":"HOLD","cls":"hold","reasons":["Live data unavailable"],"metrics":{}}
    g = d["silver_detail"]
    below_ath = abs(g["ath_chg"])
    if below_ath > 40:
        s,c = "🟢 BUY","buy"
        reasons = [f"Silver {below_ath:.1f}% below ATH — deeply undervalued",
                   "Silver outperforms gold in later bull-market stages",
                   "Industrial demand (EVs, solar panels) adds structural tailwind"]
    elif below_ath < 15:
        s,c = "🔴 WAIT","wait"
        reasons = [f"Silver near highs ({below_ath:.1f}% below ATH) — wait for pullback"]
    else:
        s,c = "🟡 HOLD","hold"
        reasons = [f"Silver {below_ath:.1f}% below ATH — gradual accumulation zone",
                   "Silver is 2–3× more volatile than gold — keep position sizing smaller"]
    metrics = {"USD Price": usd(g["usd"]), "INR Price": inr_fmt(g["inr"]),
               "24h Change": pct(g["c24h"]), "vs ATH": f"{below_ath:.1f}% below"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"CoinGecko","context":"💡 No SGB equivalent for silver — use ETFs on NSE for paper exposure."}

def sig_crypto(d):
    fs = d.get("fear_score")
    fl = d.get("fear_label","N/A")
    btc24 = d.get("btc_24h", 0)
    if fs is not None:
        if fs <= 25:   s,c = "🟢 BUY","buy"
        elif fs >= 75: s,c = "🔴 WAIT","wait"
        else:          s,c = "🟡 HOLD","hold"
    else:
        s,c = "🟡 HOLD","hold"
    reasons = []
    if fs is not None:
        if fs <= 25:   reasons += [f"Fear & Greed = {fs}/100 ({fl}) — extreme fear = best accumulation zone", "DCA over 4–8 weeks. Don't lump-sum."]
        elif fs >= 75: reasons += [f"Fear & Greed = {fs}/100 ({fl}) — euphoria, correction risk high", "Avoid FOMO. Consider booking 20–30% profits."]
        else:          reasons += [f"Fear & Greed = {fs}/100 ({fl}) — neutral zone. Continue DCA."]
    else:
        reasons = ["Fear & Greed unavailable — defaulting to neutral"]
    if btc24 < -5: reasons.append(f"BTC -{abs(btc24):.1f}% in 24h — short-term entry for believers")
    if btc24 > 10: reasons.append(f"BTC +{btc24:.1f}% today — FOMO zone, don't chase")
    reasons.append("Only invest what you can afford to lose — crypto is highly volatile")
    metrics = {}
    if d.get("btc_usd"): metrics["Bitcoin (BTC)"] = f"{usd(d['btc_usd'])}  ({pct(btc24)} 24h)"
    if d.get("eth_usd"): metrics["Ethereum (ETH)"] = f"{usd(d['eth_usd'])}  ({pct(d.get('eth_24h',0))} 24h)"
    if d.get("btc_mcap"): metrics["BTC Market Cap"] = f"${d['btc_mcap']/1e9:.0f}B"
    metrics["Fear & Greed"] = f"{fs}/100 — {fl}" if fs else "N/A"
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"CoinGecko + alternative.me (no API keys)","context":"💡 Never put more than 5–10% of portfolio in crypto. It's speculation, not investment."}

def sig_stocks(stocks):
    n = stocks.get("nifty")
    if not n:
        return {"signal":"HOLD","cls":"hold","reasons":["Nifty data unavailable"],"metrics":{}}
    ab20, ab50 = n["price"] > n["ma20"], n["price"] > n["ma50"]
    rsi = n["rsi"]
    if not ab50 and not ab20:
        s,c = "🟢 BUY","buy"
        reasons = ["Nifty below both 20 & 50-day MAs — correction zone",
                   "Historically: 10–20% corrections are excellent long-term entry points",
                   "Increase SIP or deploy lump-sum in 3–4 tranches over 4 weeks"]
    elif ab50 and ab20 and rsi > 70:
        s,c = "🔴 WAIT","wait"
        reasons = [f"RSI = {rsi:.0f} — overbought. Short-term pullback likely",
                   "Hold existing; avoid fresh lump-sum at these levels",
                   "Wait for RSI to cool below 60 before adding positions"]
    elif ab50 and ab20:
        s,c = "🟡 HOLD","hold"
        reasons = ["Nifty in healthy uptrend above both 20 & 50-day MAs",
                   "Continue monthly SIP — avoid large lump-sum at these highs"]
    else:
        s,c = "🟡 HOLD","hold"
        reasons = ["Mixed MA signals — wait for clear breakout above 50-day MA"]
    if rsi < 40: reasons.append(f"RSI = {rsi:.0f} — oversold. Buying pressure may build soon")
    if n["chg"] < -2: reasons.append(f"Today -{abs(n['chg']):.1f}% — potential short-term entry")
    metrics = {
        "Nifty 50": f"{n['price']:,.0f}  ({pct(n['chg'])} today)",
        "20-Day MA": f"{n['ma20']:,.0f}  ({'above ✅' if ab20 else 'below ⚠️'})",
        "50-Day MA": f"{n['ma50']:,.0f}  ({'above ✅' if ab50 else 'below ⚠️'})",
        "RSI (14)":  f"{rsi:.1f}  ({'Oversold 🟢' if rsi<40 else 'Overbought 🔴' if rsi>70 else 'Neutral 🟡'})"
    }
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Yahoo Finance via yfinance","context":"💡 Time in market beats timing the market. 5+ year horizon? Any dip is a buying opportunity."}

def sig_usstocks(stocks, d):
    sp = stocks.get("sp500")
    fs = d.get("fear_score")
    fl = d.get("fear_label","N/A")
    if not sp:
        return {"signal":"HOLD","cls":"hold","reasons":["S&P 500 data unavailable"],"metrics":{}}
    ab50 = sp["price"] > sp["ma50"]
    rsi = sp["rsi"]
    if fs is not None and fs <= 25:
        s,c = "🟢 BUY","buy"
        reasons = [f"Fear & Greed = {fs}/100 ({fl}) — extreme fear = historically best accumulation zone",
                   "S&P 500 corrections during extreme fear yield avg +18% over next 12 months historically",
                   "DCA into VOO / IVV. Don't try to catch the bottom."]
    elif (fs is not None and fs >= 75) or rsi > 75:
        s,c = "🔴 WAIT","wait"
        reasons = [f"Overbought: RSI={rsi:.0f}, Fear & Greed={fs if fs else 'N/A'}/100",
                   "Hold existing index funds; avoid new lump-sum entries"]
    elif ab50:
        s,c = "🟡 HOLD","hold"
        reasons = [f"S&P 500 above 50-day MA ({sp['ma50']:,.0f}) — uptrend intact",
                   "Continue regular DCA. Don't time the market."]
    else:
        s,c = "🟢 BUY","buy"
        reasons = [f"S&P 500 below 50-day MA ({sp['ma50']:,.0f}) — pullback zone, good long-term entry",
                   "Index fund investors: good accumulation opportunity"]
    reasons.append("For INR investors: Factor in USD/INR currency risk. Aim for 20–30% global allocation.")
    nq = stocks.get("nasdaq")
    metrics = {
        "S&P 500": f"{sp['price']:,.0f}  ({pct(sp['chg'])} today)",
        "NASDAQ":  f"{nq['price']:,.0f}" if nq else "N/A",
        "S&P 50-Day MA": f"{sp['ma50']:,.0f}  ({'above ✅' if ab50 else 'below ⚠️'})",
        "RSI (14)": f"{rsi:.1f}",
        "Fear & Greed": f"{fs}/100 — {fl}" if fs else "N/A"
    }
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Yahoo Finance + alternative.me Fear & Greed","context":"💡 For US-based investors: S&P 500 index funds (VOO, FXAIX) are the gold standard for long-term wealth."}

def sig_property():
    direction = "cutting" if REPO_RATE < PREV_REPO else "hiking" if REPO_RATE > PREV_REPO else "stable"
    if direction == "cutting":
        s,c = "🟢 BUY","buy"
        reasons = [f"RBI in rate-cutting cycle (repo {REPO_RATE}%) — home loan EMIs falling",
                   "Property demand typically rises 12–18 months after cuts begin",
                   "Lock in a home loan now before banks pass on the full rate cuts"]
    elif direction == "hiking":
        s,c = "🔴 WAIT","wait"
        reasons = [f"RBI hiking rates (repo {REPO_RATE}%) — home loans expensive",
                   "Wait 12–18 months for rate cycle to peak before buying"]
    else:
        s,c = "🟡 HOLD","hold"
        reasons = [f"Rates stable at {REPO_RATE}% — negotiate hard with builders"]
    reasons += ["Only buy if you plan to hold 7–10+ years",
                "REIT alternative: Mindspace/Brookfield (7–8% yield + liquidity)"]
    metrics = {"RBI Repo Rate": f"{REPO_RATE}%  ({direction.upper()})",
               "Avg Home Loan": f"~{HOME_LOAN}%", "Ideal Hold": "7–10 years min",
               "Appreciation": "5–12%/yr (city-dependent)"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":f"RBI rate cycle analysis. Repo rate: {REPO_RATE}%","context":"💡 Location beats timing in real estate. Buy right, not just cheap."}

def sig_fd():
    direction = "cutting" if REPO_RATE < PREV_REPO else "hiking" if REPO_RATE > PREV_REPO else "stable"
    if direction == "cutting":
        s,c = "🟡 HOLD","hold"
        reasons = [f"RBI cutting rates → FD rates WILL fall in 6–12 months. Lock in NOW.",
                   "Consider 3–5 year FDs before banks reduce rates",
                   "Small Finance Banks: AU, ESAF, Jana — 8.5–9.0% (DICGC insured ₹5L)",
                   "Avoid short-term FDs — renewals will be at lower rates"]
    elif direction == "hiking":
        s,c = "🟢 BUY","buy"
        reasons = ["Rates rising — roll short-term FDs to capture rate hikes"]
    else:
        s,c = "🟢 BUY","buy"
        reasons = ["Stable high rates — FD returns competitive with equity risk"]
    reasons.append("FD income is taxable at slab rate — compare with debt MFs for post-tax returns")
    metrics = {"Big Bank FD": "~7.0–7.5% p.a.", "Small Finance Bank": "~8.5–9.0%",
               "RBI Savings Bond": "~7.35% (govt-backed)", "Debt MF": "~7–8% (tax-efficient post 3yr)",
               "Repo Rate": f"{REPO_RATE}% ({direction})"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Public bank disclosures + RBI policy","context":"💡 FDs are taxable — for high earners, tax-free bonds or PPF may give better post-tax returns."}

# ─────────────────────────────────────────────────────────────
# HTML RENDERERS
# ─────────────────────────────────────────────────────────────
def render_signal_card(asset_name, sig, asset_emoji):
    reasons_html = "".join(
        f'<li><div class="reason-dot" style="background:{"var(--accent)" if sig["cls"]=="buy" else "var(--red)" if sig["cls"]=="wait" else "var(--gold)"};"></div><span>{r}</span></li>'
        for r in sig["reasons"]
    )
    metrics_html = "".join(
        f'<div class="metric-card"><div class="metric-val">{v}</div><div class="metric-lab">{k}</div></div>'
        for k, v in sig.get("metrics", {}).items()
    )
    verdict_cls = "good" if sig["cls"] == "buy" else "bad" if sig["cls"] == "wait" else "neutral"
    context = sig.get("context", "")
    source = sig.get("source", "")
    return f"""
<div class="signal-baked">
  <div class="signal-header">
    <div>
      <div class="signal-title">{asset_emoji} {asset_name}</div>
      <div class="signal-subtitle">Data fetched at build time — updated every hour via GitHub Actions</div>
    </div>
  </div>
  <div class="big-signal {sig['cls']}">{sig['signal']}</div>
  <div class="metrics-grid">{metrics_html}</div>
  <div style="margin-top:1.5rem;">
    <div style="font-size:0.75rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.7rem;">Why this signal?</div>
    <ul class="reasons-list">{reasons_html}</ul>
  </div>
  {f'<div class="verdict {verdict_cls}" style="margin-top:1rem;">{context}</div>' if context else ''}
  <div class="data-source">📡 {source} · <em>⚠️ Not financial advice. Educational use only. Consult a registered advisor before investing.</em></div>
</div>"""

def render_ticker(d, stocks):
    items = []
    if d.get("btc_usd"):
        chg = d["btc_24h"]
        cls = "up" if chg >= 0 else "dn"
        arrow = "▲" if chg >= 0 else "▼"
        items.append(f'<span class="tick-item"><span class="tick-name">BTC</span><span class="tick-val">${d["btc_usd"]:,.0f}</span><span class="tick-chg {cls}">{arrow} {abs(chg):.2f}%</span></span>')
    if d.get("eth_usd"):
        chg = d["eth_24h"]
        cls = "up" if chg >= 0 else "dn"
        arrow = "▲" if chg >= 0 else "▼"
        items.append(f'<span class="tick-item"><span class="tick-name">ETH</span><span class="tick-val">${d["eth_usd"]:,.0f}</span><span class="tick-chg {cls}">{arrow} {abs(chg):.2f}%</span></span>')
    if d.get("gold_usd"):
        chg = d["gold_24h"]
        cls = "up" if chg >= 0 else "dn"
        arrow = "▲" if chg >= 0 else "▼"
        items.append(f'<span class="tick-item"><span class="tick-name">GOLD</span><span class="tick-val">${d["gold_usd"]:,.0f}/oz</span><span class="tick-chg {cls}">{arrow} {abs(chg):.2f}%</span></span>')
    if d.get("silver_usd"):
        chg = d["silver_24h"]
        cls = "up" if chg >= 0 else "dn"
        arrow = "▲" if chg >= 0 else "▼"
        items.append(f'<span class="tick-item"><span class="tick-name">SILVER</span><span class="tick-val">${d["silver_usd"]:,.2f}/oz</span><span class="tick-chg {cls}">{arrow} {abs(chg):.2f}%</span></span>')
    n = stocks.get("nifty")
    if n:
        cls = "up" if n["chg"] >= 0 else "dn"
        arrow = "▲" if n["chg"] >= 0 else "▼"
        items.append(f'<span class="tick-item"><span class="tick-name">NIFTY 50</span><span class="tick-val">{n["price"]:,.0f}</span><span class="tick-chg {cls}">{arrow} {abs(n["chg"]):.2f}%</span></span>')
    sp = stocks.get("sp500")
    if sp:
        cls = "up" if sp["chg"] >= 0 else "dn"
        arrow = "▲" if sp["chg"] >= 0 else "▼"
        items.append(f'<span class="tick-item"><span class="tick-name">S&P 500</span><span class="tick-val">{sp["price"]:,.0f}</span><span class="tick-chg {cls}">{arrow} {abs(sp["chg"]):.2f}%</span></span>')
    if d.get("fear_score") is not None:
        fs = d["fear_score"]
        fl = d["fear_label"]
        cls = "up" if fs <= 40 else "dn" if fs >= 60 else ""
        items.append(f'<span class="tick-item"><span class="tick-name">FEAR & GREED</span><span class="tick-val {cls}">{fs}/100</span><span class="tick-chg" style="color:var(--muted)">{fl}</span></span>')
    # Duplicate for seamless scroll animation
    all_items = items + items
    return "".join(all_items)

# ─────────────────────────────────────────────────────────────
# HTML TEMPLATE — full page with all baked-in signals
# ─────────────────────────────────────────────────────────────
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FinVault — Your Complete Financial Command Center</title>
<meta name="description" content="Free financial calculators + live market signals. SIP, FIRE, EMI, tax, and BUY/HOLD/WAIT for gold, crypto, stocks, property.">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#0a0d12;--surface:#111520;--card:#161c2d;--border:#1e2740;
    --accent:#00e5b0;--accent2:#ff6b35;--accent3:#7c6aff;
    --gold:#ffd166;--red:#ff4d6d;--text:#e8eaf2;--muted:#6b7499;
    --font-display:'Syne',sans-serif;--font-mono:'DM Mono',monospace;--font-serif:'Instrument Serif',serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:var(--font-display);min-height:100vh;overflow-x:hidden}
  nav{position:fixed;top:0;left:0;right:0;z-index:100;background:rgba(10,13,18,0.9);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:0 2rem;display:flex;align-items:center;justify-content:space-between;height:64px}
  .logo{font-size:1.4rem;font-weight:800;letter-spacing:-0.03em;background:linear-gradient(135deg,var(--accent),var(--accent3));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .nav-tabs{display:flex;gap:0.25rem;list-style:none;overflow-x:auto;padding:0.5rem 0}
  .nav-tabs li{white-space:nowrap}
  .nav-tabs button{background:none;border:none;color:var(--muted);font-family:var(--font-display);font-size:0.8rem;font-weight:600;padding:0.4rem 0.9rem;border-radius:6px;cursor:pointer;transition:all 0.2s;letter-spacing:0.02em}
  .nav-tabs button:hover{color:var(--text);background:var(--surface)}
  .nav-tabs button.active{color:var(--accent);background:rgba(0,229,176,0.1)}
  .ticker-wrap{overflow:hidden;background:var(--surface);border-bottom:1px solid var(--border);padding:0.5rem 0;font-family:var(--font-mono);font-size:0.75rem}
  .ticker{display:flex;gap:3rem;animation:ticker 40s linear infinite;white-space:nowrap}
  @keyframes ticker{from{transform:translateX(0)}to{transform:translateX(-50%)}}
  .tick-item{display:flex;gap:0.5rem}
  .tick-name{color:var(--muted)}
  .tick-val{font-weight:500}
  .tick-chg.up{color:var(--accent)}
  .tick-chg.dn{color:var(--red)}
  .main{padding:3rem 2rem;max-width:1200px;margin:0 auto}
  .section{display:none}
  .section.active{display:block;animation:fadeIn 0.4s ease}
  @keyframes fadeIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
  .section-title{font-size:1.8rem;font-weight:800;letter-spacing:-0.03em;margin-bottom:0.4rem}
  .section-sub{color:var(--muted);font-size:0.9rem;margin-bottom:2.5rem;font-weight:400}
  .calc-grid{display:grid;gap:1.5rem}
  .calc-grid-2{grid-template-columns:1fr 1fr}
  .calc-grid-3{grid-template-columns:1fr 1fr 1fr}
  .calc-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:1.8rem;transition:border-color 0.2s}
  .calc-card:hover{border-color:rgba(0,229,176,0.2)}
  .calc-card h3{font-size:1rem;font-weight:700;margin-bottom:0.3rem;display:flex;align-items:center;gap:0.6rem}
  .calc-card h3 .icon{font-size:1.2rem}
  .calc-card .desc{font-size:0.8rem;color:var(--muted);margin-bottom:1.5rem;font-weight:400}
  .form-row{display:grid;gap:1rem;margin-bottom:1rem}
  .form-row-2{grid-template-columns:1fr 1fr}
  .form-row-3{grid-template-columns:1fr 1fr 1fr}
  label{display:block;font-size:0.75rem;font-weight:600;color:var(--muted);margin-bottom:0.4rem;letter-spacing:0.04em;text-transform:uppercase}
  input[type=range]{width:100%;accent-color:var(--accent);cursor:pointer}
  input[type=number],input[type=text],select{width:100%;background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:var(--font-mono);font-size:0.9rem;padding:0.6rem 0.9rem;border-radius:8px;outline:none;transition:border-color 0.2s}
  input:focus,select:focus{border-color:var(--accent)}
  .range-val{font-family:var(--font-mono);font-size:0.85rem;color:var(--accent);font-weight:500}
  .range-group{margin-bottom:1rem}
  .range-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem}
  .result-box{background:linear-gradient(135deg,rgba(0,229,176,0.08),rgba(124,106,255,0.08));border:1px solid rgba(0,229,176,0.2);border-radius:12px;padding:1.2rem 1.5rem;margin-top:1.2rem}
  .result-main{font-size:2rem;font-weight:800;font-family:var(--font-mono);color:var(--accent)}
  .result-label{font-size:0.75rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.3rem}
  .result-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.8rem;margin-top:0.8rem}
  .result-item{background:rgba(255,255,255,0.03);border-radius:8px;padding:0.8rem}
  .result-item-val{font-family:var(--font-mono);font-size:1rem;font-weight:600}
  .result-item-lab{font-size:0.7rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:0.04em;margin-top:0.2rem}
  .positive{color:var(--accent)}.negative{color:var(--red)}.warning{color:var(--gold)}.purple{color:var(--accent3)}
  .verdict{margin-top:1rem;padding:0.9rem 1.2rem;border-radius:10px;font-size:0.85rem;font-weight:600;border-left:3px solid}
  .verdict.good{background:rgba(0,229,176,0.08);border-color:var(--accent);color:var(--accent)}
  .verdict.bad{background:rgba(255,77,109,0.08);border-color:var(--red);color:var(--red)}
  .verdict.neutral{background:rgba(255,209,102,0.08);border-color:var(--gold);color:var(--gold)}
  .btn{background:var(--accent);color:#000;font-family:var(--font-display);font-size:0.85rem;font-weight:700;padding:0.7rem 1.5rem;border:none;border-radius:8px;cursor:pointer;letter-spacing:0.02em;transition:all 0.2s;margin-top:0.5rem}
  .btn:hover{background:#00c49a;transform:translateY(-1px)}
  .btn-outline{background:transparent;color:var(--accent);border:1px solid var(--accent)}
  .btn-outline:hover{background:rgba(0,229,176,0.1);transform:none}
  .mini-chart{margin-top:1rem;height:80px;position:relative}
  .bar-chart{display:flex;align-items:flex-end;gap:3px;height:70px}
  .bar-chart .bar{flex:1;background:linear-gradient(to top,var(--accent3),var(--accent));border-radius:3px 3px 0 0;transition:height 0.4s ease;min-height:3px}
  .table-wrap{overflow-x:auto;margin-top:1rem;max-height:300px;overflow-y:auto}
  table{width:100%;border-collapse:collapse;font-size:0.8rem}
  th{background:var(--surface);color:var(--muted);font-size:0.7rem;letter-spacing:0.06em;text-transform:uppercase;padding:0.6rem 0.8rem;text-align:right;position:sticky;top:0}
  th:first-child{text-align:left}
  td{padding:0.5rem 0.8rem;border-bottom:1px solid var(--border);text-align:right;font-family:var(--font-mono)}
  td:first-child{text-align:left}
  tr:hover td{background:rgba(255,255,255,0.02)}
  .scenario-row{display:flex;gap:1rem;align-items:stretch;flex-wrap:wrap}
  .scenario-card{flex:1;min-width:200px;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.2rem;text-align:center}
  .scenario-card.highlight{border-color:var(--accent);background:rgba(0,229,176,0.05)}
  .scenario-label{font-size:0.7rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--muted);margin-bottom:0.6rem}
  .scenario-val{font-family:var(--font-mono);font-size:1.5rem;font-weight:700}
  .scenario-sub{font-size:0.75rem;color:var(--muted);margin-top:0.3rem}
  .score-ring{display:flex;align-items:center;justify-content:center;gap:2rem;flex-wrap:wrap;margin:1.5rem 0}
  .ring-wrap{position:relative;width:120px;height:120px}
  .ring-wrap svg{transform:rotate(-90deg)}
  .ring-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}
  .ring-num{font-family:var(--font-mono);font-size:1.4rem;font-weight:700}
  .ring-lab{font-size:0.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em}
  .progress-row{margin-bottom:1rem}
  .progress-header{display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:0.4rem}
  .progress-bar{height:6px;background:var(--surface);border-radius:100px;overflow:hidden}
  .progress-fill{height:100%;border-radius:100px;transition:width 0.6s ease}
  .inner-tabs{display:flex;gap:0.5rem;margin-bottom:2rem;flex-wrap:wrap}
  .inner-tab{background:var(--surface);border:1px solid var(--border);color:var(--muted);font-family:var(--font-display);font-size:0.8rem;font-weight:600;padding:0.5rem 1rem;border-radius:8px;cursor:pointer;transition:all 0.2s;letter-spacing:0.02em}
  .inner-tab:hover{color:var(--text)}
  .inner-tab.active{background:rgba(0,229,176,0.1);border-color:var(--accent);color:var(--accent)}
  .inner-section{display:none}
  .inner-section.active{display:block;animation:fadeIn 0.3s ease}
  @media(max-width:768px){.calc-grid-2,.calc-grid-3{grid-template-columns:1fr}.form-row-2,.form-row-3{grid-template-columns:1fr}.nav-tabs button{font-size:0.7rem;padding:0.35rem 0.6rem}.hero h1{font-size:2rem}}
  .two-col{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
  @media(max-width:768px){.two-col{grid-template-columns:1fr}}
  .stats-bar{display:flex;justify-content:center;gap:3rem;padding:1.5rem 2rem;border-top:1px solid var(--border);border-bottom:1px solid var(--border);background:var(--surface);flex-wrap:wrap}
  .stat{text-align:center}
  .stat-num{font-size:1.6rem;font-weight:800;color:var(--accent);font-family:var(--font-mono)}
  .stat-label{font-size:0.72rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-top:0.2rem}
  .hero{padding:120px 2rem 60px;text-align:center;position:relative;overflow:hidden}
  .hero::before{content:'';position:absolute;top:-200px;left:50%;transform:translateX(-50%);width:800px;height:800px;background:radial-gradient(circle,rgba(0,229,176,0.06) 0%,transparent 70%);pointer-events:none}
  .hero-badge{display:inline-block;background:rgba(0,229,176,0.1);border:1px solid rgba(0,229,176,0.3);color:var(--accent);font-size:0.75rem;font-weight:600;padding:0.35rem 1rem;border-radius:100px;letter-spacing:0.08em;margin-bottom:1.5rem;text-transform:uppercase}
  .hero h1{font-size:clamp(2.5rem,6vw,5rem);font-weight:800;line-height:1.05;letter-spacing:-0.04em;margin-bottom:1rem}
  .hero h1 em{font-family:var(--font-serif);font-style:italic;background:linear-gradient(135deg,var(--gold),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .hero p{font-size:1.05rem;color:var(--muted);max-width:560px;margin:0 auto 2.5rem;line-height:1.7;font-weight:400}
  select option{background:var(--card)}
  /* ADVISOR BAKED STYLES */
  .advisor-tabs{display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:2rem}
  .advisor-tab{background:var(--surface);border:2px solid var(--border);color:var(--muted);font-family:var(--font-display);font-size:0.85rem;font-weight:600;padding:0.6rem 1.2rem;border-radius:10px;cursor:pointer;transition:all 0.2s}
  .advisor-tab:hover{color:var(--text);border-color:rgba(0,229,176,0.3)}
  .advisor-tab.active{color:var(--accent);background:rgba(0,229,176,0.08);border-color:var(--accent)}
  .signal-baked{display:none}
  .signal-baked.active{display:block;animation:fadeIn 0.4s ease}
  .signal-header{display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;flex-wrap:wrap}
  .signal-title{font-size:1.4rem;font-weight:800}
  .signal-subtitle{font-size:0.82rem;color:var(--muted);margin-top:0.2rem}
  .big-signal{display:inline-flex;align-items:center;gap:0.8rem;padding:1rem 2rem;border-radius:12px;font-size:1.6rem;font-weight:800;letter-spacing:-0.02em;margin:1.5rem 0}
  .big-signal.buy{background:rgba(0,229,176,0.12);color:var(--accent);border:2px solid rgba(0,229,176,0.3)}
  .big-signal.wait{background:rgba(255,77,109,0.12);color:var(--red);border:2px solid rgba(255,77,109,0.3)}
  .big-signal.hold{background:rgba(255,209,102,0.12);color:var(--gold);border:2px solid rgba(255,209,102,0.3)}
  .metrics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin:1.5rem 0}
  .metric-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1rem}
  .metric-val{font-family:var(--font-mono);font-size:1.2rem;font-weight:700}
  .metric-lab{font-size:0.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-top:0.3rem}
  .reasons-list{list-style:none;margin:1rem 0}
  .reasons-list li{padding:0.6rem 0;border-bottom:1px solid var(--border);font-size:0.85rem;display:flex;gap:0.7rem;align-items:flex-start}
  .reasons-list li:last-child{border-bottom:none}
  .reason-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0}
  .data-source{font-size:0.7rem;color:var(--muted);margin-top:1.5rem;padding-top:1rem;border-top:1px solid var(--border)}
  .disclaimer-banner{background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.2);border-radius:10px;padding:0.8rem 1.2rem;color:var(--gold);font-size:0.75rem;margin-bottom:2rem;display:flex;align-items:center;gap:0.7rem}
  .update-badge{display:inline-flex;align-items:center;gap:0.5rem;background:rgba(0,229,176,0.08);border:1px solid rgba(0,229,176,0.2);border-radius:20px;padding:0.35rem 1rem;font-family:var(--font-mono);font-size:0.72rem;color:var(--accent);margin-bottom:1.5rem}
  .update-dot{width:7px;height:7px;border-radius:50%;background:var(--accent);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.3;transform:scale(0.7)}}
  .tag{display:inline-block;font-size:0.65rem;font-weight:700;padding:0.2rem 0.5rem;border-radius:4px;text-transform:uppercase;letter-spacing:0.06em;margin-left:0.5rem}
  .tag-green{background:rgba(0,229,176,0.15);color:var(--accent)}
  .tag-red{background:rgba(255,77,109,0.15);color:var(--red)}
  .tag-gold{background:rgba(255,209,102,0.15);color:var(--gold)}
  /* PROFILE / DASHBOARD */
  .profile-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
  @media(max-width:768px){.profile-grid{grid-template-columns:1fr}}
  .dash-status{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin-bottom:2rem}
  .dash-item{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.2rem;display:flex;align-items:center;gap:1rem}
  .dash-icon{font-size:1.8rem}
  .dash-label{font-size:0.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em}
  .dash-val{font-family:var(--font-mono);font-size:1.1rem;font-weight:700;margin-top:0.2rem}
  .dash-tag{font-size:0.65rem;font-weight:700;padding:0.15rem 0.5rem;border-radius:4px;margin-top:0.3rem;display:inline-block}
  .dash-tag.ok{background:rgba(0,229,176,0.15);color:var(--accent)}
  .dash-tag.warn{background:rgba(255,209,102,0.15);color:var(--gold)}
  .dash-tag.bad{background:rgba(255,77,109,0.15);color:var(--red)}
  .rec-list{list-style:none;margin:0}
  .rec-item{display:flex;align-items:flex-start;gap:0.8rem;padding:0.9rem 0;border-bottom:1px solid var(--border);font-size:0.88rem}
  .rec-item:last-child{border-bottom:none}
  .rec-num{width:24px;height:24px;border-radius:50%;background:var(--accent);color:#000;font-size:0.7rem;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
</style>
</head>
<body>

<nav>
  <div class="logo">FinVault</div>
  <ul class="nav-tabs">
    <li><button class="active" onclick="showSection('home')">🏠 Home</button></li>
    <li><button onclick="showSection('invest')">📈 Invest</button></li>
    <li><button onclick="showSection('retire')">🏖️ Retire</button></li>
    <li><button onclick="showSection('stocks')">📉 Stocks</button></li>
    <li><button onclick="showSection('loans')">🏦 Loans</button></li>
    <li><button onclick="showSection('tax')">🧾 Tax</button></li>
    <li><button onclick="showSection('health')">💯 Health Score</button></li>
    <li><button onclick="showSection('advisor')">🔮 Market Advisor</button></li>
    <li><button onclick="showSection('profile')">👤 Profile</button></li>
    <li><button onclick="showSection('dashboard')">📊 Dashboard</button></li>
  </ul>
</nav>

<div style="margin-top:64px;" class="ticker-wrap">
  <div class="ticker">__TICKER_HTML__</div>
</div>

<div class="main">

<!-- HOME -->
<div id="home" class="section active">
  <div class="hero" style="padding:2rem 0 3rem;">
    <div class="hero-badge">Complete Financial Intelligence Platform</div>
    <h1>Master Your <em>Money,</em><br>Every Scenario</h1>
    <p>15+ advanced calculators + live market signals updated hourly. Free forever.</p>
    <div class="update-badge"><div class="update-dot"></div>Last updated: __UPDATED_AT__ UTC</div>
  </div>
  <div class="stats-bar" style="border-radius:12px;margin-bottom:2rem;">
    <div class="stat"><div class="stat-num">15+</div><div class="stat-label">Calculators</div></div>
    <div class="stat"><div class="stat-num">7</div><div class="stat-label">Live Signals</div></div>
    <div class="stat"><div class="stat-num">∞</div><div class="stat-label">Scenarios</div></div>
    <div class="stat"><div class="stat-num">100%</div><div class="stat-label">Free</div></div>
  </div>
  <div class="calc-grid calc-grid-3">
    <div class="calc-card" onclick="showSection('invest')" style="cursor:pointer;"><h3><span class="icon">📈</span>Investment Suite</h3><div class="desc">SIP, Lump Sum, Goal Planner, ROI Analyzer</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">Open →</div></div>
    <div class="calc-card" onclick="showSection('retire')" style="cursor:pointer;"><h3><span class="icon">🏖️</span>Retirement Planner</h3><div class="desc">FIRE number, corpus planning, withdrawal strategy</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">Open →</div></div>
    <div class="calc-card" onclick="showSection('stocks')" style="cursor:pointer;"><h3><span class="icon">📉</span>Stock Risk Lab</h3><div class="desc">Crash simulator, stop-loss, portfolio stress test</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">Open →</div></div>
    <div class="calc-card" onclick="showSection('loans')" style="cursor:pointer;"><h3><span class="icon">🏦</span>Loan &amp; EMI Center</h3><div class="desc">EMI calculator, amortization table, prepayment savings</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">Open →</div></div>
    <div class="calc-card" onclick="showSection('tax')" style="cursor:pointer;"><h3><span class="icon">🧾</span>Tax Optimizer</h3><div class="desc">Income tax, capital gains, old vs new regime comparison</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">Open →</div></div>
    <div class="calc-card" onclick="showSection('health')" style="cursor:pointer;"><h3><span class="icon">💯</span>Financial Health Score</h3><div class="desc">Complete financial fitness assessment with action plan</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">Open →</div></div>
    <div class="calc-card" onclick="showSection('advisor')" style="cursor:pointer;border-color:rgba(255,209,102,0.3);"><h3><span class="icon">🔮</span>Market Advisor</h3><div class="desc">Live BUY / WAIT / HOLD signals — data baked in, updated hourly</div><div class="btn" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;background:var(--gold);color:#000;">View Signals →</div></div>
    <div class="calc-card" onclick="showSection('profile')" style="cursor:pointer;border-color:rgba(124,106,255,0.3);"><h3><span class="icon">👤</span>Financial Profile</h3><div class="desc">Enter once — auto-fills all calculators, powers your Dashboard</div><div class="btn btn-outline" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;border-color:var(--accent3);color:var(--accent3);">Set Up →</div></div>
    <div class="calc-card" onclick="showSection('dashboard')" style="cursor:pointer;border-color:rgba(0,229,176,0.3);"><h3><span class="icon">📊</span>Your Dashboard</h3><div class="desc">Financial status, action plan, scenario checks</div><div class="btn" style="display:inline-block;font-size:0.75rem;margin-top:0.5rem;">View →</div></div>
  </div>
</div>

<!-- INVEST -->
<div id="invest" class="section">
  <div class="section-title">📈 Investment Suite</div>
  <div class="section-sub">Analyze if your investment will work — before you invest.</div>
  <div class="inner-tabs">
    <button class="inner-tab active" onclick="showInner('invest','sip')">SIP Calculator</button>
    <button class="inner-tab" onclick="showInner('invest','lump')">Lump Sum</button>
    <button class="inner-tab" onclick="showInner('invest','goal')">Goal Planner</button>
    <button class="inner-tab" onclick="showInner('invest','roi')">ROI Analyzer</button>
  </div>
  <div id="invest-sip" class="inner-section active">
    <div class="two-col">
      <div class="calc-card">
        <h3><span class="icon">💳</span>SIP Calculator</h3><div class="desc">Is your monthly SIP enough to reach your target?</div>
        <div class="range-group"><div class="range-header"><label>Monthly SIP Amount</label><span class="range-val" id="sipAmt-v">₹10,000</span></div><input type="range" id="sipAmt" min="500" max="200000" step="500" value="10000" oninput="calcSIP()"></div>
        <div class="range-group"><div class="range-header"><label>Annual Return Rate</label><span class="range-val" id="sipRate-v">12%</span></div><input type="range" id="sipRate" min="1" max="30" step="0.5" value="12" oninput="calcSIP()"></div>
        <div class="range-group"><div class="range-header"><label>Investment Period</label><span class="range-val" id="sipYrs-v">15 years</span></div><input type="range" id="sipYrs" min="1" max="40" step="1" value="15" oninput="calcSIP()"></div>
        <div class="result-box" id="sipResult"></div>
      </div>
      <div class="calc-card">
        <h3><span class="icon">📊</span>Year-by-Year Growth</h3><div class="desc">See how your wealth compounds over time</div>
        <div class="mini-chart"><div class="bar-chart" id="sipChart"></div></div>
        <div id="sipBreakdown" style="margin-top:1rem;"></div>
      </div>
    </div>
  </div>
  <div id="invest-lump" class="inner-section">
    <div class="two-col">
      <div class="calc-card">
        <h3><span class="icon">💰</span>Lump Sum Investment</h3><div class="desc">One-time investment growth calculator</div>
        <div class="range-group"><div class="range-header"><label>Investment Amount</label><span class="range-val" id="lsAmt-v">₹5,00,000</span></div><input type="range" id="lsAmt" min="10000" max="10000000" step="10000" value="500000" oninput="calcLS()"></div>
        <div class="range-group"><div class="range-header"><label>Annual Return Rate</label><span class="range-val" id="lsRate-v">12%</span></div><input type="range" id="lsRate" min="1" max="30" step="0.5" value="12" oninput="calcLS()"></div>
        <div class="range-group"><div class="range-header"><label>Time Period</label><span class="range-val" id="lsYrs-v">10 years</span></div><input type="range" id="lsYrs" min="1" max="40" step="1" value="10" oninput="calcLS()"></div>
        <div class="result-box" id="lsResult"></div>
      </div>
      <div class="calc-card">
        <h3><span class="icon">🔄</span>Compare with Inflation</h3><div class="desc">Real vs nominal returns after inflation</div>
        <div class="range-group"><div class="range-header"><label>Inflation Rate</label><span class="range-val" id="lsInfl-v">6%</span></div><input type="range" id="lsInfl" min="2" max="15" step="0.5" value="6" oninput="calcLS()"></div>
        <div id="lsInflResult"></div>
      </div>
    </div>
  </div>
  <div id="invest-goal" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">🎯</span>Goal-Based Investment Planner</h3><div class="desc">I want ₹X by year Y — how much must I invest monthly?</div>
      <div class="form-row form-row-3">
        <div><label>Target Amount (₹)</label><input type="number" id="goalAmt" value="10000000" oninput="calcGoal()"></div>
        <div><label>Years to Goal</label><input type="number" id="goalYrs" value="15" oninput="calcGoal()"></div>
        <div><label>Expected Return (%)</label><input type="number" id="goalRate" value="12" oninput="calcGoal()"></div>
      </div>
      <div class="result-box" id="goalResult"></div>
    </div>
  </div>
  <div id="invest-roi" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">⚖️</span>ROI Analyzer</h3><div class="desc">Compare your investment against benchmarks like FD, Gold, Nifty</div>
      <div class="form-row form-row-3">
        <div><label>Buy Price (₹)</label><input type="number" id="roiBuy" value="100000" oninput="calcROI()"></div>
        <div><label>Sell/Current Price (₹)</label><input type="number" id="roiSell" value="145000" oninput="calcROI()"></div>
        <div><label>Holding Period (years)</label><input type="number" id="roiYrs" value="3" oninput="calcROI()"></div>
      </div>
      <div id="roiResult"></div>
    </div>
  </div>
</div>

<!-- RETIRE -->
<div id="retire" class="section">
  <div class="section-title">🏖️ Retirement Planner</div>
  <div class="section-sub">Plan your freedom — whether you retire at 40 or 65.</div>
  <div class="inner-tabs">
    <button class="inner-tab active" onclick="showInner('retire','fire')">FIRE Calculator</button>
    <button class="inner-tab" onclick="showInner('retire','corpus')">Corpus Builder</button>
    <button class="inner-tab" onclick="showInner('retire','withdraw')">Withdrawal Planner</button>
  </div>
  <div id="retire-fire" class="inner-section active">
    <div class="two-col">
      <div class="calc-card">
        <h3><span class="icon">🔥</span>FIRE — Financial Independence Calculator</h3><div class="desc">How much money do you need to never work again?</div>
        <div class="range-group"><div class="range-header"><label>Monthly Expenses (₹)</label><span class="range-val" id="fireExp-v">₹60,000</span></div><input type="range" id="fireExp" min="10000" max="500000" step="5000" value="60000" oninput="calcFIRE()"></div>
        <div class="range-group"><div class="range-header"><label>Current Age</label><span class="range-val" id="fireAge-v">30</span></div><input type="range" id="fireAge" min="20" max="60" step="1" value="30" oninput="calcFIRE()"></div>
        <div class="range-group"><div class="range-header"><label>Target Retirement Age</label><span class="range-val" id="fireRetire-v">45</span></div><input type="range" id="fireRetire" min="25" max="70" step="1" value="45" oninput="calcFIRE()"></div>
        <div class="range-group"><div class="range-header"><label>Inflation Rate</label><span class="range-val" id="fireInfl-v">6%</span></div><input type="range" id="fireInfl" min="3" max="12" step="0.5" value="6" oninput="calcFIRE()"></div>
        <div class="range-group"><div class="range-header"><label>Safe Withdrawal Rate</label><span class="range-val" id="fireSWR-v">4%</span></div><input type="range" id="fireSWR" min="2" max="8" step="0.5" value="4" oninput="calcFIRE()"></div>
        <div class="result-box" id="fireResult"></div>
      </div>
      <div class="calc-card">
        <h3><span class="icon">💾</span>How to Build That Corpus</h3><div class="desc">Monthly saving needed to reach your FIRE number</div>
        <div class="range-group"><div class="range-header"><label>Current Savings (₹)</label><span class="range-val" id="fireSaved-v">₹5,00,000</span></div><input type="range" id="fireSaved" min="0" max="20000000" step="50000" value="500000" oninput="calcFIRE()"></div>
        <div class="range-group"><div class="range-header"><label>Expected Return on Investment</label><span class="range-val" id="fireRet-v">12%</span></div><input type="range" id="fireRet" min="5" max="20" step="0.5" value="12" oninput="calcFIRE()"></div>
        <div id="firePathResult"></div>
      </div>
    </div>
  </div>
  <div id="retire-corpus" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">🏗️</span>Retirement Corpus Builder</h3><div class="desc">I want to retire with X crores — what's my plan?</div>
      <div class="form-row form-row-3">
        <div><label>Target Corpus (₹)</label><input type="number" id="corpTarget" value="50000000" oninput="calcCorpus()"></div>
        <div><label>Years to Retirement</label><input type="number" id="corpYrs" value="20" oninput="calcCorpus()"></div>
        <div><label>Current Corpus (₹)</label><input type="number" id="corpCurr" value="1000000" oninput="calcCorpus()"></div>
      </div>
      <div class="form-row form-row-3">
        <div><label>Expected Return (%)</label><input type="number" id="corpRate" value="12" oninput="calcCorpus()"></div>
        <div><label>Annual Step-Up (%)</label><input type="number" id="corpStep" value="10" oninput="calcCorpus()"></div>
        <div></div>
      </div>
      <div id="corpusResult"></div>
    </div>
  </div>
  <div id="retire-withdraw" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">📤</span>Withdrawal Planner — How Long Will It Last?</h3><div class="desc">Can my retirement fund support my lifestyle until age 90?</div>
      <div class="form-row form-row-3">
        <div><label>Retirement Corpus (₹)</label><input type="number" id="wdCorpus" value="30000000" oninput="calcWithdraw()"></div>
        <div><label>Monthly Withdrawal (₹)</label><input type="number" id="wdAmt" value="80000" oninput="calcWithdraw()"></div>
        <div><label>Return on Corpus (%)</label><input type="number" id="wdRate" value="8" oninput="calcWithdraw()"></div>
      </div>
      <div class="form-row form-row-2">
        <div><label>Inflation (%)</label><input type="number" id="wdInfl" value="6" oninput="calcWithdraw()"></div>
        <div><label>Annual Withdrawal Increase (%)</label><input type="number" id="wdIncrease" value="6" oninput="calcWithdraw()"></div>
      </div>
      <div id="withdrawResult"></div>
    </div>
  </div>
</div>

<!-- STOCKS RISK LAB -->
<div id="stocks" class="section">
  <div class="section-title">📉 Stock Risk Lab</div>
  <div class="section-sub">Stress-test your portfolio before the market does it for you.</div>
  <div class="inner-tabs">
    <button class="inner-tab active" onclick="showInner('stocks','crash')">Crash Simulator</button>
    <button class="inner-tab" onclick="showInner('stocks','stoploss')">Stop-Loss Calc</button>
    <button class="inner-tab" onclick="showInner('stocks','portfolio')">Portfolio Risk</button>
  </div>
  <div id="stocks-crash" class="inner-section active">
    <div class="calc-card">
      <h3><span class="icon">💥</span>Market Crash Simulator</h3><div class="desc">If the market falls, how much do YOU lose? How long to recover?</div>
      <div class="form-row form-row-3">
        <div><label>Portfolio Value (₹)</label><input type="number" id="crashPort" value="2000000" oninput="calcCrash()"></div>
        <div><label>Equity Allocation (%)</label><input type="number" id="crashEq" value="80" oninput="calcCrash()"></div>
        <div><label>Expected Annual Return (%)</label><input type="number" id="crashRet" value="12" oninput="calcCrash()"></div>
      </div>
      <div id="crashScenarios" style="margin-top:1.5rem;"></div>
    </div>
  </div>
  <div id="stocks-stoploss" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">🛑</span>Stop-Loss &amp; Target Calculator</h3><div class="desc">Where to set your exit to protect capital and lock profits</div>
      <div class="form-row form-row-3">
        <div><label>Buy Price (₹)</label><input type="number" id="slBuy" value="500" oninput="calcStopLoss()"></div>
        <div><label>Quantity (shares)</label><input type="number" id="slQty" value="100" oninput="calcStopLoss()"></div>
        <div><label>Risk Tolerance (%)</label><input type="number" id="slRisk" value="5" oninput="calcStopLoss()"></div>
      </div>
      <div id="slResult"></div>
    </div>
  </div>
  <div id="stocks-portfolio" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">🧮</span>Portfolio Asset Stress Test</h3><div class="desc">Enter your allocation — see how different scenarios affect total value</div>
      <div class="form-row form-row-3">
        <div><label>Equity (₹)</label><input type="number" id="ptEq" value="1000000" oninput="calcPortfolio()"></div>
        <div><label>Debt/FD (₹)</label><input type="number" id="ptDebt" value="500000" oninput="calcPortfolio()"></div>
        <div><label>Gold (₹)</label><input type="number" id="ptGold" value="300000" oninput="calcPortfolio()"></div>
      </div>
      <div class="form-row form-row-3">
        <div><label>Real Estate (₹)</label><input type="number" id="ptRE" value="5000000" oninput="calcPortfolio()"></div>
        <div><label>Crypto (₹)</label><input type="number" id="ptCrypto" value="200000" oninput="calcPortfolio()"></div>
        <div><label>Cash (₹)</label><input type="number" id="ptCash" value="100000" oninput="calcPortfolio()"></div>
      </div>
      <div id="portfolioResult"></div>
    </div>
  </div>
</div>

<!-- LOANS -->
<div id="loans" class="section">
  <div class="section-title">🏦 Loan &amp; EMI Center</div>
  <div class="section-sub">Understand the real cost of borrowing — and how to escape it faster.</div>
  <div class="inner-tabs">
    <button class="inner-tab active" onclick="showInner('loans','emi')">EMI Calculator</button>
    <button class="inner-tab" onclick="showInner('loans','prepay')">Prepayment Benefit</button>
  </div>
  <div id="loans-emi" class="inner-section active">
    <div class="two-col">
      <div class="calc-card">
        <h3><span class="icon">🏠</span>EMI Calculator</h3><div class="desc">What will your monthly payment actually be?</div>
        <div class="range-group"><div class="range-header"><label>Loan Amount</label><span class="range-val" id="emiAmt-v">₹50,00,000</span></div><input type="range" id="emiAmt" min="100000" max="50000000" step="100000" value="5000000" oninput="calcEMI()"></div>
        <div class="range-group"><div class="range-header"><label>Interest Rate</label><span class="range-val" id="emiRate-v">8.5%</span></div><input type="range" id="emiRate" min="5" max="20" step="0.1" value="8.5" oninput="calcEMI()"></div>
        <div class="range-group"><div class="range-header"><label>Loan Tenure</label><span class="range-val" id="emiTenure-v">20 years</span></div><input type="range" id="emiTenure" min="1" max="30" step="1" value="20" oninput="calcEMI()"></div>
        <div id="emiResult"></div>
      </div>
      <div class="calc-card">
        <h3><span class="icon">📅</span>Amortization Schedule</h3><div class="desc">Year-by-year breakdown of principal vs interest</div>
        <div class="table-wrap">
          <table id="emiTable">
            <thead><tr><th>Year</th><th>Principal</th><th>Interest</th><th>Balance</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
  <div id="loans-prepay" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">⚡</span>Prepayment Benefit Calculator</h3><div class="desc">How much interest can you save by paying extra?</div>
      <div class="form-row form-row-3">
        <div><label>Outstanding Balance (₹)</label><input type="number" id="ppBalance" value="4000000" oninput="calcPrepay()"></div>
        <div><label>Interest Rate (%)</label><input type="number" id="ppRate" value="8.5" oninput="calcPrepay()"></div>
        <div><label>Remaining Months</label><input type="number" id="ppMonths" value="216" oninput="calcPrepay()"></div>
      </div>
      <div class="form-row form-row-2">
        <div><label>Extra Monthly Payment (₹)</label><input type="number" id="ppExtra" value="5000" oninput="calcPrepay()"></div>
        <div><label>One-Time Prepayment (₹)</label><input type="number" id="ppLump" value="500000" oninput="calcPrepay()"></div>
      </div>
      <div id="prepayResult"></div>
    </div>
  </div>
</div>

<!-- TAX -->
<div id="tax" class="section">
  <div class="section-title">🧾 Tax Optimizer</div>
  <div class="section-sub">Pay only what the law requires — not a rupee more.</div>
  <div class="inner-tabs">
    <button class="inner-tab active" onclick="showInner('tax','income')">Income Tax</button>
    <button class="inner-tab" onclick="showInner('tax','cg')">Capital Gains</button>
  </div>
  <div id="tax-income" class="inner-section active">
    <div class="calc-card">
      <h3><span class="icon">📑</span>Income Tax — Old vs New Regime</h3><div class="desc">Find which regime saves you more money in FY 2024-25</div>
      <div class="form-row form-row-3">
        <div><label>Annual Income (₹)</label><input type="number" id="taxInc" value="1500000" oninput="calcTax()"></div>
        <div><label>HRA Exemption (₹)</label><input type="number" id="taxHRA" value="120000" oninput="calcTax()"></div>
        <div><label>80C Investments (₹)</label><input type="number" id="tax80C" value="150000" oninput="calcTax()"></div>
      </div>
      <div class="form-row form-row-3">
        <div><label>Home Loan Interest (₹)</label><input type="number" id="taxHL" value="0" oninput="calcTax()"></div>
        <div><label>NPS (80CCD) (₹)</label><input type="number" id="taxNPS" value="50000" oninput="calcTax()"></div>
        <div><label>Other Deductions (₹)</label><input type="number" id="taxOther" value="0" oninput="calcTax()"></div>
      </div>
      <div id="taxResult"></div>
    </div>
  </div>
  <div id="tax-cg" class="inner-section">
    <div class="calc-card">
      <h3><span class="icon">📊</span>Capital Gains Tax Calculator</h3><div class="desc">Stocks, Mutual Funds, Property — know your tax before selling</div>
      <div class="form-row form-row-3">
        <div><label>Asset Type</label><select id="cgType" onchange="calcCG()"><option value="equity">Equity / Stocks</option><option value="mf">Equity MF</option><option value="debt">Debt / FD</option><option value="property">Property</option><option value="gold">Gold</option></select></div>
        <div><label>Purchase Price (₹)</label><input type="number" id="cgBuy" value="500000" oninput="calcCG()"></div>
        <div><label>Sale Price (₹)</label><input type="number" id="cgSell" value="800000" oninput="calcCG()"></div>
      </div>
      <div class="form-row form-row-2">
        <div><label>Holding Period (months)</label><input type="number" id="cgMonths" value="18" oninput="calcCG()"></div>
        <div><label>Tax Bracket (%)</label><input type="number" id="cgBracket" value="30" oninput="calcCG()"></div>
      </div>
      <div id="cgResult"></div>
    </div>
  </div>
</div>

<!-- HEALTH SCORE -->
<div id="health" class="section">
  <div class="section-title">💯 Financial Health Score</div>
  <div class="section-sub">Your complete financial fitness check — with an honest report card.</div>
  <div class="calc-card" style="margin-bottom:1.5rem;">
    <h3><span class="icon">📋</span>Tell Us About Your Finances</h3><div class="desc">Answer 8 questions — get your personalized financial health score</div>
    <div class="form-row form-row-3">
      <div><label>Monthly Income (₹)</label><input type="number" id="hInc" value="100000" oninput="calcHealth()"></div>
      <div><label>Monthly Expenses (₹)</label><input type="number" id="hExp" value="60000" oninput="calcHealth()"></div>
      <div><label>Monthly EMI (₹)</label><input type="number" id="hEMI" value="15000" oninput="calcHealth()"></div>
    </div>
    <div class="form-row form-row-3">
      <div><label>Monthly Savings (₹)</label><input type="number" id="hSave" value="20000" oninput="calcHealth()"></div>
      <div><label>Emergency Fund (months)</label><input type="number" id="hEF" value="3" oninput="calcHealth()"></div>
      <div><label>Total Debt (₹)</label><input type="number" id="hDebt" value="1000000" oninput="calcHealth()"></div>
    </div>
    <div class="form-row form-row-3">
      <div><label>Total Investments (₹)</label><input type="number" id="hInvest" value="500000" oninput="calcHealth()"></div>
      <div><label>Insurance Cover (₹ Cr)</label><input type="number" id="hInsure" value="1" oninput="calcHealth()"></div>
      <div><label>Age</label><input type="number" id="hAge" value="32" oninput="calcHealth()"></div>
    </div>
  </div>
  <div id="healthResult"></div>
</div>

<!-- MARKET ADVISOR — BAKED IN DATA -->
<div id="advisor" class="section">
  <div class="section-title">🔮 Market Advisor</div>
  <div class="section-sub">BUY / WAIT / HOLD signals — data fetched by Python, updated every hour via GitHub Actions.</div>
  <div class="disclaimer-banner">⚠️ <strong>Disclaimer:</strong> All signals are for educational purposes only. Not financial advice. Consult a SEBI-registered investment advisor before investing.</div>
  <div class="update-badge" style="margin-bottom:1.5rem;"><div class="update-dot"></div>Data updated: __UPDATED_AT__ UTC</div>
  <div class="advisor-tabs">
    <button class="advisor-tab active" onclick="showAdvisor('gold')">🥇 Gold</button>
    <button class="advisor-tab" onclick="showAdvisor('silver')">🥈 Silver</button>
    <button class="advisor-tab" onclick="showAdvisor('crypto')">₿ Crypto</button>
    <button class="advisor-tab" onclick="showAdvisor('stocks')">📈 Nifty/India</button>
    <button class="advisor-tab" onclick="showAdvisor('usstocks')">🇺🇸 US Markets</button>
    <button class="advisor-tab" onclick="showAdvisor('property')">🏠 Property</button>
    <button class="advisor-tab" onclick="showAdvisor('fd')">🏦 FD/Bonds</button>
  </div>
  <div id="advisor-gold" class="signal-baked active">__CARD_GOLD__</div>
  <div id="advisor-silver" class="signal-baked">__CARD_SILVER__</div>
  <div id="advisor-crypto" class="signal-baked">__CARD_CRYPTO__</div>
  <div id="advisor-stocks" class="signal-baked">__CARD_STOCKS__</div>
  <div id="advisor-usstocks" class="signal-baked">__CARD_USSTOCKS__</div>
  <div id="advisor-property" class="signal-baked">__CARD_PROPERTY__</div>
  <div id="advisor-fd" class="signal-baked">__CARD_FD__</div>
</div>

<!-- PROFILE -->
<div id="profile" class="section">
  <div class="section-title">👤 Your Financial Profile</div>
  <div class="section-sub">Enter your details once — all calculators auto-fill. Saved locally in your browser.</div>
  <div class="profile-grid">
    <div class="calc-card">
      <h3><span class="icon">💼</span>Income &amp; Expenses</h3><div class="desc">Used to power the Dashboard and Health Score</div>
      <div class="form-row form-row-2" style="margin-top:1rem;">
        <div><label>Monthly Income (₹)</label><input type="number" id="pSalary" placeholder="e.g. 80000" oninput="saveProfile()"></div>
        <div><label>Monthly Expenses (₹)</label><input type="number" id="pExpenses" placeholder="e.g. 45000" oninput="saveProfile()"></div>
      </div>
      <div class="form-row form-row-2">
        <div><label>Monthly EMI (₹)</label><input type="number" id="pEMI" placeholder="e.g. 15000" oninput="saveProfile()"></div>
        <div><label>Monthly SIP (₹)</label><input type="number" id="pSIP" placeholder="e.g. 10000" oninput="saveProfile()"></div>
      </div>
    </div>
    <div class="calc-card">
      <h3><span class="icon">🎯</span>Goals &amp; Demographics</h3><div class="desc">Personalizes retirement and goal recommendations</div>
      <div class="form-row form-row-2" style="margin-top:1rem;">
        <div><label>Current Age</label><input type="number" id="pAge" placeholder="e.g. 30" oninput="saveProfile()"></div>
        <div><label>Retirement Age</label><input type="number" id="pRetireAge" placeholder="e.g. 55" oninput="saveProfile()"></div>
      </div>
      <div class="form-row form-row-2">
        <div><label>Emergency Fund (₹)</label><input type="number" id="pEmergency" placeholder="e.g. 200000" oninput="saveProfile()"></div>
        <div><label>Total Savings (₹)</label><input type="number" id="pSavings" placeholder="e.g. 500000" oninput="saveProfile()"></div>
      </div>
    </div>
  </div>
  <button class="btn" style="margin-top:1rem;" onclick="applyProfile()">✅ Save &amp; Auto-fill Calculators</button>
  <div id="profileMsg" style="margin-top:0.8rem;font-size:0.85rem;color:var(--accent);display:none;">✓ Profile saved! Head to Dashboard for your action plan.</div>
</div>

<!-- DASHBOARD -->
<div id="dashboard" class="section">
  <div class="section-title">📊 Your Financial Dashboard</div>
  <div class="section-sub">Your complete financial status at a glance — powered by your Profile.</div>
  <div id="dashNoProfile" class="verdict neutral" style="display:none;">ℹ️ Fill in your <strong>Profile</strong> first to see your personalised dashboard.<br><button class="btn btn-outline" style="margin-top:0.8rem;" onclick="showSection('profile')">Go to Profile →</button></div>
  <div id="dashContent" style="display:none;">
    <div id="dashAlerts" style="background:rgba(0,229,176,0.06);border:1px solid rgba(0,229,176,0.2);border-radius:10px;padding:0.7rem 1.2rem;margin-bottom:1.5rem;display:flex;flex-wrap:wrap;gap:0.7rem;align-items:center;"></div>
    <div class="dash-status" id="dashStatus"></div>
    <div class="two-col">
      <div class="calc-card"><h3><span class="icon">🎯</span>Recommended Actions</h3><div class="desc">Prioritised steps based on your profile</div><ul class="rec-list" id="dashRecs" style="margin-top:1rem;"></ul></div>
      <div class="calc-card"><h3><span class="icon">🧪</span>Quick Scenario Checks</h3><div class="desc">What if...</div><div id="dashScenarios" style="margin-top:1rem;"></div></div>
    </div>
  </div>
</div>

</div><!-- /main -->

<script>
// ════════ UTILS ════════
const fmtINR = n => '₹' + Math.round(n).toLocaleString('en-IN');
const fmtC = n => {
  if(n>=1e7) return '₹'+(n/1e7).toFixed(2)+' Cr';
  if(n>=1e5) return '₹'+(n/1e5).toFixed(2)+' L';
  return fmtINR(n);
};
const fmtPct = n => n.toFixed(2)+'%';

// ════════ NAV ════════
function showSection(id) {
  document.querySelectorAll('.section').forEach(s=>{s.classList.remove('active');s.style.display='none';});
  const t=document.getElementById(id);
  if(t){t.style.display='block';t.classList.add('active');}
  document.querySelectorAll('.nav-tabs button').forEach(b=>{
    b.classList.remove('active');
    if((id==='home'&&b.textContent.includes('Home'))||(id==='invest'&&b.textContent.includes('Invest'))||(id==='retire'&&b.textContent.includes('Retire'))||(id==='stocks'&&b.textContent.includes('Stock'))||(id==='loans'&&b.textContent.includes('Loan'))||(id==='tax'&&b.textContent.includes('Tax'))||(id==='health'&&b.textContent.includes('Health'))||(id==='advisor'&&b.textContent.includes('Advisor'))||(id==='profile'&&b.textContent.includes('Profile'))||(id==='dashboard'&&b.textContent.includes('Dashboard')))
      b.classList.add('active');
  });
  if(id==='dashboard'){saveProfile();renderDashboard();}
}
function showInner(s,t) {
  document.querySelectorAll('#'+s+' .inner-section').forEach(x=>x.classList.remove('active'));
  document.getElementById(s+'-'+t).classList.add('active');
  document.querySelectorAll('#'+s+' .inner-tab').forEach(b=>{b.classList.remove('active');if(b.textContent.toLowerCase().includes(t.substring(0,4)))b.classList.add('active');});
}
function showAdvisor(id) {
  document.querySelectorAll('.signal-baked').forEach(x=>x.classList.remove('active'));
  document.getElementById('advisor-'+id).classList.add('active');
  document.querySelectorAll('.advisor-tab').forEach(b=>b.classList.remove('active'));
  event.target.classList.add('active');
}

// ════════ CALCULATORS (same as original) ════════
function calcSIP(){
  const P=+document.getElementById('sipAmt').value,r=+document.getElementById('sipRate').value/100/12,n=+document.getElementById('sipYrs').value*12;
  document.getElementById('sipAmt-v').textContent=fmtINR(P);
  document.getElementById('sipRate-v').textContent=(+document.getElementById('sipRate').value)+'%';
  document.getElementById('sipYrs-v').textContent=(+document.getElementById('sipYrs').value)+' years';
  const FV=P*((Math.pow(1+r,n)-1)/r)*(1+r),invested=P*n,gains=FV-invested;
  document.getElementById('sipResult').innerHTML=`<div class="result-label">Estimated Corpus</div><div class="result-main">${fmtC(FV)}</div><div class="result-grid"><div class="result-item"><div class="result-item-val positive">${fmtC(FV)}</div><div class="result-item-lab">Total Value</div></div><div class="result-item"><div class="result-item-val">${fmtC(invested)}</div><div class="result-item-lab">Amount Invested</div></div><div class="result-item"><div class="result-item-val positive">${fmtC(gains)}</div><div class="result-item-lab">Wealth Gained</div></div><div class="result-item"><div class="result-item-val positive">${((gains/invested)*100).toFixed(0)}%</div><div class="result-item-lab">ROI</div></div></div>`;
  const yrs=+document.getElementById('sipYrs').value;let bars='',maxVal=0,vals=[];
  for(let y=1;y<=Math.min(yrs,20);y++){const nn=y*12,v=P*((Math.pow(1+r,nn)-1)/r)*(1+r);vals.push(v);maxVal=Math.max(maxVal,v);}
  vals.forEach(v=>{bars+=`<div class="bar" style="height:${(v/maxVal*100)}%"></div>`;});
  document.getElementById('sipChart').innerHTML=bars;
  document.getElementById('sipBreakdown').innerHTML=`<div class="result-grid"><div class="result-item"><div class="result-item-val">${fmtC(invested)}</div><div class="result-item-lab">Principal (${((invested/FV)*100).toFixed(0)}%)</div></div><div class="result-item"><div class="result-item-val positive">${fmtC(gains)}</div><div class="result-item-lab">Gains (${((gains/FV)*100).toFixed(0)}%)</div></div></div>`;
}
function calcLS(){
  const P=+document.getElementById('lsAmt').value,r=+document.getElementById('lsRate').value/100,n=+document.getElementById('lsYrs').value,infl=+document.getElementById('lsInfl').value/100;
  document.getElementById('lsAmt-v').textContent=fmtC(P);document.getElementById('lsRate-v').textContent=r*100+'%';document.getElementById('lsYrs-v').textContent=n+' years';document.getElementById('lsInfl-v').textContent=infl*100+'%';
  const FV=P*Math.pow(1+r,n),realRate=((1+r)/(1+infl))-1,realFV=P*Math.pow(1+realRate,n);
  document.getElementById('lsResult').innerHTML=`<div class="result-label">Maturity Value</div><div class="result-main">${fmtC(FV)}</div><div class="result-grid"><div class="result-item"><div class="result-item-val positive">${fmtC(FV)}</div><div class="result-item-lab">Nominal Value</div></div><div class="result-item"><div class="result-item-val">${fmtC(FV-P)}</div><div class="result-item-lab">Total Gain</div></div></div>`;
  document.getElementById('lsInflResult').innerHTML=`<div class="result-box"><div class="result-label">After Inflation (Real Value)</div><div class="result-main" style="font-size:1.5rem;">${fmtC(realFV)}</div><div class="result-grid" style="margin-top:0.8rem;"><div class="result-item"><div class="result-item-val positive">${fmtC(FV)}</div><div class="result-item-lab">Nominal</div></div><div class="result-item"><div class="result-item-val warning">${fmtC(realFV)}</div><div class="result-item-lab">Real Value Today</div></div></div><div class="verdict ${realRate>0?'good':'bad'}">${realRate>0?`✅ Real return ${(realRate*100).toFixed(1)}%/yr — beats inflation.`:`⚠️ Return below inflation!`}</div></div>`;
}
function calcGoal(){
  const G=+document.getElementById('goalAmt').value,n=+document.getElementById('goalYrs').value*12,r=+document.getElementById('goalRate').value/100/12;
  const SIP=(G*r)/((Math.pow(1+r,n)-1)*(1+r)),lump=G/Math.pow(1+r*12,+document.getElementById('goalYrs').value);
  document.getElementById('goalResult').innerHTML=`<div class="result-label">To Reach ${fmtC(G)} in ${n/12} years</div><div class="result-grid"><div class="result-item"><div class="result-item-val positive">${fmtINR(SIP)}/mo</div><div class="result-item-lab">Monthly SIP Required</div></div><div class="result-item"><div class="result-item-val purple">${fmtC(lump)}</div><div class="result-item-lab">Lump Sum Today</div></div><div class="result-item"><div class="result-item-val">${fmtC(SIP*n)}</div><div class="result-item-lab">Total Invested (SIP)</div></div><div class="result-item"><div class="result-item-val positive">${fmtC(G-SIP*n)}</div><div class="result-item-lab">Market Does The Rest</div></div></div>`;
}
function calcROI(){
  const buy=+document.getElementById('roiBuy').value,sell=+document.getElementById('roiSell').value,yrs=+document.getElementById('roiYrs').value;
  const profit=sell-buy,roi=(profit/buy)*100,cagr=(Math.pow(sell/buy,1/yrs)-1)*100;
  const fdCAGR=7.5,niftyCAGR=13;const color=cagr>=niftyCAGR?'positive':cagr>=fdCAGR?'warning':'negative';const verdict=cagr>=niftyCAGR?'good':cagr>=fdCAGR?'neutral':'bad';
  const msg=cagr>=niftyCAGR?`✅ Excellent! ${fmtPct(cagr)} CAGR beats Nifty`:cagr>=fdCAGR?`⚠️ OK — beats FD but below Nifty`:`❌ Poor — even bank FD would've done better`;
  document.getElementById('roiResult').innerHTML=`<div class="result-box"><div class="result-grid"><div class="result-item"><div class="result-item-val ${profit>=0?'positive':'negative'}">${fmtINR(profit)}</div><div class="result-item-lab">Profit/Loss</div></div><div class="result-item"><div class="result-item-val ${color}">${fmtPct(roi)}</div><div class="result-item-lab">Absolute ROI</div></div><div class="result-item"><div class="result-item-val ${color}">${fmtPct(cagr)}</div><div class="result-item-lab">CAGR</div></div><div class="result-item"><div class="result-item-val">x${(sell/buy).toFixed(2)}</div><div class="result-item-lab">Money Multiplied</div></div></div></div><div class="scenario-row" style="margin-top:1rem;"><div class="scenario-card"><div class="scenario-label">Bank FD</div><div class="scenario-val warning">7.5%</div></div><div class="scenario-card"><div class="scenario-label">Nifty 50</div><div class="scenario-val positive">13%</div></div><div class="scenario-card highlight"><div class="scenario-label">Yours</div><div class="scenario-val ${color}">${fmtPct(cagr)}</div></div></div><div class="verdict ${verdict}">${msg}</div>`;
}
function calcFIRE(){
  const exp=+document.getElementById('fireExp').value,age=+document.getElementById('fireAge').value,retire=+document.getElementById('fireRetire').value,infl=+document.getElementById('fireInfl').value/100,swr=+document.getElementById('fireSWR').value/100,saved=+document.getElementById('fireSaved').value,ret=+document.getElementById('fireRet').value/100;
  document.getElementById('fireExp-v').textContent=fmtINR(exp);document.getElementById('fireAge-v').textContent=age;document.getElementById('fireRetire-v').textContent=retire;document.getElementById('fireInfl-v').textContent=(+document.getElementById('fireInfl').value)+'%';document.getElementById('fireSWR-v').textContent=(+document.getElementById('fireSWR').value)+'%';document.getElementById('fireSaved-v').textContent=fmtC(saved);document.getElementById('fireRet-v').textContent=(+document.getElementById('fireRet').value)+'%';
  const yrs=retire-age,expR=exp*Math.pow(1+infl,yrs)*12,fireNum=expR/swr,r=ret/12,n=yrs*12,futSaved=saved*Math.pow(1+ret,yrs),remaining=Math.max(0,fireNum-futSaved),sipNeeded=remaining>0?(remaining*r)/((Math.pow(1+r,n)-1)*(1+r)):0;
  document.getElementById('fireResult').innerHTML=`<div class="result-label">Your FIRE Number</div><div class="result-main">${fmtC(fireNum)}</div><div class="result-grid"><div class="result-item"><div class="result-item-val">${yrs} yrs</div><div class="result-item-lab">Time to FIRE</div></div><div class="result-item"><div class="result-item-val">${fmtINR(expR/12)}/mo</div><div class="result-item-lab">Expenses at Retirement</div></div></div>`;
  document.getElementById('firePathResult').innerHTML=`<div class="result-box"><div class="result-grid"><div class="result-item"><div class="result-item-val purple">${fmtC(futSaved)}</div><div class="result-item-lab">Savings @ Retirement</div></div><div class="result-item"><div class="result-item-val warning">${fmtC(remaining)}</div><div class="result-item-lab">Gap</div></div></div><div style="margin-top:0.8rem;"><div class="result-label">SIP Needed</div><div style="font-size:1.8rem;font-weight:800;font-family:var(--font-mono);color:var(--accent);">${fmtINR(sipNeeded)}/mo</div></div><div class="verdict ${sipNeeded<50000?'good':sipNeeded<150000?'neutral':'bad'}">${sipNeeded===0?'🎉 You already have enough!':sipNeeded<50000?`✅ Manageable — ₹${Math.round(sipNeeded/1000)}K/mo`:`⚠️ Consider extending retirement age`}</div></div>`;
}
function calcCorpus(){
  const target=+document.getElementById('corpTarget').value,yrs=+document.getElementById('corpYrs').value,curr=+document.getElementById('corpCurr').value,rate=+document.getElementById('corpRate').value/100,step=+document.getElementById('corpStep').value/100;
  const r=rate/12,n=yrs*12,futCurr=curr*Math.pow(1+rate,yrs),remaining=Math.max(0,target-futCurr),sipBase=(remaining*r)/((Math.pow(1+r,n)-1)*(1+r));
  document.getElementById('corpusResult').innerHTML=`<div class="result-box"><div class="result-grid"><div class="result-item"><div class="result-item-val positive">${fmtC(target)}</div><div class="result-item-lab">Target Corpus</div></div><div class="result-item"><div class="result-item-val purple">${fmtC(futCurr)}</div><div class="result-item-lab">Current Savings @ Maturity</div></div><div class="result-item"><div class="result-item-val">${fmtC(remaining)}</div><div class="result-item-lab">Gap</div></div><div class="result-item"><div class="result-item-val positive">${fmtINR(sipBase)}/mo</div><div class="result-item-lab">SIP Needed</div></div></div><div class="verdict good">✅ With ${(step*100).toFixed(0)}% annual step-up, your starting SIP is significantly lower.</div></div>`;
}
function calcWithdraw(){
  let corpus=+document.getElementById('wdCorpus').value;const monthly=+document.getElementById('wdAmt').value,ret=+document.getElementById('wdRate').value/100/12,inc=+document.getElementById('wdIncrease').value/100/12;
  let wd=monthly,months=0,running=corpus;
  while(running>0&&months<600){running=running*(1+ret)-wd;if(running<=0)break;wd=wd*(1+inc);months++;}
  const yrs=Math.floor(months/12);
  document.getElementById('withdrawResult').innerHTML=`<div class="result-box"><div class="result-label">Corpus Lasts</div><div class="result-main">${yrs>=50?'50+ years':yrs+' years'}</div><div class="verdict ${yrs>=30?'good':yrs>=20?'neutral':'bad'}">${yrs>=30?'✅ Excellent! Your corpus is well-funded.':yrs>=20?'⚠️ Decent runway — consider optimizing withdrawals.':'❌ Corpus depletes too fast. Reduce withdrawals or increase corpus.'}</div></div>`;
}
function calcCrash(){
  const port=+document.getElementById('crashPort').value,eq=+document.getElementById('crashEq').value/100,ret=+document.getElementById('crashRet').value/100;
  const equityVal=port*eq;
  const scenarios=[{label:'Mild Correction',drop:10,color:'var(--gold)'},{label:'Bear Market',drop:25,color:'var(--accent2)'},{label:'Major Crash',drop:40,color:'var(--red)'},{label:'Black Swan',drop:60,color:'var(--red)'}];
  let html='<div class="scenario-row">';
  scenarios.forEach(s=>{
    const loss=equityVal*s.drop/100,newPort=port-loss,recoveryYrs=Math.log(port/newPort)/Math.log(1+ret);
    html+=`<div class="scenario-card"><div class="scenario-label">${s.label}</div><div class="scenario-val" style="color:${s.color}">-${s.drop}%</div><div class="scenario-sub">Loss: ${fmtC(loss)}</div><div class="scenario-sub">Portfolio: ${fmtC(newPort)}</div><div class="scenario-sub">Recovery: ~${recoveryYrs.toFixed(1)} yrs</div></div>`;
  });
  html+='</div>';
  document.getElementById('crashScenarios').innerHTML=html;
}
function calcStopLoss(){
  const buy=+document.getElementById('slBuy').value,qty=+document.getElementById('slQty').value,risk=+document.getElementById('slRisk').value/100;
  const sl=buy*(1-risk),t1=buy*(1+risk*2),t2=buy*(1+risk*3),maxLoss=(buy-sl)*qty,t1Profit=(t1-buy)*qty,t2Profit=(t2-buy)*qty,invested=buy*qty;
  document.getElementById('slResult').innerHTML=`<div class="result-box"><div class="result-grid"><div class="result-item"><div class="result-item-val negative">${fmtINR(sl)}</div><div class="result-item-lab">Stop-Loss Price</div></div><div class="result-item"><div class="result-item-val negative">-${fmtINR(maxLoss)}</div><div class="result-item-lab">Max Loss</div></div><div class="result-item"><div class="result-item-val positive">${fmtINR(t1)}</div><div class="result-item-lab">Target 1 (1:2 RR)</div></div><div class="result-item"><div class="result-item-val positive">+${fmtINR(t1Profit)}</div><div class="result-item-lab">Profit @ T1</div></div><div class="result-item"><div class="result-item-val positive">${fmtINR(t2)}</div><div class="result-item-lab">Target 2 (1:3 RR)</div></div><div class="result-item"><div class="result-item-val positive">+${fmtINR(t2Profit)}</div><div class="result-item-lab">Profit @ T2</div></div></div></div>`;
}
function calcPortfolio(){
  const eq=+document.getElementById('ptEq').value,debt=+document.getElementById('ptDebt').value,gold=+document.getElementById('ptGold').value,re=+document.getElementById('ptRE').value,crypto=+document.getElementById('ptCrypto').value,cash=+document.getElementById('ptCash').value;
  const total=eq+debt+gold+re+crypto+cash;
  const scenarios=[{label:'Bull Market +20%',factors:[1.20,1.05,1.08,1.10,1.40,1.0]},{label:'Mild Recession -15%',factors:[0.85,0.98,1.05,0.92,0.70,1.0]},{label:'Market Crash -40%',factors:[0.60,0.95,1.15,0.80,0.40,1.0]}];
  let html='<div class="scenario-row">';
  scenarios.forEach(s=>{
    const newTotal=eq*s.factors[0]+debt*s.factors[1]+gold*s.factors[2]+re*s.factors[3]+crypto*s.factors[4]+cash*s.factors[5];
    const chg=((newTotal-total)/total)*100;
    html+=`<div class="scenario-card ${chg>0?'highlight':''}"><div class="scenario-label">${s.label}</div><div class="scenario-val ${chg>0?'positive':'negative'}">${fmtC(newTotal)}</div><div class="scenario-sub">${chg>=0?'+':''}${chg.toFixed(1)}%</div></div>`;
  });
  html+=`<div class="scenario-card highlight"><div class="scenario-label">Current Total</div><div class="scenario-val positive">${fmtC(total)}</div><div class="scenario-sub">Your Portfolio</div></div></div>`;
  document.getElementById('portfolioResult').innerHTML=html;
}
function calcEMI(){
  const P=+document.getElementById('emiAmt').value,rAnn=+document.getElementById('emiRate').value,r=rAnn/100/12,n=+document.getElementById('emiTenure').value*12;
  document.getElementById('emiAmt-v').textContent=fmtC(P);document.getElementById('emiRate-v').textContent=rAnn+'%';document.getElementById('emiTenure-v').textContent=(+document.getElementById('emiTenure').value)+' years';
  const EMI=P*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1),totalPay=EMI*n,totalInt=totalPay-P;
  document.getElementById('emiResult').innerHTML=`<div class="result-label">Monthly EMI</div><div class="result-main">${fmtINR(EMI)}</div><div class="result-grid"><div class="result-item"><div class="result-item-val">${fmtC(P)}</div><div class="result-item-lab">Principal</div></div><div class="result-item"><div class="result-item-val negative">${fmtC(totalInt)}</div><div class="result-item-lab">Total Interest</div></div><div class="result-item"><div class="result-item-val">${fmtC(totalPay)}</div><div class="result-item-lab">Total Payment</div></div><div class="result-item"><div class="result-item-val warning">${((totalInt/P)*100).toFixed(0)}%</div><div class="result-item-lab">Interest Overhead</div></div></div>`;
  let bal=P,tbod='';const yrs=Math.ceil(n/12);
  for(let y=1;y<=yrs;y++){let yPrin=0,yInt=0;for(let m=0;m<12&&(y-1)*12+m<n;m++){const intM=bal*r,prinM=EMI-intM;yInt+=intM;yPrin+=prinM;bal-=prinM;}tbod+=`<tr><td>${y}</td><td>${fmtC(yPrin)}</td><td class="negative">${fmtC(yInt)}</td><td>${fmtC(Math.max(0,bal))}</td></tr>`;}
  document.querySelector('#emiTable tbody').innerHTML=tbod;
}
function calcPrepay(){
  const bal=+document.getElementById('ppBalance').value,r=+document.getElementById('ppRate').value/100/12,n=+document.getElementById('ppMonths').value,extra=+document.getElementById('ppExtra').value,lump=+document.getElementById('ppLump').value;
  const emi=bal*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1),origTotal=emi*n;
  const newBal=Math.max(0,bal-lump),newEMI=newBal*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1),lumpSave=origTotal-(newEMI*n+lump);
  let b2=bal,m2=0;while(b2>0&&m2<n*2){b2=b2*(1+r)-(emi+extra);m2++;}
  const extraSave=origTotal-(emi+extra)*m2,monthsSaved=n-m2;
  document.getElementById('prepayResult').innerHTML=`<div class="result-box"><div class="scenario-row"><div class="scenario-card"><div class="scenario-label">💰 Lump Sum ${fmtC(lump)}</div><div class="scenario-val positive">Save ${fmtC(Math.max(0,lumpSave))}</div><div class="scenario-sub">in interest savings</div></div><div class="scenario-card highlight"><div class="scenario-label">⚡ Extra ${fmtINR(extra)}/mo</div><div class="scenario-val positive">Save ${fmtC(Math.max(0,extraSave))}</div><div class="scenario-sub">${Math.max(0,monthsSaved)} months early closure</div></div></div></div>`;
}
function calcTax(){
  const inc=+document.getElementById('taxInc').value,hra=+document.getElementById('taxHRA').value,c80=Math.min(+document.getElementById('tax80C').value,150000),hl=+document.getElementById('taxHL').value,nps=Math.min(+document.getElementById('taxNPS').value,50000),other=+document.getElementById('taxOther').value;
  const oldDed=50000+hra+c80+hl+nps+other,oldTaxable=Math.max(0,inc-oldDed),oldTax=calcSlabTax(oldTaxable,'old'),oldTaxWithCess=oldTax*1.04;
  const newTaxable=Math.max(0,inc-75000),newTax=calcSlabTax(newTaxable,'new'),newTaxWithCess=newTax*1.04;
  const better=oldTaxWithCess<newTaxWithCess?'old':'new',saving=Math.abs(oldTaxWithCess-newTaxWithCess);
  document.getElementById('taxResult').innerHTML=`<div style="margin-top:1rem;"><div class="scenario-row"><div class="scenario-card ${better==='old'?'highlight':''}"><div class="scenario-label">🏛️ Old Regime ${better==='old'?'<span class="tag tag-green">BETTER</span>':''}</div><div class="scenario-val ${better==='old'?'positive':'warning'}">${fmtINR(oldTaxWithCess)}</div><div class="scenario-sub">Taxable: ${fmtC(oldTaxable)}</div></div><div class="scenario-card ${better==='new'?'highlight':''}"><div class="scenario-label">🆕 New Regime ${better==='new'?'<span class="tag tag-green">BETTER</span>':''}</div><div class="scenario-val ${better==='new'?'positive':'warning'}">${fmtINR(newTaxWithCess)}</div><div class="scenario-sub">Taxable: ${fmtC(newTaxable)}</div></div></div><div class="verdict good">✅ Choose <strong>${better==='old'?'Old Regime':'New Regime'}</strong> — saves ${fmtINR(saving)}/year</div></div>`;
}
function calcSlabTax(income,regime){
  let tax=0;
  if(regime==='old'){if(income<=250000)tax=0;else if(income<=500000)tax=(income-250000)*0.05;else if(income<=1000000)tax=12500+(income-500000)*0.2;else tax=112500+(income-1000000)*0.3;}
  else{const slabs=[[300000,0],[600000,0.05],[900000,0.1],[1200000,0.15],[1500000,0.2],[Infinity,0.3]];let prev=0;for(const[limit,rate]of slabs){if(income<=limit){tax+=(income-prev)*rate;break;}tax+=(limit-prev)*rate;prev=limit;}}
  return Math.max(0,tax);
}
function calcCG(){
  const type=document.getElementById('cgType').value,buy=+document.getElementById('cgBuy').value,sell=+document.getElementById('cgSell').value,months=+document.getElementById('cgMonths').value,bracket=+document.getElementById('cgBracket').value/100;
  const profit=sell-buy,ltThresh={equity:12,mf:12,debt:36,property:24,gold:36},isLT=months>=ltThresh[type];
  let taxAmt,label;
  if(type==='equity'||type==='mf'){if(isLT){const exempt=100000;taxAmt=Math.max(0,profit-exempt)*0.10;label='LTCG @ 10% (₹1L exempt)';}else{taxAmt=profit*0.15;label='STCG @ 15%';}}
  else if(type==='debt'){taxAmt=profit*bracket;label=`Added to income @ ${(bracket*100).toFixed(0)}%`;}
  else{if(isLT){taxAmt=profit*0.20;label='LTCG @ 20%';}else{taxAmt=profit*bracket;label=`STCG @ ${(bracket*100).toFixed(0)}%`;}}
  const net=profit-taxAmt;
  document.getElementById('cgResult').innerHTML=`<div class="result-box"><div class="result-grid"><div class="result-item"><div class="result-item-val positive">${fmtINR(profit)}</div><div class="result-item-lab">Total Profit</div></div><div class="result-item"><div class="result-item-val negative">-${fmtINR(taxAmt)}</div><div class="result-item-lab">Tax (${label})</div></div><div class="result-item"><div class="result-item-val positive">${fmtINR(net)}</div><div class="result-item-lab">Net After Tax</div></div><div class="result-item"><div class="result-item-val">${((net/buy)*100).toFixed(1)}%</div><div class="result-item-lab">Effective Return</div></div></div><div class="verdict ${isLT?'good':'neutral'}">${isLT?`✅ Long-term (${months}mo) — lower tax.`:`⚠️ Short-term — hold until ${ltThresh[type]} months for lower tax.`}</div></div>`;
}
function calcHealth(){
  const inc=+document.getElementById('hInc').value,exp=+document.getElementById('hExp').value,emi=+document.getElementById('hEMI').value,save=+document.getElementById('hSave').value,ef=+document.getElementById('hEF').value,debt=+document.getElementById('hDebt').value,invest=+document.getElementById('hInvest').value,insure=+document.getElementById('hInsure').value,age=+document.getElementById('hAge').value;
  const savingsRate=save/inc,emiRatio=emi/inc,insureCover=insure*10000000/(inc*12*10);
  const scores={'Savings Rate':{val:Math.min(100,savingsRate*400),ideal:'>25%',yours:(savingsRate*100).toFixed(1)+'%',tip:savingsRate>=0.25?'✅ Excellent':'⚠️ Aim for 25%+'},'Emergency Fund':{val:Math.min(100,(ef/6)*100),ideal:'6 months',yours:ef+' months',tip:ef>=6?'✅ Well protected':`⚠️ Need ${6-ef} more`},'EMI Burden':{val:Math.max(0,100-(emiRatio*400)),ideal:'<30%',yours:(emiRatio*100).toFixed(1)+'%',tip:emiRatio<=0.3?'✅ Manageable':'⚠️ High — consider prepayment'},'Insurance':{val:Math.min(100,insureCover*80),ideal:'10x salary',yours:insure+'Cr',tip:insureCover>=1?'✅ Adequate':'⚠️ Increase life insurance'},'Investments':{val:Math.min(100,(invest/(inc*12*age*0.1))*100),ideal:'Age-based',yours:fmtC(invest),tip:'📊 Keep investing'},'Surplus':{val:Math.max(0,Math.min(100,((inc-exp-emi)/inc)*300)),ideal:'>20%',yours:fmtC(inc-exp-emi)+'/mo',tip:(inc-exp-emi)/inc>=0.2?'✅ Good surplus':'⚠️ Expenses too high'}};
  const totalScore=Math.round(Object.values(scores).reduce((s,v)=>s+v.val,0)/Object.keys(scores).length);
  const grade=totalScore>=80?{g:'A+',c:'var(--accent)',l:'Outstanding'}:totalScore>=70?{g:'A',c:'var(--accent)',l:'Excellent'}:totalScore>=60?{g:'B',c:'var(--gold)',l:'Good'}:totalScore>=50?{g:'C',c:'var(--gold)',l:'Needs Work'}:{g:'D',c:'var(--red)',l:'Critical'};
  let barsHtml='';
  Object.entries(scores).forEach(([name,s])=>{const color=s.val>=70?'var(--accent)':s.val>=50?'var(--gold)':'var(--red)';barsHtml+=`<div class="progress-row"><div class="progress-header"><span style="font-weight:600;font-size:0.82rem;">${name}</span><span style="font-size:0.8rem;font-family:var(--font-mono);color:${color};">${Math.round(s.val)}/100</span></div><div class="progress-bar"><div class="progress-fill" style="width:${s.val}%;background:${color};"></div></div><div style="font-size:0.72rem;color:var(--muted);margin-top:0.3rem;">${s.tip} · ${s.ideal} · ${s.yours}</div></div>`;});
  document.getElementById('healthResult').innerHTML=`<div class="calc-card"><div style="display:flex;align-items:center;gap:2rem;margin-bottom:2rem;flex-wrap:wrap;"><div style="text-align:center;"><div style="font-size:4rem;font-weight:800;font-family:var(--font-mono);color:${grade.c};">${grade.g}</div><div style="font-size:0.75rem;color:var(--muted);text-transform:uppercase;">${grade.l}</div></div><div style="flex:1;min-width:200px;"><div style="font-size:0.75rem;color:var(--muted);margin-bottom:0.3rem;">Overall Health</div><div style="font-size:2.5rem;font-weight:800;font-family:var(--font-mono);color:var(--accent);">${totalScore}<span style="font-size:1rem;color:var(--muted)">/100</span></div><div class="progress-bar" style="height:10px;margin-top:0.5rem;"><div class="progress-fill" style="width:${totalScore}%;background:linear-gradient(90deg,var(--accent3),var(--accent));"></div></div></div></div>${barsHtml}</div>`;
}

// ════════ PROFILE ════════
let userProfile={};
function saveProfile(){
  userProfile={salary:parseFloat(document.getElementById('pSalary').value)||0,expenses:parseFloat(document.getElementById('pExpenses').value)||0,emi:parseFloat(document.getElementById('pEMI').value)||0,sip:parseFloat(document.getElementById('pSIP').value)||0,age:parseInt(document.getElementById('pAge').value)||0,retireAge:parseInt(document.getElementById('pRetireAge').value)||60,emergency:parseFloat(document.getElementById('pEmergency').value)||0,savings:parseFloat(document.getElementById('pSavings').value)||0};
  try{localStorage.setItem('fv_profile',JSON.stringify(userProfile));}catch(e){}
}
function loadProfile(){
  try{const s=localStorage.getItem('fv_profile');if(s){userProfile=JSON.parse(s);document.getElementById('pSalary').value=userProfile.salary||'';document.getElementById('pExpenses').value=userProfile.expenses||'';document.getElementById('pEMI').value=userProfile.emi||'';document.getElementById('pSIP').value=userProfile.sip||'';document.getElementById('pAge').value=userProfile.age||'';document.getElementById('pRetireAge').value=userProfile.retireAge||'';document.getElementById('pEmergency').value=userProfile.emergency||'';document.getElementById('pSavings').value=userProfile.savings||'';}}catch(e){}
}
function applyProfile(){
  saveProfile();
  if(userProfile.expenses){const fe=document.getElementById('fireExp');if(fe){fe.value=Math.min(userProfile.expenses,500000);fe.dispatchEvent(new Event('input'));}}
  if(userProfile.age){const fa=document.getElementById('fireAge');if(fa){fa.value=userProfile.age;fa.dispatchEvent(new Event('input'));}const fr=document.getElementById('fireRetire');if(fr){fr.value=userProfile.retireAge;fr.dispatchEvent(new Event('input'));}}
  document.getElementById('profileMsg').style.display='block';setTimeout(()=>document.getElementById('profileMsg').style.display='none',4000);
}

// ════════ DASHBOARD ════════
function renderDashboard(){
  const np=document.getElementById('dashNoProfile'),dc=document.getElementById('dashContent');
  if(!userProfile.salary){np.style.display='block';dc.style.display='none';return;}
  np.style.display='none';dc.style.display='block';
  const{salary,expenses,emi,sip,age,retireAge,emergency,savings}=userProfile;
  const leftover=salary-expenses-emi-sip,investRate=salary>0?(sip/salary)*100:0,emergencyMonths=expenses>0?emergency/expenses:0,debtToIncome=salary>0?(emi/salary)*100:0;
  const statuses=[
    {icon:'🛡️',label:'Emergency Fund',val:fmtC(emergency),tag:emergencyMonths>=6?['ok','✅ Good']:emergencyMonths>=3?['warn','⚠️ Low']:['bad','❌ Critical'],note:`${emergencyMonths.toFixed(1)} months`},
    {icon:'📈',label:'Monthly SIP',val:fmtC(sip),tag:investRate>=20?['ok','✅ Excellent']:investRate>=10?['warn','⚠️ Moderate']:['bad','❌ Low'],note:`${investRate.toFixed(1)}% of income`},
    {icon:'🏦',label:'Debt/EMI',val:fmtC(emi),tag:debtToIncome<=30?['ok','✅ Healthy']:debtToIncome<=50?['warn','⚠️ High']:['bad','❌ Too High'],note:`${debtToIncome.toFixed(1)}% of income`},
    {icon:'💵',label:'Monthly Surplus',val:fmtC(Math.max(0,leftover)),tag:leftover>=salary*0.1?['ok','✅ Good']:leftover>=0?['warn','⚠️ Tight']:['bad','❌ Deficit'],note:leftover<0?'Overspending':'Available to invest'},
    {icon:'🎯',label:'Retirement In',val:`${Math.max(0,retireAge-age)} years`,tag:retireAge-age>=15?['ok','⏳ On track']:retireAge-age>=8?['warn','⚡ Urgent']:['bad','🔥 Critical'],note:`Target age ${retireAge}`},
    {icon:'💰',label:'Total Savings',val:fmtC(savings),tag:savings>=salary*12?['ok','✅ Strong']:savings>=salary*6?['warn','⚠️ Building']:['bad','❌ Low'],note:`~${(savings/salary).toFixed(1)} months salary`}
  ];
  document.getElementById('dashStatus').innerHTML=statuses.map(s=>`<div class="dash-item"><div class="dash-icon">${s.icon}</div><div><div class="dash-label">${s.label}</div><div class="dash-val">${s.val}</div><span class="dash-tag ${s.tag[0]}">${s.tag[1]}</span><div style="font-size:0.7rem;color:var(--muted);margin-top:0.3rem;">${s.note}</div></div></div>`).join('');
  const alerts=[];
  if(emergencyMonths<3)alerts.push('🚨 Emergency fund critical — build 6 months expenses first');
  if(debtToIncome>50)alerts.push('⚠️ EMI burden very high — consider prepaying loans');
  if(investRate<10)alerts.push('💡 Investing less than 10% of income — increase SIP');
  if(leftover<0)alerts.push('🔴 Spending exceeds income — review budget immediately');
  document.getElementById('dashAlerts').innerHTML=alerts.length?alerts.map(a=>`<span style="font-size:0.78rem;">${a}</span>`).join(' · '):`<span style="font-size:0.78rem;color:var(--accent);">✅ No critical alerts — finances look healthy!</span>`;
  const recs=[];
  if(emergencyMonths<6)recs.push(`Build emergency fund to ${fmtC(expenses*6)} (${fmtC(Math.max(0,expenses*6-emergency))} more needed)`);
  if(debtToIncome>40)recs.push(`Reduce EMI to below 30% of income. Target: ${fmtC(salary*0.3)}/mo`);
  if(investRate<20)recs.push(`Increase SIP from ${fmtC(sip)} to ${fmtC(salary*0.20)} (20% of income)`);
  if(leftover>salary*0.1)recs.push(`Deploy ${fmtC(leftover)} surplus — add to SIP or FD`);
  recs.push(`Max out PPF (${fmtC(150000)}/yr) + ELSS to reduce tax`);
  recs.push(`Health cover ≥ ${fmtC(500000)}/person, term life ≥ 10× annual income`);
  document.getElementById('dashRecs').innerHTML=recs.map((r,i)=>`<li class="rec-item"><div class="rec-num">${i+1}</div><span>${r}</span></li>`).join('');
  const yrs=Math.max(1,retireAge-age),proj=sip*(((Math.pow(1.12/12+1,yrs*12)-1)/(0.12/12))*(1+0.12/12)),fireNum=expenses*12*25;
  document.getElementById('dashScenarios').innerHTML=`<div style="display:flex;flex-direction:column;gap:0.8rem;"><div class="scenario-card" style="min-width:unset;text-align:left;padding:1rem;"><div class="scenario-label">SIP ${fmtC(sip)}/mo for ${yrs} yrs @12%</div><div class="scenario-val positive">${fmtC(Math.max(0,proj))}</div><div class="scenario-sub">Projected corpus</div></div><div class="scenario-card" style="min-width:unset;text-align:left;padding:1rem;${proj>=fireNum?'border-color:var(--accent);background:rgba(0,229,176,0.05)':''}"><div class="scenario-label">FIRE Number (25× annual expenses)</div><div class="scenario-val ${proj>=fireNum?'positive':'warning'}">${fmtC(fireNum)}</div><div class="scenario-sub">${proj>=fireNum?'✅ Your SIP plan covers this':`⚠️ ${fmtC(Math.max(0,fireNum-proj))} gap — increase SIP`}</div></div><div class="scenario-card" style="min-width:unset;text-align:left;padding:1rem;"><div class="scenario-label">If market crashes 30% today</div><div class="scenario-val warning">${fmtC((savings||0)*0.7)}</div><div class="scenario-sub">Hold, don't panic-sell</div></div></div>`;
}

// ════════ INIT ════════
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.section').forEach(s=>{if(!s.classList.contains('active'))s.style.display='none';});
  calcSIP();calcLS();calcGoal();calcROI();
  calcFIRE();calcCorpus();calcWithdraw();
  calcCrash();calcStopLoss();calcPortfolio();
  calcEMI();calcPrepay();calcTax();calcCG();calcHealth();
  loadProfile();
});
</script>
</body>
</html>"""

def build_html(crypto_data, stocks_data, signals, ticker_html, updated_at):
    # Build individual signal cards
    cards = {
        "gold":     render_signal_card("Gold",                signals["gold"],     "🥇"),
        "silver":   render_signal_card("Silver",              signals["silver"],   "🥈"),
        "crypto":   render_signal_card("Crypto (BTC/ETH)",    signals["crypto"],   "₿"),
        "stocks":   render_signal_card("Indian Stocks/Nifty", signals["stocks"],   "📈"),
        "usstocks": render_signal_card("US Markets",          signals["usstocks"], "🇺🇸"),
        "property": render_signal_card("Real Estate",         signals["property"], "🏠"),
        "fd":       render_signal_card("Fixed Deposits",      signals["fd"],       "🏦"),
    }

    # Build HTML using template substitution (avoids f-string brace conflicts with JS/CSS)
    template = _HTML_TEMPLATE
    html = template
    html = html.replace("__TICKER_HTML__", ticker_html)
    html = html.replace("__UPDATED_AT__", updated_at)
    html = html.replace("__CARD_GOLD__", cards["gold"])
    html = html.replace("__CARD_SILVER__", cards["silver"])
    html = html.replace("__CARD_CRYPTO__", cards["crypto"])
    html = html.replace("__CARD_STOCKS__", cards["stocks"])
    html = html.replace("__CARD_USSTOCKS__", cards["usstocks"])
    html = html.replace("__CARD_PROPERTY__", cards["property"])
    html = html.replace("__CARD_FD__", cards["fd"])
    return html

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="FinVault Static Site Generator")
    parser.add_argument("--dry", action="store_true", help="Print data, don't write file")
    args = parser.parse_args()

    print("🔄 FinVault Site Generator")
    print(f"   Output: {OUTPUT_FILE}\n")

    print("📡 Fetching crypto & metals data...")
    crypto_data = fetch_crypto()

    print("📈 Fetching stock data (Nifty, S&P 500, NASDAQ)...")
    stocks_data = fetch_stocks()

    print("🧮 Computing signals...")
    signals = {
        "gold":     sig_gold(crypto_data),
        "silver":   sig_silver(crypto_data),
        "crypto":   sig_crypto(crypto_data),
        "stocks":   sig_stocks(stocks_data),
        "usstocks": sig_usstocks(stocks_data, crypto_data),
        "property": sig_property(),
        "fd":       sig_fd(),
    }

    updated_at = datetime.datetime.utcnow().strftime("%d %b %Y %H:%M")

    if args.dry:
        print("\n📊 Signals:")
        for k, v in signals.items():
            print(f"  {k:12s} → {v['signal']}")
        print(f"\n⏱  Updated: {updated_at} UTC")
        return

    print("🏗️  Building HTML...")
    ticker_html = render_ticker(crypto_data, stocks_data)
    html = build_html(crypto_data, stocks_data, signals, ticker_html, updated_at)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\n✅ Generated {OUTPUT_FILE} ({size_kb:.1f} KB)")
    sigs_summary = ', '.join(f"{k}={v['signal']}" for k,v in signals.items())
    print(f"   Signals: {sigs_summary}")
    print(f"   Updated: {updated_at} UTC")
    print("\n🚀 Ready to push to GitHub!")

if __name__ == "__main__":
    main()
