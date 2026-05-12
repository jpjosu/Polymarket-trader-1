import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from agents.application.executor import Executor as Agent
from agents.polymarket.gamma import GammaMarketClient as Gamma
from agents.polymarket.polymarket import Polymarket

PAPER_TRADE_LOG = Path("paper_trades.json")


def _load_paper_log() -> list:
    if PAPER_TRADE_LOG.exists():
        try:
            with open(PAPER_TRADE_LOG, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            # Arquivo corrompido — faz backup e recomeça do zero
            PAPER_TRADE_LOG.rename(PAPER_TRADE_LOG.with_suffix(".corrupted.json"))
            print(f"[AVISO] paper_trades.json corrompido, backup criado.")
    return []


def _save_paper_trade(entry: dict) -> None:
    log = _load_paper_log()
    log.append(entry)
    # Escreve em arquivo temporario e renomeia (escrita atomica — evita truncamento)
    tmp = PAPER_TRADE_LOG.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    tmp.replace(PAPER_TRADE_LOG)
    print(f"\n[PAPER TRADE LOGGED] -> {PAPER_TRADE_LOG.resolve()}")


class Trader:
    def __init__(self, paper_trade: bool = True):
        self.paper_trade = paper_trade
        self.polymarket = Polymarket(paper_trade=paper_trade)
        self.gamma = Gamma()
        self.agent = Agent(paper_trade=paper_trade)

    def pre_trade_logic(self) -> None:
        self.clear_local_dbs()

    def clear_local_dbs(self) -> None:
        for db in ("local_db_events", "local_db_markets"):
            try:
                shutil.rmtree(db)
            except Exception:
                pass

    def one_best_trade(self) -> None:
        """
        Avalia todos os eventos e mercados disponíveis no Polymarket,
        usa o Gemini via CLI para selecionar e dimensionar a melhor trade,
        e registra em paper_trades.json (sem executar ordem real).
        """
        try:
            self.pre_trade_logic()

            import json as _json
            try:
                with open("focus_config.json") as _f:
                    _cfg = _json.load(_f)
                max_mkts = int(_cfg.get("max_markets_per_run", 5))
            except Exception:
                max_mkts = 5

            # ── Pipeline principal: busca mercados diretamente por janela de endDate ──
            raw_markets = self.polymarket.get_markets_closing_soon()
            print(f"1. FOUND {len(raw_markets)} MARKETS (closing soon, direct query)")

            if not raw_markets:
                print("Nenhum mercado encontrado na janela temporal. Ajuste min_hours/max_hours no Foco de Mercado.")
                return

            filtered_markets = self.agent.filter_markets_direct(raw_markets)
            print(f"2. FILTERED {len(filtered_markets)} MARKETS (RAG)")

            if not filtered_markets:
                print("Nenhum mercado passou pelo filtro RAG.")
                return

            filtered_markets = filtered_markets[:max_mkts]
            print(f"3. PROCESSANDO {len(filtered_markets)} MERCADO(S)...")

            # Carrega IDs de mercados ja posicionados (pendentes ou ganhos)
            existing_log = _load_paper_log()
            open_market_ids = {
                str(t.get("market_id", ""))
                for t in existing_log
                if not t.get("resolved") or t.get("won")
            }
            print(f"   Mercados ja posicionados: {len(open_market_ids)}")

            trades_executadas = 0
            for i, market in enumerate(filtered_markets):
                print(f"\n--- Trade {i+1}/{len(filtered_markets)} ---")

                # Deduplicacao: pula se ja tem posicao aberta nesse mercado
                market_doc = market[0].dict()
                market_id = str(market_doc.get("metadata", {}).get("id", ""))
                if market_id and market_id in open_market_ids:
                    print(f"   [SKIP] Ja posicionado nesse mercado (id={market_id}).")
                    continue

                # Verifica saldo antes de cada trade
                saldo = self.polymarket.get_usdc_balance()
                if saldo < 1.0:
                    print(f"   Saldo insuficiente (${saldo:.2f}). Encerrando.")
                    break

                best_trade = self.agent.source_best_trade(market)

                # Gemini decidiu pular esse mercado (sem edge, info insuficiente, etc.)
                if best_trade == "SKIP" or not best_trade:
                    print(f"   [SKIP] Sem edge identificado, proximo mercado.")
                    continue

                print(f"   TRADE CALCULADA: {best_trade[:120]}")

                amount = self.agent.format_trade_prompt_for_execution(best_trade)

                if amount <= 0:
                    print(f"   Tamanho de trade inválido (${amount}). Pulando.")
                    continue

                if self.paper_trade:
                    import re as _re
                    market_doc = market[0].dict()
                    metadata = market_doc.get("metadata", {})

                    trade_upper = best_trade.upper()
                    side = "SELL" if "SELL" in trade_upper else "BUY"

                    # Extrai outcome (YES/NO) e entry_price do output do Gemini
                    _pm = _re.search(r"price[\w\s]*:?\s*([0-9.]+)", best_trade, _re.IGNORECASE)
                    entry_price = float(_pm.group(1)) if _pm else 0.5
                    _om = _re.search(r"\b(YES|NO)\b", best_trade, _re.IGNORECASE)
                    outcome = _om.group(1).upper() if _om else ("YES" if entry_price >= 0.5 else "NO")

                    event_slug = metadata.get("event_slug")
                    market_slug = metadata.get("slug")
                    if event_slug:
                        market_url = f"https://polymarket.com/event/{event_slug}"
                    elif market_slug:
                        market_url = f"https://polymarket.com/event/{market_slug}"
                    else:
                        question_encoded = metadata.get("question", "").replace(" ", "+")[:100]
                        market_url = f"https://polymarket.com/markets?search={question_encoded}"

                    # Extrai links de pesquisa que o Gemini usou (prova de busca na internet)
                    _src_match = _re.search(r'\[SEARCH_SOURCES\]:\s*(.+)', best_trade, _re.DOTALL)
                    search_sources = []
                    if _src_match:
                        search_sources = [u.strip() for u in _src_match.group(1).split(" | ") if u.strip()]
                        print(f"   [Fontes salvas] {len(search_sources)} link(s) de pesquisa")

                    entry = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "mode": "paper",
                        "event": metadata.get("event_title", "N/A"),
                        "market": metadata.get("question", "N/A"),
                        "market_id": str(metadata.get("id", "")),
                        "market_url": market_url,
                        "side": side,
                        "outcome": outcome,
                        "entry_price": round(entry_price, 4),
                        "end_date": metadata.get("end", "N/A"),
                        "trade_output": best_trade,
                        "search_sources": search_sources,
                        "simulated_usdc_amount": round(amount, 4),
                        "resolved": False,
                        "won": None,
                        "pnl": None,
                    }
                    _save_paper_trade(entry)
                    trades_executadas += 1
                    print(f"   [PAPER] {side} ${amount:.2f} USDC -> {metadata.get('question','?')[:60]}")
                else:
                    # Descomente abaixo após revisar os ToS: polymarket.com/tos
                    # trade = self.polymarket.execute_market_order(market, amount)
                    # print(f"   TRADED {trade}")
                    print("   [LIVE] Execução real desabilitada. Descomente execute_market_order após revisar os ToS.")
                    trades_executadas += 1

            print(f"\n4. CONCLUÍDO — {trades_executadas}/{len(filtered_markets)} trade(s) registrada(s)")

        except Exception as e:
            import traceback
            print(f"Erro: {e}")
            traceback.print_exc()
            print("\nRodada encerrada com erro. Verifique o log acima.")

    def maintain_positions(self):
        pass

    def incentive_farm(self):
        pass


if __name__ == "__main__":
    t = Trader(paper_trade=True)
    t.one_best_trade()
