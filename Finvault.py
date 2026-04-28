#!/usr/bin/env python3
"""
FinVault Pro — Static Site Generator
=====================================
Run this script to generate a fresh index.html with live market data baked in.
GitHub Actions runs this every hour automatically.

SETUP (run once):
  pip install requests yfinance firebase-admin

LOCAL DEV:
  Place your Firebase service account JSON as: serviceAccountKey.json  (git-ignored)

USAGE:
  python generate_site.py          # generates index.html in current folder
  python generate_site.py --dry    # prints data but doesn't write file

GITHUB ACTIONS SECRETS REQUIRED:
  FIREBASE_SERVICE_ACCOUNT  — full contents of the service account .json file
  FIREBASE_PROJECT_ID       — e.g. finvault-pro-910dc

GITHUB ACTIONS:
  See .github/workflows/update.yml — runs this every hour, commits index.html
"""

import sys, os, datetime, json, time, argparse

# ── auto-install ──────────────────────────────────────────────
def install(pkg):
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for pkg in ["requests", "yfinance", "firebase_admin"]:
    try: __import__(pkg)
    except ImportError: install("firebase-admin" if pkg == "firebase_admin" else pkg)

import requests
import yfinance as yf

# ─────────────────────────────────────────────────────────────
# FIREBASE — Firestore replaces data.json (LKG cache)
# ─────────────────────────────────────────────────────────────
import tempfile
import firebase_admin
from firebase_admin import credentials, firestore as fs_module

_db = None   # singleton

def get_firebase_db():
    """Return Firestore client. Initialises Firebase on first call."""
    global _db
    if _db is not None:
        return _db
    if firebase_admin._apps:
        _db = fs_module.client()
        return _db

    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if sa_json:
        # ── Running in GitHub Actions: creds come from the secret ──
        try:
            sa_dict = json.loads(sa_json)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT secret is not valid JSON: {e}")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sa_dict, f)
            tmp_path = f.name
        cred = credentials.Certificate(tmp_path)
    else:
        # ── Local dev: place your downloaded key file here ──
        local_key = "serviceAccountKey.json"
        if not os.path.exists(local_key):
            raise FileNotFoundError(
                "No FIREBASE_SERVICE_ACCOUNT env var and no serviceAccountKey.json found.\n"
                "For local dev: download your Firebase service-account key and save it as serviceAccountKey.json"
            )
        cred = credentials.Certificate(local_key)

    firebase_admin.initialize_app(cred)
    _db = fs_module.client()
    return _db

# ─────────────────────────────────────────────────────────────
# CONFIG — tweak as needed
# ─────────────────────────────────────────────────────────────
REPO_RATE     = 6.25   # RBI repo rate % — UPDATE after every RBI MPC meeting (held ~every 6 weeks)
                       # RBI MPC schedule: https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx
PREV_REPO     = 6.50   # previous rate — determines cutting/hiking/stable cycle direction
HOME_LOAN     = 8.50   # avg home loan rate %
OUTPUT_FILE   = "index.html"
LKG_FILE      = "data.json"   # Last Known Good data cache — fallback if APIs fail

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 FinVault/3.0", "Accept": "application/json"})

# ─────────────────────────────────────────────────────────────
# FETCH HELPERS
# ─────────────────────────────────────────────────────────────
def save_lkg(crypto_data, stocks_data):
    """Save market data to Firestore (primary) with local file as fallback."""
    payload = {
        "crypto": crypto_data,
        "stocks": stocks_data,
        "saved_at": datetime.datetime.utcnow().isoformat()
    }
    # ── Firestore ──
    try:
        db = get_firebase_db()
        db.collection("market_data").document("latest").set(payload)
        print("  ✅ Saved to Firestore")
    except Exception as e:
        print(f"  ⚠ Firestore save failed: {e}")
    # ── Local fallback ──
    try:
        with open(LKG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"  ⚠ Local LKG save failed: {e}")

def load_lkg():
    """Load last-known-good data — Firestore first, local file as fallback."""
    # ── Try Firestore ──
    try:
        db = get_firebase_db()
        doc = db.collection("market_data").document("latest").get()
        if doc.exists:
            d = doc.to_dict()
            print(f"  ↩ Using Firestore data from {d.get('saved_at', 'unknown')}")
            return d.get("crypto", {}), d.get("stocks", {})
    except Exception as e:
        print(f"  ⚠ Firestore load failed: {e}")
    # ── Fallback: local file ──
    try:
        with open(LKG_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        saved_at = d.get("saved_at", "unknown")
        print(f"  ↩ Using local LKG data from {saved_at}")
        return d.get("crypto", {}), d.get("stocks", {})
    except Exception:
        return {}, {}

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
    data = {}
    try:
        prices = fetch(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,pax-gold,wrapped-bitcoin,staked-ether"
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
        if "wrapped-bitcoin" in prices:
            data["wbtc_usd"]  = prices["wrapped-bitcoin"]["usd"]
            data["wbtc_24h"]  = prices["wrapped-bitcoin"].get("usd_24h_change", 0) or 0
        if "staked-ether" in prices:
            data["steth_usd"] = prices["staked-ether"]["usd"]
            data["steth_24h"] = prices["staked-ether"].get("usd_24h_change", 0) or 0
    except Exception as e:
        print(f"  ⚠ CoinGecko prices failed: {e}")

    try:
        fg = fetch("https://api.alternative.me/fng/?limit=8")
        data["fear_score"] = int(fg["data"][0]["value"])
        data["fear_label"] = fg["data"][0]["value_classification"]
        data["fear_history"] = [int(x["value"]) for x in reversed(fg["data"])]
    except Exception as e:
        print(f"  ⚠ Fear & Greed failed: {e}")
        data["fear_score"] = None
        data["fear_label"] = "Unavailable"
        data["fear_history"] = []

    for coin_id, key in [("pax-gold", "gold_detail")]:
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
    data = {}
    for ticker, key in [("^VIX", "vix"), ("^INDIAVIX", "india_vix")]:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                data[key] = float(hist["Close"].iloc[-1])
        except Exception as e:
            print(f"  ⚠ {ticker} failed: {e}")
            data[key] = None

    for ticker, key in [("^NSEI", "nifty"), ("^GSPC", "sp500"), ("^IXIC", "nasdaq")]:
        try:
            hist = yf.Ticker(ticker).history(period="12mo")
            if hist.empty:
                data[key] = None
                continue
            closes   = hist["Close"]
            price    = float(closes.iloc[-1])
            prev     = float(closes.iloc[-2])
            chg      = (price - prev) / prev * 100
            ma20     = float(closes.iloc[-20:].mean())
            ma50     = float(closes.iloc[-50:].mean()) if len(closes) >= 50 else float(closes.mean())
            ma200    = float(closes.iloc[-200:].mean()) if len(closes) >= 200 else float(closes.mean())
            prev_ma50  = float(closes.iloc[-51:-1].mean()) if len(closes) >= 51 else ma50
            prev_ma200 = float(closes.iloc[-201:-1].mean()) if len(closes) >= 201 else ma200
            golden_cross = (ma50 > ma200) and (prev_ma50 <= prev_ma200)
            death_cross  = (ma50 < ma200) and (prev_ma50 >= prev_ma200)
            # Wilder's RSI: seed with simple mean for first 14 bars, then EMA (alpha=1/14)
            delta    = closes.diff()
            gain_s   = delta.clip(lower=0)
            loss_s   = (-delta.clip(upper=0))
            gain     = gain_s.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            loss     = loss_s.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rsi      = float((100 - 100 / (1 + gain / loss)).iloc[-1])
            # MACD: 12-day EMA minus 26-day EMA; Signal = 9-day EMA of MACD line
            ema12     = closes.ewm(span=12, adjust=False).mean()
            ema26     = closes.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist   = macd_line - signal_line
            macd_val    = float(macd_line.iloc[-1])
            macd_sig    = float(signal_line.iloc[-1])
            macd_cross_bull = (macd_val > macd_sig) and (float(macd_line.iloc[-2]) <= float(signal_line.iloc[-2]))
            macd_cross_bear = (macd_val < macd_sig) and (float(macd_line.iloc[-2]) >= float(signal_line.iloc[-2]))
            data[key] = {"price": price, "prev": prev, "chg": chg,
                         "ma20": ma20, "ma50": ma50, "ma200": ma200,
                         "golden_cross": golden_cross, "death_cross": death_cross,
                         "above_200": price > ma200, "rsi": rsi,
                         "macd": macd_val, "macd_signal": macd_sig,
                         "macd_cross_bull": macd_cross_bull, "macd_cross_bear": macd_cross_bear}
        except Exception as e:
            print(f"  ⚠ {ticker} failed: {e}")
            data[key] = None

    for ticker, key in [("SI=F", "silver_yf"), ("GC=F", "gold_yf")]:
        try:
            hist = yf.Ticker(ticker).history(period="3mo")
            if hist.empty:
                data[key] = None
                continue
            closes = hist["Close"]
            price  = float(closes.iloc[-1])
            prev   = float(closes.iloc[-2])
            chg    = (price - prev) / prev * 100
            ma50   = float(closes.iloc[-50:].mean()) if len(closes) >= 50 else float(closes.mean())
            ath    = float(closes.max())
            below_ath = (ath - price) / ath * 100
            data[key] = {"price": price, "chg": chg, "ma50": ma50,
                         "below_ath": below_ath, "ath": ath}
        except Exception as e:
            print(f"  ⚠ {ticker} failed: {e}")
            data[key] = None

    # Gold-to-Silver Ratio
    try:
        gold_p   = data.get("gold_yf", {}) or {}
        silver_p = data.get("silver_yf", {}) or {}
        if gold_p.get("price") and silver_p.get("price") and silver_p["price"] > 0:
            data["gold_silver_ratio"] = gold_p["price"] / silver_p["price"]
        else:
            data["gold_silver_ratio"] = None
    except Exception:
        data["gold_silver_ratio"] = None
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
        s,c = "BUY","buy"
        reasons = [f"Gold is {below_ath:.1f}% below ATH — historically strong entry zone",
                   "30-day trend stabilising — not in free-fall",
                   "Ideal: Sovereign Gold Bonds (SGB) or Gold ETF for tax efficiency"]
    elif below_ath < 5:
        s,c = "WAIT","wait"
        reasons = [f"Gold near all-time high (only {below_ath:.1f}% below) — expensive",
                   "Set a price alert; enter on 10–15% correction"]
    else:
        s,c = "HOLD","hold"
        reasons = [f"Gold {below_ath:.1f}% below ATH — fair-value zone",
                   "Hold existing; accumulate gradually on dips"]
    if g["c24h"] < -1.5: reasons.append(f"24h dip {g['c24h']:.2f}% — short-term opportunity")
    if g["c7d"] > 5: reasons.append(f"7d rally +{g['c7d']:.1f}% — wait for pullback before adding")
    metrics = {"USD Price": usd(g["usd"]), "INR Price": inr_fmt(g["inr"]),
               "24h Change": pct(g["c24h"]), "30d Change": pct(g["c30d"]),
               "vs ATH": f"{below_ath:.1f}% below"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"CoinGecko (PAXG proxy)","context":"SGB offers 2.5% annual interest on top of gold returns — best option for Indian investors."}

def sig_silver(d, stocks):
    sy = stocks.get("silver_yf")
    if not sy:
        return {"signal":"HOLD","cls":"hold","reasons":["Silver data unavailable — yfinance SI=F failed"],"metrics":{}}
    below_ath = sy["below_ath"]
    price     = sy["price"]
    chg       = sy["chg"]
    ab50      = price > sy["ma50"]
    gsr       = stocks.get("gold_silver_ratio")
    if below_ath > 40:
        s,c = "BUY","buy"
        reasons = [f"Silver {below_ath:.1f}% below 3-month high — deeply discounted",
                   "Silver outperforms gold in later bull-market stages",
                   "Industrial demand (EVs, solar panels) adds structural tailwind"]
    elif below_ath < 10:
        s,c = "WAIT","wait"
        reasons = [f"Silver near 3-month high (only {below_ath:.1f}% below) — wait for pullback",
                   "Silver is 2–3× more volatile than gold — don't chase breakouts"]
    else:
        s,c = "HOLD","hold"
        reasons = [f"Silver {below_ath:.1f}% below recent high — gradual accumulation zone",
                   "Silver is 2–3× more volatile than gold — keep position sizing smaller"]
    if gsr is not None:
        if gsr > 80:
            reasons.append(f"Gold/Silver Ratio = {gsr:.1f} — historically high; silver undervalued vs gold, favours silver accumulation")
        elif gsr < 50:
            reasons.append(f"Gold/Silver Ratio = {gsr:.1f} — historically low; silver may be overvalued relative to gold")
        else:
            reasons.append(f"Gold/Silver Ratio = {gsr:.1f} — within normal historical range (50–80)")
    if chg < -2: reasons.append(f"Today -{abs(chg):.1f}% — short-term dip, potential entry")
    if not ab50: reasons.append("Below 50-day MA — wait for stabilization or buy in tranches")
    reasons.append("For India: Nippon India Silver ETF or ICICI Prudential Silver ETF on NSE")
    metrics = {
        "Silver (USD/oz)": f"${price:.2f}  ({pct(chg)} today)",
        "50-Day MA":       f"${sy['ma50']:.2f}  ({'above' if ab50 else 'below'})",
        "vs 3-Month High": f"{below_ath:.1f}% below",
        "Gold/Silver Ratio": f"{gsr:.1f}" if gsr else "N/A",
        "Source":          "Yahoo Finance SI=F (silver futures)"
    }
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Yahoo Finance (SI=F silver futures)","context":"No SGB for silver — use Silver ETFs on NSE for tax-efficient paper exposure."}

def sig_crypto(d):
    fs = d.get("fear_score")
    fl = d.get("fear_label","N/A")
    btc24 = d.get("btc_24h", 0)
    if fs is not None:
        if fs <= 25:   s,c = "BUY","buy"
        elif fs >= 75: s,c = "WAIT","wait"
        else:          s,c = "HOLD","hold"
    else:
        s,c = "HOLD","hold"
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
    if d.get("wbtc_usd"): metrics["Wrapped BTC (DeFi)"] = f"{usd(d['wbtc_usd'])}  ({pct(d.get('wbtc_24h',0))} 24h)"
    if d.get("steth_usd"): metrics["Staked ETH (stETH)"] = f"{usd(d['steth_usd'])}  ({pct(d.get('steth_24h',0))} 24h)"
    if d.get("btc_mcap"): metrics["BTC Market Cap"] = f"${d['btc_mcap']/1e9:.0f}B"
    metrics["Fear & Greed"] = f"{fs}/100 — {fl}" if fs else "N/A"
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "fear_history": d.get("fear_history", []),
            "source":"CoinGecko + alternative.me","context":"Never put more than 5–10% of portfolio in crypto. It's speculation, not investment."}

def sig_stocks(stocks):
    n = stocks.get("nifty")
    india_vix = stocks.get("india_vix")
    if not n:
        return {"signal":"HOLD","cls":"hold","reasons":["Nifty data unavailable"],"metrics":{}}
    ab20, ab50 = n["price"] > n["ma20"], n["price"] > n["ma50"]
    ab200 = n.get("above_200", True)
    rsi = n["rsi"]
    high_vol = india_vix is not None and india_vix > 18  # tightened from 20 — India VIX structurally lower than US VIX
    rsi_ob  = 65 if high_vol else 70
    rsi_os  = 45 if high_vol else 40
    golden = n.get("golden_cross", False)
    death  = n.get("death_cross", False)
    if not ab50 and not ab20 and not ab200:
        s,c = "BUY","buy"
        reasons = ["Nifty below 20, 50 & 200-day MAs — deep correction zone",
                   "Historically: 10–20% corrections are excellent long-term entry points",
                   "Increase SIP or deploy lump-sum in 3–4 tranches over 4 weeks"]
    elif not ab50 and not ab20:
        s,c = "BUY","buy"
        reasons = ["Nifty below both 20 & 50-day MAs — correction zone",
                   "Historically: corrections are excellent long-term entry points",
                   "Increase SIP or deploy lump-sum in 3–4 tranches over 4 weeks"]
    elif ab50 and ab20 and rsi > rsi_ob:
        s,c = "WAIT","wait"
        reasons = [f"RSI = {rsi:.0f} — overbought {'(VIX-adjusted threshold)' if high_vol else ''}. Short-term pullback likely",
                   "Hold existing; avoid fresh lump-sum at these levels",
                   f"Wait for RSI to cool below {rsi_ob-5} before adding positions"]
    elif ab50 and ab20:
        s,c = "HOLD","hold"
        reasons = ["Nifty in healthy uptrend above both 20 & 50-day MAs",
                   "Continue monthly SIP — avoid large lump-sum at these highs"]
    else:
        s,c = "HOLD","hold"
        reasons = ["Mixed MA signals — wait for clear breakout above 50-day MA"]
    if golden: reasons.insert(0, "Golden Cross detected (50-day MA crossed above 200-day) — strong long-term bull signal")
    if death:  reasons.insert(0, "Death Cross detected (50-day MA crossed below 200-day) — long-term bearish signal; be cautious")
    if n.get("macd_cross_bull"): reasons.append("MACD bullish crossover — momentum turning positive, supports BUY signal")
    if n.get("macd_cross_bear"): reasons.append("MACD bearish crossover — momentum turning negative, be cautious with new entries")
    if high_vol: reasons.append(f"India VIX = {india_vix:.1f} (elevated) — widen entry zones, use tranches not lump-sum")
    if rsi < rsi_os: reasons.append(f"RSI = {rsi:.0f} — oversold. Buying pressure may build soon")
    if n["chg"] < -2: reasons.append(f"Today -{abs(n['chg']):.1f}% — potential short-term entry")
    metrics = {
        "Nifty 50": f"{n['price']:,.0f}  ({pct(n['chg'])} today)",
        "20-Day MA": f"{n['ma20']:,.0f}  ({'above' if ab20 else 'below'})",
        "50-Day MA": f"{n['ma50']:,.0f}  ({'above' if ab50 else 'below'})",
        "200-Day MA": f"{n['ma200']:,.0f}  ({'above' if ab200 else 'below'})",
        "RSI (14)":  f"{rsi:.1f}  ({'Oversold' if rsi<rsi_os else 'Overbought' if rsi>rsi_ob else 'Neutral'})",
        "MACD":      f"{n.get('macd',0):.1f}  (signal {n.get('macd_signal',0):.1f})",
    }
    if india_vix: metrics["India VIX"] = f"{india_vix:.1f}  ({'High' if india_vix > 18 else 'Normal'})"
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Yahoo Finance via yfinance","context":"Time in market beats timing the market. 5+ year horizon? Any dip is a buying opportunity."}

def sig_usstocks(stocks, d):
    sp = stocks.get("sp500")
    fs = d.get("fear_score")
    fl = d.get("fear_label","N/A")
    vix = stocks.get("vix")
    if not sp:
        return {"signal":"HOLD","cls":"hold","reasons":["S&P 500 data unavailable"],"metrics":{}}
    ab50  = sp["price"] > sp["ma50"]
    ab200 = sp.get("above_200", True)
    rsi   = sp["rsi"]
    golden = sp.get("golden_cross", False)
    death  = sp.get("death_cross", False)
    high_vol = vix is not None and vix > 25
    rsi_ob   = 72 if high_vol else 75
    if fs is not None and fs <= 25:
        s,c = "BUY","buy"
        reasons = [f"Fear & Greed = {fs}/100 ({fl}) — extreme fear = historically best accumulation zone",
                   "S&P 500 corrections during extreme fear yield avg +18% over next 12 months historically",
                   "DCA into VOO / IVV. Don't try to catch the bottom."]
    elif (fs is not None and fs >= 75) or rsi > rsi_ob:
        s,c = "WAIT","wait"
        reasons = [f"Overbought: RSI={rsi:.0f}, Fear & Greed={fs if fs else 'N/A'}/100",
                   "Hold existing index funds; avoid new lump-sum entries"]
    elif ab50:
        s,c = "HOLD","hold"
        reasons = [f"S&P 500 above 50-day MA ({sp['ma50']:,.0f}) — uptrend intact",
                   "Continue regular DCA. Don't time the market."]
    else:
        s,c = "BUY","buy"
        reasons = [f"S&P 500 below 50-day MA ({sp['ma50']:,.0f}) — pullback zone, good long-term entry",
                   "Index fund investors: good accumulation opportunity"]
    if golden: reasons.insert(0, "Golden Cross on S&P 500 — 50-day MA crossed above 200-day. Historically bullish long-term signal")
    if death:  reasons.insert(0, "Death Cross on S&P 500 — 50-day MA crossed below 200-day. Proceed cautiously, reduce lump-sum")
    if sp.get("macd_cross_bull"): reasons.append("MACD bullish crossover on S&P 500 — momentum turning positive, confirms BUY signal")
    if sp.get("macd_cross_bear"): reasons.append("MACD bearish crossover on S&P 500 — momentum weakening, wait before adding positions")
    if high_vol: reasons.append(f"VIX = {vix:.1f} (elevated fear) — use tranches over 4–6 weeks, not single lump-sum")
    if not ab200: reasons.append(f"S&P 500 below 200-day MA ({sp['ma200']:,.0f}) — long-term downtrend; scale in gradually")
    reasons.append("For INR investors: Factor in USD/INR currency risk. Aim for 20–30% global allocation.")
    nq = stocks.get("nasdaq")
    metrics = {
        "S&P 500": f"{sp['price']:,.0f}  ({pct(sp['chg'])} today)",
        "NASDAQ":  f"{nq['price']:,.0f}" if nq else "N/A",
        "S&P 50-Day MA":  f"{sp['ma50']:,.0f}  ({'above' if ab50 else 'below'})",
        "S&P 200-Day MA": f"{sp['ma200']:,.0f}  ({'above' if ab200 else 'below'})",
        "RSI (14)": f"{rsi:.1f}",
        "MACD":     f"{sp.get('macd',0):.1f}  (signal {sp.get('macd_signal',0):.1f})",
        "Fear & Greed": f"{fs}/100 — {fl}" if fs else "N/A",
    }
    if vix: metrics["CBOE VIX"] = f"{vix:.1f}  ({'Elevated' if high_vol else 'Normal'})"
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Yahoo Finance + alternative.me Fear & Greed","context":"For US-based investors: S&P 500 index funds (VOO, FXAIX) are the gold standard for long-term wealth."}

def sig_property():
    direction = "cutting" if REPO_RATE < PREV_REPO else "hiking" if REPO_RATE > PREV_REPO else "stable"
    if direction == "cutting":
        s,c = "BUY","buy"
        reasons = [f"RBI in rate-cutting cycle (repo {REPO_RATE}%) — home loan EMIs falling",
                   "Property demand typically rises 12–18 months after cuts begin",
                   "Lock in a home loan now before banks pass on the full rate cuts"]
    elif direction == "hiking":
        s,c = "WAIT","wait"
        reasons = [f"RBI hiking rates (repo {REPO_RATE}%) — home loans expensive",
                   "Wait 12–18 months for rate cycle to peak before buying"]
    else:
        s,c = "HOLD","hold"
        reasons = [f"Rates stable at {REPO_RATE}% — negotiate hard with builders"]
    reasons += ["Only buy if you plan to hold 7–10+ years",
                "REIT alternative: Mindspace/Brookfield (7–8% yield + liquidity)"]
    gross_rental = 3.0
    maintenance  = gross_rental * 0.12          # ~12% maintenance cost on gross rent
    net_before_s24 = gross_rental - maintenance
    s24_deduction   = net_before_s24 * 0.30    # Section 24: 30% standard deduction on net rent
    taxable_rental  = net_before_s24 - s24_deduction
    pt_rental_10 = gross_rental - (taxable_rental * 0.10)
    pt_rental_30 = gross_rental - (taxable_rental * 0.30)
    metrics = {"RBI Repo Rate": f"{REPO_RATE}%  ({direction.upper()})",
               "Avg Home Loan": f"~{HOME_LOAN}%", "Ideal Hold": "7–10 years min",
               "Gross Rental Yield": f"~{gross_rental:.1f}% (metro avg)",
               "Net Taxable Yield": f"~{taxable_rental:.2f}% (after 12% maintenance + Sec 24)",
               "Post-Tax Yield (10% slab)": f"~{pt_rental_10:.2f}%",
               "Post-Tax Yield (30% slab)": f"~{pt_rental_30:.2f}%",
               "Appreciation": "5–12%/yr (city-dependent)"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":f"RBI rate cycle analysis. Repo rate: {REPO_RATE}%","context":"Location beats timing in real estate. Buy right, not just cheap."}

def sig_fd():
    direction = "cutting" if REPO_RATE < PREV_REPO else "hiking" if REPO_RATE > PREV_REPO else "stable"
    if direction == "cutting":
        s,c = "BUY","buy"
        reasons = [f"RBI cutting rates → FD rates WILL fall in 6–12 months. Lock in NOW.",
                   "Consider 3–5 year FDs before banks reduce rates",
                   "Small Finance Banks: AU, ESAF, Jana — 8.5–9.0% (DICGC insured ₹5L)",
                   "Avoid short-term FDs — renewals will be at lower rates"]
    elif direction == "hiking":
        s,c = "BUY","buy"
        reasons = ["Rates rising — roll short-term FDs to capture rate hikes"]
    else:
        s,c = "BUY","buy"
        reasons = ["Stable high rates — FD returns competitive with equity risk"]
    reasons.append("FD income is taxable at slab rate — compare with debt MFs for post-tax returns")
    fd_rate      = 8.5
    inflation    = 5.0   # approximate CPI inflation — update periodically
    real_rate    = fd_rate - inflation
    pt_10 = fd_rate * (1 - 0.10)
    pt_20 = fd_rate * (1 - 0.20)
    pt_30 = fd_rate * (1 - 0.30)
    metrics = {"Big Bank FD": "~7.0–7.5% p.a.", "Small Finance Bank": "~8.5–9.0%",
               "RBI Savings Bond": "~7.35% (govt-backed)",
               "Debt MF (post Apr 2023)": "~7–8% (taxed at slab rate — indexation removed)",
               "Repo Rate": f"{REPO_RATE}% ({direction})",
               "Real Rate of Return": f"~{real_rate:.1f}% (FD rate − {inflation:.0f}% inflation)",
               "Post-Tax Yield (10%)": f"~{pt_10:.2f}% on SFB FD",
               "Post-Tax Yield (20%)": f"~{pt_20:.2f}% on SFB FD",
               "Post-Tax Yield (30%)": f"~{pt_30:.2f}% on SFB FD"}
    return {"signal":s,"cls":c,"reasons":reasons,"metrics":metrics,
            "source":"Public bank disclosures + RBI policy","context":"FDs are taxable — for high earners, tax-free bonds or PPF may give better post-tax returns. Note: Debt MFs purchased after April 1, 2023 are taxed at slab rates (indexation benefit removed), making them similar to FDs for tax purposes."}

# ─────────────────────────────────────────────────────────────
# MARKET STATUS
# ─────────────────────────────────────────────────────────────
def get_market_status():
    now_utc = datetime.datetime.utcnow()
    ist_offset = datetime.timedelta(hours=5, minutes=30)
    now_ist = now_utc + ist_offset
    edt_offset = datetime.timedelta(hours=-4)
    now_edt = now_utc + edt_offset

    def is_nse_open(t):
        if t.weekday() >= 5: return False
        open_t  = t.replace(hour=9,  minute=15, second=0, microsecond=0)
        close_t = t.replace(hour=15, minute=30, second=0, microsecond=0)
        return open_t <= t <= close_t

    def is_nyse_open(t):
        if t.weekday() >= 5: return False
        open_t  = t.replace(hour=9,  minute=30, second=0, microsecond=0)
        close_t = t.replace(hour=16, minute=0,  second=0, microsecond=0)
        return open_t <= t <= close_t

    nse_open  = is_nse_open(now_ist)
    nyse_open = is_nyse_open(now_edt)
    return {
        "nse":       "OPEN"   if nse_open  else "CLOSED",
        "nyse":      "OPEN"   if nyse_open else "CLOSED",
        "nse_dot":   "green"  if nse_open  else "red",
        "nyse_dot":  "green"  if nyse_open else "red",
        "nse_time":  now_ist.strftime("%H:%M IST"),
        "nyse_time": now_edt.strftime("%H:%M EDT"),
    }

# ─────────────────────────────────────────────────────────────
# HTML RENDERERS — Professional redesign
# ─────────────────────────────────────────────────────────────
def render_ticker(d, stocks):
    """Build the professional ticker bar HTML (duplicated for seamless loop)."""
    items = []

    def tick(sym, price_str, chg):
        cls   = "chg-up" if chg >= 0 else "chg-dn"
        arrow = "▲" if chg >= 0 else "▼"
        return (f'<span class="tick">'
                f'<span class="tick-sym">{sym}</span>'
                f'<span class="tick-price">{price_str}</span>'
                f'<span class="tick-sep">|</span>'
                f'<span class="tick-chg {cls}">{arrow} {abs(chg):.2f}%</span>'
                f'</span>')

    if d.get("btc_usd"):
        items.append(tick("BTC", f"${d['btc_usd']:,.0f}", d["btc_24h"]))
    if d.get("eth_usd"):
        items.append(tick("ETH", f"${d['eth_usd']:,.0f}", d["eth_24h"]))
    if d.get("gold_usd"):
        items.append(tick("GOLD", f"${d['gold_usd']:,.0f}/oz", d["gold_24h"]))
    sy = stocks.get("silver_yf")
    if sy:
        items.append(tick("SILVER", f"${sy['price']:.2f}/oz", sy["chg"]))
    n = stocks.get("nifty")
    if n:
        items.append(tick("NIFTY 50", f"{n['price']:,.0f}", n["chg"]))
    sp = stocks.get("sp500")
    if sp:
        items.append(tick("S&amp;P 500", f"{sp['price']:,.0f}", sp["chg"]))
    nq = stocks.get("nasdaq")
    if nq:
        items.append(tick("NASDAQ", f"{nq['price']:,.0f}", nq["chg"]))

    vix = stocks.get("vix")
    if vix:
        cls = "chg-dn" if vix > 25 else "chg-up"
        label = "ELEVATED" if vix > 25 else "NORMAL"
        items.append(
            f'<span class="tick"><span class="tick-sym">VIX</span>'
            f'<span class="tick-price {cls}">{vix:.1f}</span>'
            f'<span class="tick-sep">|</span>'
            f'<span class="tick-chg" style="color:var(--text2)">{label}</span></span>'
        )
    india_vix = stocks.get("india_vix")
    if india_vix:
        cls = "chg-dn" if india_vix > 18 else "chg-up"
        label = "ELEVATED" if india_vix > 18 else "NORMAL"
        items.append(
            f'<span class="tick"><span class="tick-sym">INDIA VIX</span>'
            f'<span class="tick-price {cls}">{india_vix:.1f}</span>'
            f'<span class="tick-sep">|</span>'
            f'<span class="tick-chg" style="color:var(--text2)">{label}</span></span>'
        )
    fs = d.get("fear_score")
    if fs is not None:
        fl = d.get("fear_label","")
        cls = "chg-up" if fs <= 40 else "chg-dn" if fs >= 60 else ""
        items.append(
            f'<span class="tick"><span class="tick-sym">F&amp;G INDEX</span>'
            f'<span class="tick-price {cls}">{fs}/100</span>'
            f'<span class="tick-sep">|</span>'
            f'<span class="tick-chg" style="color:var(--text2)">{fl.upper()}</span></span>'
        )
    if d.get("wbtc_usd"):
        items.append(tick("WBTC", f"${d['wbtc_usd']:,.0f}", d.get("wbtc_24h", 0)))
    if d.get("steth_usd"):
        items.append(tick("stETH", f"${d['steth_usd']:,.0f}", d.get("steth_24h", 0)))

    doubled = items + items
    return "".join(doubled)


def render_signal_card(asset_name, sig, asset_key):
    """Render a sidebar signal card (compact version for the sidebar)."""
    badge_cls = {"buy":"badge-buy","hold":"badge-hold","wait":"badge-wait"}.get(sig["cls"],"badge-hold")
    return (
        f'<div class="sidebar-item js-sig-item" data-asset="{asset_key}" onclick="selectSignal(\'{asset_key}\')">'
        f'<div class="sidebar-item-left">'
        f'<span class="sidebar-item-name">{asset_name}</span>'
        f'<span class="sidebar-item-desc">{sig.get("source","")[:45]}</span>'
        f'</div>'
        f'<span class="signal-badge {badge_cls}">{sig["signal"]}</span>'
        f'</div>'
    )


def render_signal_detail(asset_name, sig):
    """Render the expanded signal detail panel (baked-in HTML per asset)."""
    badge_cls = {"buy":"badge-buy","hold":"badge-hold","wait":"badge-wait"}.get(sig["cls"],"badge-hold")
    metrics_html = ""
    for k, v in sig.get("metrics", {}).items():
        metrics_html += (
            f'<div class="detail-metric">'
            f'<div class="detail-metric-val">{v}</div>'
            f'<div class="detail-metric-label">{k}</div>'
            f'</div>'
        )
    marker_cls = {"buy":"rm-buy","hold":"rm-hold","wait":"rm-wait"}.get(sig["cls"],"rm-hold")
    reasons_html = ""
    for r in sig.get("reasons", []):
        reasons_html += (
            f'<div class="reason-line">'
            f'<span class="reason-marker {marker_cls}">›</span>'
            f'<span>{r}</span>'
            f'</div>'
        )
    context = sig.get("context","")
    context_html = ""
    if context:
        context_html = f'<div class="context-note"><strong style="color:var(--blue)">Pro Tip:</strong> {context}</div>'

    # Sparkline for F&G history (crypto card)
    spark_html = ""
    fh = sig.get("fear_history", [])
    if fh and len(fh) >= 2:
        mn, mx = min(fh), max(fh)
        rng = mx - mn or 1
        n_pts = len(fh)
        pts = []
        for i, v in enumerate(fh):
            x = round(i / (n_pts - 1) * 280, 1)
            y = round(40 - (v - mn) / rng * 36 - 2, 1)
            pts.append(f"{x},{y}")
        last = fh[-1]
        sc = "#00c07f" if last <= 40 else "#e84040" if last >= 60 else "#e8a825"
        pts_str = " ".join(pts)
        spark_html = (
            f'<div class="sparkline-wrap">'
            f'<div class="spark-label">Fear &amp; Greed — 8-day trend</div>'
            f'<svg viewBox="0 0 280 44" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:44px;margin-top:4px;">'
            f'<polyline points="{pts_str}" fill="none" stroke="{sc}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg>'
            f'</div>'
        )

    return (
        f'<div class="signal-detail-panel" id="detail-{asset_name.lower().replace(" ","_").replace("/","_")}">'
        f'<div class="detail-header">'
        f'<div class="detail-title">{asset_name}</div>'
        f'<span class="signal-badge {badge_cls} detail-signal-large">{sig["signal"]}</span>'
        f'</div>'
        f'<div class="detail-metrics">{metrics_html}</div>'
        f'{spark_html}'
        f'<div class="reasons-panel">'
        f'<div class="reasons-label">Signal Rationale</div>'
        f'{reasons_html}'
        f'</div>'
        f'{context_html}'
        f'<div class="disclaimer">Data source: {sig.get("source","")} · Educational only — not investment advice. Past performance is not indicative of future results.</div>'
        f'</div>'
    )


# ─────────────────────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────────────────────
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FinVault Pro — Financial Intelligence Platform</title>
<meta name="description" content="Professional financial calculators and live market signals. SIP, FIRE, EMI, tax, and BUY/HOLD/WAIT signals for gold, crypto, stocks, property.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Epilogue:wght@300;400;500;600;700;800;900&family=IBM+Plex+Mono:wght@300;400;500;600&display=swap" rel="stylesheet">

<!-- ── Firebase Google Auth ─────────────────────────────── -->
<script type="module">
  import { initializeApp }
    from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
  import { getAuth, GoogleAuthProvider, signInWithPopup, onAuthStateChanged, signOut }
    from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

  // ⚠ These are FRONTEND (web) config values — safe to embed.
  // Get them from: Firebase Console → Project Settings → General → Your apps → Web app
  const firebaseConfig = {
    apiKey:            "__FIREBASE_API_KEY__",
    authDomain:        "__FIREBASE_AUTH_DOMAIN__",
    projectId:         "__FIREBASE_PROJECT_ID__",
    storageBucket:     "__FIREBASE_STORAGE_BUCKET__",
    messagingSenderId: "__FIREBASE_MESSAGING_SENDER_ID__",
    appId:             "__FIREBASE_APP_ID__",
  };

  const app      = initializeApp(firebaseConfig);
  const auth     = getAuth(app);
  const provider = new GoogleAuthProvider();

  onAuthStateChanged(auth, (user) => {
    if (user) {
      document.getElementById("fv-auth-wall").style.display = "none";
      document.getElementById("fv-app").style.display       = "block";
      const nameEl = document.getElementById("fv-user-name");
      if (nameEl) nameEl.textContent = user.displayName || user.email;
    } else {
      document.getElementById("fv-auth-wall").style.display = "flex";
      document.getElementById("fv-app").style.display       = "none";
    }
  });

  window.fvSignIn  = () => signInWithPopup(auth, provider).catch(e => alert("Sign-in failed: " + e.message));
  window.fvSignOut = () => signOut(auth);
</script>
<style>
:root {
  --bg:#05080f; --bg2:#080d18; --surface:#0c1220; --card:#0f1626; --card2:#121b2e;
  --border:#1e2d44; --border2:#253652;
  --blue:#1a6bff; --blue-dim:rgba(26,107,255,0.12); --blue-glow:rgba(26,107,255,0.25);
  --green:#00c07f; --green-dim:rgba(0,192,127,0.12);
  --red:#e84040;   --red-dim:rgba(232,64,64,0.12);
  --amber:#e8a825; --amber-dim:rgba(232,168,37,0.12);
  --text:#e4edf8; --text2:#b4ccdf; --muted:#6482a0; --dim:#3d5272; --label:#8fb0cc;
  --mono:'IBM Plex Mono',monospace; --sans:'Epilogue',sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.5;overflow-x:hidden;min-height:100vh}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}
/* ─ Noise ─ */
body::before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.02;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  background-size:128px}
/* ─ Status strip ─ */
.status-strip{position:fixed;top:0;left:0;right:0;z-index:200;height:28px;background:var(--bg);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 1.5rem;gap:1.5rem;font-family:var(--mono);font-size:10px;letter-spacing:.04em;color:var(--text2)}
.strip-left{display:flex;align-items:center;gap:1.5rem;flex:1;overflow:hidden}
.strip-right{display:flex;align-items:center;gap:1rem;white-space:nowrap;color:var(--muted);font-size:9px}
.market-pill{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:2px;border:1px solid var(--border2);background:var(--surface);white-space:nowrap}
.m-dot{width:5px;height:5px;border-radius:50%}
.dot-green{background:var(--green);box-shadow:0 0 6px var(--green);animation:blink 2s infinite}
.dot-red{background:var(--red)}
.m-exch{color:var(--text2);font-weight:700;letter-spacing:.05em}
.m-status{font-weight:500}
.m-status.open{color:var(--green)}
.m-status.closed{color:var(--red)}
.m-time{color:var(--muted)}
.strip-sep{color:var(--muted)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
/* ─ Nav ─ */
nav{position:fixed;top:28px;left:0;right:0;z-index:199;height:52px;background:rgba(8,13,24,.96);border-bottom:1px solid var(--border2);backdrop-filter:blur(24px);display:flex;align-items:center;padding:0 1.5rem;gap:1.5rem}
.logo{font-family:var(--sans);font-size:15px;font-weight:900;letter-spacing:-.04em;color:#fff;display:flex;align-items:center;gap:6px;white-space:nowrap}
.logo-mark{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:4px;background:var(--blue);font-size:12px;font-weight:900;color:#fff}
.logo-pro{font-size:9px;font-weight:700;letter-spacing:.1em;color:var(--blue);text-transform:uppercase;border:1px solid var(--blue-dim);padding:1px 5px;border-radius:2px;margin-left:4px}
.nav-links{display:flex;align-items:center;gap:.15rem;list-style:none;overflow-x:auto;flex:1;scrollbar-width:none}
.nav-links::-webkit-scrollbar{display:none}
.nav-links li button{background:none;border:none;font-family:var(--sans);font-size:11px;font-weight:700;color:var(--text2);letter-spacing:.04em;padding:5px 10px;border-radius:4px;cursor:pointer;transition:all .15s;white-space:nowrap;text-transform:uppercase}
.nav-links li button:hover{color:var(--text);background:var(--surface)}
.nav-links li button.active{color:var(--blue);background:var(--blue-dim)}
.nav-badge{font-family:var(--mono);font-size:9px;font-weight:500;color:var(--green);letter-spacing:.06em;background:var(--green-dim);border:1px solid rgba(0,192,127,.2);border-radius:2px;padding:2px 7px;display:flex;align-items:center;gap:4px;white-space:nowrap}
.live-dot{width:5px;height:5px;border-radius:50%;background:var(--green);animation:blink 1.5s infinite}
/* ─ Ticker ─ */
.ticker-strip{position:fixed;top:80px;left:0;right:0;z-index:198;height:34px;background:var(--bg2);border-bottom:1px solid var(--border);overflow:hidden;display:flex;align-items:center}
.ticker-inner{display:flex;gap:0;white-space:nowrap;animation:scroll-left 55s linear infinite}
.ticker-inner:hover{animation-play-state:paused}
@keyframes scroll-left{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.tick{display:inline-flex;align-items:center;gap:8px;padding:0 18px;border-right:1px solid var(--border);font-family:var(--mono);font-size:11px;height:34px}
.tick-sym{color:var(--text2);font-weight:600;letter-spacing:.05em;font-size:10px}
.tick-price{color:var(--text);font-weight:500}
.tick-chg{font-weight:500;font-size:10px}
.tick-sep{color:var(--muted);font-size:10px}
.chg-up{color:var(--green)}
.chg-dn{color:var(--red)}
/* ─ Page ─ */
.page{margin-top:114px;min-height:calc(100vh - 114px)}
.section{display:none}
.section.active{display:block;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}
/* ─ Section header ─ */
.section-header{display:flex;align-items:flex-end;justify-content:space-between;padding:1.25rem 1.5rem .9rem;border-bottom:1px solid var(--border)}
.section-title{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text2)}
.section-subtitle{font-size:20px;font-weight:800;color:#fff;margin-top:2px;line-height:1.2}
.section-meta{font-family:var(--mono);font-size:10px;color:var(--text2)}
/* ─ Home grid ─ */
.home-grid{display:grid;grid-template-columns:300px 1fr;min-height:calc(100vh - 200px)}
@media(max-width:960px){.home-grid{grid-template-columns:1fr}}
/* ─ Sidebar ─ */
.sidebar{border-right:1px solid var(--border);background:var(--bg2)}
.sidebar-group{border-bottom:1px solid var(--border)}
.sidebar-group-label{padding:9px 14px;font-size:9px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--text2);background:var(--bg);border-bottom:1px solid var(--border)}
.sidebar-item{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;cursor:pointer;border-bottom:1px solid rgba(25,36,56,.5);transition:all .12s;gap:8px}
.sidebar-item:hover{background:var(--surface)}
.sidebar-item.active{background:var(--blue-dim);border-left:2px solid var(--blue)}
.sidebar-item-left{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0}
.sidebar-item-name{font-size:12px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sidebar-item.active .sidebar-item-name{color:var(--blue)}
.sidebar-item-desc{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sidebar-item-arrow{color:var(--muted);font-size:12px}
/* ─ Signal badge ─ */
.signal-badge{font-family:var(--mono);font-size:9px;font-weight:700;padding:2px 7px;border-radius:2px;letter-spacing:.08em;text-transform:uppercase;flex-shrink:0}
.badge-buy{background:var(--green-dim);color:var(--green);border:1px solid rgba(0,192,127,.3)}
.badge-hold{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(232,168,37,.3)}
.badge-wait{background:var(--red-dim);color:var(--red);border:1px solid rgba(232,64,64,.3)}
/* ─ Main content ─ */
.main-content{min-width:0;display:flex;flex-direction:column}
.hero-band{padding:2rem 2rem 1.5rem;border-bottom:1px solid var(--border);background:linear-gradient(135deg,rgba(26,107,255,.04) 0%,transparent 60%);position:relative;overflow:hidden}
.hero-band::before{content:'FV';position:absolute;right:2rem;top:50%;transform:translateY(-50%);font-family:var(--sans);font-size:110px;font-weight:900;color:rgba(26,107,255,.04);line-height:1;pointer-events:none;user-select:none;letter-spacing:-.08em}
.hero-label{font-size:9px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--blue);margin-bottom:8px;display:flex;align-items:center;gap:6px}
.hero-label::before{content:'//';color:var(--muted);font-family:var(--mono)}
.hero-h1{font-size:26px;font-weight:900;letter-spacing:-.04em;line-height:1.15;color:#fff;margin-bottom:10px;max-width:500px}
.hero-h1 em{color:var(--blue);font-style:normal}
.hero-body{font-size:13px;color:var(--text2);max-width:440px;line-height:1.65}
/* ─ KPI row ─ */
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--border)}
@media(max-width:700px){.kpi-row{grid-template-columns:1fr 1fr}}
.kpi-cell{padding:1rem 1.5rem;border-right:1px solid var(--border)}
.kpi-cell:last-child{border-right:none}
.kpi-label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--label);margin-bottom:5px}
.kpi-value{font-family:var(--mono);font-size:20px;font-weight:600;color:#fff;line-height:1}
.kpi-value.blue{color:var(--blue)}
.kpi-value.green{color:var(--green)}
.kpi-value.amber{color:var(--amber)}
.kpi-sub{font-size:10px;color:var(--muted);margin-top:4px}
/* ─ Signal detail ─ */
.signal-area{flex:1;padding:1.5rem;overflow-y:auto}
.signal-detail-panel{display:none}
.signal-detail-panel.active{display:block;animation:fadeIn .2s ease}
.detail-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1.1rem;flex-wrap:wrap;gap:.5rem}
.detail-title{font-size:17px;font-weight:800;color:#fff}
.detail-signal-large{font-size:11px;padding:4px 12px}
.detail-metrics{display:grid;grid-template-columns:repeat(auto-fill,minmax(135px,1fr));gap:1px;background:var(--border);border:1px solid var(--border);border-radius:4px;overflow:hidden;margin-bottom:1.1rem}
.detail-metric{background:var(--surface);padding:.75rem 1rem}
.detail-metric-val{font-family:var(--mono);font-size:12px;font-weight:600;color:#fff}
.detail-metric-label{font-size:9px;color:var(--label);text-transform:uppercase;letter-spacing:.08em;margin-top:3px}
.reasons-panel{margin-top:.75rem}
.reasons-label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--text2);margin-bottom:.5rem}
.reason-line{display:flex;align-items:flex-start;gap:8px;padding:7px 0;border-bottom:1px solid var(--border);font-size:12px;color:var(--text2);line-height:1.55}
.reason-line:last-child{border-bottom:none}
.reason-marker{min-width:14px;height:14px;border-radius:2px;flex-shrink:0;margin-top:1px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700}
.rm-buy{background:var(--green-dim);color:var(--green)}
.rm-hold{background:var(--amber-dim);color:var(--amber)}
.rm-wait{background:var(--red-dim);color:var(--red)}
.context-note{margin-top:.9rem;padding:9px 13px;background:rgba(26,107,255,.06);border:1px solid rgba(26,107,255,.2);border-radius:4px;font-size:11px;color:var(--text2);line-height:1.65}
.disclaimer{margin-top:.9rem;font-size:10px;color:var(--muted);font-family:var(--mono);padding-top:.7rem;border-top:1px solid var(--border);line-height:1.6}
.sparkline-wrap{margin:.7rem 0;padding:.7rem 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.spark-label{font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--text2);margin-bottom:4px}
/* ─ Calc sections ─ */
.calc-section{}
.calc-tabs-bar{display:flex;overflow-x:auto;border-bottom:1px solid var(--border);background:var(--bg2);scrollbar-width:none}
.calc-tabs-bar::-webkit-scrollbar{display:none}
.calc-tab{flex-shrink:0;background:none;border:none;cursor:pointer;font-family:var(--sans);font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.07em;padding:11px 16px;border-right:1px solid var(--border);border-bottom:2px solid transparent;transition:all .12s;white-space:nowrap}
.calc-tab:hover{color:var(--text);background:var(--surface)}
.calc-tab.active{color:var(--blue);border-bottom-color:var(--blue);background:var(--blue-dim)}
.calc-inner{display:none}
.calc-inner.active{display:grid;grid-template-columns:360px 1fr;min-height:420px}
@media(max-width:820px){.calc-inner.active{grid-template-columns:1fr}}
.calc-inputs{padding:1.5rem;border-right:1px solid var(--border);background:var(--bg2)}
.calc-outputs{padding:1.5rem;background:var(--card)}
/* ─ Forms ─ */
.form-group{margin-bottom:1rem}
.form-label{display:block;font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--text2);margin-bottom:5px}
.form-input{width:100%;background:var(--bg);border:1px solid var(--border2);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:13px;padding:8px 11px;outline:none;transition:border-color .15s;appearance:none;-webkit-appearance:none}
.form-input:focus{border-color:var(--blue)}
.form-input::placeholder{color:var(--muted)}
select.form-input{cursor:pointer}
.form-row{display:flex;gap:10px}
.form-row .form-group{flex:1}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:3px;background:var(--border2);border-radius:2px;outline:none;margin-top:5px}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:13px;height:13px;border-radius:50%;background:var(--blue);cursor:pointer;border:2px solid var(--bg);box-shadow:0 0 0 2px var(--blue-glow)}
.range-labels{display:flex;justify-content:space-between;font-size:9px;color:var(--muted);font-family:var(--mono);margin-top:3px}
.calc-btn{width:100%;background:var(--blue);color:#fff;border:none;border-radius:4px;font-family:var(--sans);font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;padding:10px;cursor:pointer;transition:background .15s;margin-top:.4rem}
.calc-btn:hover{background:#1458d6}
/* ─ Output blocks ─ */
.output-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);border:1px solid var(--border);border-radius:4px;overflow:hidden;margin-bottom:1rem}
.output-cell{background:var(--surface);padding:.9rem 1.1rem}
.output-label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--label);margin-bottom:4px}
.output-value{font-family:var(--mono);font-size:18px;font-weight:600;color:#fff}
.output-value.big{font-size:24px}
.output-value.green{color:var(--green)}
.output-value.blue{color:var(--blue)}
.output-value.amber{color:var(--amber)}
.output-sub{font-size:10px;color:var(--muted);margin-top:3px;line-height:1.5}
.full-col{grid-column:1/-1}
/* ─ Result box ─ */
.result-box{background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1rem;margin-top:.75rem}
.result-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:var(--text2);margin-bottom:.6rem}
/* ─ Scenario table ─ */
.scenario-list{display:flex;flex-direction:column;gap:1px;background:var(--border);border:1px solid var(--border);border-radius:4px;overflow:hidden;margin-top:.75rem}
.scenario-row{display:flex;align-items:center;justify-content:space-between;background:var(--surface);padding:.65rem 1rem}
.scenario-label{font-size:11px;color:var(--text2)}
.scenario-val{font-family:var(--mono);font-size:13px;font-weight:600;color:#fff}
.scenario-val.green{color:var(--green)}
.scenario-val.amber{color:var(--amber)}
.scenario-val.red{color:var(--red)}
/* ─ Table ─ */
.table-wrap{overflow-x:auto;margin-top:.75rem;border:1px solid var(--border);border-radius:4px;max-height:260px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11px}
th{background:var(--bg);color:var(--label);font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:8px 10px;text-align:right;border-bottom:1px solid var(--border);position:sticky;top:0}
th:first-child{text-align:left}
td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--border);color:var(--text)}
td:first-child{text-align:left;color:var(--text2)}
tr:hover td{background:var(--surface)}
.negative{color:var(--red)}
.positive{color:var(--green)}
/* ─ Dashboard ─ */
.dash-kpi-row{display:grid;grid-template-columns:repeat(6,1fr);border-bottom:1px solid var(--border)}
@media(max-width:1000px){.dash-kpi-row{grid-template-columns:repeat(3,1fr)}}
@media(max-width:600px){.dash-kpi-row{grid-template-columns:repeat(2,1fr)}}
.dash-kpi{padding:.9rem 1.1rem;border-right:1px solid var(--border)}
.dash-kpi:last-child{border-right:none}
.dash-kpi-label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--label);margin-bottom:5px}
.dash-kpi-val{font-family:var(--mono);font-size:14px;font-weight:600;color:#fff}
.dash-tag{font-size:9px;font-weight:700;padding:1px 6px;border-radius:2px;display:inline-block;margin-top:4px;letter-spacing:.06em;text-transform:uppercase}
.tag-ok{background:var(--green-dim);color:var(--green)}
.tag-warn{background:var(--amber-dim);color:var(--amber)}
.tag-bad{background:var(--red-dim);color:var(--red)}
.dash-body{display:grid;grid-template-columns:1fr 360px;gap:0}
@media(max-width:900px){.dash-body{grid-template-columns:1fr}}
.dash-main{border-right:1px solid var(--border)}
.dash-side{background:var(--bg2)}
.dash-block{padding:1.1rem 1.4rem;border-bottom:1px solid var(--border)}
.dash-block-title{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--text);margin-bottom:.7rem}
.alert-list{display:flex;flex-direction:column;gap:6px}
.alert-item{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-radius:3px;font-size:11px;line-height:1.5}
.alert-item.critical{background:var(--red-dim);border:1px solid rgba(232,64,64,.2);color:#f87171}
.alert-item.warning{background:var(--amber-dim);border:1px solid rgba(232,168,37,.2);color:var(--amber)}
.alert-item.ok{background:var(--green-dim);border:1px solid rgba(0,192,127,.2);color:var(--green)}
.alert-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;margin-top:4px}
.a-red{background:var(--red)}
.a-amber{background:var(--amber)}
.a-green{background:var(--green)}
.rec-list-pro{list-style:none;display:flex;flex-direction:column;gap:0}
.rec-item-pro{display:flex;align-items:flex-start;gap:10px;padding:.7rem 0;border-bottom:1px solid var(--border);font-size:12px;color:var(--text2);line-height:1.5}
.rec-item-pro:last-child{border-bottom:none}
.rec-num-pro{min-width:20px;height:20px;border-radius:2px;background:var(--blue-dim);border:1px solid rgba(26,107,255,.25);color:var(--blue);font-size:9px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-family:var(--mono)}
/* ─ Progress bar ─ */
.progress-row{margin-bottom:.8rem}
.progress-header{display:flex;justify-content:space-between;margin-bottom:4px}
.progress-header span:first-child{font-size:12px;font-weight:600;color:var(--text)}
.progress-header span:last-child{font-size:12px;font-family:var(--mono)}
.progress-bar{height:4px;background:var(--border);border-radius:2px;overflow:hidden}
.progress-fill{height:100%;border-radius:2px;transition:width .4s ease}
.progress-note{font-size:10px;color:var(--muted);margin-top:3px}
/* ─ Profile grid ─ */
.profile-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;padding:1.5rem}
@media(max-width:700px){.profile-grid{grid-template-columns:1fr}}
/* ─ Verdict ─ */
.verdict{padding:.7rem 1rem;border-radius:4px;font-size:12px;margin-top:.75rem;font-weight:500;line-height:1.6}
.verdict.good{background:var(--green-dim);color:var(--green);border:1px solid rgba(0,192,127,.25)}
.verdict.warning{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(232,168,37,.25)}
.verdict.bad{background:var(--red-dim);color:var(--red);border:1px solid rgba(232,64,64,.25)}
.verdict.neutral{background:var(--surface);color:var(--text2);border:1px solid var(--border)}
/* ─ Tooltip ─ */
.tt{border-bottom:1px dashed var(--muted);cursor:help;position:relative}
.tt::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:var(--card2);color:var(--text);font-size:10px;white-space:normal;max-width:200px;padding:5px 9px;border-radius:4px;border:1px solid var(--border2);pointer-events:none;opacity:0;transition:opacity .15s;z-index:999;text-align:center;line-height:1.5;font-family:var(--sans);font-weight:400}
.tt:hover::after{opacity:1}
/* ─ Mini chart ─ */
.mini-chart{position:relative;height:100px;margin-top:.75rem;border:1px solid var(--border);border-radius:4px;overflow:hidden;padding:10px 10px 20px}
.chart-line-svg{width:100%;height:100%}
.chart-x-labels{display:flex;justify-content:space-between;font-size:9px;color:var(--muted);font-family:var(--mono);margin-top:4px}
.chart-legend{display:flex;gap:1rem;font-size:9px;color:var(--muted);margin-top:2px}
/* ─ Footer ─ */
footer{border-top:1px solid var(--border);padding:.9rem 1.5rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.75rem;font-size:10px;color:var(--muted);font-family:var(--mono)}
.footer-trust{display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap}
/* ─ Auth wall ─ */
#fv-auth-wall{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;background:var(--bg);gap:1.5rem;padding:2rem}
.auth-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:2.5rem 2rem;display:flex;flex-direction:column;align-items:center;gap:1.2rem;max-width:360px;width:100%;text-align:center}
.auth-logo{font-size:28px;font-weight:900;letter-spacing:-.04em;color:#fff}.auth-logo span{color:var(--blue)}
.auth-tagline{font-size:13px;color:var(--text2);line-height:1.6}
.auth-btn{display:flex;align-items:center;gap:10px;background:#fff;color:#1f1f1f;border:none;border-radius:6px;padding:11px 22px;font-size:14px;font-weight:600;cursor:pointer;transition:box-shadow .15s;width:100%;justify-content:center}
.auth-btn:hover{box-shadow:0 2px 12px rgba(0,0,0,.25)}
.auth-btn svg{flex-shrink:0}
.auth-note{font-size:10px;color:var(--muted);font-family:var(--mono)}
/* ─ User bar ─ */
#fv-user-bar{display:flex;align-items:center;gap:10px;padding:5px 14px;background:var(--surface);border-bottom:1px solid var(--border);font-size:11px;color:var(--text2);justify-content:flex-end}
#fv-user-bar button{background:none;border:1px solid var(--border2);color:var(--text2);font-size:10px;padding:3px 10px;border-radius:3px;cursor:pointer;font-family:var(--sans)}
.no-profile-notice{background:rgba(26,107,255,.06);border:1px solid rgba(26,107,255,.2);border-radius:6px;padding:1.5rem;text-align:center;color:var(--text2);font-size:13px}
.no-profile-notice h3{color:#fff;margin-bottom:.5rem;font-size:16px}
/* ─ Profile saved msg ─ */
#profileMsg{display:none;background:var(--green-dim);border:1px solid rgba(0,192,127,.3);color:var(--green);padding:.7rem 1rem;border-radius:4px;font-size:12px;margin-top:.75rem}
/* ─ Sensitivity row ─ */
.sens-row{display:flex;justify-content:space-between;padding:.25rem 0;border-bottom:1px solid var(--border);font-size:11px;font-family:var(--mono)}
.sens-row:last-child{border-bottom:none}
/* ─ Summary bar ─ */
.summary-bar{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--border);background:var(--bg)}
@media(max-width:600px){.summary-bar{grid-template-columns:1fr 1fr}}
.sum-cell{padding:.75rem 1rem;border-right:1px solid var(--border)}
.sum-cell:last-child{border-right:none}
.sum-label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--label);margin-bottom:3px}
.sum-value{font-family:var(--mono);font-size:15px;font-weight:600;color:#fff;line-height:1}
.sum-value.green{color:var(--green)}.sum-value.blue{color:var(--blue)}.sum-value.amber{color:var(--amber)}.sum-value.red{color:var(--red)}
.sum-action{font-size:10px;color:var(--text2);margin-top:3px;line-height:1.4}
/* ─ Number counter animation ─ */
@keyframes numPop{0%{opacity:.4;transform:translateY(4px)}100%{opacity:1;transform:none}}
.num-animated{animation:numPop .45s ease forwards}
/* ─ Pie chart container ─ */
.pie-wrap{display:flex;flex-direction:column;align-items:center;gap:.75rem;margin-top:.75rem}
.pie-legend{display:flex;flex-direction:column;gap:4px;width:100%;max-width:300px}
.pie-legend-row{display:flex;align-items:center;justify-content:space-between;font-size:11px;gap:6px}
.pie-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.pie-legend-label{flex:1;color:var(--text2)}
.pie-legend-val{font-family:var(--mono);color:var(--text);font-size:11px}

/* ═══════════════════════════════════════════════
   RESPONSIVE — MOBILE FIXES
   Gaps identified: output-grid, form-row, section-header,
   kpi-row small, padding reduction, iOS input zoom, hero font
═══════════════════════════════════════════════ */

/* Tablet (≤768px) */
@media(max-width:768px){
  /* Section header: title + meta stack vertically */
  .section-header{flex-direction:column;align-items:flex-start;gap:.4rem}
  /* Hero text smaller */
  .hero-h1{font-size:20px}
  .hero-band{padding:1.25rem 1.25rem 1rem}
  /* Signal area padding tighter */
  .signal-area{padding:1rem}
  /* Calc inputs/outputs padding tighter */
  .calc-inputs,.calc-outputs{padding:1rem}
  /* Dashboard blocks tighter */
  .dash-block{padding:.9rem 1rem}
  .dash-kpi{padding:.75rem .9rem}
  /* Scenario rows wrap label if long */
  .scenario-row{flex-wrap:wrap;gap:.3rem}
  /* Alert items readable */
  .alert-item{font-size:12px}
  /* Rec items readable */
  .rec-item-pro{font-size:12px}
}

/* Mobile (≤480px) */
@media(max-width:480px){
  /* output-grid → single column (was always 2-col) */
  .output-grid{grid-template-columns:1fr}
  /* form-row → stack inputs vertically (was side-by-side) */
  .form-row{flex-direction:column;gap:0}
  /* kpi-row → single column on small phones */
  .kpi-row{grid-template-columns:1fr 1fr}
  .kpi-cell{border-right:none;border-bottom:1px solid var(--border)}
  /* Detail metrics → 2 per row minimum */
  .detail-metrics{grid-template-columns:repeat(auto-fill,minmax(120px,1fr))}
  /* Hero font */
  .hero-h1{font-size:18px}
  .hero-body{font-size:12px}
  /* Status strip — hide right side on tiny screens */
  .strip-right{display:none}
  /* Nav badge — shorten */
  .nav-badge{font-size:8px;padding:2px 5px}
  /* Ticker symbol size */
  .tick{padding:0 12px;gap:5px}
  /* Signal detail header wraps */
  .detail-header{flex-direction:column;align-items:flex-start;gap:.5rem}
  /* Sidebar item desc hidden on tiny screens */
  .sidebar-item-desc{display:none}
  /* Smaller heading for calc outputs */
  .output-value.big{font-size:18px}
  .output-value{font-size:15px}
  /* Calc tabs scroll better */
  .calc-tab{padding:10px 12px;font-size:10px}
  /* Table font size */
  table{font-size:10px}
  th,td{padding:6px 7px}
}

/* iOS: prevent zoom on input focus (font-size must be >= 16px) */
@media(max-width:768px){
  .form-input,select.form-input{font-size:16px}
}
</style>
</head>
<body>

<!-- ══ AUTH WALL — shown until user signs in ══════════════ -->
<div id="fv-auth-wall" style="display:none">
  <div class="auth-card">
    <div class="auth-logo">Fin<span>Vault</span><sup style="font-size:12px;color:var(--text2);font-weight:400">PRO</sup></div>
    <p class="auth-tagline">Professional financial intelligence — live market signals, calculators &amp; your personal dashboard.</p>
    <button class="auth-btn" onclick="fvSignIn()">
      <!-- Google G logo -->
      <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
      Sign in with Google
    </button>
    <span class="auth-note">Your data is never sold &middot; Educational use only</span>
  </div>
</div>

<!-- ══ APP — hidden until authenticated ══════════════════ -->
<div id="fv-app" style="display:none">

<!-- User bar -->
<div id="fv-user-bar">
  <span>Signed in as <strong id="fv-user-name"></strong></span>
  <button onclick="fvSignOut()">Sign out</button>
</div>

<!-- Status strip -->
<div class="status-strip">
  <div class="strip-left">__MARKET_STATUS__
    <span class="strip-sep">·</span>
    <span>RBI Repo: <strong style="color:var(--amber)">__REPO_RATE__%</strong></span>
    <span class="strip-sep">·</span>
    <span>Data: CoinGecko &middot; NSE &middot; NYSE &middot; yFinance</span>
  </div>
  <div class="strip-right">Educational use only &mdash; not investment advice</div>
</div>

<!-- Nav -->
<nav>
  <div class="logo"><span class="logo-mark">F</span>FinVault<span class="logo-pro">PRO</span></div>
  <ul class="nav-links">
    <li><button class="active" onclick="showSection('home')">Overview</button></li>
    <li><button onclick="showSection('invest')">Invest</button></li>
    <li><button onclick="showSection('retire')">Retire</button></li>
    <li><button onclick="showSection('stocks')">Risk Lab</button></li>
    <li><button onclick="showSection('loans')">Loans</button></li>
    <li><button onclick="showSection('tax')">Tax</button></li>
    <li><button onclick="showSection('health')">Health Score</button></li>
    <li><button onclick="showSection('profile')">Profile</button></li>
    <li><button onclick="showSection('dashboard')">Dashboard</button></li>
  </ul>
  <div class="nav-badge"><span class="live-dot"></span>LIVE &middot; __UPDATED_AT__ UTC</div>
</nav>

<!-- Ticker -->
<div class="ticker-strip">
  <div class="ticker-inner">__TICKER_HTML__</div>
</div>

<div class="page">

<!-- ═══ HOME ═══ -->
<div id="home" class="section active">
  <div class="section-header">
    <div>
      <div class="section-title">Overview &mdash; Market Signals</div>
      <div class="section-subtitle">Financial Intelligence Platform</div>
    </div>
    <div class="section-meta">Updated __UPDATED_AT__ UTC &middot; GitHub Actions</div>
  </div>
  <div class="home-grid">
    <!-- Sidebar -->
    <div class="sidebar">
      <div class="sidebar-group">
        <div class="sidebar-group-label">Asset Signals</div>
        __SIDEBAR_ITEMS__
      </div>
      <div class="sidebar-group">
        <div class="sidebar-group-label">Tools</div>
        <div class="sidebar-item" onclick="showSection('invest')">
          <div class="sidebar-item-left">
            <span class="sidebar-item-name">SIP / Lump Sum</span>
            <span class="sidebar-item-desc">Corpus projector &middot; DCA planner</span>
          </div><span class="sidebar-item-arrow">›</span>
        </div>
        <div class="sidebar-item" onclick="showSection('retire')">
          <div class="sidebar-item-left">
            <span class="sidebar-item-name">FIRE Calculator</span>
            <span class="sidebar-item-desc">Retirement corpus &middot; Withdrawal planner</span>
          </div><span class="sidebar-item-arrow">›</span>
        </div>
        <div class="sidebar-item" onclick="showSection('loans')">
          <div class="sidebar-item-left">
            <span class="sidebar-item-name">EMI &amp; Prepayment</span>
            <span class="sidebar-item-desc">Home loan &middot; Prepay savings</span>
          </div><span class="sidebar-item-arrow">›</span>
        </div>
        <div class="sidebar-item" onclick="showSection('tax')">
          <div class="sidebar-item-left">
            <span class="sidebar-item-name">Tax Optimizer</span>
            <span class="sidebar-item-desc">Old vs new regime &middot; LTCG &middot; STCG</span>
          </div><span class="sidebar-item-arrow">›</span>
        </div>
        <div class="sidebar-item" onclick="showSection('dashboard')">
          <div class="sidebar-item-left">
            <span class="sidebar-item-name">Financial Dashboard</span>
            <span class="sidebar-item-desc">Health score &middot; Alerts &middot; Plan</span>
          </div><span class="sidebar-item-arrow">›</span>
        </div>
      </div>
    </div>
    <!-- Main panel -->
    <div class="main-content">
      <div class="hero-band">
        <div class="hero-label">Financial Command Center</div>
        <h1 class="hero-h1">Plan, Stress-Test &amp; <em>Optimise</em><br>Your Entire Financial Life</h1>
        <p class="hero-body">Professional-grade calculators for investments, retirement, risk, loans and tax &mdash; built on transparent assumptions and live market signals.</p>
      </div>
      <div class="kpi-row">
        <div class="kpi-cell">
          <div class="kpi-label">Calculators</div>
          <div class="kpi-value blue">15+</div>
          <div class="kpi-sub">Professional-grade tools</div>
        </div>
        <div class="kpi-cell">
          <div class="kpi-label">Asset Signals</div>
          <div class="kpi-value">7</div>
          <div class="kpi-sub">Updated every hour</div>
        </div>
        <div class="kpi-cell">
          <div class="kpi-label">Fear &amp; Greed</div>
          <div class="kpi-value amber" id="kpiFG">N/A</div>
          <div class="kpi-sub" id="kpiFGLabel">—</div>
        </div>
        <div class="kpi-cell">
          <div class="kpi-label">Data Feed</div>
          <div class="kpi-value green">Live</div>
          <div class="kpi-sub">NSE &middot; NYSE &middot; CoinGecko</div>
        </div>
      </div>
      <!-- Signal detail panels (one per asset, shown/hidden by JS) -->
      <div class="signal-area">
        __SIGNAL_DETAILS__
      </div>
    </div>
  </div>
</div>

<!-- ═══ INVEST ═══ -->
<div id="invest" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Tools &mdash; Investment</div>
      <div class="section-subtitle">SIP, Lump Sum &amp; Goal Calculators</div>
    </div>
    <div class="section-meta">Compounding projections &middot; Scenario analysis</div>
  </div>
  <div class="calc-section">
    <div class="calc-tabs-bar">
      <button class="calc-tab active" onclick="switchTab(this,'invest','sip')">SIP Projector</button>
      <button class="calc-tab" onclick="switchTab(this,'invest','lump')">Lump Sum</button>
      <button class="calc-tab" onclick="switchTab(this,'invest','goal')">Goal Planner</button>
      <button class="calc-tab" onclick="switchTab(this,'invest','roi')">ROI Analyzer</button>
      <button class="calc-tab" onclick="switchTab(this,'invest','scenario')">Scenario Engine</button>
    </div>
    <!-- SIP -->
    <div id="invest-sip" class="calc-inner active">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Monthly SIP (₹)</label>
          <input class="form-input" type="number" id="sipAmt" value="10000" oninput="calcSIP()">
        </div>
        <div class="form-group">
          <label class="form-label">Annual Return Rate: <span id="sipRate-v" style="color:var(--blue)">12%</span></label>
          <input type="range" id="sipRate" min="1" max="30" step="0.5" value="12" oninput="calcSIP();document.getElementById('sipRate-v').textContent=this.value+'%'">
          <div class="range-labels"><span>1%</span><span>30%</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Investment Period: <span id="sipYrs-v" style="color:var(--blue)">15 yrs</span></label>
          <input type="range" id="sipYrs" min="1" max="40" step="1" value="15" oninput="calcSIP();document.getElementById('sipYrs-v').textContent=this.value+' yrs'">
          <div class="range-labels"><span>1</span><span>40 yrs</span></div>
        </div>
        <button class="calc-btn" onclick="calcSIP()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="summary-bar" id="sipSummaryBar">
          <div class="sum-cell"><div class="sum-label">Corpus</div><div class="sum-value green" id="sb-sipCorpus">—</div><div class="sum-action" id="sb-sipAction">Enter values to calculate</div></div>
          <div class="sum-cell"><div class="sum-label">Wealth Multiple</div><div class="sum-value blue" id="sb-sipMultiple">—</div></div>
          <div class="sum-cell"><div class="sum-label">Total Invested</div><div class="sum-value" id="sb-sipInvested">—</div></div>
          <div class="sum-cell"><div class="sum-label">Wealth Gained</div><div class="sum-value green" id="sb-sipGain">—</div></div>
        </div>
        <div class="output-grid">
          <div class="output-cell full-col">
            <div class="output-label">Projected Corpus</div>
            <div class="output-value big green" id="sipCorpus">—</div>
            <div class="output-sub" id="sipCorpusSub"></div>
          </div>
          <div class="output-cell">
            <div class="output-label">Total Invested</div>
            <div class="output-value" id="sipInvested">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Wealth Gained</div>
            <div class="output-value green" id="sipGain">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Wealth Multiple</div>
            <div class="output-value blue" id="sipMultiple">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">CAGR (est.)</div>
            <div class="output-value" id="sipCagr">—</div>
          </div>
        </div>
        <div class="result-title">Rate Sensitivity</div>
        <div id="sipSensitivity"></div>
        <div class="mini-chart">
          <svg class="chart-line-svg" id="sipChartSvg" viewBox="0 0 500 80" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <defs><linearGradient id="cg1" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#1a6bff" stop-opacity=".3"/><stop offset="100%" stop-color="#1a6bff" stop-opacity="0"/></linearGradient></defs>
            <path id="sipChartPath" d="" fill="url(#cg1)"/>
            <path id="sipChartLine" d="" fill="none" stroke="#1a6bff" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>
      </div>
    </div>
    <!-- Lump Sum -->
    <div id="invest-lump" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Investment Amount (₹)</label>
          <input class="form-input" type="number" id="lsAmt" value="500000" oninput="calcLS()">
        </div>
        <div class="form-group">
          <label class="form-label">Annual Return: <span id="lsRate-v" style="color:var(--blue)">12%</span></label>
          <input type="range" id="lsRate" min="1" max="30" step="0.5" value="12" oninput="calcLS();document.getElementById('lsRate-v').textContent=this.value+'%'">
          <div class="range-labels"><span>1%</span><span>30%</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Period: <span id="lsYrs-v" style="color:var(--blue)">10 yrs</span></label>
          <input type="range" id="lsYrs" min="1" max="40" step="1" value="10" oninput="calcLS();document.getElementById('lsYrs-v').textContent=this.value+' yrs'">
          <div class="range-labels"><span>1</span><span>40 yrs</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Inflation Rate: <span id="lsInfl-v" style="color:var(--amber)">6%</span></label>
          <input type="range" id="lsInfl" min="2" max="15" step="0.5" value="6" oninput="calcLS();document.getElementById('lsInfl-v').textContent=this.value+'%'">
          <div class="range-labels"><span>2%</span><span>15%</span></div>
        </div>
        <button class="calc-btn" onclick="calcLS()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="output-grid">
          <div class="output-cell full-col">
            <div class="output-label">Projected Value</div>
            <div class="output-value big green" id="lsCorpus">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Nominal Gain</div>
            <div class="output-value green" id="lsGain">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Real Value (Infl. adj.)</div>
            <div class="output-value amber" id="lsReal">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Wealth Multiple</div>
            <div class="output-value blue" id="lsMultiple">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Real Return</div>
            <div class="output-value" id="lsRealRet">—</div>
          </div>
        </div>
        <div id="lsInflResult"></div>
      </div>
    </div>
    <!-- Goal Planner -->
    <div id="invest-goal" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Target Amount (₹)</label>
          <input class="form-input" type="number" id="goalAmt" value="10000000" oninput="calcGoal()">
        </div>
        <div class="form-group">
          <label class="form-label">Years to Goal</label>
          <input class="form-input" type="number" id="goalYrs" value="15" oninput="calcGoal()">
        </div>
        <div class="form-group">
          <label class="form-label">Expected Return (%)</label>
          <input class="form-input" type="number" id="goalRate" value="12" oninput="calcGoal()">
        </div>
        <button class="calc-btn" onclick="calcGoal()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="output-grid">
          <div class="output-cell full-col">
            <div class="output-label">Monthly SIP Needed</div>
            <div class="output-value big green" id="goalSIP">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Lump Sum Needed</div>
            <div class="output-value blue" id="goalLS">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Total via SIP</div>
            <div class="output-value" id="goalTotal">—</div>
          </div>
        </div>
        <div id="goalResult"></div>
      </div>
    </div>
    <!-- ROI -->
    <div id="invest-roi" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Buy Price (₹)</label>
          <input class="form-input" type="number" id="roiBuy" value="100000" oninput="calcROI()">
        </div>
        <div class="form-group">
          <label class="form-label">Current / Sell Price (₹)</label>
          <input class="form-input" type="number" id="roiSell" value="145000" oninput="calcROI()">
        </div>
        <div class="form-group">
          <label class="form-label">Holding Period (years)</label>
          <input class="form-input" type="number" id="roiYrs" value="3" oninput="calcROI()">
        </div>
        <button class="calc-btn" onclick="calcROI()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="output-grid">
          <div class="output-cell">
            <div class="output-label">Absolute Return</div>
            <div class="output-value green" id="roiAbs">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">CAGR</div>
            <div class="output-value blue" id="roiCagr">—</div>
          </div>
        </div>
        <div id="roiResult"></div>
      </div>
    </div>
    <!-- Scenario Engine -->
    <div id="invest-scenario" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group"><label class="form-label">Monthly SIP (₹)</label><input class="form-input" type="number" id="scSIP" value="15000" oninput="calcScenario()"></div>
        <div class="form-group"><label class="form-label">Investment Period (years)</label><input class="form-input" type="number" id="scYrs" value="20" oninput="calcScenario()"></div>
        <div class="form-group"><label class="form-label">Base Return Rate (%)</label><input class="form-input" type="number" id="scBase" value="12" oninput="calcScenario()"></div>
        <div class="form-group"><label class="form-label">Annual SIP Step-Up (%)</label><input class="form-input" type="number" id="scStepUp" value="10" oninput="calcScenario()"></div>
        <div class="form-group"><label class="form-label">Inflation Shock (%)</label><input class="form-input" type="number" id="scInflation" value="8" oninput="calcScenario()"></div>
        <div class="form-group"><label class="form-label">Market Crash Year</label><input class="form-input" type="number" id="scCrashYr" value="5" oninput="calcScenario()"></div>
        <div class="form-group"><label class="form-label">Crash Depth (%)</label><input class="form-input" type="number" id="scCrashPct" value="40" oninput="calcScenario()"></div>
        <button class="calc-btn" onclick="calcScenario()">Run All Scenarios</button>
      </div>
      <div class="calc-outputs">
        <div id="scenarioResult"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ RETIRE ═══ -->
<div id="retire" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Tools &mdash; Retirement</div>
      <div class="section-subtitle">FIRE &amp; Corpus Planning</div>
    </div>
  </div>
  <div class="calc-section">
    <div class="calc-tabs-bar">
      <button class="calc-tab active" onclick="switchTab(this,'retire','fire')">FIRE Calculator</button>
      <button class="calc-tab" onclick="switchTab(this,'retire','corpus')">Corpus Builder</button>
      <button class="calc-tab" onclick="switchTab(this,'retire','withdraw')">Withdrawal Planner</button>
    </div>
    <!-- FIRE -->
    <div id="retire-fire" class="calc-inner active">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Monthly Expenses (₹): <span id="fireExp-v" style="color:var(--blue)">₹60,000</span></label>
          <input type="range" id="fireExp" min="10000" max="500000" step="5000" value="60000" oninput="calcFIRE()">
          <div class="range-labels"><span>₹10K</span><span>₹5L</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Current Age: <span id="fireAge-v" style="color:var(--blue)">30</span></label>
          <input type="range" id="fireAge" min="20" max="60" step="1" value="30" oninput="calcFIRE()">
          <div class="range-labels"><span>20</span><span>60</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Target Retire Age: <span id="fireRetire-v" style="color:var(--blue)">45</span></label>
          <input type="range" id="fireRetire" min="25" max="70" step="1" value="45" oninput="calcFIRE()">
          <div class="range-labels"><span>25</span><span>70</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Inflation: <span id="fireInfl-v" style="color:var(--amber)">6%</span></label>
          <input type="range" id="fireInfl" min="3" max="12" step="0.5" value="6" oninput="calcFIRE()">
          <div class="range-labels"><span>3%</span><span>12%</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Safe Withdrawal Rate: <span id="fireSWR-v" style="color:var(--blue)">4%</span></label>
          <input type="range" id="fireSWR" min="2" max="8" step="0.5" value="4" oninput="calcFIRE()">
          <div class="range-labels"><span>2%</span><span>8%</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Current Savings (₹): <span id="fireSaved-v" style="color:var(--blue)">₹5L</span></label>
          <input type="range" id="fireSaved" min="0" max="20000000" step="50000" value="500000" oninput="calcFIRE()">
          <div class="range-labels"><span>0</span><span>₹2Cr</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Expected Return: <span id="fireRet-v" style="color:var(--blue)">12%</span></label>
          <input type="range" id="fireRet" min="5" max="20" step="0.5" value="12" oninput="calcFIRE()">
          <div class="range-labels"><span>5%</span><span>20%</span></div>
        </div>
        <button class="calc-btn" onclick="calcFIRE()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="summary-bar">
          <div class="sum-cell"><div class="sum-label">FIRE Number</div><div class="sum-value green" id="sb-fireNum">—</div><div class="sum-action" id="sb-fireAction">Corpus needed at retirement</div></div>
          <div class="sum-cell"><div class="sum-label">Years to FIRE</div><div class="sum-value blue" id="sb-fireYrs">—</div></div>
          <div class="sum-cell"><div class="sum-label">SIP Needed</div><div class="sum-value" id="sb-fireSIP">—</div></div>
          <div class="sum-cell"><div class="sum-label">Status</div><div class="sum-value" id="sb-fireStatus">—</div></div>
        </div>
        <div class="output-grid">
          <div class="output-cell full-col">
            <div class="output-label">FIRE Number</div>
            <div class="output-value big green" id="fireNum">—</div>
            <div class="output-sub">Corpus needed at retirement</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Years to FIRE</div>
            <div class="output-value blue" id="fireYrs">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Monthly SIP Needed</div>
            <div class="output-value" id="fireSIPNeeded">—</div>
          </div>
        </div>
        <div id="fireResult"></div>
        <div id="firePathResult"></div>
      </div>
    </div>
    <!-- Corpus Builder -->
    <div id="retire-corpus" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Target Corpus (₹)</label>
          <input class="form-input" type="number" id="corpTarget" value="50000000" oninput="calcCorpus()">
        </div>
        <div class="form-group">
          <label class="form-label">Years to Retirement</label>
          <input class="form-input" type="number" id="corpYrs" value="20" oninput="calcCorpus()">
        </div>
        <div class="form-group">
          <label class="form-label">Current Corpus (₹)</label>
          <input class="form-input" type="number" id="corpCurr" value="1000000" oninput="calcCorpus()">
        </div>
        <div class="form-group">
          <label class="form-label">Expected Return (%)</label>
          <input class="form-input" type="number" id="corpRate" value="12" oninput="calcCorpus()">
        </div>
        <div class="form-group">
          <label class="form-label">Annual Step-Up (%)</label>
          <input class="form-input" type="number" id="corpStep" value="10" oninput="calcCorpus()">
        </div>
        <button class="calc-btn" onclick="calcCorpus()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div id="corpusResult"></div>
      </div>
    </div>
    <!-- Withdrawal Planner -->
    <div id="retire-withdraw" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Retirement Corpus (₹)</label>
          <input class="form-input" type="number" id="wdCorpus" value="30000000" oninput="calcWithdraw()">
        </div>
        <div class="form-group">
          <label class="form-label">Monthly Withdrawal (₹)</label>
          <input class="form-input" type="number" id="wdAmt" value="80000" oninput="calcWithdraw()">
        </div>
        <div class="form-group">
          <label class="form-label">Return on Corpus (%)</label>
          <input class="form-input" type="number" id="wdRate" value="8" oninput="calcWithdraw()">
        </div>
        <div class="form-group">
          <label class="form-label">Inflation (%)</label>
          <input class="form-input" type="number" id="wdInfl" value="6" oninput="calcWithdraw()">
        </div>
        <div class="form-group">
          <label class="form-label">Annual Withdrawal Increase (%)</label>
          <input class="form-input" type="number" id="wdIncrease" value="6" oninput="calcWithdraw()">
        </div>
        <button class="calc-btn" onclick="calcWithdraw()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div id="withdrawResult"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ STOCKS / RISK LAB ═══ -->
<div id="stocks" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Tools &mdash; Risk</div>
      <div class="section-subtitle">Stock Risk Lab</div>
    </div>
  </div>
  <div class="calc-section">
    <div class="calc-tabs-bar">
      <button class="calc-tab active" onclick="switchTab(this,'stocks','crash')">Crash Simulator</button>
      <button class="calc-tab" onclick="switchTab(this,'stocks','stoploss')">Stop-Loss Calc</button>
      <button class="calc-tab" onclick="switchTab(this,'stocks','portfolio')">Portfolio Stress</button>
    </div>
    <!-- Crash -->
    <div id="stocks-crash" class="calc-inner active">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Portfolio Value (₹)</label>
          <input class="form-input" type="number" id="crashPort" value="2000000" oninput="calcCrash()">
        </div>
        <div class="form-group">
          <label class="form-label">Equity Allocation (%)</label>
          <input class="form-input" type="number" id="crashEq" value="80" oninput="calcCrash()">
        </div>
        <div class="form-group">
          <label class="form-label">Expected Annual Return (%)</label>
          <input class="form-input" type="number" id="crashRet" value="12" oninput="calcCrash()">
        </div>
        <button class="calc-btn" onclick="calcCrash()">Simulate</button>
      </div>
      <div class="calc-outputs">
        <div id="crashScenarios"></div>
      </div>
    </div>
    <!-- Stop-Loss -->
    <div id="stocks-stoploss" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Buy Price (₹)</label>
          <input class="form-input" type="number" id="slBuy" value="500" oninput="calcStopLoss()">
        </div>
        <div class="form-group">
          <label class="form-label">Quantity</label>
          <input class="form-input" type="number" id="slQty" value="100" oninput="calcStopLoss()">
        </div>
        <div class="form-group">
          <label class="form-label">Risk Tolerance (%)</label>
          <input class="form-input" type="number" id="slRisk" value="5" oninput="calcStopLoss()">
        </div>
        <button class="calc-btn" onclick="calcStopLoss()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div id="slResult"></div>
      </div>
    </div>
    <!-- Portfolio -->
    <div id="stocks-portfolio" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-row">
          <div class="form-group"><label class="form-label">Equity (₹)</label><input class="form-input" type="number" id="ptEq" value="1000000" oninput="calcPortfolio()"></div>
          <div class="form-group"><label class="form-label">Debt/FD (₹)</label><input class="form-input" type="number" id="ptDebt" value="500000" oninput="calcPortfolio()"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Gold (₹)</label><input class="form-input" type="number" id="ptGold" value="300000" oninput="calcPortfolio()"></div>
          <div class="form-group"><label class="form-label">Real Estate (₹)</label><input class="form-input" type="number" id="ptRE" value="5000000" oninput="calcPortfolio()"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Crypto (₹)</label><input class="form-input" type="number" id="ptCrypto" value="200000" oninput="calcPortfolio()"></div>
          <div class="form-group"><label class="form-label">Cash (₹)</label><input class="form-input" type="number" id="ptCash" value="100000" oninput="calcPortfolio()"></div>
        </div>
        <button class="calc-btn" onclick="calcPortfolio()">Stress Test</button>
      </div>
      <div class="calc-outputs">
        <div id="portfolioResult"></div>
        <div class="pie-wrap" id="portfolioPieWrap" style="display:none">
          <div style="position:relative;width:220px;height:220px"><canvas id="portfolioPie" role="img" aria-label="Asset allocation pie chart"></canvas></div>
          <div class="pie-legend" id="portfolioPieLegend"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ LOANS ═══ -->
<div id="loans" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Tools &mdash; Loans</div>
      <div class="section-subtitle">EMI &amp; Prepayment Calculator</div>
    </div>
  </div>
  <div class="calc-section">
    <div class="calc-tabs-bar">
      <button class="calc-tab active" onclick="switchTab(this,'loans','emi')">EMI Calculator</button>
      <button class="calc-tab" onclick="switchTab(this,'loans','prepay')">Prepayment Benefit</button>
    </div>
    <!-- EMI -->
    <div id="loans-emi" class="calc-inner active">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Loan Amount: <span id="emiAmt-v" style="color:var(--blue)">₹50,00,000</span></label>
          <input type="range" id="emiAmt" min="100000" max="50000000" step="100000" value="5000000" oninput="calcEMI()">
          <div class="range-labels"><span>₹1L</span><span>₹5Cr</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Interest Rate: <span id="emiRate-v" style="color:var(--amber)">8.5%</span></label>
          <input type="range" id="emiRate" min="5" max="20" step="0.1" value="8.5" oninput="calcEMI()">
          <div class="range-labels"><span>5%</span><span>20%</span></div>
        </div>
        <div class="form-group">
          <label class="form-label">Tenure: <span id="emiTenure-v" style="color:var(--blue)">20 yrs</span></label>
          <input type="range" id="emiTenure" min="1" max="30" step="1" value="20" oninput="calcEMI()">
          <div class="range-labels"><span>1</span><span>30 yrs</span></div>
        </div>
        <button class="calc-btn" onclick="calcEMI()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="summary-bar">
          <div class="sum-cell"><div class="sum-label">Monthly EMI</div><div class="sum-value" id="sb-emiEMI">—</div><div class="sum-action" id="sb-emiAction">Move to Prepayment tab to save interest</div></div>
          <div class="sum-cell"><div class="sum-label">Total Interest</div><div class="sum-value red" id="sb-emiInterest">—</div></div>
          <div class="sum-cell"><div class="sum-label">Total Payment</div><div class="sum-value" id="sb-emiTotal">—</div></div>
          <div class="sum-cell"><div class="sum-label">Interest Ratio</div><div class="sum-value amber" id="sb-emiRatio">—</div></div>
        </div>
        <div class="output-grid">
          <div class="output-cell full-col">
            <div class="output-label">Monthly EMI</div>
            <div class="output-value big" id="emiEMI">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Total Interest</div>
            <div class="output-value red" id="emiInterest">—</div>
          </div>
          <div class="output-cell">
            <div class="output-label">Total Payment</div>
            <div class="output-value" id="emiTotal">—</div>
          </div>
        </div>
        <div class="result-title">Rate Sensitivity (&plusmn;1%)</div>
        <div id="emiSensitivity"></div>
        <div class="result-title" style="margin-top:.75rem">Amortization Table</div>
        <div class="table-wrap">
          <table id="emiTable">
            <thead><tr><th>Year</th><th>Principal</th><th>Interest</th><th>Balance</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
    <!-- Prepay -->
    <div id="loans-prepay" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Outstanding Balance (₹)</label>
          <input class="form-input" type="number" id="ppBalance" value="4000000" oninput="calcPrepay()">
        </div>
        <div class="form-group">
          <label class="form-label">Interest Rate (%)</label>
          <input class="form-input" type="number" id="ppRate" value="8.5" oninput="calcPrepay()">
        </div>
        <div class="form-group">
          <label class="form-label">Remaining Months</label>
          <input class="form-input" type="number" id="ppMonths" value="216" oninput="calcPrepay()">
        </div>
        <div class="form-group">
          <label class="form-label">Extra Monthly Payment (₹)</label>
          <input class="form-input" type="number" id="ppExtra" value="5000" oninput="calcPrepay()">
        </div>
        <div class="form-group">
          <label class="form-label">One-Time Prepayment (₹)</label>
          <input class="form-input" type="number" id="ppLump" value="500000" oninput="calcPrepay()">
        </div>
        <button class="calc-btn" onclick="calcPrepay()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div id="prepayResult"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ TAX ═══ -->
<div id="tax" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Tools &mdash; Tax</div>
      <div class="section-subtitle">Tax Optimizer &amp; Capital Gains</div>
    </div>
  </div>
  <div class="calc-section">
    <div class="calc-tabs-bar">
      <button class="calc-tab active" onclick="switchTab(this,'tax','income')">Income Tax</button>
      <button class="calc-tab" onclick="switchTab(this,'tax','cg')">Capital Gains</button>
    </div>
    <!-- Income Tax -->
    <div id="tax-income" class="calc-inner active">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Annual Income (₹)</label>
          <input class="form-input" type="number" id="taxInc" value="1500000" oninput="calcTax()">
        </div>
        <div class="form-group">
          <label class="form-label">HRA Exemption (₹)</label>
          <input class="form-input" type="number" id="taxHRA" value="120000" oninput="calcTax()">
        </div>
        <div class="form-group">
          <label class="form-label">80C Investments (₹ max 1.5L)</label>
          <input class="form-input" type="number" id="tax80C" value="150000" oninput="calcTax()">
        </div>
        <div class="form-group">
          <label class="form-label">Home Loan Interest (₹)</label>
          <input class="form-input" type="number" id="taxHL" value="0" oninput="calcTax()">
        </div>
        <div class="form-group">
          <label class="form-label">NPS 80CCD (₹ max 50K)</label>
          <input class="form-input" type="number" id="taxNPS" value="50000" oninput="calcTax()">
        </div>
        <div class="form-group">
          <label class="form-label">Other Deductions (₹)</label>
          <input class="form-input" type="number" id="taxOther" value="0" oninput="calcTax()">
        </div>
        <button class="calc-btn" onclick="calcTax()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div class="summary-bar">
          <div class="sum-cell"><div class="sum-label">Best Regime</div><div class="sum-value green" id="sb-taxRegime">—</div><div class="sum-action" id="sb-taxAction">—</div></div>
          <div class="sum-cell"><div class="sum-label">Tax Saving</div><div class="sum-value green" id="sb-taxSaving">—</div></div>
          <div class="sum-cell"><div class="sum-label">Old Regime Tax</div><div class="sum-value amber" id="sb-taxOld">—</div></div>
          <div class="sum-cell"><div class="sum-label">New Regime Tax</div><div class="sum-value blue" id="sb-taxNew">—</div></div>
        </div>
        <div id="taxResult"></div>
        <div style="font-size:10px;color:var(--muted);margin-top:.75rem;font-family:var(--mono)">FY 2024-25 &middot; Resident individual &middot; Verify with a CA</div>
      </div>
    </div>
    <!-- Capital Gains -->
    <div id="tax-cg" class="calc-inner">
      <div class="calc-inputs">
        <div class="form-group">
          <label class="form-label">Asset Type</label>
          <select class="form-input" id="cgType" onchange="calcCG()">
            <option value="equity">Equity / Stocks</option>
            <option value="mf">Equity Mutual Fund</option>
            <option value="debt">Debt / FD</option>
            <option value="property">Property</option>
            <option value="gold">Gold</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Purchase Price (₹)</label>
          <input class="form-input" type="number" id="cgBuy" value="500000" oninput="calcCG()">
        </div>
        <div class="form-group">
          <label class="form-label">Sale Price (₹)</label>
          <input class="form-input" type="number" id="cgSell" value="800000" oninput="calcCG()">
        </div>
        <div class="form-group">
          <label class="form-label">Holding Period (months)</label>
          <input class="form-input" type="number" id="cgMonths" value="18" oninput="calcCG()">
        </div>
        <div class="form-group">
          <label class="form-label">Tax Bracket (%)</label>
          <input class="form-input" type="number" id="cgBracket" value="30" oninput="calcCG()">
        </div>
        <button class="calc-btn" onclick="calcCG()">Calculate</button>
      </div>
      <div class="calc-outputs">
        <div id="cgResult"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ HEALTH SCORE ═══ -->
<div id="health" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Tools &mdash; Assessment</div>
      <div class="section-subtitle">Financial Health Score</div>
    </div>
  </div>
  <div class="calc-section">
    <div class="calc-inner active" style="grid-template-columns:360px 1fr">
      <div class="calc-inputs">
        <div class="form-group"><label class="form-label">Monthly Income (₹)</label><input class="form-input" type="number" id="hInc" value="150000" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Monthly Expenses (₹)</label><input class="form-input" type="number" id="hExp" value="60000" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Monthly EMI (₹)</label><input class="form-input" type="number" id="hEMI" value="30000" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Monthly Savings (₹)</label><input class="form-input" type="number" id="hSave" value="30000" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Emergency Fund (months)</label><input class="form-input" type="number" id="hEF" value="4" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Total Debt (₹)</label><input class="form-input" type="number" id="hDebt" value="3000000" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Total Investments (₹)</label><input class="form-input" type="number" id="hInvest" value="1000000" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Life Insurance (Cr)</label><input class="form-input" type="number" id="hInsure" value="1" oninput="calcHealth()"></div>
        <div class="form-group"><label class="form-label">Age</label><input class="form-input" type="number" id="hAge" value="32" oninput="calcHealth()"></div>
        <button class="calc-btn" onclick="calcHealth()">Calculate Score</button>
      </div>
      <div class="calc-outputs">
        <div id="healthResult"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ PROFILE ═══ -->
<div id="profile" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Your Profile</div>
      <div class="section-subtitle">Financial Snapshot</div>
    </div>
    <div class="section-meta">Saved locally &middot; Auto-fills all calculators</div>
  </div>
  <div class="profile-grid">
    <div>
      <div class="form-group"><label class="form-label">Monthly Take-Home Salary (₹)</label><input class="form-input" type="number" id="pSalary" placeholder="e.g. 150000"></div>
      <div class="form-group"><label class="form-label">Monthly Living Expenses (₹)</label><input class="form-input" type="number" id="pExpenses" placeholder="e.g. 60000"></div>
      <div class="form-group"><label class="form-label">Monthly EMI Total (₹)</label><input class="form-input" type="number" id="pEMI" placeholder="e.g. 35000"></div>
      <div class="form-group"><label class="form-label">Monthly SIP / Investment (₹)</label><input class="form-input" type="number" id="pSIP" placeholder="e.g. 25000"></div>
    </div>
    <div>
      <div class="form-group"><label class="form-label">Your Age</label><input class="form-input" type="number" id="pAge" placeholder="e.g. 32"></div>
      <div class="form-group"><label class="form-label">Target Retirement Age</label><input class="form-input" type="number" id="pRetireAge" placeholder="e.g. 55"></div>
      <div class="form-group"><label class="form-label">Emergency Fund (₹)</label><input class="form-input" type="number" id="pEmergency" placeholder="e.g. 360000"></div>
      <div class="form-group"><label class="form-label">Total Savings &amp; Investments (₹)</label><input class="form-input" type="number" id="pSavings" placeholder="e.g. 1500000"></div>
    </div>
  </div>
  <div style="padding:0 1.5rem 1.5rem;display:flex;gap:.75rem;flex-wrap:wrap">
    <button class="calc-btn" style="max-width:200px" onclick="applyProfile()">Save &amp; Apply Profile</button>
    <div id="profileMsg">Profile saved! Dashboard and calculators updated.</div>
  </div>
</div>

<!-- ═══ DASHBOARD ═══ -->
<div id="dashboard" class="section">
  <div class="section-header">
    <div>
      <div class="section-title">Financial Dashboard</div>
      <div class="section-subtitle">Health Score &amp; Action Plan</div>
    </div>
  </div>
  <div id="dashNoProfile" class="no-profile-notice" style="margin:1.5rem;display:none">
    <h3>Profile not set up yet</h3>
    <p>Go to the <strong>Profile</strong> tab and enter your financial details to unlock your personal dashboard.</p>
    <button class="calc-btn" style="max-width:180px;margin:1rem auto 0" onclick="showSection('profile')">Set Up Profile →</button>
  </div>
  <div id="dashContent">
    <div class="dash-kpi-row" id="dashKPIs"></div>
    <div class="dash-body">
      <div class="dash-main">
        <div class="dash-block">
          <div class="dash-block-title">Active Alerts</div>
          <div class="alert-list" id="dashAlerts"></div>
        </div>
        <div class="dash-block">
          <div class="dash-block-title">Priority Recommendations</div>
          <ul class="rec-list-pro" id="dashRecs"></ul>
        </div>
      </div>
      <div class="dash-side">
        <div class="dash-block">
          <div class="dash-block-title">Retirement Scenarios</div>
          <div id="dashScenarios"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Footer -->
<footer>
  <div style="font-weight:700;color:var(--text2)">FinVault Pro</div>
  <div class="footer-trust">
    <span>&#9889; Hourly GitHub Actions</span>
    <span>&#128274; No login &middot; data stays local</span>
    <span>&#128225; NSE &middot; NYSE &middot; CoinGecko &middot; yFinance</span>
    <span>&#9888;&#65039; Educational only &mdash; not investment advice</span>
  </div>
</footer>

</div><!-- /page -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
// ════════ UTILS ════════
function fmtC(v){if(!v&&v!==0)return'—';if(v>=1e7)return'₹'+(v/1e7).toFixed(2)+' Cr';if(v>=1e5)return'₹'+(v/1e5).toFixed(2)+' L';return'₹'+Math.round(v).toLocaleString('en-IN')}
function fmtINR(v){return'₹'+Math.round(v).toLocaleString('en-IN')}
function pct(v,dp=2){return(v>=0?'+':'')+v.toFixed(dp)+'%'}
// ── Number counter animation ──
function animateNum(el,rawVal,fmt){
  if(!el)return;
  el.classList.remove('num-animated');
  void el.offsetWidth;
  el.classList.add('num-animated');
  const start=performance.now(),dur=500,from=0;
  function step(ts){
    const p=Math.min((ts-start)/dur,1),ease=1-Math.pow(1-p,3);
    el.textContent=fmt(from+(rawVal-from)*ease);
    if(p<1)requestAnimationFrame(step);else el.textContent=fmt(rawVal);
  }
  requestAnimationFrame(step);
}

// ════════ NAV ════════
function showSection(id){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.nav-links button').forEach(b=>b.classList.remove('active'));
  const sec=document.getElementById(id);
  if(sec)sec.classList.add('active');
  document.querySelectorAll('.nav-links button').forEach(b=>{
    if(b.getAttribute('onclick')&&b.getAttribute('onclick').includes("'"+id+"'"))b.classList.add('active');
  });
  if(id==='dashboard')renderDashboard();
}

// ════════ CALC TABS ════════
function switchTab(btn,section,tab){
  document.querySelectorAll('#'+section+' .calc-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('#'+section+' .calc-inner').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  const el=document.getElementById(section+'-'+tab);
  if(el)el.classList.add('active');
}

// ════════ SIGNAL PANEL ════════
function selectSignal(key){
  document.querySelectorAll('.js-sig-item').forEach(i=>i.classList.remove('active'));
  const item=document.querySelector('.js-sig-item[data-asset="'+key+'"]');
  if(item)item.classList.add('active');
  document.querySelectorAll('.signal-detail-panel').forEach(p=>p.classList.remove('active'));
  const det=document.getElementById('det-'+key);
  if(det)det.classList.add('active');
}

// ════════ SIP ════════
function calcSIP(){
  const sip=parseFloat(document.getElementById('sipAmt').value)||0;
  const rate=parseFloat(document.getElementById('sipRate').value)||12;
  const yrs=parseInt(document.getElementById('sipYrs').value)||15;
  document.getElementById('sipAmt-v')&&(document.getElementById('sipAmt-v').textContent=fmtC(sip));
  const r=rate/100/12,n=yrs*12;
  const corpus=sip*(Math.pow(1+r,n)-1)/r*(1+r);
  const invested=sip*n,gain=corpus-invested;
  const multiple=corpus/invested;
  // summary bar + animated numbers
  animateNum(document.getElementById('sb-sipCorpus'),corpus,fmtC);
  animateNum(document.getElementById('sb-sipInvested'),invested,fmtC);
  animateNum(document.getElementById('sb-sipGain'),gain,fmtC);
  const sbMult=document.getElementById('sb-sipMultiple');
  if(sbMult){sbMult.classList.remove('num-animated');void sbMult.offsetWidth;sbMult.classList.add('num-animated');sbMult.textContent=multiple.toFixed(2)+'x';}
  const sbAct=document.getElementById('sb-sipAction');
  if(sbAct)sbAct.textContent=multiple>=5?'Excellent compounding — stay invested!':multiple>=3?'Good growth — increase SIP yearly by 10%':'Consider increasing SIP or tenure';
  // detail outputs
  document.getElementById('sipCorpus').textContent=fmtC(corpus);
  document.getElementById('sipCorpusSub').textContent='at '+rate+'% p.a. over '+yrs+' years';
  document.getElementById('sipInvested').textContent=fmtC(invested);
  document.getElementById('sipGain').textContent=fmtC(gain);
  document.getElementById('sipMultiple').textContent=multiple.toFixed(2)+'x';
  document.getElementById('sipCagr').textContent=rate+'%';
  // Sensitivity
  const rates=[rate-4,rate-2,rate,rate+2,rate+4].filter(x=>x>0);
  document.getElementById('sipSensitivity').innerHTML=rates.map(rv=>{
    const c2=sip*(Math.pow(1+rv/100/12,n)-1)/(rv/100/12)*(1+rv/100/12);
    const hi=rv>rate,lo=rv<rate;
    return'<div class="sens-row"><span style="color:var(--muted)">'+rv.toFixed(1)+'%'+(rv===rate?' (current)':'')+'</span><span style="color:'+(hi?'var(--green)':lo?'var(--red)':'var(--text)')+'">'+fmtC(c2)+'</span></div>';
  }).join('');
  // Mini chart
  const pts=[];const ptsFill=[];
  for(let y=0;y<=yrs;y++){const c=sip*(Math.pow(1+r,y*12)-1)/r*(1+r);pts.push({x:y/yrs*500,y:c});}
  const maxC=pts[pts.length-1].y||1;
  const svgPts=pts.map(p=>p.x+','+(80-p.y/maxC*72)).join(' ');
  document.getElementById('sipChartLine').setAttribute('d','M'+svgPts.replace(/ /g,' L'));
  document.getElementById('sipChartPath').setAttribute('d','M'+svgPts.replace(/ /g,' L')+'L500,80 L0,80 Z');
}

// ════════ LUMP SUM ════════
function calcLS(){
  const amt=parseFloat(document.getElementById('lsAmt').value)||0;
  const rate=parseFloat(document.getElementById('lsRate').value)||12;
  const yrs=parseInt(document.getElementById('lsYrs').value)||10;
  const infl=parseFloat(document.getElementById('lsInfl').value)||6;
  const corpus=amt*Math.pow(1+rate/100,yrs);
  const real=corpus/Math.pow(1+infl/100,yrs);
  const realRet=((Math.pow(corpus/amt,1/yrs)-1)*100).toFixed(2);
  document.getElementById('lsCorpus').textContent=fmtC(corpus);
  document.getElementById('lsGain').textContent=fmtC(corpus-amt);
  document.getElementById('lsReal').textContent=fmtC(real);
  document.getElementById('lsMultiple').textContent=(corpus/amt).toFixed(2)+'x';
  document.getElementById('lsRealRet').textContent=((real/amt-1)*100/yrs).toFixed(1)+'% real';
  document.getElementById('lsInflResult').innerHTML='<div class="result-box"><div class="result-title">Inflation Impact</div><div class="sens-row"><span>Nominal value</span><span style="color:var(--green)">'+fmtC(corpus)+'</span></div><div class="sens-row"><span>Real value ('+infl+'% inflation)</span><span style="color:var(--amber)">'+fmtC(real)+'</span></div><div class="sens-row"><span>Purchasing power lost</span><span style="color:var(--red)">'+fmtC(corpus-real)+'</span></div></div>';
}

// ════════ GOAL PLANNER ════════
function calcGoal(){
  const target=parseFloat(document.getElementById('goalAmt').value)||0;
  const yrs=parseInt(document.getElementById('goalYrs').value)||15;
  const rate=parseFloat(document.getElementById('goalRate').value)||12;
  const r=rate/100/12,n=yrs*12;
  const sip=target*r/(Math.pow(1+r,n)-1)/(1+r);
  const ls=target/Math.pow(1+rate/100,yrs);
  document.getElementById('goalSIP').textContent=fmtC(sip);
  document.getElementById('goalLS').textContent=fmtC(ls);
  document.getElementById('goalTotal').textContent=fmtC(sip*n);
  document.getElementById('goalResult').innerHTML='<div class="result-box"><div class="result-title">Summary</div><div class="sens-row"><span>Target corpus</span><span>'+fmtC(target)+'</span></div><div class="sens-row"><span>Monthly SIP required</span><span style="color:var(--green)">'+fmtC(sip)+'</span></div><div class="sens-row"><span>Or lump-sum today</span><span style="color:var(--blue)">'+fmtC(ls)+'</span></div><div class="sens-row"><span>Total via SIP route</span><span>'+fmtC(sip*n)+'</span></div></div>';
}

// ════════ ROI ════════
function calcROI(){
  const buy=parseFloat(document.getElementById('roiBuy').value)||0;
  const sell=parseFloat(document.getElementById('roiSell').value)||0;
  const yrs=parseFloat(document.getElementById('roiYrs').value)||1;
  const abs=(sell-buy)/buy*100;
  const cagr=(Math.pow(sell/buy,1/yrs)-1)*100;
  document.getElementById('roiAbs').textContent=abs.toFixed(2)+'%';
  document.getElementById('roiCagr').textContent=cagr.toFixed(2)+'%';
  const benchmarks=[['FD (7.5%)',7.5],['Gold (10%)',10],['Nifty (12%)',12],['Nifty (15%)',15]];
  const bRows=benchmarks.map(([label,r])=>{const bv=buy*Math.pow(1+r/100,yrs);const diff=sell-bv;return'<div class="sens-row"><span>'+label+'</span><span style="color:'+(diff>=0?'var(--green)':'var(--red)')+'">'+fmtC(bv)+' ('+(diff>=0?'+':'')+fmtC(diff)+')</span></div>';}).join('');
  document.getElementById('roiResult').innerHTML='<div class="result-box"><div class="result-title">vs Benchmarks (same period)</div>'+bRows+'</div>';
}

// ════════ FIRE ════════
function calcFIRE(){
  const exp=parseFloat(document.getElementById('fireExp').value)||0;
  const age=parseInt(document.getElementById('fireAge').value)||30;
  const retire=parseInt(document.getElementById('fireRetire').value)||45;
  const infl=parseFloat(document.getElementById('fireInfl').value)||6;
  const swr=parseFloat(document.getElementById('fireSWR').value)||4;
  const saved=parseFloat(document.getElementById('fireSaved').value)||0;
  const ret=parseFloat(document.getElementById('fireRet').value)||12;
  const yrs=Math.max(1,retire-age);
  const inflExp=exp*Math.pow(1+infl/100,yrs);
  const fireNum=inflExp*12/(swr/100);
  const r=ret/100/12,n=yrs*12;
  const futureSaved=saved*Math.pow(1+ret/100,yrs);
  const gap=Math.max(0,fireNum-futureSaved);
  const sipNeeded=gap>0?gap*r/((Math.pow(1+r,n)-1)*(1+r)):0;
  document.getElementById('fireNum').textContent=fmtC(fireNum);
  document.getElementById('fireYrs').textContent=yrs+' years';
  document.getElementById('fireSIPNeeded').textContent=fmtC(sipNeeded);
  document.getElementById('fireExp-v').textContent=fmtC(exp);
  document.getElementById('fireAge-v').textContent=age;
  document.getElementById('fireRetire-v').textContent=retire;
  document.getElementById('fireInfl-v').textContent=infl+'%';
  document.getElementById('fireSWR-v').textContent=swr+'%';
  document.getElementById('fireSaved-v').textContent=fmtC(saved);
  document.getElementById('fireRet-v').textContent=ret+'%';
  const status=futureSaved>=fireNum;
  document.getElementById('fireResult').innerHTML='<div class="verdict '+(status?'good':'warning')+'"><strong>'+(status?'On Track':'Gap Alert')+'</strong> — '+(status?'Your current savings + SIP covers the FIRE number.':'You need '+fmtC(sipNeeded)+'/mo SIP to bridge the gap.')+'</div>';
  document.getElementById('firePathResult').innerHTML='<div class="scenario-list"><div class="scenario-row"><span class="scenario-label">Monthly expenses at retirement</span><span class="scenario-val">'+fmtC(inflExp)+'</span></div><div class="scenario-row"><span class="scenario-label">FIRE number ('+swr+'% SWR)</span><span class="scenario-val green">'+fmtC(fireNum)+'</span></div><div class="scenario-row"><span class="scenario-label">Current savings future value</span><span class="scenario-val">'+fmtC(futureSaved)+'</span></div><div class="scenario-row"><span class="scenario-label">Gap to bridge via SIP</span><span class="scenario-val '+(gap>0?'amber':'green')+'">'+fmtC(gap)+'</span></div></div>';
  // summary bar
  animateNum(document.getElementById('sb-fireNum'),fireNum,fmtC);
  animateNum(document.getElementById('sb-fireSIP'),sipNeeded,fmtC);
  const sbFY=document.getElementById('sb-fireYrs');if(sbFY){sbFY.classList.remove('num-animated');void sbFY.offsetWidth;sbFY.classList.add('num-animated');sbFY.textContent=yrs+' yrs';}
  const sbFS=document.getElementById('sb-fireStatus');if(sbFS){sbFS.classList.remove('num-animated');void sbFS.offsetWidth;sbFS.classList.add('num-animated');sbFS.textContent=status?'On Track':'Gap Alert';sbFS.style.color=status?'var(--green)':'var(--amber)';}
  const sbFA=document.getElementById('sb-fireAction');if(sbFA)sbFA.textContent=status?'You are on track — keep investing!':'Increase SIP by '+fmtC(sipNeeded)+'/mo to retire at '+retire;
}

// ════════ CORPUS ════════
function calcCorpus(){
  const target=parseFloat(document.getElementById('corpTarget').value)||0;
  const yrs=parseInt(document.getElementById('corpYrs').value)||20;
  const curr=parseFloat(document.getElementById('corpCurr').value)||0;
  const rate=parseFloat(document.getElementById('corpRate').value)||12;
  const step=parseFloat(document.getElementById('corpStep').value)||10;
  const r=rate/100/12;
  const futureCurr=curr*Math.pow(1+rate/100,yrs);
  const gap=Math.max(0,target-futureCurr);
  let sip=0,n=yrs*12;
  if(step===0){sip=gap*r/((Math.pow(1+r,n)-1)*(1+r));}
  else{let s=gap*r/((Math.pow(1+r,n)-1)*(1+r));sip=s;}
  document.getElementById('corpusResult').innerHTML='<div class="output-grid"><div class="output-cell full-col"><div class="output-label">Monthly SIP Needed</div><div class="output-value big green">'+fmtC(sip)+'</div></div><div class="output-cell"><div class="output-label">Target Corpus</div><div class="output-value">'+fmtC(target)+'</div></div><div class="output-cell"><div class="output-label">Covered by Savings</div><div class="output-value blue">'+fmtC(futureCurr)+'</div></div></div><div class="scenario-list" style="margin-top:.75rem"><div class="scenario-row"><span class="scenario-label">Corpus target</span><span class="scenario-val">'+fmtC(target)+'</span></div><div class="scenario-row"><span class="scenario-label">Current savings grow to</span><span class="scenario-val blue">'+fmtC(futureCurr)+'</span></div><div class="scenario-row"><span class="scenario-label">SIP must generate</span><span class="scenario-val amber">'+fmtC(gap)+'</span></div><div class="scenario-row"><span class="scenario-label">Monthly SIP required</span><span class="scenario-val green">'+fmtC(sip)+'</span></div></div>';
}

// ════════ WITHDRAW ════════
function calcWithdraw(){
  const corpus=parseFloat(document.getElementById('wdCorpus').value)||0;
  const wd=parseFloat(document.getElementById('wdAmt').value)||0;
  const ret=parseFloat(document.getElementById('wdRate').value)||8;
  const infl=parseFloat(document.getElementById('wdInfl').value)||6;
  const inc=parseFloat(document.getElementById('wdIncrease').value)||6;
  let bal=corpus,m=0,monthly=wd;
  while(bal>0&&m<600){bal=bal*(1+ret/100/12)-monthly;monthly*=(1+inc/100/12);m++;}
  const yrs=m/12;
  document.getElementById('withdrawResult').innerHTML='<div class="output-grid"><div class="output-cell full-col"><div class="output-label">Corpus Lasts</div><div class="output-value big '+(yrs>=30?'green':'amber')+'">'+yrs.toFixed(1)+' years</div><div class="output-sub">until age '+(yrs>=30?'well into retirement':'— consider increasing corpus')+'</div></div></div><div class="verdict '+(yrs>=30?'good':'warning')+'">'+(yrs>=30?'Corpus is sufficient for 30+ year retirement.':'Corpus may run out. Increase corpus or reduce withdrawal.')+'</div>';
}

// ════════ CRASH ════════
function calcCrash(){
  const port=parseFloat(document.getElementById('crashPort').value)||0;
  const eq=parseFloat(document.getElementById('crashEq').value)||80;
  const ret=parseFloat(document.getElementById('crashRet').value)||12;
  const eqVal=port*eq/100;
  const nonEq=port-eqVal;
  const scenarios=[['10% correction (common)',10,2],['20% bear market',20,4],['30% crash (2008-like)',30,6],['50% crash (rare/extreme)',50,10]];
  document.getElementById('crashScenarios').innerHTML='<div class="scenario-list">'+scenarios.map(([label,drop,recYrs])=>{
    const loss=eqVal*drop/100;const after=port-loss;
    const recMonths=Math.log(port/after)/Math.log(1+ret/100/12);
    return'<div class="scenario-row" style="flex-direction:column;align-items:flex-start;padding:.9rem 1rem;gap:4px"><div style="font-size:11px;font-weight:700;color:var(--text)">'+label+'</div><div style="display:flex;gap:2rem;font-family:var(--mono);font-size:11px"><span style="color:var(--red)">Loss: '+fmtC(loss)+'</span><span>After: '+fmtC(after)+'</span><span style="color:var(--text2)">Recovery: ~'+Math.ceil(recMonths/12)+' yrs</span></div></div>';
  }).join('')+'</div><div class="verdict neutral" style="margin-top:.75rem">Hold — do not panic-sell. Markets recover. Time in market beats timing the market.</div>';
}

// ════════ STOP-LOSS ════════
function calcStopLoss(){
  const buy=parseFloat(document.getElementById('slBuy').value)||0;
  const qty=parseFloat(document.getElementById('slQty').value)||0;
  const risk=parseFloat(document.getElementById('slRisk').value)||5;
  const sl=buy*(1-risk/100);
  const targets=[1.5,2,3].map(r=>buy*(1+r*risk/100));
  document.getElementById('slResult').innerHTML='<div class="output-grid"><div class="output-cell"><div class="output-label">Stop-Loss Price</div><div class="output-value red">₹'+sl.toFixed(2)+'</div><div class="output-sub">Max loss: '+fmtC(qty*(buy-sl))+'</div></div><div class="output-cell"><div class="output-label">1.5R Target</div><div class="output-value green">₹'+targets[0].toFixed(2)+'</div></div><div class="output-cell"><div class="output-label">2R Target</div><div class="output-value green">₹'+targets[1].toFixed(2)+'</div></div><div class="output-cell"><div class="output-label">3R Target</div><div class="output-value green">₹'+targets[2].toFixed(2)+'</div></div></div>';
}

// ════════ PORTFOLIO ════════
function calcPortfolio(){
  const eq=+document.getElementById('ptEq').value||0,debt=+document.getElementById('ptDebt').value||0,gold=+document.getElementById('ptGold').value||0,re=+document.getElementById('ptRE').value||0,crypto=+document.getElementById('ptCrypto').value||0,cash=+document.getElementById('ptCash').value||0;
  const total=eq+debt+gold+re+crypto+cash;
  if(!total)return;
  const assets=[['Equity',eq,12,-30],['Debt/FD',debt,8,-2],['Gold',gold,10,-15],['Real Estate',re,10,-25],['Crypto',crypto,50,-60],['Cash',cash,3,-1]];
  const crashImpact=assets.reduce((s,[,v,,crash])=>s+v*crash/100,0);
  document.getElementById('portfolioResult').innerHTML='<div class="output-grid"><div class="output-cell full-col"><div class="output-label">Total Portfolio</div><div class="output-value big">'+fmtC(total)+'</div></div></div><div class="scenario-list" style="margin-top:.75rem">'+assets.filter(([,v])=>v>0).map(([label,v,exp,crash])=>'<div class="scenario-row"><span class="scenario-label">'+label+' ('+((v/total)*100).toFixed(0)+'%)</span><span style="font-family:var(--mono);font-size:11px"><span style="color:var(--green)">+'+exp+'%</span> / <span style="color:var(--red)">'+crash+'%</span></span></div>').join('')+'<div class="scenario-row" style="background:rgba(232,64,64,.05)"><span class="scenario-label" style="font-weight:700">30% crash impact</span><span class="scenario-val red">'+fmtC(crashImpact)+'</span></div></div>';
  // Doughnut pie chart
  const pieWrap=document.getElementById('portfolioPieWrap');
  const activeAssets=assets.filter(([,v])=>v>0);
  if(pieWrap&&activeAssets.length){
    pieWrap.style.display='flex';
    const COLORS=['#1a6bff','#00c07f','#e8a825','#e84040','#a259ff','#4ecdc4'];
    const labels=activeAssets.map(([l])=>l),vals=activeAssets.map(([,v])=>v);
    const canvas=document.getElementById('portfolioPie');
    if(window._portfolioPieChart){window._portfolioPieChart.destroy();}
    window._portfolioPieChart=new Chart(canvas,{
      type:'doughnut',
      data:{labels,datasets:[{data:vals,backgroundColor:COLORS.slice(0,labels.length),borderWidth:1,borderColor:'#05080f',hoverOffset:6}]},
      options:{responsive:true,maintainAspectRatio:true,cutout:'62%',plugins:{legend:{display:false},tooltip:{callbacks:{label:function(ctx){return ctx.label+': '+fmtC(ctx.raw)+' ('+(ctx.raw/total*100).toFixed(1)+'%)';}}}}},
    });
    document.getElementById('portfolioPieLegend').innerHTML=labels.map((l,i)=>'<div class="pie-legend-row"><span class="pie-dot" style="background:'+COLORS[i]+'"></span><span class="pie-legend-label">'+l+'</span><span class="pie-legend-val">'+((vals[i]/total)*100).toFixed(1)+'%</span></div>').join('');
  }
}

// ════════ EMI ════════
function calcEMI(){
  const P=+document.getElementById('emiAmt').value||0;
  const rAnn=+document.getElementById('emiRate').value||8.5;
  const yrs=+document.getElementById('emiTenure').value||20;
  document.getElementById('emiAmt-v').textContent=fmtC(P);
  document.getElementById('emiRate-v').textContent=rAnn+'%';
  document.getElementById('emiTenure-v').textContent=yrs+' yrs';
  const r=rAnn/100/12,n=yrs*12;
  const EMI=P*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1);
  const totalPay=EMI*n,interest=totalPay-P;
  document.getElementById('emiEMI').textContent=fmtINR(EMI);
  document.getElementById('emiInterest').textContent=fmtC(interest);
  document.getElementById('emiTotal').textContent=fmtC(totalPay);
  // summary bar
  animateNum(document.getElementById('sb-emiEMI'),EMI,fmtINR);
  animateNum(document.getElementById('sb-emiInterest'),interest,fmtC);
  animateNum(document.getElementById('sb-emiTotal'),totalPay,fmtC);
  const ratio=interest/totalPay*100;
  const sbR=document.getElementById('sb-emiRatio');if(sbR){sbR.classList.remove('num-animated');void sbR.offsetWidth;sbR.classList.add('num-animated');sbR.textContent=ratio.toFixed(0)+'% interest';}
  const sbA=document.getElementById('sb-emiAction');if(sbA)sbA.textContent=ratio>60?'High interest burden — consider prepaying early':ratio>40?'Moderate burden — lump-sum prepay saves big':'Healthy loan structure';
  // Sensitivity
  const sensRates=[rAnn-1,rAnn-0.5,rAnn,rAnn+0.5,rAnn+1];
  document.getElementById('emiSensitivity').innerHTML=sensRates.filter(x=>x>0).map(rv=>{
    const rr=rv/100/12,e=P*rr*Math.pow(1+rr,n)/(Math.pow(1+rr,n)-1);
    const diff=e-EMI;
    return'<div class="sens-row"><span style="color:var(--muted)">'+rv.toFixed(1)+'%'+(rv===rAnn?' ◀':'')+'</span><span style="color:'+(rv===rAnn?'var(--text)':diff<0?'var(--green)':'var(--red)')+'">'+fmtINR(e)+'</span></div>';
  }).join('');
  // Table
  let bal=P,tbod='';
  for(let y=1;y<=yrs;y++){let yP=0,yI=0;for(let m=0;m<12;m++){const im=bal*r,pm=EMI-im;yI+=im;yP+=pm;bal-=pm;}tbod+=`<tr><td>${y}</td><td class="positive">${fmtC(yP)}</td><td class="negative">${fmtC(yI)}</td><td>${fmtC(Math.max(0,bal))}</td></tr>`;}
  document.querySelector('#emiTable tbody').innerHTML=tbod;
}

// ════════ PREPAY ════════
function calcPrepay(){
  const bal=+document.getElementById('ppBalance').value,r=+document.getElementById('ppRate').value/100/12,n=+document.getElementById('ppMonths').value,extra=+document.getElementById('ppExtra').value,lump=+document.getElementById('ppLump').value;
  const emi=bal*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1),origTotal=emi*n;
  const newBal=Math.max(0,bal-lump),newEMI=newBal*r*Math.pow(1+r,n)/(Math.pow(1+r,n)-1),lumpSave=origTotal-(newEMI*n+lump);
  let b2=bal,m2=0;while(b2>0&&m2<n*2){b2=b2*(1+r)-(emi+extra);m2++;}
  const extraSave=origTotal-(emi+extra)*m2,monthsSaved=n-m2;
  document.getElementById('prepayResult').innerHTML='<div class="output-grid"><div class="output-cell"><div class="output-label">Lump Sum '+fmtC(lump)+' saves</div><div class="output-value green">'+fmtC(Math.max(0,lumpSave))+'</div><div class="output-sub">in interest</div></div><div class="output-cell"><div class="output-label">Extra '+fmtINR(extra)+'/mo saves</div><div class="output-value green">'+fmtC(Math.max(0,extraSave))+'</div><div class="output-sub">'+Math.max(0,monthsSaved)+' months early closure</div></div></div>';
}

// ════════ TAX ════════
function calcSlabTax(income,regime){
  let tax=0;
  if(regime==='old'){if(income<=250000)tax=0;else if(income<=500000)tax=(income-250000)*.05;else if(income<=1000000)tax=12500+(income-500000)*.2;else tax=112500+(income-1000000)*.3;}
  else{const slabs=[[300000,0],[600000,.05],[900000,.1],[1200000,.15],[1500000,.2],[Infinity,.3]];let prev=0;for(const[limit,rate]of slabs){if(income<=limit){tax+=(income-prev)*rate;break;}tax+=(limit-prev)*rate;prev=limit;}}
  return Math.max(0,tax);
}
function calcTax(){
  var inc=+document.getElementById('taxInc').value,hra=+document.getElementById('taxHRA').value,c80=Math.min(+document.getElementById('tax80C').value,150000),hl=+document.getElementById('taxHL').value,nps=Math.min(+document.getElementById('taxNPS').value,50000),other=+document.getElementById('taxOther').value;
  var oldDed=50000+hra+c80+hl+nps+other,oldTaxable=Math.max(0,inc-oldDed),oldTax=calcSlabTax(oldTaxable,'old')*1.04;
  var newTaxable=Math.max(0,inc-75000),newTax=calcSlabTax(newTaxable,'new')*1.04;
  var better=oldTax<newTax?'old':'new',saving=Math.abs(oldTax-newTax);
  var oldBetter=better==='old', newBetter=better==='new';
  var oldLabel='Old Regime'+(oldBetter?' — RECOMMENDED':'');
  var newLabel='New Regime'+(newBetter?' — RECOMMENDED':'');
  var oldValCls=oldBetter?'green':'amber';
  var newValCls=newBetter?'green':'amber';
  var choiceLabel=oldBetter?'Old Regime':'New Regime';
  var oldCell='<div class="output-cell"><div class="output-label">'+oldLabel+'</div><div class="output-value '+oldValCls+'">'+fmtINR(oldTax)+'</div><div class="output-sub">Taxable: '+fmtC(oldTaxable)+'</div></div>';
  var newCell='<div class="output-cell"><div class="output-label">'+newLabel+'</div><div class="output-value '+newValCls+'">'+fmtINR(newTax)+'</div><div class="output-sub">Taxable: '+fmtC(newTaxable)+'</div></div>';
  document.getElementById('taxResult').innerHTML='<div class="output-grid">'+oldCell+newCell+'</div><div class="verdict good">Choose <strong>'+choiceLabel+'</strong> — saves '+fmtINR(saving)+'/year</div>';
  // summary bar
  const sbTR=document.getElementById('sb-taxRegime');if(sbTR){sbTR.classList.remove('num-animated');void sbTR.offsetWidth;sbTR.classList.add('num-animated');sbTR.textContent=choiceLabel;}
  animateNum(document.getElementById('sb-taxSaving'),saving,fmtINR);
  animateNum(document.getElementById('sb-taxOld'),oldTax,fmtINR);
  animateNum(document.getElementById('sb-taxNew'),newTax,fmtINR);
  const sbTA=document.getElementById('sb-taxAction');if(sbTA)sbTA.textContent='Saves '+fmtINR(saving)+' vs the other regime';
}

// ════════ CG ════════
function calcCG(){
  const type=document.getElementById('cgType').value,buy=+document.getElementById('cgBuy').value,sell=+document.getElementById('cgSell').value,months=+document.getElementById('cgMonths').value,bracket=+document.getElementById('cgBracket').value/100;
  const profit=sell-buy,ltThresh={equity:12,mf:12,debt:36,property:24,gold:36},isLT=months>=ltThresh[type];
  let taxAmt,label;
  if(type==='equity'||type==='mf'){if(isLT){taxAmt=Math.max(0,profit-100000)*.10;label='LTCG @ 10% (₹1L exempt)';}else{taxAmt=profit*.15;label='STCG @ 15%';}}
  else if(type==='debt'){taxAmt=profit*bracket;label='Added to income @ '+(bracket*100).toFixed(0)+'%';}
  else{if(isLT){taxAmt=profit*.20;label='LTCG @ 20%';}else{taxAmt=profit*bracket;label='STCG @ '+(bracket*100).toFixed(0)+'%';}}
  const net=profit-taxAmt;
  document.getElementById('cgResult').innerHTML='<div class="output-grid"><div class="output-cell"><div class="output-label">Gross Profit</div><div class="output-value green">'+fmtINR(profit)+'</div></div><div class="output-cell"><div class="output-label">Tax ('+label+')</div><div class="output-value red">-'+fmtINR(taxAmt)+'</div></div><div class="output-cell"><div class="output-label">Net After Tax</div><div class="output-value green">'+fmtINR(net)+'</div></div><div class="output-cell"><div class="output-label">Effective Return</div><div class="output-value blue">'+((net/buy)*100).toFixed(1)+'%</div></div></div><div class="verdict '+(isLT?'good':'neutral')+'">'+(isLT?'Long-term holding ('+months+'mo) — lower tax rate applies.':'Short-term — hold until '+ltThresh[type]+' months for lower tax rate.')+'</div>';
}

// ════════ HEALTH ════════
function calcHealth(){
  const inc=+document.getElementById('hInc').value,exp=+document.getElementById('hExp').value,emi=+document.getElementById('hEMI').value,save=+document.getElementById('hSave').value,ef=+document.getElementById('hEF').value,debt=+document.getElementById('hDebt').value,invest=+document.getElementById('hInvest').value,insure=+document.getElementById('hInsure').value,age=+document.getElementById('hAge').value;
  const savingsRate=save/inc,emiRatio=emi/inc,insureCover=insure*10000000/(inc*12*10);
  const scores={'Savings Rate':{val:Math.min(100,savingsRate*400),ideal:'>25%',yours:(savingsRate*100).toFixed(1)+'%',tip:savingsRate>=.25?'Excellent':'Aim for 25%+'},
    'Emergency Fund':{val:Math.min(100,(ef/6)*100),ideal:'6 months',yours:ef+' months',tip:ef>=6?'Well protected':'Need '+(6-ef)+' more months'},
    'EMI Burden':{val:Math.max(0,100-emiRatio*400),ideal:'<30%',yours:(emiRatio*100).toFixed(1)+'%',tip:emiRatio<=.3?'Manageable':'High — consider prepayment'},
    'Insurance':{val:Math.min(100,insureCover*80),ideal:'10x salary',yours:insure+'Cr',tip:insureCover>=1?'Adequate':'Increase life insurance'},
    'Investments':{val:Math.min(100,(invest/(inc*12*age*.1))*100),ideal:'Age-based',yours:fmtC(invest),tip:'Keep investing'},
    'Surplus':{val:Math.max(0,Math.min(100,((inc-exp-emi)/inc)*300)),ideal:'>20%',yours:fmtC(inc-exp-emi)+'/mo',tip:(inc-exp-emi)/inc>=.2?'Good surplus':'Expenses too high'}};
  const totalScore=Math.round(Object.values(scores).reduce((s,v)=>s+v.val,0)/Object.keys(scores).length);
  const grade=totalScore>=80?{g:'A+',c:'var(--green)',l:'Outstanding'}:totalScore>=70?{g:'A',c:'var(--green)',l:'Excellent'}:totalScore>=60?{g:'B',c:'var(--amber)',l:'Good'}:totalScore>=50?{g:'C',c:'var(--amber)',l:'Needs Work'}:{g:'D',c:'var(--red)',l:'Critical'};
  let barsHtml='';
  Object.entries(scores).forEach(([name,s])=>{const color=s.val>=70?'var(--green)':s.val>=50?'var(--amber)':'var(--red)';barsHtml+=`<div class="progress-row"><div class="progress-header"><span>${name}</span><span style="color:${color};font-family:var(--mono)">${Math.round(s.val)}/100</span></div><div class="progress-bar"><div class="progress-fill" style="width:${s.val}%;background:${color}"></div></div><div class="progress-note">${s.tip} · ${s.ideal} · ${s.yours}</div></div>`;});
  document.getElementById('healthResult').innerHTML=`<div style="display:flex;align-items:center;gap:2rem;margin-bottom:1.5rem;flex-wrap:wrap"><div style="text-align:center"><div style="font-size:3.5rem;font-weight:900;font-family:var(--mono);color:${grade.c}">${grade.g}</div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em">${grade.l}</div></div><div style="flex:1;min-width:200px"><div class="output-label">Overall Health Score</div><div style="font-size:2rem;font-weight:800;font-family:var(--mono);color:var(--text)">${totalScore}<span style="font-size:1rem;color:var(--muted)">/100</span></div><div class="progress-bar" style="height:5px;margin-top:.5rem"><div class="progress-fill" style="width:${totalScore}%;background:${grade.c}"></div></div></div></div>${barsHtml}`;
}

// ════════ SCENARIO ENGINE ════════
function calcScenario(){
  const sip=+document.getElementById('scSIP').value||15000;
  const yrs=+document.getElementById('scYrs').value||20;
  const base=+document.getElementById('scBase').value||12;
  const stepUp=+document.getElementById('scStepUp').value||10;
  const inflShock=+document.getElementById('scInflation').value||8;
  const crashYr=+document.getElementById('scCrashYr').value||5;
  const crashPct=+document.getElementById('scCrashPct').value||40;
  function flatSIP(s,r,n){const rm=r/100/12;return s*(Math.pow(1+rm,n)-1)/rm*(1+rm);}
  const A=flatSIP(sip,base,yrs*12);
  let corpStepUp=0;let curSIP=sip;
  for(let y=0;y<yrs;y++){const fvThisYear=flatSIP(curSIP,base,12);corpStepUp=(corpStepUp+fvThisYear)*Math.pow(1+base/100,(yrs-y-1));curSIP*=(1+stepUp/100);}
  const B=corpStepUp;
  const adjReturn=Math.max(1,base-inflShock+3);
  const C=flatSIP(sip,adjReturn,yrs*12);
  let corpD=0;
  for(let y=0;y<yrs;y++){corpD+=flatSIP(sip,base,12);corpD*=Math.pow(1+base/100,1);if(y+1===crashYr)corpD*=(1-crashPct/100);}
  const D=Math.max(0,corpD);
  const earlyYrs=Math.max(1,yrs-5);
  const E=flatSIP(sip,base,earlyYrs*12);
  const rows=[
    {label:'Base — flat SIP @ '+base+'%',val:A,cls:'green',note:'Your baseline projection'},
    {label:'Step-Up — +'+stepUp+'% SIP per year',val:B,cls:'green',note:B>A?'+'+(((B-A)/A)*100).toFixed(0)+'% more than baseline':'Marginal gain'},
    {label:'Inflation shock — returns drop to '+adjReturn.toFixed(1)+'%',val:C,cls:C>A*0.7?'amber':'red',note:(((C-A)/A)*100).toFixed(0)+'% vs baseline'},
    {label:'Market crash — '+crashPct+'% drop in yr '+crashYr,val:D,cls:D>A*0.6?'amber':'red',note:'Crash yr '+crashYr+', then full recovery'},
    {label:'Retire 5 yrs early — '+earlyYrs+' yrs of SIP',val:E,cls:'amber',note:(yrs-earlyYrs)+' fewer years of compounding'},
  ];
  const best=Math.max(...rows.map(r=>r.val));
  document.getElementById('scenarioResult').innerHTML=
    '<div class="result-box"><div class="result-title">Scenario comparison — ₹'+Math.round(sip/1000)+'K/mo SIP · '+yrs+' yr horizon</div></div>'+
    '<div class="scenario-list" style="margin-top:.5rem">'+
    rows.map(r=>'<div class="scenario-row"><div style="display:flex;flex-direction:column;gap:2px"><span class="scenario-label" style="font-weight:600">'+r.label+'</span><span style="font-size:10px;color:var(--muted)">'+r.note+'</span></div><span class="scenario-val '+r.cls+'">'+fmtC(r.val)+(r.val===best?' ★':'')+'</span></div>').join('')+
    '</div>'+
    '<div class="verdict '+(B>A*1.5?'good':'neutral')+'" style="margin-top:.75rem"><strong>Key insight:</strong> '+
    (B>A*1.5?'A '+stepUp+'% annual step-up grows your corpus '+((B/A-1)*100).toFixed(0)+'% more than flat SIP over '+yrs+' years — the single biggest lever.':
     'Even a moderate step-up significantly outperforms flat SIP over time. Crashes recover — skipping investments does not.')+'</div>';
}

// ════════ PROFILE ════════
let userProfile={};
function saveProfile(){
  userProfile={salary:parseFloat(document.getElementById('pSalary').value)||0,expenses:parseFloat(document.getElementById('pExpenses').value)||0,emi:parseFloat(document.getElementById('pEMI').value)||0,sip:parseFloat(document.getElementById('pSIP').value)||0,age:parseInt(document.getElementById('pAge').value)||0,retireAge:parseInt(document.getElementById('pRetireAge').value)||60,emergency:parseFloat(document.getElementById('pEmergency').value)||0,savings:parseFloat(document.getElementById('pSavings').value)||0};
  try{localStorage.setItem('fv_pro_profile',JSON.stringify(userProfile));}catch(e){}
}
function loadProfile(){
  try{const s=localStorage.getItem('fv_pro_profile');if(s){userProfile=JSON.parse(s);['pSalary','pExpenses','pEMI','pSIP','pAge','pRetireAge','pEmergency','pSavings'].forEach(id=>{const field=id.replace('p','').toLowerCase();const map={psalary:'salary',pexpenses:'expenses',pemi:'emi',psip:'sip',page:'age',pretireage:'retireAge',pemergency:'emergency',psavings:'savings'};document.getElementById(id).value=userProfile[map[id.toLowerCase()]]||'';});}}catch(e){}
}
function applyProfile(){
  saveProfile();
  document.getElementById('profileMsg').style.display='block';
  setTimeout(()=>document.getElementById('profileMsg').style.display='none',4000);
}

// ════════ DASHBOARD ════════
function renderDashboard(){
  const np=document.getElementById('dashNoProfile');
  const dc=document.getElementById('dashContent');
  if(!userProfile.salary){np.style.display='block';dc.style.display='none';return;}
  np.style.display='none';dc.style.display='block';
  const{salary,expenses,emi,sip,age,retireAge,emergency,savings}=userProfile;
  const leftover=salary-expenses-emi-sip;
  const investRate=salary>0?(sip/salary)*100:0;
  const emergencyMonths=expenses>0?emergency/expenses:0;
  const debtToIncome=salary>0?(emi/salary)*100:0;
  const kpis=[
    {label:'Emergency Fund',val:fmtC(emergency),tag:emergencyMonths>=6?['ok',emergencyMonths.toFixed(1)+' months']:emergencyMonths>=3?['warn',emergencyMonths.toFixed(1)+' months']:['bad',emergencyMonths.toFixed(1)+' months']},
    {label:'Monthly SIP',val:fmtC(sip),tag:investRate>=20?['ok',investRate.toFixed(1)+'% income']:investRate>=10?['warn',investRate.toFixed(1)+'% income']:['bad',investRate.toFixed(1)+'% income']},
    {label:'EMI Burden',val:fmtC(emi),tag:debtToIncome<=30?['ok',debtToIncome.toFixed(1)+'% income']:debtToIncome<=50?['warn',debtToIncome.toFixed(1)+'% income']:['bad',debtToIncome.toFixed(1)+'% income']},
    {label:'Monthly Surplus',val:fmtC(Math.max(0,leftover)),tag:leftover>=salary*.1?['ok','Available']:leftover>=0?['warn','Tight']:['bad','Deficit']},
    {label:'Retire In',val:Math.max(0,retireAge-age)+' yrs',tag:retireAge-age>=15?['ok','On track']:retireAge-age>=8?['warn','Urgent']:['bad','Critical']},
    {label:'Total Savings',val:fmtC(savings),tag:savings>=salary*12?['ok','Strong']:savings>=salary*6?['warn','Building']:['bad','Low']}
  ];
  document.getElementById('dashKPIs').innerHTML=kpis.map(k=>'<div class="dash-kpi"><div class="dash-kpi-label">'+k.label+'</div><div class="dash-kpi-val">'+k.val+'</div><span class="dash-tag tag-'+k.tag[0]+'">'+k.tag[1]+'</span></div>').join('');
  // Alerts
  const alerts=[];
  if(emergencyMonths<3)alerts.push(['critical','Emergency fund critical — build 6 months expenses first']);
  else if(emergencyMonths<6)alerts.push(['warning','Emergency fund at '+emergencyMonths.toFixed(1)+' months — target is 6 months']);
  if(debtToIncome>50)alerts.push(['critical','EMI burden very high ('+debtToIncome.toFixed(1)+'%) — consider prepaying loans']);
  if(investRate<10)alerts.push(['warning','Investing only '+investRate.toFixed(1)+'% of income — increase SIP to 20%']);
  if(leftover<0)alerts.push(['critical','Spending exceeds income — review budget immediately']);
  if(!alerts.length)alerts.push(['ok','No critical alerts — finances look healthy']);
  document.getElementById('dashAlerts').innerHTML=alerts.map(([cls,msg])=>'<div class="alert-item '+cls+'"><span class="alert-dot a-'+(cls==='ok'?'green':cls==='warning'?'amber':'red')+'"></span>'+msg+'</div>').join('');
  // Recs
  const recs=[];
  if(emergencyMonths<6)recs.push('Build emergency fund to '+fmtC(expenses*6)+' ('+fmtC(Math.max(0,expenses*6-emergency))+' more needed)');
  if(debtToIncome>40)recs.push('Reduce EMI to below 30% of income. Target: '+fmtC(salary*.3)+'/mo');
  if(investRate<20)recs.push('Increase SIP from '+fmtC(sip)+' to '+fmtC(salary*.20)+' (20% of income)');
  if(leftover>salary*.1)recs.push('Deploy '+fmtC(leftover)+' surplus — add to SIP or FD');
  recs.push('Max out PPF (₹1,50,000/yr) + ELSS to reduce tax');
  recs.push('Health cover ≥ ₹10L/person, term life ≥ 10× annual income');
  document.getElementById('dashRecs').innerHTML=recs.map((r,i)=>'<li class="rec-item-pro"><span class="rec-num-pro">'+(i+1)+'</span><span>'+r+'</span></li>').join('');
  // Scenarios
  const yrs=Math.max(1,retireAge-age),proj=sip*(Math.pow(1+.12/12,yrs*12)-1)/(.12/12)*(1+.12/12),fireNum=expenses*12*25;
  document.getElementById('dashScenarios').innerHTML='<div class="scenario-list"><div class="scenario-row"><span class="scenario-label">SIP '+fmtC(sip)+'/mo · '+yrs+' yrs @12%</span><span class="scenario-val green">'+fmtC(proj)+'</span></div><div class="scenario-row"><span class="scenario-label">FIRE Number (25× expenses)</span><span class="scenario-val '+(proj>=fireNum?'green':'amber')+'">'+fmtC(fireNum)+'</span></div><div class="scenario-row" style="background:rgba('+(proj>=fireNum?'0,192,127':'232,168,37')+',.05)"><span class="scenario-label">'+(proj>=fireNum?'Surplus over FIRE':'Gap to FIRE')+'</span><span class="scenario-val '+(proj>=fireNum?'green':'amber')+'">'+fmtC(Math.abs(proj-fireNum))+'</span></div><div class="scenario-row"><span class="scenario-label">If market crashes -30%</span><span class="scenario-val amber">'+fmtC((savings||0)*.7)+'</span></div></div>';
}

// ════════ INIT ════════
document.addEventListener('DOMContentLoaded',()=>{
  calcSIP();calcLS();calcGoal();calcROI();calcScenario();
  calcFIRE();calcCorpus();calcWithdraw();
  calcCrash();calcStopLoss();calcPortfolio();
  calcEMI();calcPrepay();calcTax();calcCG();calcHealth();
  loadProfile();
  // Activate first signal
  const first=document.querySelector('.js-sig-item');
  if(first){first.classList.add('active');const key=first.dataset.asset;const det=document.getElementById('det-'+key);if(det)det.classList.add('active');}
});
</script>
</div><!-- /fv-app -->
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# BUILD HTML
# ─────────────────────────────────────────────────────────────

# Asset config: (label shown in sidebar, signal key, signal object key)
ASSET_CONFIG = [
    ("Gold / PAXG",          "gold",     "gold"),
    ("Silver (SI=F)",         "silver",   "silver"),
    ("Crypto · BTC / ETH",   "crypto",   "crypto"),
    ("Indian Equities",       "stocks",   "stocks"),
    ("US Markets · S&amp;P", "usstocks", "usstocks"),
    ("Real Estate",           "property", "property"),
    ("Fixed Deposits",        "fd",       "fd"),
]


def build_html(crypto_data, stocks_data, signals, ticker_html, updated_at, market_status=None):
    if market_status is None:
        market_status = get_market_status()

    # ── Market status strip ──────────────────────────────────
    nse_cls  = "open"  if market_status["nse"]  == "OPEN"  else "closed"
    nyse_cls = "open"  if market_status["nyse"] == "OPEN"  else "closed"
    ms_html = (
        f'<span class="market-pill">'
        f'<span class="m-dot dot-{market_status["nse_dot"]}"></span>'
        f'<span class="m-exch">NSE</span>'
        f'<span class="m-status {nse_cls}">{market_status["nse"]}</span>'
        f'<span class="m-time">{market_status["nse_time"]}</span>'
        f'</span>'
        f'<span class="market-pill">'
        f'<span class="m-dot dot-{market_status["nyse_dot"]}"></span>'
        f'<span class="m-exch">NYSE</span>'
        f'<span class="m-status {nyse_cls}">{market_status["nyse"]}</span>'
        f'<span class="m-time">{market_status["nyse_time"]}</span>'
        f'</span>'
    )

    # ── Sidebar signal items ─────────────────────────────────
    sidebar_html = ""
    for label, key, sig_key in ASSET_CONFIG:
        sig = signals[sig_key]
        badge_cls = {"buy":"badge-buy","hold":"badge-hold","wait":"badge-wait"}.get(sig["cls"],"badge-hold")
        sidebar_html += (
            f'<div class="sidebar-item js-sig-item" data-asset="{key}" onclick="selectSignal(\'{key}\')">'
            f'<div class="sidebar-item-left">'
            f'<span class="sidebar-item-name">{label}</span>'
            f'<span class="sidebar-item-desc">{sig.get("source","")[:45]}</span>'
            f'</div>'
            f'<span class="signal-badge {badge_cls}">{sig["signal"]}</span>'
            f'</div>'
        )

    # ── Signal detail panels ─────────────────────────────────
    details_html = ""
    asset_names = {
        "gold":     "Gold / PAXG",
        "silver":   "Silver (SI=F futures)",
        "crypto":   "Crypto · BTC / ETH / DeFi",
        "stocks":   "Indian Equities · Nifty 50",
        "usstocks": "US Markets · S&P 500",
        "property": "Real Estate",
        "fd":       "Fixed Deposits",
    }
    for _, key, sig_key in ASSET_CONFIG:
        sig = signals[sig_key]
        badge_cls = {"buy":"badge-buy","hold":"badge-hold","wait":"badge-wait"}.get(sig["cls"],"badge-hold")
        metrics_html = ""
        for k, v in sig.get("metrics", {}).items():
            metrics_html += (
                f'<div class="detail-metric">'
                f'<div class="detail-metric-val">{v}</div>'
                f'<div class="detail-metric-label">{k}</div>'
                f'</div>'
            )
        marker_cls = {"buy":"rm-buy","hold":"rm-hold","wait":"rm-wait"}.get(sig["cls"],"rm-hold")
        reasons_html = ""
        for r in sig.get("reasons", []):
            reasons_html += (
                f'<div class="reason-line">'
                f'<span class="reason-marker {marker_cls}">›</span>'
                f'<span>{r}</span>'
                f'</div>'
            )
        context = sig.get("context","")
        context_html = (
            f'<div class="context-note"><strong style="color:var(--blue)">Pro Tip:</strong> {context}</div>'
            if context else ""
        )
        # Fear & Greed sparkline (crypto only)
        spark_html = ""
        fh = sig.get("fear_history", [])
        if fh and len(fh) >= 2:
            mn, mx = min(fh), max(fh)
            rng = mx - mn or 1
            n_pts = len(fh)
            pts = []
            for i, v in enumerate(fh):
                x = round(i / (n_pts - 1) * 280, 1)
                y = round(40 - (v - mn) / rng * 36 - 2, 1)
                pts.append(f"{x},{y}")
            last = fh[-1]
            sc = "#00c07f" if last <= 40 else "#e84040" if last >= 60 else "#e8a825"
            pts_str = " ".join(pts)
            spark_html = (
                f'<div class="sparkline-wrap">'
                f'<div class="spark-label">Fear &amp; Greed — 8-day trend</div>'
                f'<svg viewBox="0 0 280 44" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:44px;margin-top:4px;">'
                f'<polyline points="{pts_str}" fill="none" stroke="{sc}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
                f'</svg>'
                f'</div>'
            )

        details_html += (
            f'<div class="signal-detail-panel" id="det-{key}">'
            f'<div class="detail-header">'
            f'<div class="detail-title">{asset_names.get(key, key)}</div>'
            f'<span class="signal-badge {badge_cls} detail-signal-large">{sig["signal"]}</span>'
            f'</div>'
            f'<div class="detail-metrics">{metrics_html}</div>'
            f'{spark_html}'
            f'<div class="reasons-panel">'
            f'<div class="reasons-label">Signal Rationale</div>'
            f'{reasons_html}'
            f'</div>'
            f'{context_html}'
            f'<div class="disclaimer">Data source: {sig.get("source","")} · Educational only — not investment advice. Past performance is not indicative of future results.</div>'
            f'</div>'
        )

    # ── Fear & Greed KPI ────────────────────────────────────
    fs = crypto_data.get("fear_score")
    fl = crypto_data.get("fear_label", "—")
    fg_kpi_val  = f"{fs}" if fs is not None else "N/A"
    fg_kpi_sub  = fl if fl else "—"

    # ── Assemble ─────────────────────────────────────────────
    html = _HTML_TEMPLATE
    html = html.replace("__TICKER_HTML__",    ticker_html)
    html = html.replace("__UPDATED_AT__",     updated_at)
    html = html.replace("__MARKET_STATUS__",  ms_html)
    html = html.replace("__REPO_RATE__",      str(REPO_RATE))
    html = html.replace("__SIDEBAR_ITEMS__",  sidebar_html)
    html = html.replace("__SIGNAL_DETAILS__", details_html)
    # ── Inject Firebase frontend config from environment variables ──
    html = html.replace("__FIREBASE_API_KEY__",            os.environ.get("FIREBASE_API_KEY", ""))
    html = html.replace("__FIREBASE_AUTH_DOMAIN__",        os.environ.get("FIREBASE_AUTH_DOMAIN", ""))
    html = html.replace("__FIREBASE_PROJECT_ID__",         os.environ.get("FIREBASE_PROJECT_ID", ""))
    html = html.replace("__FIREBASE_STORAGE_BUCKET__",     os.environ.get("FIREBASE_STORAGE_BUCKET", ""))
    html = html.replace("__FIREBASE_MESSAGING_SENDER_ID__",os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""))
    html = html.replace("__FIREBASE_APP_ID__",             os.environ.get("FIREBASE_APP_ID", ""))
    # Inline KPI values via JS init snippet
    fg_js = (
        f"document.getElementById('kpiFG').textContent='{fg_kpi_val}';"
        f"document.getElementById('kpiFGLabel').textContent='{fg_kpi_sub}';"
    )
    html = html.replace(
        "// ════════ INIT ════════",
        f"// ════════ INIT ════════\n  {fg_js}"
    )
    return html


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="FinVault Pro Static Site Generator")
    parser.add_argument("--dry", action="store_true", help="Print data, don't write file")
    args = parser.parse_args()

    print("🔄 FinVault Pro Site Generator")
    print(f"   Output: {OUTPUT_FILE}\n")

    print("📡 Fetching crypto & metals data...")
    try:
        crypto_data = fetch_crypto()
    except Exception as e:
        print(f"  ⚠ Live crypto fetch failed: {e}")
        crypto_data, _ = load_lkg()

    print("📈 Fetching stock data (Nifty, S&P 500, NASDAQ)...")
    try:
        stocks_data = fetch_stocks()
    except Exception as e:
        print(f"  ⚠ Live stocks fetch failed: {e}")
        _, stocks_data = load_lkg()

    if crypto_data or stocks_data:
        save_lkg(crypto_data or {}, stocks_data or {})

    print("🧮 Computing signals...")
    signals = {
        "gold":     sig_gold(crypto_data),
        "silver":   sig_silver(crypto_data, stocks_data),
        "crypto":   sig_crypto(crypto_data),
        "stocks":   sig_stocks(stocks_data),
        "usstocks": sig_usstocks(stocks_data, crypto_data),
        "property": sig_property(),
        "fd":       sig_fd(),
    }

    updated_at   = datetime.datetime.utcnow().strftime("%d %b %Y %H:%M")
    market_status = get_market_status()

    if args.dry:
        print("\n📊 Signals:")
        for k, v in signals.items():
            print(f"  {k:12s} → {v['signal']}")
        print(f"\n⏱  Updated: {updated_at} UTC")
        print(f"   NSE: {market_status['nse']}  |  NYSE: {market_status['nyse']}")
        return

    print("🏗️  Building HTML...")
    ticker_html = render_ticker(crypto_data, stocks_data)
    html = build_html(crypto_data, stocks_data, signals, ticker_html, updated_at, market_status)

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
