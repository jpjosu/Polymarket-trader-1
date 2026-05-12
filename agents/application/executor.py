import os
import json
import ast
import re
import subprocess
from typing import List, Dict, Any

import math

from dotenv import load_dotenv

from agents.polymarket.gamma import GammaMarketClient as Gamma
from agents.connectors.chroma import PolymarketRAG as Chroma
from agents.utils.objects import SimpleEvent, SimpleMarket
from agents.application.prompts import Prompter
from agents.polymarket.polymarket import Polymarket


# ---------------------------------------------------------------------------
# Gemini CLI wrapper — autentica via OAuth (sem API key)
# Instalar: npm install -g @google/gemini-cli
# Autenticar: gemini   (na primeira vez abre o browser para login Google)
# ---------------------------------------------------------------------------

class _GeminiResponse:
    """Mimics LangChain message response interface."""
    def __init__(self, content: str):
        self.content = content


class GeminiCLI:
    """
    Wrapper que chama o Gemini CLI via subprocess.
    Substitui ChatOpenAI sem precisar de API key — usa OAuth do Google.
    """
    def __init__(self, model: str = "gemini-2.5-pro", timeout: int = 600):
        self.model = model
        self.timeout = timeout

    def invoke(self, prompt) -> _GeminiResponse:
        # Aceita tanto string quanto lista de mensagens LangChain
        if isinstance(prompt, list):
            parts = []
            for msg in prompt:
                role = getattr(msg, "type", "human")
                parts.append(f"[{role.upper()}]: {msg.content}")
            full_prompt = "\n\n".join(parts)
        else:
            full_prompt = str(prompt)

        # Localiza o gemini CLI — tenta o PATH primeiro, depois o caminho padrão do npm no Windows
        import shutil
        gemini_cmd = shutil.which("gemini") or shutil.which("gemini.cmd")
        if not gemini_cmd:
            npm_path = os.path.expandvars(r"%APPDATA%\npm\gemini.cmd")
            if os.path.exists(npm_path):
                gemini_cmd = npm_path
        if not gemini_cmd:
            raise RuntimeError(
                "Gemini CLI não encontrado. Instale com: npm install -g @google/gemini-cli\n"
                "Depois autentique com: gemini"
            )

        # Caminho para o arquivo temporário de prompt
        temp_prompt_path = os.path.join(os.getcwd(), "gemini_prompt_temp.txt")
        
        try:
            # Escreve o prompt no arquivo temporário com encoding UTF-8
            with open(temp_prompt_path, "w", encoding="utf-8") as f:
                f.write(full_prompt)
            
            # No Windows, usamos 'type' para pipear o arquivo para o gemini
            # Isso é o mais robusto para textos grandes
            cmd = f'type "{temp_prompt_path}" | "{gemini_cmd}" --yolo'
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=True,
                encoding="utf-8",
                errors="replace",
            )
            
            output = result.stdout.strip()
            
            # Limpa o arquivo temporário após o uso
            if os.path.exists(temp_prompt_path):
                os.remove(temp_prompt_path)
                
            if result.returncode != 0 and not output:
                error_msg = result.stderr.strip()
                raise RuntimeError(f"Gemini CLI error (code {result.returncode}): {error_msg}")
            return _GeminiResponse(output)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Gemini CLI timeout após {self.timeout}s")


def retain_keys(data, keys_to_retain):
    if isinstance(data, dict):
        return {
            key: retain_keys(value, keys_to_retain)
            for key, value in data.items()
            if key in keys_to_retain
        }
    elif isinstance(data, list):
        return [retain_keys(item, keys_to_retain) for item in data]
    else:
        return data


class Executor:
    def __init__(self, paper_trade: bool = True) -> None:
        load_dotenv()
        # Token limit generoso para o Gemini 2.5 Pro (1M tokens de contexto)
        self.token_limit = 800_000
        self.prompter = Prompter()
        self.llm = GeminiCLI()
        self.gamma = Gamma()
        self.chroma = Chroma()
        self.polymarket = Polymarket(paper_trade=paper_trade)

    def get_llm_response(self, user_input: str) -> str:
        prompt = f"{self.prompter.market_analyst()}\n\n{user_input}"
        result = self.llm.invoke(prompt)
        return result.content

    def get_superforecast(
        self, event_title: str, market_question: str, outcome: str
    ) -> str:
        prompt = self.prompter.superforecaster(
            description=event_title, question=market_question, outcome=outcome
        )
        result = self.llm.invoke(prompt)
        return result.content


    def estimate_tokens(self, text: str) -> int:
        # This is a rough estimate. For more accurate results, consider using a tokenizer.
        return len(text) // 4  # Assuming average of 4 characters per token

    def process_data_chunk(self, data1: List[Dict[Any, Any]], data2: List[Dict[Any, Any]], user_input: str) -> str:
        prompt = f"{self.prompter.prompts_polymarket(data1=data1, data2=data2)}\n\n{user_input}"
        result = self.llm.invoke(prompt)
        return result.content


    def divide_list(self, original_list, i):
        # Calculate the size of each sublist
        sublist_size = math.ceil(len(original_list) / i)
        
        # Use list comprehension to create sublists
        return [original_list[j:j+sublist_size] for j in range(0, len(original_list), sublist_size)]
    
    def get_polymarket_llm(self, user_input: str) -> str:
        data1 = self.gamma.get_current_events()
        data2 = self.gamma.get_current_markets()
        
        combined_data = str(self.prompter.prompts_polymarket(data1=data1, data2=data2))
        
        # Estimate total tokens
        total_tokens = self.estimate_tokens(combined_data)
        
        # Set a token limit (adjust as needed, leaving room for system and user messages)
        token_limit = self.token_limit
        if total_tokens <= token_limit:
            # If within limit, process normally
            return self.process_data_chunk(data1, data2, user_input)
        else:
            # If exceeding limit, process in chunks
            chunk_size = len(combined_data) // ((total_tokens // token_limit) + 1)
            print(f'total tokens {total_tokens} exceeding llm capacity, now will split and answer')
            group_size = (total_tokens // token_limit) + 1 # 3 is safe factor
            keys_no_meaning = ['image','pagerDutyNotificationEnabled','resolvedBy','endDate','clobTokenIds','negRiskMarketID','conditionId','updatedAt','startDate']
            useful_keys = ['id','questionID','description','liquidity','clobTokenIds','outcomes','outcomePrices','volume','startDate','endDate','question','questionID','events']
            data1 = retain_keys(data1, useful_keys)
            cut_1 = self.divide_list(data1, group_size)
            cut_2 = self.divide_list(data2, group_size)
            cut_data_12 = zip(cut_1, cut_2)

            results = []

            for cut_data in cut_data_12:
                sub_data1 = cut_data[0]
                sub_data2 = cut_data[1]
                sub_tokens = self.estimate_tokens(str(self.prompter.prompts_polymarket(data1=sub_data1, data2=sub_data2)))

                result = self.process_data_chunk(sub_data1, sub_data2, user_input)
                results.append(result)
            
            combined_result = " ".join(results)
            
        
            
            return combined_result
    def filter_events(self, events: "list[SimpleEvent]") -> str:
        prompt = self.prompter.filter_events(events)
        result = self.llm.invoke(prompt)
        return result.content

    def filter_events_with_rag(self, events: "list[SimpleEvent]") -> str:
        prompt = self.prompter.filter_events()
        print()
        print("... prompting ... ", prompt)
        print()
        return self.chroma.events(events, prompt)

    def map_filtered_events_to_markets(
        self, filtered_events: "list[SimpleEvent]"
    ) -> "list[SimpleMarket]":
        markets = []
        for e in filtered_events:
            data = json.loads(e[0].json())
            market_ids = data["metadata"]["markets"].split(",")
            for market_id in market_ids:
                market_data = self.gamma.get_market(market_id)
                formatted_market_data = self.polymarket.map_api_to_market(market_data)
                markets.append(formatted_market_data)
        # Aplica janela temporal nos mercados individuais (não nos eventos)
        markets = self.polymarket.filter_markets_by_end_window(markets)
        return markets

    def filter_markets(self, markets) -> "list[tuple]":
        prompt = self.prompter.filter_markets()
        print()
        print("... prompting ... ", prompt)
        print()
        return self.chroma.markets(markets, prompt)

    def filter_markets_direct(self, raw_markets: "list[dict]") -> "list[tuple]":
        """RAG sobre lista de dicts vinda direto do endpoint /markets (sem passar por eventos)."""
        mapped = [self.polymarket.map_api_to_market(m) for m in raw_markets]
        prompt = self.prompter.filter_markets()
        return self.chroma.markets(mapped, prompt)

    @staticmethod
    def _extract_urls(text: str) -> list:
        """Extrai todas as URLs do texto retornado pelo Gemini."""
        url_pattern = re.compile(
            r'https?://[^\s\)\]\'"<>]+',
            re.IGNORECASE
        )
        urls = url_pattern.findall(text)
        # Remove duplicatas preservando ordem
        seen = set()
        unique = []
        for u in urls:
            u = u.rstrip(".,;:")
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    def source_best_trade(self, market_object) -> str:
        market_document = market_object[0].dict()
        market = market_document["metadata"]
        outcome_prices = ast.literal_eval(market["outcome_prices"])
        outcomes = ast.literal_eval(market["outcomes"])
        question = market["question"]
        description = market_document["page_content"]

        prompt = self.prompter.superforecaster(question, description, outcomes)
        print(f"[Analisando] {question[:80]}")
        result = self.llm.invoke(prompt)
        forecast = result.content
        print(f"[Forecast] {forecast[:300]}")

        # Coleta URLs da etapa de analise (pesquisas Google do Gemini)
        all_urls = self._extract_urls(forecast)

        # Respeita decisao SKIP do Gemini — nao entra em mercados sem edge claro
        if "decision: SKIP" in forecast or "decision:SKIP" in forecast:
            print("[SKIP] Gemini decidiu nao entrar nesse mercado.")
            return "SKIP"

        # Confianca baixa tambem pula
        if "confidence: LOW" in forecast or "confidence:LOW" in forecast:
            print("[SKIP] Confianca baixa, pulando.")
            return "SKIP"

        prompt = self.prompter.one_best_trade(forecast, outcomes, outcome_prices)
        result = self.llm.invoke(prompt)
        content = result.content
        print(f"[Trade] {content[:200]}")

        # Coleta URLs da etapa de sizing tambem
        all_urls += self._extract_urls(content)
        all_urls = list(dict.fromkeys(all_urls))  # dedup final

        if all_urls:
            print(f"[Fontes] {len(all_urls)} link(s) encontrado(s):")
            for u in all_urls[:5]:
                print(f"   - {u}")
            # Anexa as fontes ao final do output para trade.py capturar
            content += f"\n\n[SEARCH_SOURCES]: {' | '.join(all_urls)}"

        return content

    def format_trade_prompt_for_execution(self, best_trade: str) -> float:
        MAX_TRADE_USDC = 100.0

        data = best_trade.split(",")
        size_matches = re.findall(r"\d+\.?\d*", data[1]) if len(data) > 1 else []
        raw_size = float(size_matches[0]) if size_matches else 0.05
        size = min(raw_size, 0.10)

        price_match = re.search(r"price['\"]?\s*:\s*([0-9.]+)", best_trade, re.IGNORECASE)
        price = float(price_match.group(1)) if price_match else 0.5

        distance = abs(price - 0.5)

        if distance < 0.05:
            confidence_cap = 25.0
        elif distance < 0.15:
            confidence_cap = 50.0
        elif distance < 0.25:
            confidence_cap = 75.0
        else:
            confidence_cap = MAX_TRADE_USDC

        effective_cap = min(MAX_TRADE_USDC, confidence_cap)
        usdc_balance = self.polymarket.get_usdc_balance()
        amount = min(size * usdc_balance, effective_cap)

        print(f"   Sizing: price={price:.2f} distance={distance:.2f} cap=${effective_cap:.0f} -> ${amount:.2f}")
        return round(amount, 2)

    def source_best_market_to_create(self, filtered_markets) -> str:
        prompt = self.prompter.create_new_market(filtered_markets)
        print()
        print("... prompting ... ", prompt)
        print()
        result = self.llm.invoke(prompt)
        content = result.content
        return content
