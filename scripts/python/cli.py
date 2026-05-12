import typer
from devtools import pprint

from agents.polymarket.polymarket import Polymarket
from agents.connectors.chroma import PolymarketRAG
from agents.connectors.news import News
from agents.application.trade import Trader
from agents.application.executor import Executor
from agents.application.creator import Creator

app = typer.Typer()
polymarket = Polymarket(paper_trade=True)
newsapi_client = News()
polymarket_rag = PolymarketRAG()


@app.command()
def get_all_markets(limit: int = 5, sort_by: str = "spread") -> None:
    """
    Query Polymarket's markets
    """
    print(f"limit: int = {limit}, sort_by: str = {sort_by}")
    markets = polymarket.get_all_markets()
    markets = polymarket.filter_markets_for_trading(markets)
    if sort_by == "spread":
        markets = sorted(markets, key=lambda x: x.spread, reverse=True)
    markets = markets[:limit]
    pprint(markets)


@app.command()
def get_relevant_news(keywords: str) -> None:
    """
    Use NewsAPI to query the internet
    """
    articles = newsapi_client.get_articles_for_cli_keywords(keywords)
    pprint(articles)


@app.command()
def get_all_events(limit: int = 5, sort_by: str = "number_of_markets") -> None:
    """
    Query Polymarket's events
    """
    print(f"limit: int = {limit}, sort_by: str = {sort_by}")
    events = polymarket.get_all_events()
    events = polymarket.filter_events_for_trading(events)
    if sort_by == "number_of_markets":
        events = sorted(events, key=lambda x: len(x.markets), reverse=True)
    events = events[:limit]
    pprint(events)


@app.command()
def create_local_markets_rag(local_directory: str) -> None:
    """
    Create a local markets database for RAG
    """
    polymarket_rag.create_local_markets_rag(local_directory=local_directory)


@app.command()
def query_local_markets_rag(vector_db_directory: str, query: str) -> None:
    """
    RAG over a local database of Polymarket's events
    """
    response = polymarket_rag.query_local_markets_rag(
        local_directory=vector_db_directory, query=query
    )
    pprint(response)


@app.command()
def ask_superforecaster(event_title: str, market_question: str, outcome: str) -> None:
    """
    Ask a superforecaster about a trade
    """
    print(
        f"event: str = {event_title}, question: str = {market_question}, outcome (usually yes or no): str = {outcome}"
    )
    executor = Executor(paper_trade=True)
    response = executor.get_superforecast(
        event_title=event_title, market_question=market_question, outcome=outcome
    )
    print(f"Response:{response}")


@app.command()
def create_market() -> None:
    """
    Format a request to create a market on Polymarket
    """
    c = Creator()
    market_description = c.one_best_market()
    print(f"market_description: str = {market_description}")


@app.command()
def ask_llm(user_input: str) -> None:
    """
    Ask a question to the Gemini CLI (OAuth, no API key needed).
    """
    executor = Executor(paper_trade=True)
    response = executor.get_llm_response(user_input)
    print(f"LLM Response: {response}")


@app.command()
def ask_polymarket_llm(user_input: str) -> None:
    """
    Ask Gemini about current Polymarket markets and events.
    """
    executor = Executor(paper_trade=True)
    response = executor.get_polymarket_llm(user_input=user_input)
    print(f"LLM + current markets&events response: {response}")


@app.command()
def run_autonomous_trader(paper: bool = True) -> None:
    """
    Roda o trader autônomo com Gemini CLI (OAuth).
    Use --no-paper para modo live (requer carteira Polygon).
    """
    mode = "PAPER" if paper else "LIVE"
    print(f"\n>>> Iniciando trader em modo {mode}... <<<\n")
    trader = Trader(paper_trade=paper)
    trader.one_best_trade()


@app.command()
def paper_trade_log() -> None:
    """
    Exibe o histórico de trades simuladas.
    """
    import json
    from pathlib import Path
    log_file = Path("paper_trades.json")
    if not log_file.exists():
        print("Nenhuma trade registrada ainda. Rode: run-autonomous-trader")
        return
    with open(log_file) as f:
        trades = json.load(f)
    print(f"\n📋 {len(trades)} trade(s) simulada(s):\n")
    for i, t in enumerate(trades, 1):
        print(f"[{i}] {t['timestamp']}")
        print(f"    Mercado : {t['market']}")
        print(f"    Trade   : {t['trade_output'][:120]}...")
        print(f"    USDC    : ${t['simulated_usdc_amount']}\n")


if __name__ == "__main__":
    app()
