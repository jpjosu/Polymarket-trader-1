import json
import os
import time

from langchain_core.documents import Document
from langchain_community.vectorstores.chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

from agents.polymarket.gamma import GammaMarketClient
from agents.utils.objects import SimpleEvent, SimpleMarket

# Embeddings locais — sem API key, roda 100% offline
# Modelo leve (~90MB), baixa uma vez e fica em cache
_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def get_embedding_function():
    return HuggingFaceEmbeddings(model_name=_EMBEDDING_MODEL)


def _safe_str(val) -> str:
    """Converte qualquer valor para string segura para metadados do Chroma."""
    if val is None:
        return ""
    return str(val)


class PolymarketRAG:
    def __init__(self, local_db_directory=None, embedding_function=None) -> None:
        self.gamma_client = GammaMarketClient()
        self.local_db_directory = local_db_directory
        self.embedding_function = embedding_function or get_embedding_function()

    def query_local_markets_rag(
        self, local_directory=None, query=None
    ) -> "list[tuple]":
        local_db = Chroma(
            persist_directory=local_directory,
            embedding_function=self.embedding_function,
        )
        response_docs = local_db.similarity_search_with_score(query=query)
        return response_docs

    def events(self, events: "list[SimpleEvent]", prompt: str) -> "list[tuple]":
        """Constrói vector DB de eventos e retorna os mais similares ao prompt."""
        docs = []
        for x in events:
            d = x.dict() if hasattr(x, "dict") else dict(x)
            content = d.get("description") or d.get("title") or "No description"
            metadata = {
                "id":      _safe_str(d.get("id")),
                "markets": _safe_str(d.get("markets")),
                "slug":    _safe_str(d.get("slug")),
            }
            docs.append(Document(page_content=content, metadata=metadata))

        if not docs:
            return []

        local_db = Chroma.from_documents(
            docs,
            self.embedding_function,
            persist_directory="./local_db_events/chroma",
        )
        return local_db.similarity_search_with_score(query=prompt)

    def markets(self, markets: "list[SimpleMarket]", prompt: str) -> "list[tuple]":
        """Constrói vector DB de mercados e retorna os mais similares ao prompt."""
        docs = []
        for m in markets:
            d = m if isinstance(m, dict) else m.dict()
            content = d.get("description") or d.get("question") or "No description"
            metadata = {
                "id":            _safe_str(d.get("id")),
                "outcomes":      _safe_str(d.get("outcomes")),
                "outcome_prices":_safe_str(d.get("outcome_prices")),
                "question":      _safe_str(d.get("question")),
                "clob_token_ids":_safe_str(d.get("clob_token_ids")),
                "slug":          _safe_str(d.get("slug")),
                "end":           _safe_str(d.get("end")),
                "event_id":      _safe_str(d.get("event_id")),
                "event_title":   _safe_str(d.get("event_title")),
                "event_slug":    _safe_str(d.get("event_slug")),
            }
            docs.append(Document(page_content=content, metadata=metadata))

        if not docs:
            return []

        local_db = Chroma.from_documents(
            docs,
            self.embedding_function,
            persist_directory="./local_db_markets/chroma",
        )
        return local_db.similarity_search_with_score(query=prompt)
