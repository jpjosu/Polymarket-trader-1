# Setup — Paper Trading com Gemini CLI (sem API key)

Este guia configura o bot em modo **paper trading** usando o **Gemini CLI via OAuth** —
sem precisar de API key, só uma conta Google.

---

## 1. Pré-requisitos

- Python 3.9+
- Node.js 18+ (para o Gemini CLI)

---

## 2. Instalar o Gemini CLI

```bash
npm install -g @google/gemini-cli
```

Autentique com sua conta Google (só precisa fazer uma vez):

```bash
gemini
```

Vai abrir o browser para login. Depois é só fechar — o token fica salvo.

Teste se funcionou:

```bash
gemini -p "Qual a probabilidade de chover amanhã?"
```

---

## 3. Configurar o projeto Python

```bash
cd E:\BOTAGENTICPOLY

# Criar e ativar ambiente virtual
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/Mac

# Instalar dependências
pip install -r requirements.txt

# Criar arquivo .env
cp .env.example .env
```

Abra o `.env` e preencha o que quiser (tudo é opcional para paper trading).

---

## 4. Rodar

Defina o PYTHONPATH antes de qualquer comando:

```bash
set PYTHONPATH=.          # Windows CMD
$env:PYTHONPATH="."       # Windows PowerShell
export PYTHONPATH="."     # Linux/Mac
```

### Comandos disponíveis

| Comando | O que faz |
|---|---|
| `python scripts/python/cli.py run-autonomous-trader` | Roda o trader em paper mode |
| `python scripts/python/cli.py paper-trade-log` | Exibe histórico de trades simuladas |
| `python scripts/python/cli.py get-all-markets` | Lista os mercados ativos |
| `python scripts/python/cli.py get-all-events` | Lista os eventos ativos |
| `python scripts/python/cli.py ask-llm "pergunta"` | Pergunta direta ao Gemini |
| `python scripts/python/cli.py ask-superforecaster "evento" "pergunta" "yes"` | Pede uma previsão de probabilidade |

### Exemplo de uso completo

```bash
# Rodar uma rodada de paper trading
python scripts/python/cli.py run-autonomous-trader

# Ver o que foi registrado
python scripts/python/cli.py paper-trade-log

# Perguntar sobre mercados ao Gemini
python scripts/python/cli.py ask-polymarket-llm "quais mercados de politica estao abertos?"
```

---

## 5. Onde ficam os logs de paper trading

Todas as trades simuladas são salvas em:

```
paper_trades.json
```

Cada entrada tem: timestamp, nome do mercado, decisão do Gemini (preço/tamanho/direção)
e o valor USDC simulado (começa com $1000).

---

## 6. Quando quiser ir para live trading

1. Adicione sua `POLYGON_WALLET_PRIVATE_KEY` no `.env`
2. Leia os Termos de Serviço: https://polymarket.com/tos
3. No `trade.py`, descomente a linha `execute_market_order`
4. Rode com: `python scripts/python/cli.py run-autonomous-trader --no-paper`
