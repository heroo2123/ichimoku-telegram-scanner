from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class V3Settings:
    supabase_url: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    dashboard_api_key: str = os.getenv("DASHBOARD_API_KEY", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    delivery_backend: str = os.getenv("DELIVERY_BACKEND", "auto").strip().lower()
    delivery_claim_limit: int = _int("DELIVERY_CLAIM_LIMIT", 1000)
    delivery_claim_timeout_minutes: int = _int("DELIVERY_CLAIM_TIMEOUT_MINUTES", 30)
    paper_starting_equity: float = _float("PAPER_STARTING_EQUITY", 100_000.0)
    paper_risk_per_trade_pct: float = _float("PAPER_RISK_PER_TRADE_PCT", 0.5)
    paper_max_position_pct: float = _float("PAPER_MAX_POSITION_PCT", 15.0)
    paper_fee_bps: float = _float("PAPER_FEE_BPS", 10.0)
    paper_slippage_bps: float = _float("PAPER_SLIPPAGE_BPS", 5.0)
    paper_max_positions: int = _int("PAPER_MAX_POSITIONS", 12)
    paper_max_cluster_positions: int = _int("PAPER_MAX_CLUSTER_POSITIONS", 3)
    paper_min_grade: str = os.getenv("PAPER_MIN_GRADE", "B").upper()
    lifecycle_entry_zone_atr: float = _float("LIFECYCLE_ENTRY_ZONE_ATR", 0.6)
    lifecycle_extended_atr: float = _float("LIFECYCLE_EXTENDED_ATR", 2.5)
    lifecycle_complete_sessions: int = _int("LIFECYCLE_COMPLETE_SESSIONS", 20)
    lifecycle_refresh_limit: int = _int("LIFECYCLE_REFRESH_LIMIT", 5000)

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def database_delivery_enabled(self) -> bool:
        return self.supabase_enabled and self.delivery_backend in {"auto", "supabase", "dual"}


settings = V3Settings()
