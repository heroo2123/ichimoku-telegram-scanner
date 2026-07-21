from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from .models import utc_now_iso
from .settings import settings

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


class LocalStore:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.data_dir / f"v3_{name}.json"

    def _read(self, name: str, default: Any) -> Any:
        path = self._path(name)
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        except Exception:
            return default

    def _write(self, name: str, value: Any) -> None:
        path = self._path(name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(path)

    def upsert_signals(self, rows: Iterable[Dict[str, Any]], *, preserve_lifecycle: bool = False) -> None:
        data = self._read("signals", {})
        for row in rows:
            signal_id = str(row["id"])
            existing = dict(data.get(signal_id) or {})
            merged = {**existing, **row}
            if preserve_lifecycle and existing:
                for key in ("status", "detected_at", "delivered_at"):
                    if existing.get(key) is not None:
                        merged[key] = existing[key]
            data[signal_id] = merged
        self._write("signals", data)

    def list_signals(self, limit: int = 200, status: Optional[str] = None) -> List[Dict[str, Any]]:
        values = list(self._read("signals", {}).values())
        if status:
            values = [row for row in values if row.get("status") == status]
        values.sort(key=lambda row: (row.get("signal_date", ""), row.get("score", 0)), reverse=True)
        return values[:limit]

    def record_event(self, signal_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        events = self._read("events", [])
        events.append({"signal_id": signal_id, "event_type": event_type, "payload": payload, "created_at": utc_now_iso()})
        self._write("events", events[-5000:])

    def save_regime(self, row: Dict[str, Any]) -> None:
        regimes = self._read("regimes", [])
        regimes.append(row)
        self._write("regimes", regimes[-1000:])

    def list_regimes(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(reversed(self._read("regimes", [])))[:limit]

    def save_backtest(self, row: Dict[str, Any]) -> None:
        runs = self._read("backtests", [])
        runs.append(row)
        self._write("backtests", runs[-100:])

    def list_backtests(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(reversed(self._read("backtests", [])))[:limit]

    def save_calibration(self, row: Dict[str, Any]) -> None:
        models = self._read("calibrations", [])
        models.append({**row, "trained_at": row.get("trained_at") or utc_now_iso()})
        self._write("calibrations", models[-100:])

    def list_calibrations(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(reversed(self._read("calibrations", [])))[:limit]

    def save_paper_state(self, state: Dict[str, Any]) -> None:
        self._write("paper", state)

    def load_paper_state(self) -> Dict[str, Any]:
        return self._read("paper", {})

    def create_dashboard_session(self, token_hash: str, expires_at: str, user_agent_hash: Optional[str]) -> None:
        sessions = self._read("dashboard_sessions", {})
        sessions[token_hash] = {
            "token_hash": token_hash,
            "expires_at": expires_at,
            "user_agent_hash": user_agent_hash,
            "revoked_at": None,
            "last_seen_at": utc_now_iso(),
        }
        self._write("dashboard_sessions", sessions)

    def validate_dashboard_session(self, token_hash: str) -> bool:
        sessions = self._read("dashboard_sessions", {})
        row = sessions.get(token_hash) or {}
        if row.get("revoked_at"):
            return False
        try:
            expires = datetime.fromisoformat(str(row.get("expires_at", "")).replace("Z", "+00:00"))
            valid = expires > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            valid = False
        if valid:
            row["last_seen_at"] = utc_now_iso()
            sessions[token_hash] = row
            self._write("dashboard_sessions", sessions)
        return valid

    def revoke_dashboard_session(self, token_hash: str) -> None:
        sessions = self._read("dashboard_sessions", {})
        if token_hash in sessions:
            sessions[token_hash]["revoked_at"] = utc_now_iso()
            self._write("dashboard_sessions", sessions)


class SupabaseStore(LocalStore):
    def __init__(self):
        super().__init__()
        self.base = f"{settings.supabase_url}/rest/v1"
        key = settings.supabase_service_role_key
        self.headers = {
            "apikey": key,
            "Content-Type": "application/json",
        }
        # New sb_secret_* keys are opaque API keys, not JWTs. Sending them as
        # Authorization: Bearer causes Supabase to reject them as invalid JWTs.
        # Keep Bearer support only for the legacy JWT-based service_role key.
        if key and not key.startswith("sb_"):
            self.headers["Authorization"] = f"Bearer {key}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        extra_headers = kwargs.pop("headers", {})
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            headers = dict(self.headers)
            headers.update(extra_headers)
            try:
                response = requests.request(
                    method,
                    f"{self.base}/{path}",
                    headers=headers,
                    timeout=45,
                    **kwargs,
                )
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"Supabase temporary HTTP {response.status_code}: {response.text[:300]}",
                        response=response,
                    )
                response.raise_for_status()
                return response.json() if response.content else None
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    def upsert_signals(self, rows: Iterable[Dict[str, Any]], *, preserve_lifecycle: bool = False) -> None:
        payload = list(rows)
        if payload:
            self._request(
                "POST",
                "rpc/upsert_signal_records",
                json={"p_rows": payload, "p_preserve_lifecycle": preserve_lifecycle},
            )
        super().upsert_signals(payload, preserve_lifecycle=preserve_lifecycle)

    def list_signals(self, limit: int = 200, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = f"signals?select=*&order=signal_date.desc,score.desc&limit={int(limit)}"
        if status:
            query += f"&status=eq.{status}"
        try:
            return self._request("GET", query)
        except Exception:
            return super().list_signals(limit, status)

    def record_event(self, signal_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        row = {"signal_id": signal_id, "event_type": event_type, "payload": payload}
        self._request("POST", "signal_events", json=row, headers={"Prefer": "return=minimal"})
        super().record_event(signal_id, event_type, payload)

    def save_regime(self, row: Dict[str, Any]) -> None:
        self._request("POST", "market_regimes", json=row, headers={"Prefer": "return=minimal"})
        super().save_regime(row)

    def list_regimes(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            return self._request("GET", f"market_regimes?select=*&order=as_of.desc&limit={int(limit)}")
        except Exception:
            return super().list_regimes(limit)

    def save_backtest(self, row: Dict[str, Any]) -> None:
        self._request("POST", "backtest_runs", json=row, headers={"Prefer": "return=minimal"})
        super().save_backtest(row)

    def list_backtests(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            return self._request("GET", f"backtest_runs?select=*&order=completed_at.desc&limit={int(limit)}")
        except Exception:
            return super().list_backtests(limit)

    def save_calibration(self, row: Dict[str, Any]) -> None:
        payload = {**row, "trained_at": row.get("trained_at") or utc_now_iso()}
        self._request("POST", "model_calibrations", json=payload, headers={"Prefer": "return=minimal"})
        super().save_calibration(payload)

    def list_calibrations(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            return self._request("GET", f"model_calibrations?select=*&order=trained_at.desc&limit={int(limit)}")
        except Exception:
            return super().list_calibrations(limit)

    def save_paper_state(self, state: Dict[str, Any]) -> None:
        row = {"account_key": "default", "state": state, "updated_at": utc_now_iso()}
        self._request("POST", "paper_accounts?on_conflict=account_key", json=row, headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        super().save_paper_state(state)

    def load_paper_state(self) -> Dict[str, Any]:
        try:
            rows = self._request("GET", "paper_accounts?select=state&account_key=eq.default&limit=1")
            return rows[0]["state"] if rows else super().load_paper_state()
        except Exception:
            return super().load_paper_state()

    def create_dashboard_session(self, token_hash: str, expires_at: str, user_agent_hash: Optional[str]) -> None:
        self._request(
            "POST",
            "dashboard_sessions?on_conflict=token_hash",
            json={
                "token_hash": token_hash,
                "expires_at": expires_at,
                "user_agent_hash": user_agent_hash,
                "last_seen_at": utc_now_iso(),
                "revoked_at": None,
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def validate_dashboard_session(self, token_hash: str) -> bool:
        rows = self._request(
            "GET",
            "dashboard_sessions",
            params={
                "select": "token_hash",
                "token_hash": f"eq.{token_hash}",
                "revoked_at": "is.null",
                "expires_at": f"gt.{utc_now_iso()}",
                "limit": "1",
            },
        )
        if not rows:
            return False
        self._request(
            "PATCH",
            "dashboard_sessions",
            params={"token_hash": f"eq.{token_hash}"},
            json={"last_seen_at": utc_now_iso()},
            headers={"Prefer": "return=minimal"},
        )
        return True

    def revoke_dashboard_session(self, token_hash: str) -> None:
        self._request(
            "PATCH",
            "dashboard_sessions",
            params={"token_hash": f"eq.{token_hash}"},
            json={"revoked_at": utc_now_iso()},
            headers={"Prefer": "return=minimal"},
        )


def get_store() -> LocalStore:
    return SupabaseStore() if settings.supabase_enabled else LocalStore()
