from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .settings import settings
from .storage import get_store
from .telegram_commands import handle_update

app = FastAPI(title="Ichimoku Scanner V3", version="3.1.0")

COOKIE_NAME = "ichimoku_dashboard_session"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def _legacy_session_token() -> str:
    if not settings.dashboard_api_key:
        return ""
    raw = f"ichimoku-v3:{settings.dashboard_api_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _key_fingerprint() -> str:
    return hashlib.sha256(settings.dashboard_api_key.encode("utf-8")).hexdigest()


def _issue_session(response: JSONResponse, request: Request) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(seconds=COOKIE_MAX_AGE_SECONDS)
    user_agent = request.headers.get("user-agent", "")
    user_agent_hash = hashlib.sha256(user_agent.encode("utf-8")).hexdigest() if user_agent else None
    get_store().create_dashboard_session(_token_hash(token), expires.isoformat(), user_agent_hash, _key_fingerprint())
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _authorize(
    request: Request,
    key: Optional[str] = None,
    session: Optional[str] = None,
) -> None:
    if not settings.dashboard_api_key:
        return

    legacy_session = _legacy_session_token()
    header_key_ok = bool(key) and hmac.compare_digest(key or "", settings.dashboard_api_key)
    legacy_header_ok = bool(session and legacy_session) and hmac.compare_digest(
        session or "", legacy_session
    )
    cookie_value = request.cookies.get(COOKIE_NAME, "")
    legacy_cookie_ok = bool(cookie_value and legacy_session) and hmac.compare_digest(cookie_value, legacy_session)
    cookie_ok = False
    if cookie_value and not legacy_cookie_ok:
        try:
            cookie_ok = get_store().validate_dashboard_session(_token_hash(cookie_value), _key_fingerprint())
        except Exception:
            cookie_ok = False

    if not (header_key_ok or legacy_header_ok or legacy_cookie_ok or cookie_ok):
        raise HTTPException(status_code=401, detail="Dashboard is locked")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": "3.1.0", "supabase": settings.supabase_enabled}


@app.post("/auth/unlock")
async def unlock(request: Request) -> JSONResponse:
    body = await request.json()
    key = str(body.get("key", "")).strip()
    if not settings.dashboard_api_key or not hmac.compare_digest(key, settings.dashboard_api_key):
        raise HTTPException(status_code=401, detail="Dashboard is locked")
    response = JSONResponse({"ok": True})
    _issue_session(response, request)
    return response


@app.post("/auth/migrate")
def migrate_legacy_session(
    request: Request,
    x_dashboard_session: Optional[str] = Header(default=None),
) -> JSONResponse:
    legacy = _legacy_session_token()
    if not legacy or not x_dashboard_session or not hmac.compare_digest(x_dashboard_session, legacy):
        raise HTTPException(status_code=401, detail="Legacy session rejected")
    response = JSONResponse({"ok": True})
    _issue_session(response, request)
    return response


@app.post("/auth/logout")
def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(COOKIE_NAME, "")
    if token and token != _legacy_session_token():
        try:
            get_store().revoke_dashboard_session(_token_hash(token))
        except Exception:
            pass
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


@app.get("/api/runs")
def scanner_runs(
    request: Request,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
    x_dashboard_session: Optional[str] = Header(default=None),
) -> list:
    _authorize(request, key=x_api_key, session=x_dashboard_session)
    return get_store().list_scanner_runs(min(max(limit, 1), 100))


@app.get("/api/queue-health")
def queue_health(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    x_dashboard_session: Optional[str] = Header(default=None),
) -> dict:
    _authorize(request, key=x_api_key, session=x_dashboard_session)
    return get_store().delivery_queue_health()


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
<div class='grid'><div class='card'><h3>Market regimes</h3><div id='regimes' class='muted'>Loading…</div></div><div class='card'><h3>Paper portfolio</h3><div id='paper' class='muted'>Loading…</div></div><div class='card'><h3>Scanner health</h3><div id='scanner-health' class='muted'>Loading…</div></div><div id='access-card' class='card' hidden><h3>Unlock dashboard</h3><input id='key' type='password' autocomplete='current-password' placeholder='Dashboard API key'><button id='unlock' onclick='unlockDashboard()'>Unlock this phone</button><p id='status' class='muted note'>You only need to enter the key once on this browser.</p></div></div>
<h2>Latest signals</h2><div style='overflow:auto'><table><thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Type</th><th>Grade</th><th>Status</th><th>Entry zone</th><th>Invalidation</th></tr></thead><tbody id='signals'><tr><td colspan='8' class='muted'>Loading…</td></tr></tbody></table></div>
<script>
const LEGACY_SESSION_STORAGE_KEY='ichimokuDashboardSession';
const accessCard=document.getElementById('access-card');
const keyInput=document.getElementById('key');
const unlockButton=document.getElementById('unlock');
const statusBox=document.getElementById('status');
async function j(url,options={}){const r=await fetch(url,{credentials:'same-origin',...options});if(!r.ok){const body=await r.text();const err=new Error(body);err.status=r.status;throw err}return r.json()}
function esc(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]))}
function showLocked(message){accessCard.hidden=false;document.getElementById('regimes').innerHTML='Unlock the dashboard to load data.';document.getElementById('paper').innerHTML='Unlock the dashboard to load data.';document.getElementById('scanner-health').innerHTML='Unlock the dashboard to load data.';document.getElementById('signals').innerHTML="<tr><td colspan='8' class='muted'>Unlock the dashboard to load signals.</td></tr>";statusBox.textContent=message;statusBox.className='muted note'}
function renderData(s,r,p,runs,q){accessCard.hidden=true;document.getElementById('signals').innerHTML=s.length?s.map(x=>`<tr><td><b>${esc(x.symbol)}</b></td><td>${esc(x.market)}</td><td class='${x.direction==='bullish'?'bullish':'bearish'}'>${esc(x.direction)}</td><td>${esc(x.signal_type)}</td><td>${esc(x.grade)}/${esc(x.score)}</td><td>${esc(x.status)}</td><td>${esc(x.risk_plan?.entry_low??'-')} – ${esc(x.risk_plan?.entry_high??'-')}</td><td>${esc(x.risk_plan?.invalidation??'-')}</td></tr>`).join(''):"<tr><td colspan='8' class='muted'>No signals stored yet. The next completed market scans will populate this table.</td></tr>";document.getElementById('regimes').innerHTML=r.slice(0,4).map(x=>`<p><b>${esc(x.market)}</b>: ${esc(x.regime)} (${esc(x.score)}) — ${esc(x.volatility)} volatility</p>`).join('')||'No regime data yet';document.getElementById('paper').innerHTML=`Equity: ${Number(p.equity||0).toLocaleString()}<br>Open positions: ${Object.keys(p.positions||{}).length}<br>Closed trades: ${(p.closed_trades||[]).length}<br>Unrealized P&amp;L: ${Number(p.unrealized_pnl||0).toLocaleString()}`;const last=runs[0];document.getElementById('scanner-health').innerHTML=last?`Last run: <b>${esc(last.market)}</b> — <span class='${last.status==='completed'?'ok':last.status==='failed'?'error':''}'>${esc(last.status)}</span><br>${esc(last.completed_at||last.started_at)}<br>Queue: ${Number(q.pending||0)} pending, ${Number(q.in_progress||0)} processing, ${Number(q.failed||0)} failed`:`No V3.1 run records yet.<br>Queue: ${Number(q.pending||0)} pending, ${Number(q.in_progress||0)} processing, ${Number(q.failed||0)} failed`}
async function migrateLegacySession(){const legacy=localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)||'';if(!legacy)return;try{const response=await fetch('/auth/migrate',{method:'POST',credentials:'same-origin',headers:{'X-Dashboard-Session':legacy}});if(response.ok)localStorage.removeItem(LEGACY_SESSION_STORAGE_KEY)}catch(e){}}
async function loadAll(){try{const [s,r,p,runs,q]=await Promise.all([j('/api/signals?limit=100'),j('/api/regimes'),j('/api/paper'),j('/api/runs?limit=10'),j('/api/queue-health')]);renderData(s,r,p,runs,q)}catch(e){if(e.status===401){showLocked('Enter the dashboard key once. This browser will remember the session.')}else showLocked('Could not load data: '+e.message)}}
async function unlockDashboard(){const key=keyInput.value.trim();if(!key){statusBox.textContent='Enter the dashboard API key.';statusBox.className='error note';return}unlockButton.disabled=true;statusBox.textContent='Unlocking…';statusBox.className='muted note';try{await j('/auth/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});localStorage.removeItem(LEGACY_SESSION_STORAGE_KEY);keyInput.value='';await loadAll()}catch(e){statusBox.textContent=e.status===401?'That key was rejected. Copy the complete DASHBOARD_API_KEY from Render → Environment.':'Could not unlock: '+e.message;statusBox.className='error note'}finally{unlockButton.disabled=false}}
keyInput.addEventListener('keydown',e=>{if(e.key==='Enter')unlockDashboard()});
migrateLegacySession().finally(loadAll);
</script></main></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return DASHBOARD_HTML
