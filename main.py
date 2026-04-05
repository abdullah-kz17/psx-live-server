from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import re
from datetime import datetime

app = FastAPI(title="PSX Live Data Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def fetch_psx_symbol(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    url = f"https://dps.psx.com.pk/company/{symbol}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://dps.psx.com.pk/",
    }

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await client.get("https://dps.psx.com.pk/", headers=headers)
        resp = await client.get(url, headers=headers)
        html = resp.text

    def find(pattern, default="N/A"):
        m = re.search(pattern, html, re.DOTALL)
        if not m:
            return default
        return m.group(1).strip()

    # Price: <div class="quote__close">Rs.265.21</div>
    price = find(r'class="quote__close"[^>]*>Rs\.([\d,]+\.?\d*)<')

    # Change value: <div class="change__value">0.65</div>
    change = find(r'class="change__value"[^>]*>([-\d.]+)<')

    # Direction: icon-up-dir or icon-down-dir
    direction = find(r'class="icon-(up|down)-dir"')
    if change != "N/A":
        change = ("-" if direction == "down" else "+") + change

    # % Change: <div class="change__percent">  (0.25%)</div>
    pct = find(r'class="change__percent"[^>]*>\s*\(([-\d.]+%)\)')

    # Stats: <div class="stats_label">Open</div><div class="stats_value">260.10</div>
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
        "time":   datetime.now().strftime("%H:%M:%S"),
        "status": "ok" if price != "N/A" else "parse_error",
    }


@app.get("/")
def root():
    return {"message": "PSX Live Data Server is running"}


@app.get("/debug/{symbol}")
async def debug(symbol: str):
    symbol = symbol.strip().upper()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dps.psx.com.pk/",
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await client.get("https://dps.psx.com.pk/", headers=headers)
        resp = await client.get(f"https://dps.psx.com.pk/company/{symbol}", headers=headers)
    text = resp.text
    idx = text.find("stats_label")
    snippet = text[max(0, idx-100):idx+800] if idx != -1 else text[2000:3500]
    return {"snippet": snippet, "http_status": resp.status_code}


@app.get("/quote/{symbol}")
async def get_quote(symbol: str):
    try:
        return await fetch_psx_symbol(symbol)
    except Exception as e:
        return {"symbol": symbol.upper(), "status": "error", "error": str(e),
                "time": datetime.now().strftime("%H:%M:%S")}


@app.get("/quotes")
async def get_quotes(symbols: str):
    import asyncio
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    results  = await asyncio.gather(*[fetch_psx_symbol(s) for s in sym_list],
                                     return_exceptions=True)
    output = []
    for sym, r in zip(sym_list, results):
        if isinstance(r, Exception):
            output.append({"symbol": sym, "status": "error", "error": str(r),
                           "time": datetime.now().strftime("%H:%M:%S")})
        else:
            output.append(r)
    return {"data": output, "count": len(output)}