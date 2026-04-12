from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import re
import asyncio
import gc
import pytz
from datetime import datetime

app = FastAPI(title="PSX Live Data Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

import random

_client = None

# A list of realistic User-Agents to rotate
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (AppleWebKit/537.36; Chrome/123.0.0.0; Mobile) Safari/537.36"
]

async def get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=True,
            http2=True, # Use HTTP/2 for better stealth
            # Limits: Disable keep-alive to avoid connection-based bot detection
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=0),
        )
    return _client

def get_headers():
    ua = random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close", # Explicitly request connection closure
        "Referer": "https://dps.psx.com.pk/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }

_sem = asyncio.Semaphore(2)


def get_pk_time():
    """Returns formatted Pakistan time (GMT+5)."""
    pk_tz = pytz.timezone("Asia/Karachi")
    return datetime.now(pk_tz).strftime("%H:%M:%S")


async def fetch_kse100() -> dict:
    time_str = get_pk_time()
    try:
        client = await get_client()
        resp = await client.get("https://dps.psx.com.pk/", headers=get_headers())
        html = resp.text

        def find(pattern, default="N/A"):
            m = re.search(pattern, html, re.DOTALL)
            return m.group(1).strip() if m else default

        value  = find(r'KSE100\s*\n\s*([\d,]+\.?\d*)')
        change = find(r'KSE100\s*\n\s*[\d,]+\.?\d*\s*\n\s*([-\d.]+)')
        pct    = find(r'KSE100\s*\n\s*[\d,]+\.?\d*\s*\n\s*[-\d.]+\s*\n\s*\(([-\d.]+%)\)')

        del html
        gc.collect()

        return {
            "index":  "KSE100",
            "value":  value,
            "change": change,
            "pct":    pct,
            "time":   time_str,
            "status": "ok" if value != "N/A" else "parse_error",
        }
    except Exception as e:
        return {"index": "KSE100", "status": "error",
                "error": str(e), "time": time_str}


async def fetch_psx_symbol(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    time_str = get_pk_time()

    html = None
    last_err = None
    
    # Retry loop: Try up to 3 times to mitigate intermittent disconnects
    for attempt in range(3):
        # Add small random jitter (0.3s to 0.8s) to break request patterns
        await asyncio.sleep(random.uniform(0.3, 0.8))
        
        async with _sem:
            try:
                client = await get_client()
                resp = await client.get(
                    f"https://dps.psx.com.pk/company/{symbol}",
                    headers=get_headers()
                )
                html = resp.text
                # Simple validation that we got a real page
                if html and ("quote__close" in html or "stats_label" in html):
                    break # Success
                elif attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                last_err = str(e)
                # If disconnected, wait longer and try again
                await asyncio.sleep(2 * (attempt + 1))
                
    if not html:
        return {"symbol": symbol, "status": "error",
                "error": last_err or "Max retries reached", "time": time_str}

    def find(pattern, default="N/A"):
        m = re.search(pattern, html, re.DOTALL)
        return m.group(1).strip() if m else default

    def clean_num(val):
        """Removes commas and extra spaces from numeric strings."""
        if val == "N/A": return val
        return val.replace(",", "").strip()

    # --- Raw Scraped Data ---
    price_raw  = find(r'class="quote__close"[^>]*>Rs\.([\d,]+\.?\d*)<')
    change_raw = find(r'class="change__value"[^>]*>([-\d.]+)<')
    pct_raw    = find(r'class="change__percent"[^>]*>\s*\(([-\d.]+%)\)')
    
    def find_stat(label):
        return find(
            r'class="stats_label"[^>]*>\s*' + label +
            r'\s*</div>\s*<div class="stats_value"[^>]*>([\d,]+\.?\d*)<'
        )

    open_p_raw = find_stat("Open")
    high_raw   = find_stat("High")
    low_raw    = find_stat("Low")
    vol_raw    = find_stat("Volume")
    ldcp_raw   = find_stat("LDCP")

    # --- Clean Data & Type Conversion ---
    def to_num(val, type_func=float):
        cleaned = clean_num(val)
        if cleaned == "N/A": return "N/A"
        try:
            return type_func(cleaned)
        except:
            return cleaned # Fallback to cleaned string if float conversion fails

    price  = to_num(price_raw)
    change_val = to_num(change_raw)
    pct_val    = to_num(pct_raw.replace("%", "")) # Remove % for numeric conversion
    open_p = to_num(open_p_raw)
    high   = to_num(high_raw)
    low    = to_num(low_raw)
    volume = to_num(vol_raw, int) # Volume is usually an integer
    ldcp   = to_num(ldcp_raw)

    # --- Robust Direction Calculation ---
    direction = "even"
    if isinstance(price, (int, float)) and isinstance(ldcp, (int, float)):
        if price > ldcp:
            direction = "up"
        elif price < ldcp:
            direction = "down"

    # --- Apply Signs and Formatting for Strings (if needed) ---
    # We'll return numeric values for price, volume, etc., 
    # but change and pct might be better as signed strings for the dashboard display.
    # Actually, let's keep them as numeric types so Google Sheets can handle the sorting.
    # The prefix can be added in Sheets via custom formatting, but for simplicity
    # we can send them as signed numbers.
    
    change_signed = change_val
    if isinstance(change_val, (int, float)):
        if direction == "down":
            change_signed = -abs(change_val)
        else:
            change_signed = abs(change_val)
            
    pct_signed = pct_val
    if isinstance(pct_val, (int, float)):
        if direction == "down":
            pct_signed = -abs(pct_val)
        else:
            pct_signed = abs(pct_val)
        # Add back the % sign if returning as string, but let's return it as number/100 or just the percent number.
        # Given the user's dashboard example, they likely want the string "+9.13%".
        # But if we want sorting, we need numbers.
        # Let's return both or just clean strings that Sheets can parse as numbers.
        # Actually, if I return `-12.27` as a number, Sheets is happy.
        pass

    del html
    gc.collect()

    return {
        "symbol": symbol,
        "price":  price,
        "change": change_signed,
        "pct":    pct_signed,
        "volume": volume,
        "open":   open_p,
        "high":   high,
        "low":    low,
        "ldcp":   ldcp,
        "time":   time_str,
        "status": "ok" if price != "N/A" else "parse_error",
    }


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"message": "PSX Live Data Server is running"}


@app.get("/kse100")
async def kse100():
    return await fetch_kse100()


@app.get("/quote/{symbol}")
async def get_quote(symbol: str):
    try:
        return await fetch_psx_symbol(symbol)
    except Exception as e:
        return {"symbol": symbol.upper(), "status": "error",
                "error": str(e), "time": datetime.now().strftime("%H:%M:%S")}


@app.get("/quotes")
async def get_quotes(symbols: str):
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    output = []
    for i in range(0, len(sym_list), 3):
        batch = sym_list[i:i+3]
        results = await asyncio.gather(
            *[fetch_psx_symbol(s) for s in batch],
            return_exceptions=True
        )
        for sym, r in zip(batch, results):
            if isinstance(r, Exception):
                output.append({"symbol": sym, "status": "error",
                               "error": str(r),
                               "time": get_pk_time()})
            else:
                output.append(r)
        if i + 3 < len(sym_list):
            await asyncio.sleep(0.5)
    gc.collect()
    return {"data": output, "count": len(output)}


@app.get("/debug/{symbol}")
async def debug(symbol: str):
    symbol = symbol.strip().upper()
    try:
        resp = await client.get(
            f"https://dps.psx.com.pk/company/{symbol}", headers=get_headers())
        text = resp.text
        idx = text.find("quote__change")
        snippet = text[max(0, idx-50):idx+400] if idx != -1 else text[2000:3000]
        del text
        return {"snippet": snippet, "http_status": resp.status_code}
    except Exception as e:
        return {"error": str(e)}