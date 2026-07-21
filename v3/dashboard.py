from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .settings import settings
from .storage import get_store
from .telegram_commands import handle_update

app = FastAPI(title="Ichimoku Scanner V3", version="3.0.0")


def _authorize(key: Optional[str]) -> None:
    if settings.dashboard_api_key and not hmac.compare_digest(key or "", settings.dashboard_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": "3.0.0", "supabase": settings.supabase_enabled}


@app.get("/api/signals")
def signals(limit: int = 100, status: Optional[str] = None, x_api_key: Optional[str] = Header(default=None)) -> list:
    _authorize(x_api_key)
    return get_store().list_signals(min(max(limit, 1), 1000), status)


@app.get("/api/regimes")
def regimes(limit: int = 20, x_api_key: Optional[str] = Header(default=None)) -> list:
    _authorize(x_api_key)
    return get_store().list_regimes(min(max(limit, 1), 100))


@app.get("/api/backtests")
def backtests(limit: int = 20, x_api_key: Optional[str] = Header(default=None)) -> list:
    _authorize(x_api_key)
    return get_store().list_backtests(min(max(limit, 1), 100))


@app.get("/api/paper")
def paper(x_api_key: Optional[str] = Header(default=None)) -> dict:
    _authorize(x_api_key)
    return get_store().load_paper_state()


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)) -> dict:
    if settings.telegram_webhook_secret and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    update = await request.json()
    return handle_update(update)


DASHBOARD_HTML = """
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Ichimoku V3</title><style>
body{font-family:system-ui;margin:0;background:#0d1117;color:#e6edf3}header{padding:24px;background:#161b22;position:sticky;top:0}main{padding:20px;max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px}table{width:100%;border-collapse:collapse;background:#161b22}th,td{padding:10px;border-bottom:1px solid #30363d;text-align:left;font-size:14px}.bullish{color:#3fb950}.bearish{color:#f85149}.muted{color:#8b949e}input{padding:8px;background:#0d1117;color:white;border:1px solid #30363d;border-radius:6px}</style></head>
<body><header><h1>Ichimoku Scanner V3</h1><span class='muted'>Signals, regimes, risk plans and paper portfolio</span></header><main>
<div class='grid'><div class='card'><h3>Market regimes</h3><div id='regimes'>Loading…</div></div><div class='card'><h3>Paper portfolio</h3><div id='paper'>Loading…</div></div><div class='card'><h3>Filters</h3><input id='key' placeholder='Dashboard API key'><button onclick='loadAll()'>Refresh</button></div></div>
<h2>Latest signals</h2><div style='overflow:auto'><table><thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Type</th><th>Grade</th><th>Status</th><th>Entry zone</th><th>Invalidation</th></tr></thead><tbody id='signals'></tbody></table></div>
<script>
const headers=()=>{const k=document.getElementById('key').value;return k?{'X-API-Key':k}:{}};
async function j(url){const r=await fetch(url,{headers:headers()});if(!r.ok)throw new Error(await r.text());return r.json()}
async function loadAll(){try{const [s,r,p]=await Promise.all([j('/api/signals?limit=100'),j('/api/regimes'),j('/api/paper')]);
document.getElementById('signals').innerHTML=s.map(x=>`<tr><td><b>${x.symbol}</b></td><td>${x.market}</td><td class='${x.direction}'>${x.direction}</td><td>${x.signal_type}</td><td>${x.grade}/${x.score}</td><td>${x.status}</td><td>${x.risk_plan?.entry_low??'-'} – ${x.risk_plan?.entry_high??'-'}</td><td>${x.risk_plan?.invalidation??'-'}</td></tr>`).join('');
document.getElementById('regimes').innerHTML=r.slice(0,4).map(x=>`<p><b>${x.market}</b>: ${x.regime} (${x.score}) — ${x.volatility} volatility</p>`).join('')||'No regime data yet';
document.getElementById('paper').innerHTML=`Equity: ${(p.equity||0).toLocaleString()}<br>Open positions: ${Object.keys(p.positions||{}).length}<br>Closed trades: ${(p.closed_trades||[]).length}`;}catch(e){alert(e)}}
loadAll();</script></main></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return DASHBOARD_HTML
