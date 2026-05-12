# core polymarket api
# https://github.com/Polymarket/py-clob-client/tree/main/examples

import os
import pdb
import time
import ast
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

from web3 import Web3
from web3.constants import MAX_INT
# web3 v7 renomeou geth_poa_middleware para ExtraDataToPOAMiddleware
try:
    from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
except ImportError:
    from web3.middleware import geth_poa_middleware

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.constants import AMOY, POLYGON
from py_order_utils.builders import OrderBuilder
from py_order_utils.model import OrderData
from py_order_utils.signer import Signer
from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    OrderType,
    OrderBookSummary,
)
from py_clob_client.order_builder.constants import BUY

from agents.utils.objects import SimpleMarket, SimpleEvent

load_dotenv()


def _hours_until_end(end_str: str) -> float | None:
    """Retorna quantas horas faltam até end_str, ou None se não conseguir parsear."""
    if not end_str:
        return None
    try:
        normalized = end_str.replace("Z", "+00:00")
        end_dt = datetime.fromisoformat(normalized)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        diff = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return diff
    except Exception:
        return None


class Polymarket:
    def __init__(self, paper_trade: bool = True) -> None:
        self.paper_trade = paper_trade
        self.paper_balance = 1000.0  # USDC simulado para paper trading
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.gamma_markets_endpoint = self.gamma_url + "/markets"
        self.gamma_events_endpoint = self.gamma_url + "/events"

        self.clob_url = "https://clob.polymarket.com"
        self.clob_auth_endpoint = self.clob_url + "/auth/api-key"

        self.chain_id = 137  # POLYGON
        self.private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
        self.polygon_rpc = "https://polygon-rpc.com"
        self.w3 = Web3(Web3.HTTPProvider(self.polygon_rpc))

        self.exchange_address = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
        self.neg_risk_exchange_address = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

        self.erc20_approve = """[{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"owner","type":"address"},{"indexed":true,"internalType":"address","name":"spender","type":"address"},{"indexed":false,"internalType":"uint256","name":"value","type":"uint256"}],"name":"Approval","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"authorizer","type":"address"},{"indexed":true,"internalType":"bytes32","name":"nonce","type":"bytes32"}],"name":"AuthorizationCanceled","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"authorizer","type":"address"},{"indexed":true,"internalType":"bytes32","name":"nonce","type":"bytes32"}],"name":"AuthorizationUsed","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"account","type":"address"}],"name":"Blacklisted","type":"event"},{"anonymous":false,"inputs":[{"indexed":false,"internalType":"address","name":"userAddress","type":"address"},{"indexed":false,"internalType":"address payable","name":"relayerAddress","type":"address"},{"indexed":false,"internalType":"bytes","name":"functionSignature","type":"bytes"}],"name":"MetaTransactionExecuted","type":"event"},{"anonymous":false,"inputs":[],"name":"Pause","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"newRescuer","type":"address"}],"name":"RescuerChanged","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"bytes32","name":"role","type":"bytes32"},{"indexed":true,"internalType":"bytes32","name":"previousAdminRole","type":"bytes32"},{"indexed":true,"internalType":"bytes32","name":"newAdminRole","type":"bytes32"}],"name":"RoleAdminChanged","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"bytes32","name":"role","type":"bytes32"},{"indexed":true,"internalType":"address","name":"account","type":"address"},{"indexed":true,"internalType":"address","name":"sender","type":"address"}],"name":"RoleGranted","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"bytes32","name":"role","type":"bytes32"},{"indexed":true,"internalType":"address","name":"account","type":"address"},{"indexed":true,"internalType":"address","name":"sender","type":"address"}],"name":"RoleRevoked","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"from","type":"address"},{"indexed":true,"internalType":"address","name":"to","type":"address"},{"indexed":false,"internalType":"uint256","name":"value","type":"uint256"}],"name":"Transfer","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"account","type":"address"}],"name":"UnBlacklisted","type":"event"},{"anonymous":false,"inputs":[],"name":"Unpause","type":"event"},{"inputs":[],"name":"APPROVE_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"BLACKLISTER_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"CANCEL_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DECREASE_ALLOWANCE_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DEFAULT_ADMIN_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DEPOSITOR_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"DOMAIN_SEPARATOR","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"EIP712_VERSION","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"INCREASE_ALLOWANCE_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"META_TRANSACTION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"PAUSER_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"PERMIT_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"RESCUER_ROLE","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"TRANSFER_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"WITHDRAW_WITH_AUTHORIZATION_TYPEHASH","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"}],"name":"allowance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"approveWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"authorizer","type":"address"},{"internalType":"bytes32","name":"nonce","type":"bytes32"}],"name":"authorizationState","outputs":[{"internalType":"enum GasAbstraction.AuthorizationState","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"blacklist","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"blacklisters","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"authorizer","type":"address"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"cancelAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"subtractedValue","type":"uint256"}],"name":"decreaseAllowance","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"decrement","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"decreaseAllowanceWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"user","type":"address"},{"internalType":"bytes","name":"depositData","type":"bytes"}],"name":"deposit","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"userAddress","type":"address"},{"internalType":"bytes","name":"functionSignature","type":"bytes"},{"internalType":"bytes32","name":"sigR","type":"bytes32"},{"internalType":"bytes32","name":"sigS","type":"bytes32"},{"internalType":"uint8","name":"sigV","type":"uint8"}],"name":"executeMetaTransaction","outputs":[{"internalType":"bytes","name":"","type":"bytes"}],"stateMutability":"payable","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"}],"name":"getRoleAdmin","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"uint256","name":"index","type":"uint256"}],"name":"getRoleMember","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"}],"name":"getRoleMemberCount","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"grantRole","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"hasRole","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"addedValue","type":"uint256"}],"name":"increaseAllowance","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"increment","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"increaseAllowanceWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"string","name":"newName","type":"string"},{"internalType":"string","name":"newSymbol","type":"string"},{"internalType":"uint8","name":"newDecimals","type":"uint8"},{"internalType":"address","name":"childChainManager","type":"address"}],"name":"initialize","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"initialized","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"isBlacklisted","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"name","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"}],"name":"nonces","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"pause","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"paused","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"pausers","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"deadline","type":"uint256"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"permit","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"renounceRole","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"contract IERC20","name":"tokenContract","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"rescueERC20","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"rescuers","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"revokeRole","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"symbol","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"totalSupply","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"sender","type":"address"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"transferFrom","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"from","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"transferWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"unBlacklist","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"unpause","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"string","name":"newName","type":"string"},{"internalType":"string","name":"newSymbol","type":"string"}],"name":"updateMetadata","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"owner","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"uint256","name":"validAfter","type":"uint256"},{"internalType":"uint256","name":"validBefore","type":"uint256"},{"internalType":"bytes32","name":"nonce","type":"bytes32"},{"internalType":"uint8","name":"v","type":"uint8"},{"internalType":"bytes32","name":"r","type":"bytes32"},{"internalType":"bytes32","name":"s","type":"bytes32"}],"name":"withdrawWithAuthorization","outputs":[],"stateMutability":"nonpayable","type":"function"}]"""
        self.erc1155_set_approval = """[{"inputs": [{ "internalType": "address", "name": "operator", "type": "address" },{ "internalType": "bool", "name": "approved", "type": "bool" }],"name": "setApprovalForAll","outputs": [],"stateMutability": "nonpayable","type": "function"}]"""

        self.usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        self.ctf_address = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

        self.web3 = Web3(Web3.HTTPProvider(self.polygon_rpc))
        self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)

        self.usdc = self.web3.eth.contract(
            address=self.usdc_address, abi=self.erc20_approve
        )
        self.ctf = self.web3.eth.contract(
            address=self.ctf_address, abi=self.erc1155_set_approval
        )

        if not self.paper_trade:
            self._init_api_keys()
            self._init_approvals(False)
        else:
            self.client = None
            self.credentials = None

    def _init_api_keys(self) -> None:
        self.client = ClobClient(
            self.clob_url, key=self.private_key, chain_id=self.chain_id
        )
        self.credentials = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.credentials)
        # print(self.credentials)

    def _init_approvals(self, run: bool = False) -> None:
        if not run:
            return

        priv_key = self.private_key
        pub_key = self.get_address_for_private_key()
        chain_id = self.chain_id
        web3 = self.web3
        nonce = web3.eth.get_transaction_count(pub_key)
        usdc = self.usdc
        ctf = self.ctf

        # CTF Exchange
        raw_usdc_approve_txn = usdc.functions.approve(
            "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", int(MAX_INT, 0)
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_usdc_approve_tx = web3.eth.account.sign_transaction(
            raw_usdc_approve_txn, private_key=priv_key
        )
        send_usdc_approve_tx = web3.eth.send_raw_transaction(
            signed_usdc_approve_tx.raw_transaction
        )
        usdc_approve_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_usdc_approve_tx, 600
        )
        print(usdc_approve_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        raw_ctf_approval_txn = ctf.functions.setApprovalForAll(
            "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", True
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_ctf_approval_tx = web3.eth.account.sign_transaction(
            raw_ctf_approval_txn, private_key=priv_key
        )
        send_ctf_approval_tx = web3.eth.send_raw_transaction(
            signed_ctf_approval_tx.raw_transaction
        )
        ctf_approval_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_ctf_approval_tx, 600
        )
        print(ctf_approval_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        # Neg Risk CTF Exchange
        raw_usdc_approve_txn = usdc.functions.approve(
            "0xC5d563A36AE78145C45a50134d48A1215220f80a", int(MAX_INT, 0)
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_usdc_approve_tx = web3.eth.account.sign_transaction(
            raw_usdc_approve_txn, private_key=priv_key
        )
        send_usdc_approve_tx = web3.eth.send_raw_transaction(
            signed_usdc_approve_tx.raw_transaction
        )
        usdc_approve_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_usdc_approve_tx, 600
        )
        print(usdc_approve_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        raw_ctf_approval_txn = ctf.functions.setApprovalForAll(
            "0xC5d563A36AE78145C45a50134d48A1215220f80a", True
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_ctf_approval_tx = web3.eth.account.sign_transaction(
            raw_ctf_approval_txn, private_key=priv_key
        )
        send_ctf_approval_tx = web3.eth.send_raw_transaction(
            signed_ctf_approval_tx.raw_transaction
        )
        ctf_approval_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_ctf_approval_tx, 600
        )
        print(ctf_approval_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        # Neg Risk Adapter
        raw_usdc_approve_txn = usdc.functions.approve(
            "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296", int(MAX_INT, 0)
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_usdc_approve_tx = web3.eth.account.sign_transaction(
            raw_usdc_approve_txn, private_key=priv_key
        )
        send_usdc_approve_tx = web3.eth.send_raw_transaction(
            signed_usdc_approve_tx.raw_transaction
        )
        usdc_approve_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_usdc_approve_tx, 600
        )
        print(usdc_approve_tx_receipt)

        nonce = web3.eth.get_transaction_count(pub_key)

        raw_ctf_approval_txn = ctf.functions.setApprovalForAll(
            "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296", True
        ).build_transaction({"chainId": chain_id, "from": pub_key, "nonce": nonce})
        signed_ctf_approval_tx = web3.eth.account.sign_transaction(
            raw_ctf_approval_txn, private_key=priv_key
        )
        send_ctf_approval_tx = web3.eth.send_raw_transaction(
            signed_ctf_approval_tx.raw_transaction
        )
        ctf_approval_tx_receipt = web3.eth.wait_for_transaction_receipt(
            send_ctf_approval_tx, 600
        )
        print(ctf_approval_tx_receipt)

    def get_all_markets(self) -> "list[SimpleMarket]":
        markets = []
        res = httpx.get(self.gamma_markets_endpoint)
        if res.status_code == 200:
            for market in res.json():
                try:
                    market_data = self.map_api_to_market(market)
                    markets.append(SimpleMarket(**market_data))
                except Exception as e:
                    print(e)
                    pass
        return markets

    def filter_markets_for_trading(self, markets: "list[SimpleMarket]"):
        tradeable_markets = []
        for market in markets:
            if market.active:
                tradeable_markets.append(market)
        return tradeable_markets

    def get_market(self, token_id: str) -> SimpleMarket:
        params = {"clob_token_ids": token_id}
        res = httpx.get(self.gamma_markets_endpoint, params=params)
        if res.status_code == 200:
            data = res.json()
            market = data[0]
            return self.map_api_to_market(market, token_id)

    def map_api_to_market(self, market, token_id: str = "") -> SimpleMarket:
        raw_id = market.get("id", 0)
        try:
            parsed_id = int(raw_id)
        except (ValueError, TypeError):
            parsed_id = abs(hash(str(raw_id))) % (10 ** 9)
        market = {
            "id": parsed_id,
            "question": market.get("question", ""),
            "slug": market.get("slug", ""),
            "end": market.get("endDate", ""),
            "description": market.get("description", ""),
            "active": market.get("active", False),
            "funded": market.get("funded", False),
            "rewardsMinSize": float(market.get("rewardsMinSize") or 0),
            "rewardsMaxSpread": float(market.get("rewardsMaxSpread") or 0),
            "spread": float(market.get("spread") or 0),
            "outcomes": str(market.get("outcomes", "[]")),
            "outcome_prices": str(market.get("outcomePrices", "[]")),
            "clob_token_ids": str(market.get("clobTokenIds", "[]")),
            "event_id": str(market.get("event_id") or (market.get("events")[0].get("id") if market.get("events") else "")),
            "event_title": str(market.get("event_title") or (market.get("events")[0].get("title") if market.get("events") else "")),
            "event_slug": str(market.get("event_slug") or (market.get("events")[0].get("slug") if market.get("events") else "")),
        }
        if token_id:
            market["clob_token_ids"] = token_id
        return market

    def get_all_events(self) -> "list[SimpleEvent]":
        events = []
        res = httpx.get(self.gamma_events_endpoint)
        if res.status_code == 200:
            print(len(res.json()))
            for event in res.json():
                try:
                    print(1)
                    event_data = self.map_api_to_event(event)
                    events.append(SimpleEvent(**event_data))
                except Exception as e:
                    print(e)
                    pass
        return events

    def map_api_to_event(self, event) -> SimpleEvent:
        description = event["description"] if "description" in event.keys() else ""
        return {
            "id": int(event["id"]),
            "ticker": event["ticker"],
            "slug": event["slug"],
            "title": event["title"],
            "description": description,
            "active": event["active"],
            "closed": event["closed"],
            "archived": event["archived"],
            "new": event["new"],
            "featured": event["featured"],
            "restricted": event["restricted"],
            "end": event["endDate"],
            "markets": ",".join([str(x["id"]) for x in event["markets"]]),
        }

    def filter_events_for_trading(
        self, events: "list[SimpleEvent]"
    ) -> "list[SimpleEvent]":
        # Filtra apenas status — a janela temporal é aplicada nos mercados individuais
        tradeable_events = []
        for event in events:
            restriction_ok = self.paper_trade or not event.restricted
            if event.active and restriction_ok and not event.archived and not event.closed:
                tradeable_events.append(event)
        return tradeable_events

    def filter_markets_by_end_window(
        self, markets: "list[SimpleMarket]"
    ) -> "list[SimpleMarket]":
        """Mantém apenas mercados cujo endDate está dentro da janela min_hours–max_hours."""
        cfg = self.load_focus_config()
        min_h = float(cfg.get("min_hours", 1.0))
        max_h = float(cfg.get("max_hours", 48.0))

        kept, skipped = [], 0
        for market in markets:
            # map_api_to_market retorna dict; SimpleMarket usa atributo .end
            end_val = market.get("end", "") if isinstance(market, dict) else market.end
            hours = _hours_until_end(end_val)
            if hours is not None and min_h <= hours <= max_h:
                kept.append(market)
            else:
                skipped += 1

        print(f"   Filtro temporal (mercados): {len(kept)} dentro de {min_h:.0f}h–{max_h:.0f}h, {skipped} ignorados")
        return kept

    def load_focus_config(self) -> dict:
        import json
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "focus_config.json")
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            return {"mode": "all", "keywords": [], "min_volume": 0, "max_markets_per_run": 5}

    def filter_events_by_focus(self, events: "list[SimpleEvent]") -> "list[SimpleEvent]":
        config = self.load_focus_config()
        mode = config.get("mode", "all")
        if mode == "all":
            return events
        keywords = [k.lower() for k in config.get("keywords", [])]
        min_volume = config.get("min_volume", 0)
        if not keywords:
            return events
        filtered = []
        for event in events:
            text = f"{event.title} {event.description}".lower()
            if any(kw in text for kw in keywords):
                filtered.append(event)
        print(f"   Filtro de foco: {len(filtered)}/{len(events)} eventos correspondem às keywords")
        return filtered

    def get_all_tradeable_events(self, limit: int = 100) -> "list[SimpleEvent]":
        res = httpx.get(
            self.gamma_events_endpoint,
            params={"active": "true", "closed": "false", "archived": "false", "limit": limit},
        )
        events = []
        if res.status_code == 200:
            for event in res.json():
                try:
                    event_data = self.map_api_to_event(event)
                    events.append(SimpleEvent(**event_data))
                except Exception:
                    pass
        print(f"   API retornou {len(events)} eventos ativos")
        events = self.filter_events_for_trading(events)   # filtra active/archived/closed
        print(f"   Após filtro de status: {len(events)} evento(s) restantes")
        return self.filter_events_by_focus(events)        # filtro de keywords

    def _fetch_markets_page(self, params: dict, timeout: int = 20) -> "list[dict]":
        """Faz uma única chamada ao endpoint /markets e retorna lista de dicts (ou [])."""
        try:
            res = httpx.get(self.gamma_markets_endpoint, params=params, timeout=timeout)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            print(f"   [_fetch_markets_page] erro: {e}")
        return []

    # ── Mapa de keywords → tag_slugs para busca via API ──
    KEYWORD_TAG_MAP = {
        "nba": ["nba"],
        "soccer": ["soccer", "epl", "lal", "bun", "sea", "fl1", "ucl", "mls"],
        "football": ["soccer", "epl", "lal", "bun", "sea", "fl1", "ucl", "mls", "nfl"],
        "nfl": ["nfl"],
        "premier league": ["epl"],
        "la liga": ["lal"],
        "champions league": ["ucl"],
        "europa league": ["uel"],
        "mlb": ["mlb"],
        "nhl": ["nhl"],
        "mma": ["mma"],
        "ufc": ["ufc"],
        "playoff": ["nba", "nfl", "mlb", "nhl"],
        "championship": ["nba", "nfl", "mlb", "nhl", "ucl"],
        "series": ["nba", "mlb", "nhl"],
    }

    def _build_tag_slugs_from_keywords(self, keywords: "list[str]") -> "list[str]":
        """Converte keywords do focus_config em tag_slugs da Gamma API."""
        slugs: set = set()
        for kw in keywords:
            mapped = self.KEYWORD_TAG_MAP.get(kw.lower(), [])
            if mapped:
                slugs.update(mapped)
            else:
                # Tenta usar a keyword diretamente como tag_slug
                slugs.add(kw.lower().replace(" ", "-"))
        return list(slugs)

    def _extract_markets_from_events(self, events: "list[dict]", seen: set) -> "list[dict]":
        """Extrai mercados de uma lista de eventos, preenchendo endDate/description herdados."""
        markets: list[dict] = []
        for event in events:
            event_id = event.get("id", "")
            event_end = event.get("endDate", "")
            event_title = event.get("title", "")
            for mkt in event.get("markets", []):
                mid = mkt.get("id") or mkt.get("conditionId")
                if mid and mid not in seen:
                    seen.add(mid)
                    mkt = dict(mkt)
                    mkt["event_id"] = event_id
                    mkt["event_title"] = event_title
                    mkt["event_slug"] = event.get("slug", "")
                    if not mkt.get("endDate"):
                        mkt["endDate"] = event_end
                    if not mkt.get("description"):
                        mkt["description"] = event_title
                    if not mkt.get("question"):
                        mkt["question"] = event_title
                    markets.append(mkt)
        return markets

    def _fetch_markets_by_api_filter(
        self, min_h: float, max_h: float, tag_slugs: "list[str] | None" = None
    ) -> "list[dict]":
        """
        Busca mercados DIRETAMENTE no endpoint /markets com filtros de data e tag_slug.
        Complementa _fetch_events_by_api_filter para capturar jogos que não estão
        aninhados em eventos (comum em NBA, futebol, etc.).
        """
        now = datetime.now(timezone.utc)
        min_dt = (now + timedelta(hours=min_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        max_dt = (now + timedelta(hours=max_h)).strftime("%Y-%m-%dT%H:%M:%SZ")

        base = {
            "active": "true",
            "closed": "false",
            "end_date_min": min_dt,
            "end_date_max": max_dt,
            "limit": 100,
        }

        all_markets: list[dict] = []
        seen: set = set()

        slugs_to_query = tag_slugs if tag_slugs else [None]

        for slug in slugs_to_query:
            offset = 0
            while True:
                params = {**base, "offset": offset}
                if slug:
                    params["tag_slug"] = slug
                try:
                    res = httpx.get(self.gamma_markets_endpoint, params=params, timeout=20)
                    if res.status_code != 200:
                        break
                    markets = res.json()
                except Exception as e:
                    label = f"tag_slug={slug}" if slug else "geral"
                    print(f"   [/markets filter] {label} erro: {e}")
                    break
                if not markets:
                    break
                for mkt in markets:
                    mid = mkt.get("id") or mkt.get("conditionId")
                    if mid and mid not in seen:
                        seen.add(mid)
                        # Preenche campos de evento se vieram aninhados em events[]
                        if not mkt.get("event_id") and mkt.get("events"):
                            first_evt = mkt["events"][0] if isinstance(mkt["events"], list) and mkt["events"] else {}
                            mkt["event_id"] = first_evt.get("id", "")
                            mkt["event_title"] = first_evt.get("title", "")
                            mkt["event_slug"] = first_evt.get("slug", "")
                        all_markets.append(mkt)
                if len(markets) < 100:
                    break
                offset += 100

        return all_markets

    def _fetch_events_by_api_filter(
        self, min_h: float, max_h: float, tag_slugs: "list[str] | None" = None
    ) -> "list[dict]":
        """
        Busca eventos usando os filtros nativos da Gamma API:
          - end_date_min / end_date_max  → janela temporal server-side
          - tag_slug                     → filtra por esporte/categoria

        Retorna lista de dicts de mercados (já extraídos dos eventos).
        """
        now = datetime.now(timezone.utc)
        min_dt = (now + timedelta(hours=min_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        max_dt = (now + timedelta(hours=max_h)).strftime("%Y-%m-%dT%H:%M:%SZ")

        base = {
            "active": "true",
            "closed": "false",
            "end_date_min": min_dt,
            "end_date_max": max_dt,
            "limit": 100,
        }

        all_markets: list[dict] = []
        seen: set = set()

        if tag_slugs:
            # Busca por cada tag_slug separadamente (API não suporta múltiplos)
            for slug in tag_slugs:
                offset = 0
                while True:
                    params = {**base, "tag_slug": slug, "offset": offset}
                    try:
                        res = httpx.get(self.gamma_events_endpoint, params=params, timeout=20)
                        if res.status_code != 200:
                            break
                        events = res.json()
                    except Exception as e:
                        print(f"   [API filter] tag_slug={slug} erro: {e}")
                        break
                    if not events:
                        break
                    mkts = self._extract_markets_from_events(events, seen)
                    all_markets.extend(mkts)
                    if len(events) < 100:
                        break
                    offset += 100
        else:
            # Sem tag_slug — busca geral com janela temporal
            offset = 0
            while True:
                params = {**base, "offset": offset}
                try:
                    res = httpx.get(self.gamma_events_endpoint, params=params, timeout=20)
                    if res.status_code != 200:
                        break
                    events = res.json()
                except Exception as e:
                    print(f"   [API filter] erro: {e}")
                    break
                if not events:
                    break
                mkts = self._extract_markets_from_events(events, seen)
                all_markets.extend(mkts)
                if len(events) < 100:
                    break
                offset += 100

        return all_markets

    def get_markets_closing_soon(self, limit: int = 200) -> "list[dict]":
        """
        Estratégia de busca usando filtros nativos da Gamma API:
          1. /events?tag_slug=X&end_date_min=Y&end_date_max=Z  (busca principal por esporte + janela)
          2. /events?end_date_min=Y&end_date_max=Z             (busca geral dentro da janela)

        Funciona corretamente para jogos esportivos (NBA, futebol, etc.) que antes
        não apareciam porque a API /events?q=keyword e /markets não indexam esportes.
        """
        cfg = self.load_focus_config()
        min_h = float(cfg.get("min_hours", 1.0))
        max_h = float(cfg.get("max_hours", 48.0))
        min_liq = float(cfg.get("min_liquidity", 0.0))
        keywords = [k.lower() for k in cfg.get("keywords", [])]
        mode = cfg.get("mode", "all")

        seen_ids: set = set()
        final: list[dict] = []

        def _add_unique(batch: "list[dict]") -> int:
            added = 0
            for m in batch:
                mid = m.get("id") or m.get("conditionId") or m.get("questionID")
                if mid and mid not in seen_ids:
                    # Filtro Híbrido: Liquidez ou Volume 24h (para capturar CLOB e AMM)
                    liquidity = float(m.get("liquidity") or 0)
                    volume_24h = float(m.get("volume24h") or 0)
                    
                    # Se não tiver o mínimo de liquidez nem volume relevante, ignora
                    # Usamos 10% do min_liquidity como threshold para volume se não houver liquidez reportada
                    if min_liq:
                        if liquidity < min_liq and volume_24h < (min_liq * 0.5):
                            continue

                    # Armazena métricas para ordenação
                    m["_activity_score"] = liquidity + (volume_24h * 0.2)
                    seen_ids.add(mid)
                    final.append(m)
                    added += 1
            return added

        # ── 1. Busca por tag_slug (esportes) — apenas /events ──
        # Jogos esportivos no Polymarket existem SOMENTE em /events?tag_slug=X,
        # não no endpoint /markets. Buscar /markets com tag_slug retornaria zero.
        if keywords and mode != "all":
            tag_slugs = self._build_tag_slugs_from_keywords(keywords)
            print(f"   Keywords {keywords} -> tag_slugs {tag_slugs}")

            sports_from_events = self._fetch_events_by_api_filter(min_h, max_h, tag_slugs)
            added_ev = _add_unique(sports_from_events)
            print(f"   /events tag_slug: {added_ev} mercados em {min_h:.0f}h-{max_h:.0f}h")

        # ── 2. Busca geral dentro da janela temporal — /events + /markets ──
        # 2a. /events sem tag (todos os tipos de evento)
        general_from_events = self._fetch_events_by_api_filter(min_h, max_h, tag_slugs=None)
        added_general_ev = _add_unique(general_from_events)
        print(f"   /events geral: {added_general_ev} mercados novos em {min_h:.0f}h-{max_h:.0f}h")

        # 2b. /markets sem tag (todos os mercados standalone)
        general_from_markets = self._fetch_markets_by_api_filter(min_h, max_h, tag_slugs=None)
        added_general_mk = _add_unique(general_from_markets)
        added_general = added_general_ev + added_general_mk
        print(f"   /markets geral: {added_general_mk} mercados novos em {min_h:.0f}h-{max_h:.0f}h")

        # ── 3. Filtro de keyword client-side (se modo keyword) ──
        if keywords and mode != "all":
            # Filtra para manter apenas mercados relevantes às keywords
            def _passes_kw(m: dict) -> bool:
                text = f"{m.get('question','')}{m.get('description','')}".lower()
                # Tags do evento também podem conter a keyword
                tags_text = " ".join(str(t) for t in (m.get("tags") or []))
                full_text = f"{text} {tags_text}".lower()
                return any(kw in full_text for kw in keywords)

            # Os mercados de sports (via tag_slug) já são relevantes — não filtramos
            # Os mercados gerais precisam do filtro de keyword
            sports_count = len(final) - added_general
            sports_part = final[:sports_count]  # já filtrados por tag_slug
            general_part = final[sports_count:]  # precisam filtro keyword

            filtered_general = [m for m in general_part if _passes_kw(m)]
            final = sports_part + filtered_general
            print(f"   Filtro keyword nos gerais: {len(general_part)} -> {len(filtered_general)}")

        # ── 4. Ordenação Final por Atividade ──
        final.sort(key=lambda x: x.get("_activity_score", 0), reverse=True)

        print(f"   TOTAL FINAL: {len(final)} mercados dentro de {min_h:.0f}h-{max_h:.0f}h (ordenados por atividade)")

        # Diagnóstico resumido
        for m in final[:5]:
            h = _hours_until_end(m.get("endDate", ""))
            hstr = f"{h:.1f}h" if h is not None else "?"
            liq = float(m.get("liquidity") or 0.0)
            vol = float(m.get("volume24h") or 0.0)
            print(f"     [{hstr}] {m.get('question','?')[:65]} (Liq: ${liq:,.0f} | Vol24h: ${vol:,.0f})")

        return final

    def get_sampling_simplified_markets(self) -> "list[SimpleEvent]":
        markets = []
        raw_sampling_simplified_markets = self.client.get_sampling_simplified_markets()
        for raw_market in raw_sampling_simplified_markets["data"]:
            token_one_id = raw_market["tokens"][0]["token_id"]
            market = self.get_market(token_one_id)
            markets.append(market)
        return markets

    def get_orderbook(self, token_id: str) -> OrderBookSummary:
        return self.client.get_order_book(token_id)

    def get_orderbook_price(self, token_id: str) -> float:
        return float(self.client.get_price(token_id))

    def get_address_for_private_key(self):
        account = self.w3.eth.account.from_key(str(self.private_key))
        return account.address

    def build_order(
        self,
        market_token: str,
        amount: float,
        nonce: str = str(round(time.time())),
        side: str = "BUY",
        expiration: str = "0",
    ):
        signer = Signer(self.private_key)
        builder = OrderBuilder(self.exchange_address, self.chain_id, signer)

        buy = side == "BUY"
        side = 0 if buy else 1
        maker_amount = amount if buy else 0
        taker_amount = amount if not buy else 0
        order_data = OrderData(
            maker=self.get_address_for_private_key(),
            tokenId=market_token,
            makerAmount=maker_amount,
            takerAmount=taker_amount,
            feeRateBps="1",
            nonce=nonce,
            side=side,
            expiration=expiration,
        )
        order = builder.build_signed_order(order_data)
        return order

    def execute_order(self, price, size, side, token_id) -> str:
        return self.client.create_and_post_order(
            OrderArgs(price=price, size=size, side=side, token_id=token_id)
        )

    def execute_market_order(self, market, amount) -> str:
        token_id = ast.literal_eval(market[0].dict()["metadata"]["clob_token_ids"])[1]
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
        )
        signed_order = self.client.create_market_order(order_args)
        print("Execute market order... signed_order ", signed_order)
        resp = self.client.post_order(signed_order, orderType=OrderType.FOK)
        print(resp)
        print("Done!")
        return resp

    def get_usdc_balance(self) -> float:
        if self.paper_trade:
            try:
                import json as _json
                log_path = Path("paper_trades.json")
                if log_path.exists():
                    with open(log_path) as f:
                        trades = _json.load(f)
                    spent = sum(t.get("simulated_usdc_amount", 0) for t in trades if not t.get("resolved"))
                    return max(0.0, self.paper_balance - spent)
            except Exception:
                pass
            return self.paper_balance
        balance_res = self.usdc.functions.balanceOf(
            self.get_address_for_private_key()
        ).call()
        return float(balance_res / 10e5)


def main():
    print(Polymarket().get_all_events())


if __name__ == "__main__":
    load_dotenv()
    p = Polymarket()
    balance = p.get_usdc_balance()
    print(f"Balance: {balance}")
