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

    # Price: Rs.271.47
    price = find(r'Rs\.([\d,]+\.?\d*)')

    # Change and % change sit right below price in the markup
    change  = find(r'Rs\.[\d,]+\.?\d*\s*\n\s*([-\d.]+)')
    pct     = find(r'\(([-\d.]+%)\)')

    # Key stats block: "Open\n272.99"
    open_p  = find(r'Open\s*[\n\r]+\s*([\d,]+\.?\d*)')
    high    = find(r'High\s*[\n\r]+\s*([\d,]+\.?\d*)')
    low     = find(r'Low\s*[\n\r]+\s*([\d,]+\.?\d*)')
    volume  = find(r'Volume\s*[\n\r]+\s*([\d,]+)')
    ldcp    = find(r'LDCP\s*[\n\r]+\s*([\d,]+\.?\d*)')

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
    """Shows raw HTML snippet — use this to diagnose parse issues"""
    symbol = symbol.strip().upper()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dps.psx.com.pk/",
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await client.get("https://dps.psx.com.pk/", headers=headers)
        resp = await client.get(f"https://dps.psx.com.pk/company/{symbol}", headers=headers)
    text = resp.text
    idx = text.find("Rs.")
    snippet = text[max(0, idx-50):idx+600] if idx != -1 else text[2000:3500]
    return {"snippet": snippet, "http_status": resp.status_code, "length": len(text)}


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