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

# ---------------------------------------------------------------------------
# Helper — fetch one symbol from PSX website (real browser session simulation)
# ---------------------------------------------------------------------------
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
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://dps.psx.com.pk/",
    }

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # Step 1: Get session cookie from homepage first
        await client.get("https://dps.psx.com.pk/", headers=headers)

        # Step 2: Fetch the company page with session cookie
        resp = await client.get(url, headers=headers)
        html = resp.text

    # Parse the key values from HTML
    def extract(pattern, html, group=1):
        m = re.search(pattern, html, re.DOTALL)
        return m.group(group).strip() if m else None

    # PSX company page has structured data we can parse
    price     = extract(r'"currentPrice"\s*:\s*"?([\d.]+)"?', html)
    change    = extract(r'"change"\s*:\s*"?([-\d.]+)"?', html)
    pct       = extract(r'"percentageChange"\s*:\s*"?([-\d.]+)"?', html)
    volume    = extract(r'"volume"\s*:\s*"?([\d,]+)"?', html)
    open_p    = extract(r'"open"\s*:\s*"?([\d.]+)"?', html)
    high      = extract(r'"high"\s*:\s*"?([\d.]+)"?', html)
    low       = extract(r'"low"\s*:\s*"?([\d.]+)"?', html)
    ldcp      = extract(r'"ldcp"\s*:\s*"?([\d.]+)"?', html)

    # Fallback: try alternate patterns used in PSX HTML
    if not price:
        price  = extract(r'id="currentPrice"[^>]*>([\d.]+)<', html)
    if not price:
        price  = extract(r'class="[^"]*current[^"]*price[^"]*"[^>]*>([\d.]+)<', html)

    # If still no price, try the JSON-LD structured data
    if not price:
        price  = extract(r'"price"\s*:\s*"?([\d.]+)"?', html)

    return {
        "symbol":    symbol,
        "price":     price     or "N/A",
        "change":    change    or "N/A",
        "pct":       (pct + "%" if pct else "N/A"),
        "volume":    volume    or "N/A",
        "open":      open_p    or "N/A",
        "high":      high      or "N/A",
        "low":       low       or "N/A",
        "ldcp":      ldcp      or "N/A",
        "time":      datetime.now().strftime("%H:%M:%S"),
        "status":    "ok" if price else "parse_error",
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "PSX Live Data Server is running ✅"}


@app.get("/quote/{symbol}")
async def get_quote(symbol: str):
    """Get live quote for a single PSX symbol. E.g. /quote/OGDC"""
    try:
        data = await fetch_psx_symbol(symbol)
        return data
    except Exception as e:
        return {
            "symbol": symbol.upper(),
            "status": "error",
            "error":  str(e),
            "time":   datetime.now().strftime("%H:%M:%S"),
        }


@app.get("/quotes")
async def get_quotes(symbols: str):
    """
    Get quotes for multiple symbols (comma-separated).
    E.g. /quotes?symbols=OGDC,HBL,ENGRO
    """
    import asyncio
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    tasks = [fetch_psx_symbol(s) for s in sym_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for sym, result in zip(sym_list, results):
        if isinstance(result, Exception):
            output.append({
                "symbol": sym,
                "status": "error",
                "error":  str(result),
                "time":   datetime.now().strftime("%H:%M:%S"),
            })
        else:
            output.append(result)

    return {"data": output, "count": len(output)}
