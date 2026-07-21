import "jsr:@supabase/functions-js/edge-runtime.d.ts";

type Json = Record<string, unknown>;

const SUPABASE_URL = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

function dbHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    apikey: SERVICE_KEY,
    "Content-Type": "application/json",
  };
  if (SERVICE_KEY && !SERVICE_KEY.startsWith("sb_")) {
    headers.Authorization = `Bearer ${SERVICE_KEY}`;
  }
  return headers;
}

async function dbGet(path: string): Promise<unknown> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: dbHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Database request failed (${response.status})`);
  }
  return await response.json();
}

function unwrapSetting(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const nested = (value as Record<string, unknown>).value;
    if (typeof nested === "string") return nested;
  }
  return "";
}

async function loadBotConfig(): Promise<{ token: string; chatId: string }> {
  const environmentToken = Deno.env.get("TELEGRAM_BOT_TOKEN") ?? "";
  const environmentChatId = Deno.env.get("TELEGRAM_CHAT_ID") ?? "";
  if (environmentToken && environmentChatId) {
    return { token: environmentToken, chatId: environmentChatId };
  }
  // Transitional fallback for the already-live installation. Once the two
  // Edge Function secrets are set, Telegram credentials are no longer read
  // from a database row.
  const rows = await dbGet(
    "user_settings?select=setting_key,value&setting_key=in.%28telegram_bot_token%2Ctelegram_chat_id%29",
  ) as Array<Record<string, unknown>>;
  const values = new Map(rows.map((row) => [String(row.setting_key), unwrapSetting(row.value)]));
  return {
    token: values.get("telegram_bot_token") ?? "",
    chatId: values.get("telegram_chat_id") ?? "",
  };
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) {
    mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return mismatch === 0;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function topLines(signals: Array<Record<string, unknown>>, limit = 10): string {
  const lines = signals.slice(0, limit).map((signal) => {
    const icon = signal.direction === "bullish" ? "🟢" : "🔴";
    return `${icon} <b>${escapeHtml(signal.symbol)}</b> ${escapeHtml(signal.grade)}/${escapeHtml(signal.score)} — ${escapeHtml(signal.signal_type)} [${escapeHtml(signal.status)}]`;
  });
  return lines.join("\n") || "No signals available.";
}

async function sendMessage(token: string, chatId: string, text: string): Promise<void> {
  const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  if (!response.ok) {
    throw new Error(`Telegram send failed (${response.status})`);
  }
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") {
    return new Response(JSON.stringify({ ok: true, service: "ichimoku-v3-telegram-commands" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const { token, chatId: allowedChatId } = await loadBotConfig();
    if (!token || !allowedChatId) {
      return new Response(JSON.stringify({ ok: false, error: "bot_not_configured" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    }

    const expectedSecret = await sha256Hex(`ichimoku-v3-webhook:${token}`);
    const receivedSecret = request.headers.get("x-telegram-bot-api-secret-token") ?? "";
    if (!safeEqual(receivedSecret, expectedSecret)) {
      return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }

    const update = await request.json() as Json;
    const message = (update.message ?? update.edited_message ?? {}) as Json;
    const chat = (message.chat ?? {}) as Json;
    const incomingChatId = String(chat.id ?? "");
    const text = String(message.text ?? "").trim();

    if (!incomingChatId || !text.startsWith("/")) {
      return new Response(JSON.stringify({ ok: true, handled: false }), {
        headers: { "Content-Type": "application/json" },
      });
    }
    if (incomingChatId !== allowedChatId) {
      return new Response(JSON.stringify({ ok: true, handled: false, reason: "unauthorized_chat" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    const command = text.split(/\s+/)[0].split("@")[0].toLowerCase();
    const signals = await dbGet(
      "signals?select=*&order=signal_date.desc,score.desc&limit=200",
    ) as Array<Record<string, unknown>>;

    let reply: string;
    if (command === "/top" || command === "/signals") {
      reply = `<b>Top Ichimoku V3 signals</b>\n${topLines(signals)}`;
    } else if (command === "/active") {
      const active = signals.filter((row) => ["active", "confirmed", "entry_zone"].includes(String(row.status)));
      reply = `<b>Active setups</b>\n${topLines(active)}`;
    } else if (command === "/performance") {
      const runs = await dbGet(
        "backtest_runs?select=symbol,summary,completed_at&order=completed_at.desc&limit=5",
      ) as Array<Record<string, unknown>>;
      const lines = runs.map((run) => {
        const summary = (run.summary ?? {}) as Record<string, unknown>;
        return `${escapeHtml(run.symbol)}: ${escapeHtml(summary.signals ?? 0)} signals`;
      });
      reply = lines.length ? `<b>Recent backtests</b>\n${lines.join("\n")}` : "No backtests stored yet.";
    } else if (command === "/paper") {
      const rows = await dbGet(
        "paper_accounts?select=state&account_key=eq.default&limit=1",
      ) as Array<Record<string, unknown>>;
      const state = (rows[0]?.state ?? {}) as Record<string, unknown>;
      const positions = (state.positions ?? {}) as Record<string, unknown>;
      const closedTrades = Array.isArray(state.closed_trades) ? state.closed_trades : [];
      const equity = Number(state.equity ?? 100000);
      reply = `<b>Paper portfolio</b>\nEquity: ${equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}\nOpen positions: ${Object.keys(positions).length}\nClosed trades: ${closedTrades.length}`;
    } else if (command === "/status") {
      const regimes = await dbGet(
        "market_regimes?select=market,regime,score,volatility&order=as_of.desc&limit=2",
      ) as Array<Record<string, unknown>>;
      const runs = await dbGet(
        "scanner_runs?select=market,mode,status,started_at,completed_at&order=started_at.desc&limit=4",
      ) as Array<Record<string, unknown>>;
      const queueRows = await dbGet(
        "delivery_queue?select=status&status=in.%28pending%2Cin_progress%2Cfailed%29&limit=1000",
      ) as Array<Record<string, unknown>>;
      const queue = { pending: 0, in_progress: 0, failed: 0 };
      for (const row of queueRows) {
        const status = String(row.status ?? "") as keyof typeof queue;
        if (status in queue) queue[status] += 1;
      }
      const regimeText = regimes.map((row) => `${escapeHtml(row.market)}=${escapeHtml(row.regime)}`).join(", ") || "not available yet";
      const runText = runs.map((run) => `${escapeHtml(run.market)} ${escapeHtml(run.mode)}: ${escapeHtml(run.status)} — ${escapeHtml(run.completed_at ?? run.started_at)}`).join("\n") || "No V3.1 run records yet.";
      reply = `<b>Ichimoku V3 status</b>\nSignals stored: ${signals.length}\nSupabase: enabled\nQueue: ${queue.pending} pending, ${queue.in_progress} processing, ${queue.failed} failed\nRegimes: ${regimeText}\n\n<b>Recent runs</b>\n${runText}`;
    } else if (command === "/help" || command === "/start") {
      reply = "<b>Ichimoku V3 commands</b>\n/status — scanner status\n/top — top signals\n/active — active setups\n/performance — recent backtests\n/paper — paper portfolio\n/help — command list";
    } else {
      reply = "Unknown command. Use /help.";
    }

    await sendMessage(token, incomingChatId, reply);
    return new Response(JSON.stringify({ ok: true, handled: true, command }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    console.error(error);
    return new Response(JSON.stringify({ ok: false, error: "internal_error" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});
