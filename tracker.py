"""
tracker.py — Tracks all open/closed positions and trade history.
Updated to store 'captured_state' for Mahoraga adaptation.
"""
import json
import os
from datetime import datetime
from typing import Optional

POSITIONS_FILE = "positions.json"

def _load() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {"positions": {}, "trades": []}
    with open(POSITIONS_FILE) as f:
        return json.load(f)

def _save(data: dict):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def record_buy(ca: str, sol_spent: float, tokens_received: int, tx: str, captured_state: dict = None):
    """Log a buy. Creates a new open position."""
    data = _load()
    data["positions"][ca] = {
        "sol_spent": sol_spent,
        "tokens_received": tokens_received,
        "buy_time": datetime.now().isoformat(),
        "buy_tx": tx,
        "status": "open",
        "captured_state": captured_state or {}
    }
    data["trades"].append({
        "ca": ca,
        "action": "buy",
        "sol_amount": sol_spent,
        "tokens": tokens_received,
        "tx": tx,
        "time": datetime.now().isoformat(),
        "captured_state": captured_state or {}
    })
    _save(data)

def record_sell(ca: str, sol_received: float, tx: str) -> Optional[dict]:
    """Log a sell. Closes the position. Returns the full trade record for adaptation."""
    data = _load()
    position = data["positions"].get(ca)
    trade_record = None
    
    if position:
        pnl = round(sol_received - position["sol_spent"], 6)
        data["positions"][ca].update({
            "status": "closed",
            "sell_tx": tx,
            "sol_received": sol_received,
            "pnl_sol": pnl,
            "sell_time": datetime.now().isoformat()
        })
        
        trade_record = {
            "ca": ca,
            "action": "sell",
            "sol_amount": sol_received,
            "tx": tx,
            "time": datetime.now().isoformat(),
            "pnl_sol": pnl,
            "captured_state": position.get("captured_state", {})
        }
        data["trades"].append(trade_record)
        
    _save(data)
    return trade_record

def get_position(ca: str) -> Optional[dict]:
    return _load()["positions"].get(ca)

def get_pnl_summary() -> dict:
    data = _load()
    sells = [t for t in data["trades"] if t.get("action") == "sell" and t.get("pnl_sol") is not None]
    total_pnl = sum(t["pnl_sol"] for t in sells)
    wins = [t for t in sells if t["pnl_sol"] > 0]
    losses = [t for t in sells if t["pnl_sol"] <= 0]
    open_count = sum(1 for p in data["positions"].values() if p["status"] == "open")

    return {
        "total_closed": len(sells),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else 0,
        "total_pnl_sol": round(total_pnl, 6),
        "open_count": open_count
    }
