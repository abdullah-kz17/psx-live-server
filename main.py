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
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://dps.psx.com.pk/",
}

_sem = asyncio.Semaphore(2)


# ------------------------------------------------------------
#  Fetch single company
# ------------------------------------------------------------
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

    def find(pattern, default="N/A"):
        m = re.search(pattern, html, re.DOTALL)
        return m.group(1).strip() if m else default

    def find_stat(label):
        return find(
            r'class="stats_label"[^>]*>\s*' + label +
            r'\s*</div>\s*<div class="stats_value"[^>]*>([\d,]+\.?\d*)<'
        )

    price     = find(r'class="quote__close"[^>]*>Rs\.([\d,]+\.?\d*)<')
    change    = find(r'class="change__value"[^>]*>([-\d.]+)<')
    pct       = find(r'class="change__percent"[^>]*>\s*\(([-\d.]+%)\)')
    open_p    = find_stat("Open")
    high      = find_stat("High")
    low       = find_stat("Low")
    volume    = find_stat("Volume")
    ldcp      = find_stat("LDCP")

    del html
    gc.collect()

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


# ------------------------------------------------------------
#  KSE-100 INDEX
# ------------------------------------------------------------
@app.get("/kse100")
async def get_kse100():
    time_str = datetime.now().strftime("%H:%M:%S")

    try:
        client = await get_client()
        resp = await client.get(
            "https://dps.psx.com.pk/indices/KSE100",
            headers=HEADERS
        )
        html = resp.text

        def find(pattern, default="N/A"):
            m = re.search(pattern, html, re.DOTALL)
            return m.group(1).strip() if m else default

        value  = find(r'class="quote__close"[^>]*>([\d,]+\.?\d*)<')
        change = find(r'class="change__value"[^>]*>([-\d.]+)<')
        pct    = find(r'class="change__percent"[^>]*>\s*\(([-\d.]+%)\)')

        del html
        gc.collect()

        return {
            "index": "KSE-100",
            "value": value,
            "change": change,
            "pct": pct,
            "time": time_str,
            "status": "ok"
        }

    except Exception as e:
        return {"index": "KSE-100", "status": "error",
                "error": str(e), "time": time_str}


# ------------------------------------------------------------
#  Multiple quotes
# ------------------------------------------------------------
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
        for r in results:
            output.append(r)
        await asyncio.sleep(0.5)

    gc.collect()
    return {"data": output}