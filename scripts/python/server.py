import json
import sys
import os
import threading
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import re
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]
PAPER_TRADES_FILE = BASE_DIR / "paper_trades.json"
FOCUS_CONFIG_FILE = BASE_DIR / "focus_config.json"

app = FastAPI(title="PolyBot Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Bot state ──────────────────────────────────────────────────────────────────
bot_state = {"running": False, "log": [], "last_run": None}
bot_lock  = threading.Lock()


def load_trades() -> list:
    if PAPER_TRADES_FILE.exists():
        with open(PAPER_TRADES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def load_config() -> dict:
    if FOCUS_CONFIG_FILE.exists():
        with open(FOCUS_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(data: dict):
    with open(FOCUS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)



# ── Auto-resolve ───────────────────────────────────────────────────────────────

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"


def _fetch_market_status(market_id: str) -> dict:
    """Busca dados do mercado na Gamma API. Tenta /{id} e fallback ?id={id}."""
    try:
        # Tentativa 1: endpoint direto /markets/{id}
        r = httpx.get(f"{GAMMA_MARKETS}/{market_id}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            # API pode retornar lista ou dict
            if isinstance(data, list):
                return data[0] if data else None
            if isinstance(data, dict):
                return data
    except Exception:
        pass

    try:
        # Tentativa 2: query param ?id={id}
        r = httpx.get(GAMMA_MARKETS, params={"id": market_id}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[0] if data else None
            if isinstance(data, dict):
                return data
    except Exception:
        pass

    return None


def _calc_pnl(won: bool, amount: float, entry_price: float) -> float:
    if won:
        return round(amount * (1.0 / max(entry_price, 0.01) - 1.0), 4)
    return round(-amount, 4)


def auto_resolve_trades() -> dict:
    trades = load_trades()
    now = datetime.now(timezone.utc)
    updated = 0
    skipped = 0
    errors = []
    resolve_log = []

    for i, t in enumerate(trades):
        if t.get("resolved"):
            continue

        # Só tenta resolver mercados cujo end_date ja passou
        end_str = t.get("end_date", "")
        if end_str and end_str != "N/A":
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt > now:
                    skipped += 1
                    continue
            except Exception:
                pass

        market_id = t.get("market_id", "")
        if not market_id:
            errors.append(f"Trade #{i+1}: sem market_id")
            continue

        data = _fetch_market_status(market_id)
        if not data:
            errors.append(f"Trade #{i+1}: API falhou market_id={market_id}")
            continue

        outcome_prices = data.get("outcomePrices") or []
        outcomes = data.get("outcomes") or []
        if isinstance(outcome_prices, str):
            try: outcome_prices = json.loads(outcome_prices)
            except Exception: outcome_prices = []
        if isinstance(outcomes, str):
            try: outcomes = json.loads(outcomes)
            except Exception: outcomes = []

        # Determina vencedor com thresholds progressivos:
        # 1. Oráculo oficial (resolved=True) → confia no campo outcomePrices
        # 2. Mercado fechado (active=False) + preço >= 0.80 → resolução provável
        # 3. Preço >= 0.95 → resolução praticamente certa mesmo sem fechamento oficial
        officially_resolved = data.get("resolved", False)
        market_active = data.get("active", True)
        # "active" pode ser bool True/False ou string "true"/"false"/"closed"
        if isinstance(market_active, str):
            market_active = market_active.lower() not in ("false", "closed", "0")

        market_closed = (not market_active) or officially_resolved

        winning_idx = None
        resolve_how = ""

        # Tenta cada threshold em ordem decrescente de confiança
        thresholds = [
            (0.995, "oraculo"),
            (0.95,  "price>=0.95"),
            (0.80,  "price>=0.80 (fechado)" if market_closed else None),
        ]
        for thresh, label in thresholds:
            if label is None:
                continue
            for idx, p in enumerate(outcome_prices):
                try:
                    if float(p) >= thresh:
                        winning_idx = idx
                        resolve_how = label
                        break
                except Exception:
                    pass
            if winning_idx is not None:
                break

        if winning_idx is None:
            try:
                prices_f = [float(p) for p in outcome_prices]
                max_price = max(prices_f) if prices_f else 0
            except Exception:
                max_price = 0
            status_str = "fechado" if market_closed else "aberto"
            resolve_log.append(
                f"Trade #{i+1} ({t.get('market','?')[:40]}): sem resolucao "
                f"(max={max_price:.2f}, {status_str})"
            )
            skipped += 1
            continue

        winning_outcome = str(outcomes[winning_idx]).upper() if winning_idx < len(outcomes) else "YES"
        our_outcome = str(t.get("outcome", "YES")).upper()
        won = (our_outcome == winning_outcome or
               (our_outcome == "YES" and winning_idx == 0) or
               (our_outcome == "NO"  and winning_idx == 1))

        entry_price = float(t.get("entry_price", 0.5) or 0.5)
        amount      = float(t.get("simulated_usdc_amount", 0) or 0)
        pnl         = _calc_pnl(won, amount, entry_price)

        resolve_log.append(
            f"Trade #{i+1} ({t.get('market','?')[:40]}): {'GANHOU' if won else 'PERDEU'} "
            f"PnL=${pnl:+.2f} [{resolve_how}] nosso={our_outcome} vencedor={winning_outcome}"
        )

        trades[i]["resolved"] = True
        trades[i]["won"]      = won
        trades[i]["pnl"]      = pnl
        updated += 1

    if updated:
        tmp = PAPER_TRADES_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)
        tmp.replace(PAPER_TRADES_FILE)

    return {"updated": updated, "skipped": skipped, "errors": errors, "log": resolve_log}


def _background_resolve_loop():
    """Verifica resultados de trades a cada 5 minutos."""
    import time
    while True:
        try:
            auto_resolve_trades()
        except Exception:
            pass
        time.sleep(300)  # 5 minutos


def _background_trading_loop():
    """Executa o bot automaticamente a cada 10 minutos."""
    import time, sys, os, subprocess
    time.sleep(30)  # aguarda o servidor subir completamente antes do primeiro run
    while True:
        with bot_lock:
            already_running = bot_state["running"]
        if not already_running:
            with bot_lock:
                bot_state["running"] = True
                bot_state["log"] = ["[Auto-Loop] Iniciando ciclo automatico..."]
                bot_state["last_run"] = datetime.now(timezone.utc).isoformat()

            def _run():
                try:
                    python = sys.executable
                    script = BASE_DIR / "agents" / "application" / "trade.py"
                    env = os.environ.copy()
                    env["PYTHONPATH"] = str(BASE_DIR)
                    env["PYTHONIOENCODING"] = "utf-8"
                    proc = subprocess.Popen(
                        [python, str(script)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        cwd=str(BASE_DIR),
                        env=env,
                        encoding="utf-8",
                        errors="replace",
                    )
                    for line in proc.stdout:
                        with bot_lock:
                            bot_state["log"].append(line.rstrip())
                            if len(bot_state["log"]) > 500:
                                bot_state["log"] = bot_state["log"][-500:]
                    proc.wait()
                    with bot_lock:
                        bot_state["log"].append(f"[Auto-Loop] Ciclo concluido (codigo {proc.returncode})")
                except Exception as ex:
                    with bot_lock:
                        bot_state["log"].append(f"[Auto-Loop] ERRO: {ex}")
                finally:
                    with bot_lock:
                        bot_state["running"] = False

            threading.Thread(target=_run, daemon=True).start()
        time.sleep(600)  # 10 minutos

# ── API ────────────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def get_trades():
    return load_trades()


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def update_config(body: dict):
    save_config(body)
    return {"ok": True}


@app.get("/api/stats")
def get_stats():
    trades   = load_trades()
    resolved = [t for t in trades if t.get("resolved")]
    pending  = [t for t in trades if not t.get("resolved")]
    won      = [t for t in resolved if t.get("won")]
    lost     = [t for t in resolved if t.get("won") is False]
    total_usdc = sum(t.get("simulated_usdc_amount", 0) for t in trades)
    pnl_total  = sum(t.get("pnl") or 0 for t in resolved)
    win_rate   = round(len(won) / len(resolved) * 100, 1) if resolved else None
    import time as _time
    return {
        "total_trades": len(trades),
        "pending": len(pending),
        "resolved": len(resolved),
        "won": len(won),
        "lost": len(lost),
        "win_rate": win_rate,
        "total_usdc_deployed": round(total_usdc, 2),
        "pnl": round(pnl_total, 2),
        "bot_running": bot_state["running"],
        "last_run": bot_state["last_run"],
        "auto_loop": True,
    }


@app.post("/api/run")
def run_bot():
    with bot_lock:
        if bot_state["running"]:
            return {"ok": False, "msg": "Bot já está em execução"}
        bot_state["running"] = True
        bot_state["log"] = ["[PolyBot] Iniciando..."]
        bot_state["last_run"] = datetime.now(timezone.utc).isoformat()

    def _run():
        try:
            python = sys.executable
            script = BASE_DIR / "agents" / "application" / "trade.py"
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONPATH"] = str(BASE_DIR)
            proc = subprocess.Popen(
                [python, str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(BASE_DIR),
                env=env,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                with bot_lock:
                    bot_state["log"].append(line.rstrip())
                    if len(bot_state["log"]) > 500:
                        bot_state["log"] = bot_state["log"][-500:]
            proc.wait()
            with bot_lock:
                bot_state["log"].append(f"[PolyBot] Finalizado (código {proc.returncode})")
        except Exception as ex:
            with bot_lock:
                bot_state["log"].append(f"[ERRO] {ex}")
        finally:
            with bot_lock:
                bot_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "msg": "Bot iniciado"}


@app.get("/api/run/log")
def get_log():
    with bot_lock:
        return {"running": bot_state["running"], "log": bot_state["log"]}


@app.patch("/api/trades/{idx}/resolve")
def resolve_trade(idx: int, won: bool, pnl: float = 0.0):
    trades = load_trades()
    if idx < 0 or idx >= len(trades):
        raise HTTPException(status_code=404, detail="Trade não encontrada")
    trades[idx]["resolved"] = True
    trades[idx]["won"] = won
    trades[idx]["pnl"] = round(pnl, 4)
    with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)
    return trades[idx]



@app.post("/api/auto-resolve")
def trigger_auto_resolve():
    result = auto_resolve_trades()
    return result


@app.get("/api/debug/market/{market_id}")
def debug_market(market_id: str):
    """Retorna dados brutos da Gamma API para um market_id — útil para diagnóstico."""
    raw1, raw2 = None, None
    try:
        r = httpx.get(f"{GAMMA_MARKETS}/{market_id}", timeout=10)
        raw1 = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:500]}
    except Exception as e:
        raw1 = {"error": str(e)}
    try:
        r = httpx.get(GAMMA_MARKETS, params={"id": market_id}, timeout=10)
        raw2 = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:500]}
    except Exception as e:
        raw2 = {"error": str(e)}
    parsed = _fetch_market_status(market_id)
    return {
        "market_id": market_id,
        "endpoint_direct": raw1,
        "endpoint_query": raw2,
        "parsed": parsed,
    }


# ── Dashboard HTML ─────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PolyBot Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}
header{background:#1a1d2e;border-bottom:1px solid #2d3148;padding:16px 28px;display:flex;align-items:center;gap:12px}
header h1{font-size:1.2rem;font-weight:700;color:#a78bfa}
.dot{width:8px;height:8px;border-radius:50%;background:#22c55e;flex-shrink:0}
.dot.off{background:#ef4444;animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.dot{animation:pulse 2s infinite}
nav{display:flex;gap:4px;margin-left:auto}
.nav-btn{background:none;border:1px solid #2d3148;color:#94a3b8;border-radius:8px;padding:6px 14px;font-size:.8rem;cursor:pointer}
.nav-btn.active,.nav-btn:hover{background:#312e81;border-color:#4f46e5;color:#c4b5fd}
main{padding:24px 28px;max-width:1400px;margin:0 auto}
.page{display:none}.page.active{display:block}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:14px;margin-bottom:24px}
.card{background:#1a1d2e;border:1px solid #2d3148;border-radius:12px;padding:18px}
.card label{font-size:.7rem;color:#64748b;text-transform:uppercase;letter-spacing:.06em}
.card .val{font-size:1.8rem;font-weight:700;margin-top:4px}
.green{color:#22c55e}.red{color:#ef4444}.purple{color:#a78bfa}.yellow{color:#f59e0b}
section{background:#1a1d2e;border:1px solid #2d3148;border-radius:12px;padding:20px;margin-bottom:18px}
section h2{font-size:.8rem;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th{padding:10px 12px;text-align:left;font-size:.72rem;color:#64748b;text-transform:uppercase;border-bottom:1px solid #2d3148}
td{padding:10px 12px;font-size:.83rem;border-bottom:1px solid #1e2235;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1e2235}
.buy{color:#22c55e;font-weight:700}.sell{color:#ef4444;font-weight:700}
.s-pending{color:#f59e0b}.s-won{color:#22c55e}.s-lost{color:#ef4444}
a.lnk{color:#818cf8;font-size:.8rem;text-decoration:none}a.lnk:hover{text-decoration:underline}
.btn{border:none;border-radius:8px;padding:8px 18px;font-size:.82rem;cursor:pointer;font-weight:600}
.btn-run{background:#4f46e5;color:#fff;padding:10px 24px;font-size:.9rem}
.btn-run:hover{background:#4338ca}
.btn-run:disabled{background:#1e293b;color:#475569;cursor:not-allowed}
.btn-sm{background:#1e293b;border:1px solid #334155;color:#94a3b8;border-radius:6px;padding:4px 10px;font-size:.75rem;cursor:pointer}
.btn-sm:hover{background:#334155}
.btn-won{background:#166534;color:#22c55e}
.btn-lost{background:#7f1d1d;color:#ef4444}
.btn-cancel{background:#1e293b;color:#94a3b8}
.badge{display:inline-block;background:#312e81;color:#a78bfa;border-radius:6px;padding:2px 9px;font-size:.78rem;margin:2px}
.log-box{background:#0a0c14;border:1px solid #1e2235;border-radius:10px;padding:14px;height:380px;overflow-y:auto;font-family:'Cascadia Code','Fira Code',monospace;font-size:.78rem;color:#94a3b8;white-space:pre-wrap}
.log-box .info{color:#60a5fa}.log-box .ok{color:#22c55e}.log-box .err{color:#ef4444}
.run-header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.run-status{font-size:.8rem;color:#f59e0b}
.cfg-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.cfg-field label{font-size:.75rem;color:#64748b;display:block;margin-bottom:4px}
.cfg-field input,.cfg-field select{width:100%;background:#0f1117;border:1px solid #2d3148;color:#e2e8f0;border-radius:8px;padding:8px 12px;font-size:.88rem}
.cfg-field input:focus,.cfg-field select:focus{outline:none;border-color:#6366f1}
.kw-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.kw-tag{background:#312e81;color:#a78bfa;border-radius:6px;padding:3px 10px;font-size:.8rem;cursor:pointer}
.kw-tag:hover{background:#4c1d95}
.kw-input-row{display:flex;gap:8px;margin-top:8px}
.kw-input-row input{flex:1;background:#0f1117;border:1px solid #2d3148;color:#e2e8f0;border-radius:8px;padding:7px 12px;font-size:.85rem}
.save-btn{background:#312e81;color:#a78bfa;border:none;border-radius:8px;padding:9px 20px;font-size:.85rem;cursor:pointer;margin-top:14px}
.save-btn:hover{background:#4338ca;color:#fff}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:100;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:#1a1d2e;border:1px solid #2d3148;border-radius:14px;padding:26px;width:330px}
.modal h3{margin-bottom:16px;font-size:.95rem}
.modal label{font-size:.78rem;color:#94a3b8;display:block;margin-bottom:4px}
.modal input{width:100%;background:#0f1117;border:1px solid #2d3148;color:#e2e8f0;border-radius:8px;padding:8px 12px;font-size:.88rem;margin-bottom:14px}
.modal-btns{display:flex;gap:8px;justify-content:flex-end}
footer{text-align:center;padding:20px;color:#334155;font-size:.72rem}
</style>
</head>
<body>
<header>
  <div class="dot" id="bot-dot"></div>
  <h1>⬡ PolyBot</h1>
  <span id="loop-badge" style="font-size:.72rem;background:#0f3026;color:#22c55e;border:1px solid #166534;border-radius:6px;padding:3px 10px;margin-left:4px">Auto ⟳ 10min</span>
  <nav>
    <button class="nav-btn active" onclick="showPage('trades',this)">Trades</button>
    <button class="nav-btn" onclick="showPage('run',this)">Executar</button>
    <button class="nav-btn" onclick="showPage('config',this)">Config</button>
    <button class="nav-btn" id="resolve-btn" onclick="checkResults()" style="border-color:#22c55e;color:#22c55e">✓ Verificar Resultados</button>
  </nav>
</header>

<main>
<!-- ── TRADES ── -->
<div class="page active" id="page-trades">
  <div class="stats">
    <div class="card"><label>Total Trades</label><div class="val purple" id="s-total">—</div></div>
    <div class="card"><label>Pendentes</label><div class="val yellow" id="s-pending">—</div></div>
    <div class="card"><label>Win Rate</label><div class="val green" id="s-winrate">—</div></div>
    <div class="card"><label>W / L</label><div class="val" id="s-wl">—</div></div>
    <div class="card"><label>USDC Simulado</label><div class="val purple" id="s-usdc">—</div></div>
    <div class="card"><label>PnL</label><div class="val" id="s-pnl">—</div></div>
  </div>
  <section>
    <h2>Histórico de Trades</h2>
    <table>
      <thead><tr><th>#</th><th>Data</th><th>Mercado</th><th>Side</th><th>USDC</th><th>Encerra em</th><th>Status</th><th>PnL</th><th>Link</th><th>Fontes</th><th></th></tr></thead>
      <tbody id="trades-body"><tr><td colspan="10" style="text-align:center;padding:32px;color:#475569">Carregando...</td></tr></tbody>
    </table>
  </section>
</div>

<!-- ── EXECUTAR ── -->
<div class="page" id="page-run">
  <section>
    <div class="run-header">
      <button class="btn btn-run" id="run-btn" onclick="runBot()">▶ Executar Bot</button>
      <span class="run-status" id="run-status"></span>
    </div>
    <h2>Log de Execução</h2>
    <div class="log-box" id="log-box">Aguardando execução...</div>
  </section>
</div>

<!-- ── CONFIG ── -->
<div class="page" id="page-config">
  <section>
    <h2>Configuração do Bot</h2>
    <div class="cfg-grid">
      <div class="cfg-field">
        <label>Modo</label>
        <select id="cfg-mode">
          <option value="keywords">keywords</option>
          <option value="all">all</option>
        </select>
      </div>
      <div class="cfg-field">
        <label>Máx. mercados por run</label>
        <input type="number" id="cfg-max" min="1" max="500"/>
      </div>
      <div class="cfg-field">
        <label>Horas mínimas até encerrar</label>
        <input type="number" id="cfg-minh" step="0.5" min="0"/>
      </div>
      <div class="cfg-field">
        <label>Horas máximas até encerrar</label>
        <input type="number" id="cfg-maxh" step="0.5" min="1"/>
      </div>
      <div class="cfg-field">
        <label>Liquidez mínima (USDC)</label>
        <input type="number" id="cfg-liq" min="0"/>
      </div>
      <div class="cfg-field">
        <label>Volume mínimo (USDC)</label>
        <input type="number" id="cfg-vol" min="0"/>
      </div>
    </div>
    <div style="margin-top:16px">
      <label style="font-size:.75rem;color:#64748b;display:block;margin-bottom:6px">Keywords (clique para remover)</label>
      <div class="kw-list" id="kw-list"></div>
      <div class="kw-input-row">
        <input id="kw-input" placeholder="Adicionar keyword (ex: UFC)" onkeydown="if(event.key==='Enter')addKw()"/>
        <button class="btn btn-sm" onclick="addKw()">+ Add</button>
      </div>
    </div>
    <button class="save-btn" onclick="saveConfig()">💾 Salvar Configuração</button>
    <span id="cfg-msg" style="margin-left:12px;font-size:.8rem;color:#22c55e"></span>
  </section>
</div>
</main>

<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3>Resolver Trade</h3>
    <label>PnL (USDC) — positivo se ganhou, negativo se perdeu</label>
    <input type="number" id="modal-pnl" step="0.01" value="0"/>
    <div class="modal-btns">
      <button class="btn btn-cancel" onclick="closeModal()">Cancelar</button>
      <button class="btn btn-lost" onclick="submitResolve(false)">✗ Perdeu</button>
      <button class="btn btn-won" onclick="submitResolve(true)">✓ Ganhou</button>
    </div>
  </div>
</div>

<footer>PolyBot · paper trading mode · localhost:5050</footer>

<script>
let currentIdx=null, cfgKeywords=[], logPoll=null;

function showPage(name,btn){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  btn.classList.add('active');
  if(name==='config') loadConfig();
  if(name==='run') startLogPoll();
  else stopLogPoll();
}

/* ── Stats & Trades ── */
function fmt(ts){return new Date(ts).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});}
function fmtEnd(ts){
  if(!ts||ts==='N/A') return '—';
  const h=Math.round((new Date(ts)-Date.now())/36e5);
  if(h<0) return '<span style="color:#475569">Enc.</span>';
  if(h<2) return `<span class="red">${h}h</span>`;
  if(h<6) return `<span class="yellow">${h}h</span>`;
  return h+'h';
}

async function loadStats(){
  const s=await fetch('/api/stats').then(r=>r.json());
  document.getElementById('s-total').textContent=s.total_trades;
  document.getElementById('s-pending').textContent=s.pending;
  document.getElementById('s-winrate').textContent=s.win_rate!=null?s.win_rate+'%':'—';
  document.getElementById('s-wl').innerHTML=`<span class="green">${s.won}W</span> / <span class="red">${s.lost}L</span>`;
  const pEl=document.getElementById('s-pnl');
  pEl.textContent='$'+s.pnl.toFixed(2); pEl.className='val '+(s.pnl>=0?'green':'red');
  document.getElementById('s-usdc').textContent='$'+s.total_usdc_deployed.toFixed(2);
  const dot=document.getElementById('bot-dot');
  dot.className='dot'+(s.bot_running?'':' off');
}

async function loadTrades(){
  const trades=await fetch('/api/trades').then(r=>r.json());
  const tb=document.getElementById('trades-body');
  if(!trades.length){tb.innerHTML='<tr><td colspan="10" style="text-align:center;padding:36px;color:#475569">Nenhuma trade ainda. Execute o bot!</td></tr>';return;}
  tb.innerHTML=[...trades].reverse().map((t,ri)=>{
    const idx=trades.length-1-ri;
    const sc=t.resolved?(t.won?'s-won':'s-lost'):'s-pending';
    const st=t.resolved?(t.won?'✓ Ganhou':'✗ Perdeu'):'⏳ Pendente';
    const pnl=t.pnl!=null?`<span class="${t.pnl>=0?'green':'red'}">$${t.pnl.toFixed(2)}</span>`:'—';
    const mkt=(t.market||'—').slice(0,52);
    const res=!t.resolved?`<button class="btn btn-sm" onclick="openModal(${idx})">Resolver</button>`:'—';
    return `<tr>
      <td style="color:#475569">${idx+1}</td>
      <td style="color:#94a3b8;white-space:nowrap">${fmt(t.timestamp)}</td>
      <td title="${t.market||''}">${mkt}${(t.market||'').length>52?'…':''}</td>
      <td class="${(t.side||'buy').toLowerCase()}">${t.side||'—'}</td>
      <td>$${(t.simulated_usdc_amount||0).toFixed(2)}</td>
      <td>${fmtEnd(t.end_date)}</td>
      <td class="${sc}">${st}</td>
      <td>${pnl}</td>
      <td>${t.market_url?`<a class="lnk" href="${t.market_url}" target="_blank">↗ Ver</a>`:'—'}</td>
      <td>${(t.search_sources&&t.search_sources.length)?t.search_sources.map((u,i)=>`<a class="lnk" href="${u}" target="_blank" title="${u}">[${i+1}]</a>`).join(' '):'<span style="color:#475569">—</span>'}</td>
      <td>${res}</td></tr>`;
  }).join('');
}

/* ── Run ── */
async function runBot(){
  const btn=document.getElementById('run-btn');
  const st=document.getElementById('run-status');
  btn.disabled=true; btn.textContent='⏳ Executando...';
  st.textContent='Bot em execução...';
  document.getElementById('log-box').textContent='';
  const r=await fetch('/api/run',{method:'POST'}).then(r=>r.json());
  st.textContent=r.msg;
  startLogPoll();
}

function startLogPoll(){
  stopLogPoll();
  logPoll=setInterval(async()=>{
    const d=await fetch('/api/run/log').then(r=>r.json());
    const box=document.getElementById('log-box');
    box.textContent=d.log.join('\n');
    box.scrollTop=box.scrollHeight;
    const btn=document.getElementById('run-btn');
    const st=document.getElementById('run-status');
    if(!d.running){
      btn.disabled=false; btn.textContent='▶ Executar Bot';
      st.textContent=d.log.length?'Concluído':'Aguardando';
      stopLogPoll();
      loadStats(); loadTrades();
    }
  },1500);
}
function stopLogPoll(){if(logPoll){clearInterval(logPoll);logPoll=null;}}

/* ── Config ── */
async function loadConfig(){
  const c=await fetch('/api/config').then(r=>r.json());
  document.getElementById('cfg-mode').value=c.mode||'keywords';
  document.getElementById('cfg-max').value=c.max_markets_per_run||5;
  document.getElementById('cfg-minh').value=c.min_hours||1;
  document.getElementById('cfg-maxh').value=c.max_hours||48;
  document.getElementById('cfg-liq').value=c.min_liquidity||0;
  document.getElementById('cfg-vol').value=c.min_volume||0;
  cfgKeywords=[...(c.keywords||[])];
  renderKws();
}
function renderKws(){
  document.getElementById('kw-list').innerHTML=cfgKeywords.map((k,i)=>
    `<span class="kw-tag" onclick="removeKw(${i})">${k} ✕</span>`).join('');
}
function addKw(){
  const inp=document.getElementById('kw-input');
  const v=inp.value.trim().toUpperCase();
  if(v&&!cfgKeywords.includes(v)){cfgKeywords.push(v);renderKws();}
  inp.value='';
}
function removeKw(i){cfgKeywords.splice(i,1);renderKws();}
async function saveConfig(){
  const body={
    mode:document.getElementById('cfg-mode').value,
    max_markets_per_run:parseInt(document.getElementById('cfg-max').value)||5,
    min_hours:parseFloat(document.getElementById('cfg-minh').value)||1,
    max_hours:parseFloat(document.getElementById('cfg-maxh').value)||48,
    min_liquidity:parseFloat(document.getElementById('cfg-liq').value)||0,
    min_volume:parseFloat(document.getElementById('cfg-vol').value)||0,
    keywords:cfgKeywords,
  };
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const msg=document.getElementById('cfg-msg');
  msg.textContent='✓ Salvo!'; setTimeout(()=>msg.textContent='',2000);
}

/* ── Modal ── */
function openModal(idx){currentIdx=idx;document.getElementById('modal-pnl').value='0';document.getElementById('modal').classList.add('open');}
function closeModal(){document.getElementById('modal').classList.remove('open');currentIdx=null;}
async function submitResolve(won){
  const pnl=parseFloat(document.getElementById('modal-pnl').value)||0;
  await fetch(`/api/trades/${currentIdx}/resolve?won=${won}&pnl=${pnl}`,{method:'PATCH'});
  closeModal(); loadStats(); loadTrades();
}

/* ── Auto-resolve ── */
async function checkResults(){
  const btn=document.getElementById('resolve-btn');
  btn.textContent='⏳ Verificando...'; btn.disabled=true;
  const r=await fetch('/api/auto-resolve',{method:'POST'}).then(r=>r.json());
  btn.disabled=false; btn.textContent='✓ Verificar Resultados';

  // Monta mensagem detalhada com log de cada trade
  let msg = r.updated>0
    ? `${r.updated} trade(s) resolvida(s)!\n`
    : r.skipped>0
      ? `Nenhuma nova resolucao (${r.skipped} aguardando)\n`
      : 'Nenhuma trade pendente\n';

  if(r.log && r.log.length){
    msg += '\nDetalhes:\n' + r.log.join('\n');
  }
  if(r.errors && r.errors.length){
    msg += '\n\nAvisos:\n' + r.errors.join('\n');
  }
  alert(msg);
  loadStats(); loadTrades();
}

/* ── Countdown loop ── */
let countdown = 600;
function updateCountdown(){
  const m=Math.floor(countdown/60), s=countdown%60;
  const badge=document.getElementById('loop-badge');
  if(badge) badge.textContent=`Auto ⟳ ${m}:${s.toString().padStart(2,'0')}`;
  if(countdown<=0){ countdown=600; loadStats(); loadTrades(); } else countdown--;
}
setInterval(updateCountdown, 1000);

/* ── Init ── */
async function init(){await Promise.all([loadStats(),loadTrades()]);}
init();
setInterval(()=>{if(document.getElementById('page-trades').classList.contains('active')){loadStats();loadTrades();}},30000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=HTML)


@app.on_event("startup")
def startup_event():
    threading.Thread(target=_background_resolve_loop, daemon=True).start()
    threading.Thread(target=_background_trading_loop, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5050)
