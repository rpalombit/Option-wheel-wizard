#!/usr/bin/env python3
"""
OptionSuite_FreshStart.py
Cleaned and updated backend engine for Spike Scanner + Buyback Engine (Unified Collapse Logic).

This version contains:
- SpikeScanner (original logic)
- Unified BuybackEngine (Step B)
- Shared utilities
- Config dataclasses
"""

from __future__ import annotations

import time
import math
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Dict, Callable, Tuple, Optional
import yfinance as yf


# ===============================================================
#                     SPIKE SCANNER (unchanged)
# ===============================================================

@dataclass
class SpikeConfig:
    tickers: List[str]
    min_pct: float = 20.0       # minimum spike percentage
    min_abs: float = 0.05       # minimum absolute premium increase
    min_premium: float = 0.05   # minimum premium filter
    max_spread_pct: float = 25.0
    exp_filter_days: Optional[int] = None
    cooldown_secs: int = 20


class SpikeScanner:
    """
    Basic multi-ticker spike scanner.
    Scans option chains, detects sudden premium spikes, passes events to callback.
    """

    def __init__(self, cfg: SpikeConfig, alert_fn: Callable[[Dict], None]):
        self.cfg = cfg
        self.alert_fn = alert_fn
        self._last_prem = {}      # contract → last observed premium
        self._last_alert = {}     # contract → timestamp of last alert

    def _key(self, t, e, k, s):
        return (t.upper(), e, k, float(s))

    def _spread_pct(self, row):
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        if bid <= 0 or ask <= 0 or ask <= bid:
            return 999.0
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100

    def _choose_premium(self, row):
        last = float(row.get("lastPrice") or 0)
        if last > 0:
            return last
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return max(bid, ask, 0)

    def run_once(self):
        now = time.time()
        for tk in self.cfg.tickers:
            ticker = yf.Ticker(tk)
            try:
                expiries = ticker.options
            except Exception:
                continue

            for exp in expiries:
                if self.cfg.exp_filter_days:
                    # expiration filter: only exp ≤ X days
                    try:
                        exp_dt = dt.datetime.strptime(exp, "%Y-%m-%d")
                        days = (exp_dt - dt.datetime.now()).days
                        if days > self.cfg.exp_filter_days:
                            continue
                    except:
                        pass

                try:
                    chain = ticker.option_chain(exp)
                except Exception:
                    continue

                for kind_label, df in (("C", chain.calls), ("P", chain.puts)):
                    for _, row in df.iterrows():
                        strike = float(row["strike"])
                        prem = self._choose_premium(row)
                        if prem < self.cfg.min_premium:
                            continue

                        spr = self._spread_pct(row)
                        if spr > self.cfg.max_spread_pct:
                            continue

                        key = self._key(tk, exp, kind_label, strike)
                        prev = self._last_prem.get(key, None)
                        self._last_prem[key] = prem

                        if prev is None:
                            continue

                        if prem <= 0 or prev <= 0:
                            continue

                        pct = (prem - prev) / prev * 100
                        if pct >= self.cfg.min_pct and (prem - prev) >= self.cfg.min_abs:
                            last_ts = self._last_alert.get(key, 0)
                            if now - last_ts < self.cfg.cooldown_secs:
                                continue

                            self._last_alert[key] = now

                            event = {
                                "type": "SPIKE",
                                "ticker": tk.upper(),
                                "expiry": exp,
                                "kind": kind_label,
                                "strike": strike,
                                "prev": prev,
                                "prem": prem,
                                "pct": pct,
                                "spread": spr,
                                "ts": dt.datetime.now().isoformat(),
                            }
                            self.alert_fn(event)
# ===============================================================
#          UNIFIED BUYBACK ENGINE (Step B Implementation)
# ===============================================================

@dataclass
class Contract:
    """Represents a short option position to monitor."""
    ticker: str
    kind: str         # 'C' or 'P'
    strike: float
    expiry: str       # "YYYY-MM-DD"
    open_credit: float
    qty: int = 1
    note: str = ""


@dataclass
class BuybackConfig:
    """Config for Unified Buyback Engine."""
    contracts: List[Contract]
    targets: List[float] = field(default_factory=lambda: [80.0, 90.0])
    floor: float = 0.05
    min_capture_pct: float = 50.0
    drop_pct_since_last: float = 30.0
    interval_secs: int = 15
    max_spread_pct: float = 20.0
    scan_entire_chain: bool = False   # optional wide-scan mode


class BuybackEngine:
    """Unified Buyback collapse detector (single alert type)."""

    def __init__(self, cfg: BuybackConfig, alert_fn: Callable[[Dict], None]):
        self.cfg = cfg
        self.alert_fn = alert_fn

        # Contract-level state
        self._prev_prem: Dict[Tuple, float] = {}   # last observed premium
        self._last_alert: Dict[Tuple, float] = {}  # last alert for cooldown
        self.cooldown_secs = 60                    # per-contract cooldown

    # ----------------------------------------------------------
    #                    UTILITIES
    # ----------------------------------------------------------

    def _key(self, ticker: str, expiry: str, kind: str, strike: float):
        return (ticker.upper(), expiry, kind.upper(), float(strike))

    def _spread_pct(self, row: dict) -> float:
        try:
            bid = float(row.get("bid") or 0)
            ask = float(row.get("ask") or 0)
        except:
            return 999.0
        if bid <= 0 or ask <= 0 or ask <= bid:
            return 999.0
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100

    def _choose_premium(self, row: dict) -> float:
        try:
            last = float(row.get("lastPrice") or 0)
        except:
            last = 0

        if last > 0:
            return last

        try:
            bid = float(row.get("bid") or 0)
            ask = float(row.get("ask") or 0)
        except:
            return 0

        if bid > 0 and ask > 0:
            return (bid + ask) / 2

        return max(bid, ask)

    def _fetch_chain(self, ticker: str, expiry: str):
        tk = yf.Ticker(ticker)
        try:
            chain = tk.option_chain(expiry)
            return chain.calls, chain.puts
        except:
            return None, None

    def _fetch_row(self, c: Contract) -> Optional[dict]:
        calls_df, puts_df = self._fetch_chain(c.ticker, c.expiry)
        if calls_df is None:
            return None

        df = calls_df if c.kind.upper() == "C" else puts_df
        sub = df[df["strike"] == c.strike]
        if sub.empty:
            return None

        return dict(sub.iloc[0])

    # ----------------------------------------------------------
    #                    CORE ENGINE LOGIC
    # ----------------------------------------------------------

    def run_once(self):
        """Run one cycle across user contracts."""
        now = time.time()

        # 1) Focused mode: user's contract list
        for c in self.cfg.contracts:
            self._check_contract(c, now)

        # 2) Wide mode: scan *all* contracts (optional)
        if self.cfg.scan_entire_chain:
            self._scan_chain_for_collapse(now)

    def _check_contract(self, c: Contract, now: float):
        """Check one contract for collapse events."""
        key = self._key(c.ticker, c.expiry, c.kind, c.strike)
        row = self._fetch_row(c)
        if row is None:
            return

        prem = self._choose_premium(row)
        spr = self._spread_pct(row)

        if prem <= 0 or spr > self.cfg.max_spread_pct:
            return

        prev = self._prev_prem.get(key, float("nan"))
        self._prev_prem[key] = prem

        # Capture %
        capture = 0.0
        if c.open_credit > 0:
            capture = (c.open_credit - prem) / c.open_credit * 100

        # Drop since last sample
        drop = 0.0
        if math.isfinite(prev) and prev > 0 and prem < prev:
            drop = (prev - prem) / prev * 100

        # Per-contract cooldown
        last_ts = self._last_alert.get(key, 0)
        if now - last_ts < self.cooldown_secs:
            return

        # -------------------------------
        # Determine if this is a Buyback Opp
        # -------------------------------
        reasons = []

        # Floor price
        if prem <= self.cfg.floor:
            reasons.append("FLOOR")

        # Capture targets
        if capture >= self.cfg.min_capture_pct:
            for t in self.cfg.targets:
                if capture >= t:
                    reasons.append(f"TARGET_{int(t)}")

        # Fast drop
        if drop >= self.cfg.drop_pct_since_last:
            reasons.append(f"FAST_DROP_{int(drop)}")

        if not reasons:
            return

        # Mark cooldown
        self._last_alert[key] = now

        # Build alert event
        event = {
            "type": "BUYBACK",
            "ticker": c.ticker.upper(),
            "expiry": c.expiry,
            "kind": c.kind.upper(),
            "strike": c.strike,
            "premium": prem,
            "capture_pct": capture,
            "drop_pct": drop,
            "spread_pct": spr,
            "reasons": reasons,
            "open_credit": c.open_credit,
            "qty": c.qty,
            "note": c.note,
            "ts": dt.datetime.now().isoformat(),
        }

        self.alert_fn(event)
    # ----------------------------------------------------------
    #       OPTIONAL — SCAN ENTIRE CHAIN FOR COLLAPSE
    # ----------------------------------------------------------

    def _scan_chain_for_collapse(self, now: float):
        """
        Wide mode:
            For each ticker in user contracts,
            scan ALL expirations and ALL strikes for collapse events.

        Collapse logic here is simpler (no open_credit available):
            - floor price hit
            - fast drop since last observed
        """
        tickers = sorted({c.ticker.upper() for c in self.cfg.contracts})

        for tk in tickers:
            ticker = yf.Ticker(tk)

            try:
                expiries = ticker.options
            except Exception:
                continue

            for exp in expiries:
                calls_df, puts_df = self._fetch_chain(tk, exp)
                if calls_df is None:
                    continue

                for kind_label, df in (("C", calls_df), ("P", puts_df)):
                    for _, row in df.iterrows():
                        strike = float(row["strike"])
                        key = self._key(tk, exp, kind_label, strike)

                        prem = self._choose_premium(row)
                        spr = self._spread_pct(row)

                        if prem <= 0 or spr > self.cfg.max_spread_pct:
                            continue

                        prev = self._prev_prem.get(key, float("nan"))
                        self._prev_prem[key] = prem

                        drop = 0.0
                        if math.isfinite(prev) and prev > 0 and prem < prev:
                            drop = (prev - prem) / prev * 100

                        last_ts = self._last_alert.get(key, 0)
                        if now - last_ts < self.cooldown_secs:
                            continue

                        reasons = []
                        if prem <= self.cfg.floor:
                            reasons.append("FLOOR_CHAIN")
                        if drop >= self.cfg.drop_pct_since_last:
                            reasons.append(f"FAST_DROP_CHAIN_{int(drop)}")

                        if not reasons:
                            continue

                        self._last_alert[key] = now

                        event = {
                            "type": "BUYBACK_CHAIN",
                            "ticker": tk,
                            "expiry": exp,
                            "kind": kind_label,
                            "strike": strike,
                            "premium": prem,
                            "drop_pct": drop,
                            "spread_pct": spr,
                            "reasons": reasons,
                            "ts": dt.datetime.now().isoformat(),
                        }

                        self.alert_fn(event)


# ===============================================================
#                       WRAPPER CLASSES
# ===============================================================

class StoppableSpike:
    """
    Thread-friendly wrapper for SpikeScanner.
    The GUI will call .stop() to end the loop cleanly.
    """

    def __init__(self, cfg: SpikeConfig, alert_fn: Callable[[Dict], None]):
        self.scanner = SpikeScanner(cfg, alert_fn)
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        while not self._stop_flag:
            self.scanner.run_once()
            time.sleep(1)


class StoppableBuyback:
    """
    Thread-friendly wrapper for Unified BuybackEngine.
    Uses .stop() flag to halt the loop from GUI.
    """

    def __init__(self, cfg: BuybackConfig, alert_fn: Callable[[Dict], None]):
        self.engine = BuybackEngine(cfg, alert_fn)
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        while not self._stop_flag:
            self.engine.run_once()
            time.sleep(self.engine.cfg.interval_secs)

# ===============================================================
#                POSITIONS / CSV HELPER FUNCTIONS
# ===============================================================

"""
These helpers allow OptionSuite to load/save position files
for the Buyback Engine or manual contract lists.
"""

import csv
from pathlib import Path


def load_positions_csv(path: str) -> List[Contract]:
    """
    Load short option positions from a CSV file.

    Expected CSV columns:
        ticker, kind, strike, expiry, open_credit, qty, note

    Extra columns will be ignored.
    Missing optional columns default automatically.
    """
    pos = []
    p = Path(path)
    if not p.exists():
        return pos

    with p.open("r", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                c = Contract(
                    ticker=row.get("ticker", "").strip().upper(),
                    kind=row.get("kind", "").strip().upper(),
                    strike=float(row.get("strike", 0)),
                    expiry=row.get("expiry", "").strip(),
                    open_credit=float(row.get("open_credit", 0)),
                    qty=int(row.get("qty", 1)),
                    note=row.get("note", ""),
                )
                pos.append(c)
            except Exception:
                continue

    return pos


def save_positions_csv(path: str, contracts: List[Contract]) -> None:
    """
    Save a list of Contract objects to CSV.
    """
    p = Path(path)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "kind", "strike", "expiry", "open_credit", "qty", "note"])
        for c in contracts:
            w.writerow([
                c.ticker, c.kind, c.strike, c.expiry,
                c.open_credit, c.qty, c.note
            ])


# ===============================================================
#             EXTRA UTILITIES / SHARED FUNCTIONS
# ===============================================================

def safe_float(x, default=float("nan")):
    try:
        return float(x)
    except:
        return default


def safe_int(x, default=0):
    try:
        return int(x)
    except:
        return default


def format_event_msg(event: Dict) -> str:
    """
    Create a human-readable log line for SPIKE or BUYBACK events.
    The GUI can use this or override with its own formatting.
    """
    t = event.get("type")

    if t == "SPIKE":
        return (
            f"[SPIKE] {event.get('ticker')} {event.get('expiry')} "
            f"{event.get('kind')}{event.get('strike')} "
            f"prem={event.get('prem'):.2f} "
            f"pct={event.get('pct'):.1f}% "
            f"spread={event.get('spread'):.1f}% "
            f"@ {event.get('ts')}"
        )

    if t == "BUYBACK":
        reasons = ",".join(event.get("reasons", []))
        return (
            f"[BUYBACK] {event.get('ticker')} {event.get('expiry')} "
            f"{event.get('kind')}{event.get('strike')} "
            f"prem={event.get('premium'):.2f} "
            f"capture={event.get('capture_pct'):.1f}% "
            f"drop={event.get('drop_pct'):.1f}% "
            f"spread={event.get('spread_pct'):.1f}% "
            f"reasons={reasons} "
            f"@ {event.get('ts')}"
        )

    if t == "BUYBACK_CHAIN":
        reasons = ",".join(event.get("reasons", []))
        return (
            f"[BUYBACK_CHAIN] {event.get('ticker')} {event.get('expiry')} "
            f"{event.get('kind')}{event.get('strike')} "
            f"prem={event.get('premium'):.2f} "
            f"drop={event.get('drop_pct'):.1f}% "
            f"spread={event.get('spread_pct'):.1f}% "
            f"reasons={reasons} "
            f"@ {event.get('ts')}"
        )

    return f"[EVENT] {event}"

# ===============================================================
#                 SIMPLE COMMAND-LINE INTERFACE
# ===============================================================

"""
This section gives OptionSuite a minimal CLI interface.
You can run parts of the engine directly without the GUI.
Useful for debugging, quick tests, or running headless.
"""

def _cli_alert_printer(event: Dict):
    """Default CLI alert printer."""
    print(format_event_msg(event))


def run_spike_cli():
    """
    Example CLI runner for SpikeScanner.
    Edit tickers list or pass arguments as needed.
    """
    cfg = SpikeConfig(
        tickers=["AMD", "AAPL"],
        min_pct=20,
        min_abs=0.05,
        min_premium=0.05,
        max_spread_pct=25,
        exp_filter_days=21,
        cooldown_secs=15,
    )
    runner = StoppableSpike(cfg, _cli_alert_printer)
    print("[SPIKE] Starting CLI spike scan. Press Ctrl+C to stop.")
    try:
        runner.run()
    except KeyboardInterrupt:
        print("[SPIKE] Stopped.")


def run_buyback_cli():
    """
    Example CLI runner for BuybackEngine.
    Modify below for your own test contracts.
    """
    contracts = [
        Contract("AMD", "C", 200, "2025-01-17", open_credit=1.20),
    ]
    cfg = BuybackConfig(
        contracts=contracts,
        targets=[80, 90, 95],
        floor=0.05,
        min_capture_pct=50,
        drop_pct_since_last=30,
        interval_secs=15,
        max_spread_pct=20,
        scan_entire_chain=False,
    )
    runner = StoppableBuyback(cfg, _cli_alert_printer)
    print("[BUYBACK] Starting CLI buyback monitor. Press Ctrl+C to stop.")
    try:
        runner.run()
    except KeyboardInterrupt:
        print("[BUYBACK] Stopped.")

# ===============================================================
#           OPTIONAL: MAIN ENTRYPOINT FOR CLI EXECUTION
# ===============================================================

"""
This lets you run the backend directly:

    python OptionSuite_FreshStart.py --spike
    python OptionSuite_FreshStart.py --buyback

Everything else (GUI) imports this file but is optional here.
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("   python OptionSuite_FreshStart.py --spike")
        print("   python OptionSuite_FreshStart.py --buyback")
        print("")
        sys.exit(0)

    mode = sys.argv[1].lower()

    if mode == "--spike":
        run_spike_cli()
        return

    if mode == "--buyback":
        run_buyback_cli()
        return

    print(f"Unknown mode: {mode}")
    print("Use --spike or --buyback")


if __name__ == "__main__":
    main()

# ===============================================================
#                      END OF FILE
# ===============================================================

# The full backend engine is now complete.
# This file includes:
#   - SpikeScanner (unchanged)
#   - Unified BuybackEngine (Step B)
#   - Stoppable wrappers for threading
#   - CSV load/save helpers
#   - Utility formatting functions
#   - Minimal CLI interface
#
# This is the complete OptionSuite_FreshStart_UPDATED backend.



