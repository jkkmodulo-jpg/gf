"""
trader.py — Solana trading via Jupiter API.
Handles quotes, swap execution, price checks.
No Anthropic dependency — CA extraction is pure regex in bot.py.
"""

import base64
import logging
import requests
import base58
from typing import Optional

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from config import (
    SOLANA_PRIVATE_KEY,
    RPC_URL,
    SLIPPAGE_BPS,
    SOL_MINT,
    LAMPORTS_PER_SOL,
    TRADE_AMOUNT_SOL,
)

log = logging.getLogger("trader")

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"


# ── WALLET ────────────────────────────────────────────────────────────────────

def load_keypair() -> Keypair:
    return Keypair.from_bytes(base58.b58decode(SOLANA_PRIVATE_KEY))


# ── PRICE CHECK ───────────────────────────────────────────────────────────────

def get_quote(input_mint: str, output_mint: str, amount: int) -> Optional[dict]:
    try:
        resp = requests.get(
            JUPITER_QUOTE_URL,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": SLIPPAGE_BPS,
            },
            timeout=10
        )
        data = resp.json()
        if "error" in data:
            log.warning(f"Quote error: {data['error']}")
            return None
        return data
    except Exception as e:
        log.error(f"Quote request failed: {e}")
        return None


def get_token_value_in_sol(token_mint: str, token_amount: int) -> Optional[float]:
    """Check current value of a token holding in SOL."""
    quote = get_quote(token_mint, SOL_MINT, token_amount)
    if not quote:
        return None
    return int(quote["outAmount"]) / LAMPORTS_PER_SOL


# ── SWAP EXECUTION ────────────────────────────────────────────────────────────

def execute_swap(input_mint: str, output_mint: str, amount: int) -> dict:
    """
    Core swap. Works for buy (SOL→token) and sell (token→SOL).
    Returns: {success, signature, out_amount, explorer} or {success, error}
    """
    keypair = load_keypair()
    wallet  = str(keypair.pubkey())

    # Step 1: Quote
    quote = get_quote(input_mint, output_mint, amount)
    if not quote:
        return {"success": False, "error": "Failed to get Jupiter quote"}

    # Step 2: Build swap transaction
    try:
        swap_resp = requests.post(
            JUPITER_SWAP_URL,
            json={
                "quoteResponse": quote,
                "userPublicKey": wallet,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            },
            timeout=15
        )
        swap_data = swap_resp.json()
    except Exception as e:
        return {"success": False, "error": f"Swap build failed: {e}"}

    if "error" in swap_data:
        return {"success": False, "error": swap_data["error"]}

    # Step 3: Sign transaction
    try:
        raw_tx   = base64.b64decode(swap_data["swapTransaction"])
        txn      = VersionedTransaction.from_bytes(raw_tx)
        signed   = VersionedTransaction(txn.message, [keypair])
        signed_b64 = base64.b64encode(bytes(signed)).decode("utf-8")
    except Exception as e:
        return {"success": False, "error": f"Signing failed: {e}"}

    # Step 4: Send to RPC
    try:
        rpc_resp = requests.post(
            RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    signed_b64,
                    {
                        "encoding": "base64",
                        "skipPreflight": True,
                        "maxRetries": 3,
                    }
                ]
            },
            timeout=20
        )
        rpc_data = rpc_resp.json()
    except Exception as e:
        return {"success": False, "error": f"RPC send failed: {e}"}

    if "error" in rpc_data:
        return {"success": False, "error": str(rpc_data["error"])}

    sig = rpc_data["result"]
    return {
        "success": True,
        "signature": sig,
        "out_amount": int(quote.get("outAmount", 0)),
        "explorer": f"https://solscan.io/tx/{sig}"
    }


def buy_token(token_mint: str, sol_amount: float = None) -> dict:
    amount = int((sol_amount or TRADE_AMOUNT_SOL) * LAMPORTS_PER_SOL)
    log.info(f"BUY {token_mint[:8]}... | {sol_amount or TRADE_AMOUNT_SOL} SOL")
    return execute_swap(SOL_MINT, token_mint, amount)


def sell_token(token_mint: str, token_amount: int) -> dict:
    log.info(f"SELL {token_mint[:8]}... | {token_amount:,} tokens")
    return execute_swap(token_mint, SOL_MINT, token_amount)
