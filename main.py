from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import re
import asyncio
import gc
from datetime import datetime

app = FastAPI(title="PSX Live Data Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global single client — reuse instead of creating per request
_client = None

async def get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
        )
    return _client


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://dps.psx.com.pk/",
}

# Semaphore — max 2 PSX fetches at the same time to cap memory
_sem = asyncio.Semaphore(2)


async def fetch_psx_symbol(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    time_str = datetime.now().strftime("%H:%M:%S")

    async with _sem:
        try:
            client = await get_client()
            resp = await client.get(
                f"https://dps.psx.com.pk/company/{symbol}",
                headers=HEADERS
            )
            html = resp.text
        except Exception as e:
            return {"symbol": symbol, "status": "error",
                    "error": str(e), "time": time_str}

    # Parse immediately, then let html be garbage collected
    def find(pattern, default="N/A"):
        m = re.search(pattern, html, re.DOTALL)
        return m.group(1).strip() if m else default

    price     = find(r'class="quote__close"[^>]*>Rs\.([\d,]+\.?\d*)<')
    change    = find(r'class="change__value"[^>]*>([-\d.]+)<')
    direction = find(r'class="icon-(up|down)-dir"')
    pct       = find(r'class="change__percent"[^>]*>\s*\(([-\d.]+%)\)')

    def find_stat(label):
        return find(
            r'class="stats_label"[^>]*>\s*' + label +
            r'\s*</div>\s*<div class="stats_value"[^>]*>([\d,]+\.?\d*)<'
        )

    open_p = find_stat("Open")
    high   = find_stat("High")
    low    = find_stat("Low")
    volume = find_stat("Volume")
    ldcp   = find_stat("LDCP")

    # Explicitly delete html string to free memory now
    del html
    gc.collect()

    if change != "N/A":
        change = ("-" if direction == "down" else "+") + change

    return {
        "symbol": symbol,
        "price":  price,
        "change": change,
        "pct":    pct,
        "volume": volume,
        "open":   open_p,
        "high":   high,
        "low":    low,
        "ldcp":   ldcp,
        "time":   time_str,
        "status": "ok" if price != "N/A" else "parse_error",
    }


@app.get("/")
def root():
    return {"message": "PSX Live Data Server is running"}


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

    # Process in batches of 3 to keep memory low
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
                               "time": datetime.now().strftime("%H:%M:%S")})
            else:
                output.append(r)
        # Small pause between batches to avoid memory spike
        if i + 3 < len(sym_list):
            await asyncio.sleep(0.5)

    gc.collect()
    return {"data": output, "count": len(output)}


@app.get("/debug/{symbol}")
async def debug(symbol: str):
    symbol = symbol.strip().upper()
    try:
        client = await get_client()
        resp = await client.get(
            f"https://dps.psx.com.pk/company/{symbol}",
            headers=HEADERS
        )
        text = resp.text
        idx = text.find("stats_label")
        snippet = text[max(0, idx-100):idx+600] if idx != -1 else text[2000:3000]
        del text
        return {"snippet": snippet, "http_status": resp.status_code}
    except Exception as e:
        return {"error": str(e)}