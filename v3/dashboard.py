from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .settings import settings
from .storage import get_store
from .telegram_commands import handle_update

app = FastAPI(title="Ichimoku Scanner V3", version="3.0.3")

COOKIE_NAME = "ichimoku_dashboard_session"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def _session_token() -> str:
    if not settings.dashboard_api_key:
        return ""
    raw = f"ichimoku-v3:{settings.dashboard_api_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _authorize(
    request: Request,
    key: Optional[str] = None,
    session: Optional[str] = None,
) -> None:
    if not settings.dashboard_api_key:
        return

    expected_session = _session_token()
    header_key_ok = bool(key) and hmac.compare_digest(key or "", settings.dashboard_api_key)
    session_header_ok = bool(session and expected_session) and hmac.compare_digest(
        session or "", expected_session
    )
    cookie_value = request.cookies.get(COOKIE_NAME, "")
    cookie_ok = bool(cookie_value and expected_session) and hmac.compare_digest(
        cookie_value, expected_session
    )

    if not (header_key_ok or session_header_ok or cookie_ok):
        raise HTTPException(status_code=401, detail="Dashboard is locked")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": "3.0.3", "supabase": settings.supabase_enabled}


@app.post("/auth/unlock")
async def unlock(request: Request) -> JSONResponse:
    body = await request.json()
    key = str(body.get("key", "")).strip()
    _authorize(request, key=key)

    token = _session_token()
    response = JSONResponse({"ok": True, "session_token": token})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/auth/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/signals")
def signals(
    request: Request,
    limit: int = 100,
    status: Optional[str] = None,
    x_api_key: Optional[str] = Header(default=None),
    x_dashboard_session: Optional[str] = Header(default=None),
) -> list:
    _authorize(request, key=x_api_key, session=x_dashboard_session)
    return get_store().list_signals(min(max(limit, 1), 1000), status)


@app.get("/api/regimes")
def regimes(
    request: Request,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
    x_dashboard_session: Optional[str] = Header(default=None),
) -> list:
    _authorize(request, key=x_api_key, session=x_dashboard_session)
    return get_store().list_regimes(min(max(limit, 1), 100))


@app.get("/api/backtests")
def backtests(
    request: Request,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
    x_dashboard_session: Optional[str] = Header(default=None),
) -> list:
    _authorize(request, key=x_api_key, session=x_dashboard_session)
    return get_store().list_backtests(min(max(limit, 1), 100))


@app.get("/api/paper")
def paper(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    x_dashboard_session: Optional[str] = Header(default=None),
) -> dict:
    _authorize(request, key=x_api_key, session=x_dashboard_session)
    state = get_store().load_paper_state()
    if state:
        return state
    return {
        "equity": settings.paper_starting_equity,
        "cash": settings.paper_starting_equity,
        "positions": {},
        "closed_trades": [],
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> dict:
    if settings.telegram_webhook_secret and not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    update = await request.json()
    return handle_update(update)


DASHBOARD_HTML = """
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Ichimoku V3</title><style>
body{font-family:system-ui;margin:0;background:#0d1117;color:#e6edf3}header{padding:24px;background:#161b22;position:sticky;top:0}main{padding:20px;max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px}table{width:100%;border-collapse:collapse;background:#161b22}th,td{padding:10px;border-bottom:1px solid #30363d;text-align:left;font-size:14px}.bullish{color:#3fb950}.bearish{color:#f85149}.muted{color:#8b949e}.ok{color:#3fb950}.error{color:#f85149}input{box-sizing:border-box;width:100%;padding:10px;background:#0d1117;color:white;border:1px solid #30363d;border-radius:6px;margin-bottom:10px}button{padding:10px 14px;border:0;border-radius:6px;cursor:pointer}button:disabled{opacity:.6;cursor:wait}.note{font-size:13px;line-height:1.4}[hidden]{display:none!important}</style></head>
<body><header><h1>Ichimoku Scanner V3</h1><span class='muted'>Signals, regimes, risk plans and paper portfolio</span></header><main>
<div class='grid'><div class='card'><h3>Market regimes</h3><div id='regimes' class='muted'>Loading…</div></div><div class='card'><h3>Paper portfolio</h3><div id='paper' class='muted'>Loading…</div></div><div id='access-card' class='card' hidden><h3>Unlock dashboard</h3><input id='key' type='password' autocomplete='current-password' placeholder='Dashboard API key'><button id='unlock' onclick='unlockDashboard()'>Unlock this phone</button><p id='status' class='muted note'>You only need to enter the key once on this browser.</p></div></div>
<h2>Latest signals</h2><div style='overflow:auto'><table><thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Type</th><th>Grade</th><th>Status</th><th>Entry zone</th><th>Invalidation</th></tr></thead><tbody id='signals'><tr><td colspan='8' class='muted'>Loading…</td></tr></tbody></table></div>
<script>
const SESSION_STORAGE_KEY='ichimokuDashboardSession';
const accessCard=document.getElementById('access-card');
const keyInput=document.getElementById('key');
const unlockButton=document.getElementById('unlock');
const statusBox=document.getElementById('status');
let sessionToken=localStorage.getItem(SESSION_STORAGE_KEY)||'';
function sessionHeaders(){return sessionToken?{'X-Dashboard-Session':sessionToken}:{}}
async function j(url,options={}){const headers={...sessionHeaders(),...(options.headers||{})};const r=await fetch(url,{credentials:'same-origin',...options,headers});if(!r.ok){const body=await r.text();const err=new Error(body);err.status=r.status;throw err}return r.json()}
function showLocked(message){accessCard.hidden=false;document.getElementById('regimes').innerHTML='Unlock the dashboard to load data.';document.getElementById('paper').innerHTML='Unlock the dashboard to load data.';document.getElementById('signals').innerHTML="<tr><td colspan='8' class='muted'>Unlock the dashboard to load signals.</td></tr>";statusBox.textContent=message;statusBox.className='muted note'}
function renderData(s,r,p){accessCard.hidden=true;document.getElementById('signals').innerHTML=s.length?s.map(x=>`<tr><td><b>${x.symbol}</b></td><td>${x.market}</td><td class='${x.direction}'>${x.direction}</td><td>${x.signal_type}</td><td>${x.grade}/${x.score}</td><td>${x.status}</td><td>${x.risk_plan?.entry_low??'-'} – ${x.risk_plan?.entry_high??'-'}</td><td>${x.risk_plan?.invalidation??'-'}</td></tr>`).join(''):"<tr><td colspan='8' class='muted'>No signals stored yet. The next completed market scans will populate this table.</td></tr>";document.getElementById('regimes').innerHTML=r.slice(0,4).map(x=>`<p><b>${x.market}</b>: ${x.regime} (${x.score}) — ${x.volatility} volatility</p>`).join('')||'No regime data yet';document.getElementById('paper').innerHTML=`Equity: ${(p.equity||0).toLocaleString()}<br>Open positions: ${Object.keys(p.positions||{}).length}<br>Closed trades: ${(p.closed_trades||[]).length}`}
async function loadAll(){try{const [s,r,p]=await Promise.all([j('/api/signals?limit=100'),j('/api/regimes'),j('/api/paper')]);renderData(s,r,p)}catch(e){if(e.status===401){sessionToken='';localStorage.removeItem(SESSION_STORAGE_KEY);showLocked('Enter the dashboard key once. This browser will remember the session.')}else showLocked('Could not load data: '+e.message)}}
async function unlockDashboard(){const key=keyInput.value.trim();if(!key){statusBox.textContent='Enter the dashboard API key.';statusBox.className='error note';return}unlockButton.disabled=true;statusBox.textContent='Unlocking…';statusBox.className='muted note';try{const result=await j('/auth/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});sessionToken=result.session_token||'';if(sessionToken)localStorage.setItem(SESSION_STORAGE_KEY,sessionToken);keyInput.value='';await loadAll()}catch(e){statusBox.textContent=e.status===401?'That key was rejected. Copy the complete DASHBOARD_API_KEY from Render → Environment.':'Could not unlock: '+e.message;statusBox.className='error note'}finally{unlockButton.disabled=false}}
keyInput.addEventListener('keydown',e=>{if(e.key==='Enter')unlockDashboard()});
loadAll();
</script></main></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return DASHBOARD_HTML
