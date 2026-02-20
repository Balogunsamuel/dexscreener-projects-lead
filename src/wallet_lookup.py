"""
Deployer wallet lookup via Etherscan-compatible block explorer APIs.

Identifies the contract creator (deployer) by querying the earliest
contract creation transaction.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Config
from .utils import rate_limiters

logger = logging.getLogger("dexbot.wallet")


class WalletLookup:
    """Look up the deployer wallet for a token contract."""

    def __init__(self, config: Config):
        self._config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
        )
        # Etherscan APIs: 5 calls/sec on free tier
        self._limiter = rate_limiters.get("explorer", max_calls=4, period=1.0)

    async def close(self) -> None:
        await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_deployer(self, chain: str, contract_address: str) -> Optional[str]:
        """
        Query the block explorer to find the deployer (contract creator) wallet.

        Uses the `txlist` API to find the earliest transaction creating the contract.
        Falls back to `getcontractcreation` if available.

        Args:
            chain: Chain name (ethereum, bsc, base, solana)
            contract_address: The token contract address

        Returns:
            The deployer wallet address, or None if not found.
        """
        # ── Method 3: Solana RPC (for solana chain) ──
        if chain == "solana":
            return await self._get_solana_deployer(contract_address)

        explorer = self._config.explorer_configs.get(chain)
        if not explorer:
            logger.warning("No explorer config for chain: %s", chain)
            return None

        api_url = explorer["api_url"]
        api_key = explorer["api_key"]

        if not api_key:
            logger.warning("No API key for %s explorer — skipping wallet lookup", chain)
            return None

        # ── Method 1: contractcreation endpoint (most reliable) ──
        deployer = await self._try_contract_creation(api_url, api_key, contract_address)
        if deployer:
            return deployer

        # ── Method 2: txlist for the contract (fallback) ──
        deployer = await self._try_txlist_fallback(api_url, api_key, contract_address)
        return deployer

    async def _get_solana_deployer(self, token_address: str) -> Optional[str]:
        """
        Solana deployer lookup via RPC.
        Strategy:
        1. getSignaturesForAddress(token_address, limit=1000) -> oldest signature (mint tx)
           Note: We want the *first* transaction (minting). getSignaturesForAddress returns newest first.
           We can walk back the history or, for simplicity in MVP, just try to get the very last item 
           if we assume the token is relatively new and hasn't had thousands of txs yet.
           However, for high volume tokens this might miss the creation.
           Better approach for MVP: getSignaturesForAddress with `before` param, or just get the last one 
           from a large limit if the token is new (which it is, per our freshness filter).
        2. getTransaction(signature) -> extract fee payer / first signer.
        """
        rpc_url = getattr(self._config, "solana_rpc_url", None)
        if not rpc_url:
            return None

        # 1. Fetch signatures (history).
        # Since we filter for tokens < 15 mins old, 1000 txs might be enough to reach the bottom (creation).
        # If not, we'd need to paginate. For MVP, we'll take the last one from the batch.
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    token_address,
                    {"limit": 1000} 
                ]
            }
            async with self._limiter: # Reuse limiter or create new one? 
                # Note: Ethereum limiter is 5/sec. Solana public RPC has different limits.
                # We'll use the same limiter to avoid spamming too hard generally, 
                # although ideally we'd have a separate one.
                resp = await self._client.post(rpc_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            
            if "error" in data:
                logger.warning("Solana RPC error (sigs): %s", data["error"])
                return None
            
            result = data.get("result", [])
            if not result:
                return None
            
            # The last signature in the list is the oldest returned.
            # Ideally this is the mint transaction for a new token.
            creation_tx_sig = result[-1]["signature"]
            
            # 2. Fetch transaction details
            payload_tx = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "getTransaction",
                "params": [
                    creation_tx_sig,
                    {"maxSupportedTransactionVersion": 0}
                ]
            }
            
            async with self._limiter:
                resp = await self._client.post(rpc_url, json=payload_tx)
                resp.raise_for_status()
                data_tx = resp.json()
                
            if "error" in data_tx:
                logger.warning("Solana RPC error (tx): %s", data_tx["error"])
                return None
            
            tx_result = data_tx.get("result", {})
            if not tx_result:
                return None
                
            # Parse deployer: usually the first account in output, which is the fee payer/signer.
            # In `getTransaction`, `transaction.message.accountKeys` contains the accounts.
            # If it's a versioned tx, parsing is slightly different but usually the first account is signer.
            
            transaction = tx_result.get("transaction", {})
            message = transaction.get("message", {})
            account_keys = message.get("accountKeys", [])
            
            deployer = None
            if account_keys:
                # Check format: sometimes it's a list of strings, sometimes list of dicts
                first_acc = account_keys[0]
                if isinstance(first_acc, str):
                    deployer = first_acc
                elif isinstance(first_acc, dict):
                    deployer = first_acc.get("pubkey")
            
            if deployer:
                logger.info("Found Solana deployer: %s", deployer)
                return deployer

        except Exception as e:
            logger.debug("Solana deployer lookup failed: %s", e)
            
        return None

    async def _try_contract_creation(
        self, api_url: str, api_key: str, contract_address: str
    ) -> Optional[str]:
        """Try the getcontractcreation endpoint."""
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract_address,
            "apikey": api_key,
        }

        try:
            async with self._limiter:
                resp = await self._client.get(api_url, params=params)
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") == "1" and data.get("result"):
                result = data["result"]
                if isinstance(result, list) and len(result) > 0:
                    creator = result[0].get("contractCreator", "")
                    if creator:
                        logger.info(
                            "Found deployer via contractcreation: %s", creator[:10] + "…"
                        )
                        return creator
        except Exception as e:
            logger.debug("contractcreation lookup failed: %s", e)

        return None

    async def _try_txlist_fallback(
        self, api_url: str, api_key: str, contract_address: str
    ) -> Optional[str]:
        """Fallback: look up earliest internal/normal tx to the contract."""
        params = {
            "module": "account",
            "action": "txlist",
            "address": contract_address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 1,
            "sort": "asc",
            "apikey": api_key,
        }

        try:
            async with self._limiter:
                resp = await self._client.get(api_url, params=params)
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") == "1" and data.get("result"):
                result = data["result"]
                if isinstance(result, list) and len(result) > 0:
                    tx = result[0]
                    # If the `to` field is empty, this is a contract creation tx
                    if tx.get("to") == "" or tx.get("to") is None:
                        deployer = tx.get("from", "")
                        if deployer:
                            logger.info(
                                "Found deployer via txlist: %s", deployer[:10] + "…"
                            )
                            return deployer
                    # Otherwise the `from` of earliest tx is likely deployer
                    deployer = tx.get("from", "")
                    if deployer:
                        return deployer
        except Exception as e:
            logger.debug("txlist fallback failed: %s", e)

        return None
