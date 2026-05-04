"""
pumpfun_listener.py — Real-time Pump.fun token launch detector.
Subscribes to Solana WebSocket RPC and catches new tokens at the moment of creation.
No polling. Zero delay. This is how you get in early.
"""
import asyncio
import json
import logging
import requests
import websockets

log = logging.getLogger("pumpfun")

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class PumpFunListener:
    def __init__(self, ws_url: str, rpc_url: str):
        self.ws_url = ws_url
        self.rpc_url = rpc_url
        self.running = False
        self.seen_sigs: set[str] = set()
        self.seen_mints: set[str] = set()

    async def run(self, on_new_token, notify):
        self.running = True
        log.info("PumpFun WebSocket listener starting...")

        while self.running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=15,
                    close_timeout=5,
                ) as ws:
                    sub_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [PUMP_PROGRAM_ID]},
                            {"commitment": "processed"},
                        ],
                    }
                    await ws.send(json.dumps(sub_msg))
                    log.info("Subscribed to Pump.fun program logs")
                    await notify(
                        "☸️ PUMP.FUN LISTENER ONLINE\n"
                        "Real-time detection active — catching tokens at birth."
                    )

                    async for raw in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(raw)
                            await self._handle(data, on_new_token)
                        except Exception as e:
                            log.error(f"PumpFun message error: {e}")

            except Exception as e:
                if self.running:
                    log.error(f"PumpFun WS disconnected: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def _handle(self, data: dict, on_new_token):
        try:
            result = data.get("params", {}).get("result", {})
            value = result.get("value", {})
            logs = value.get("logs", [])
            signature = value.get("signature", "")

            # Only care about Create instructions = new token launches
            if not any("Instruction: Create" in line for line in logs):
                return

            if not signature or signature in self.seen_sigs:
                return
            self.seen_sigs.add(signature)

            # Fetch the full transaction to extract the mint address
            mint = await asyncio.get_event_loop().run_in_executor(
                None, self._get_mint_from_tx, signature
            )
            if mint and mint not in self.seen_mints:
                self.seen_mints.add(mint)
                log.info(f"PUMP.FUN NEW TOKEN DETECTED: {mint[:8]}...")
                await on_new_token(mint)

        except Exception as e:
            log.error(f"PumpFun handle error: {e}")

    def _get_mint_from_tx(self, signature: str) -> str | None:
        """Fetch the transaction and extract the new token mint."""
        try:
            resp = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                },
                timeout=10,
            )
            tx = resp.json().get("result")
            if not tx:
                return None

            # postTokenBalances contains the mint addresses involved in the tx
            post_balances = tx.get("meta", {}).get("postTokenBalances", [])
            for balance in post_balances:
                mint = balance.get("mint", "")
                if mint and mint not in self.seen_mints:
                    return mint

        except Exception as e:
            log.error(f"TX fetch error for {signature[:8]}: {e}")
        return None

    def stop(self):
        self.running = False
