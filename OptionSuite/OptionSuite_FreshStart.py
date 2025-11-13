#!/usr/bin/env python3
# OptionSuite_FreshStart.py
from __future__ import annotations

import argparse, csv, dataclasses, datetime as dt, functools, itertools, json, math, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Set

try:
    import pandas as pd
    import numpy as np
    import yfinance as yf
except Exception as e:
    print("Missing dependency. Please pip install yfinance pandas numpy", file=sys.stderr)
    raise

# --------- small utils ----------
def now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def safe_float(x, default=float("nan")):
    try:
        return float(x)
    except Exception:
        return default

def mid_from_row(row):
    bid = safe_float(row.get("bid"))
    ask = safe_float(row.get("ask"))
    if math.isfinite(bid) and math.isfinite(ask) and (ask > 0 or bid > 0):
        if not math.isfinite(bid):
            return ask
        if not math.isfinite(ask):
            return bid
        return (bid + ask) / 2.0
    return float("nan")

def choose_premium(row):
    last = safe_float(row.get("lastPrice"))
    if math.isfinite(last) and last > 0:
        return last
    mid = mid_from_row(row)
    if math.isfinite(mid) and mid > 0:
        return mid
    ask = safe_float(row.get("ask"))
    bid = safe_float(row.get("bid"))
    for v in (ask, bid):
        if math.isfinite(v) and v > 0:
            return v
    return float("nan")

def spread_pct(row):
    bid = safe_float(row.get("bid"))
    ask = safe_float(row.get("ask"))
    mid = mid_from_row(row)
    if not all(map(math.isfinite, (bid, ask, mid))) or mid <= 0:
        return float("inf")
    return (ask - bid) / mid

def parse_tickers_input(raw: str, presets_dir: str) -> List[str]:
    if not raw:
        return []
    tokens = re.split(r"[,\s]+", raw.strip())
    out, seen = [], set()
    for t in tokens:
        if not t:
            continue
        if t.startswith("@"):
            name = t[1:]
            path = os.path.join(presets_dir, f"{name}.txt")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip().upper()
                        if not s or s.startswith("#"):
                            continue
                        if s not in seen:
                            out.append(s)
                            seen.add(s)
            else:
                print(f"[WARN] preset not found: {name} -> {path}")
        else:
            s = t.strip().upper()
            if s and s not in seen:
                out.append(s)
                seen.add(s)
    return out

# --------- spot fetch (no FutureWarning) ----------
def get_spot_price(t) -> float:
    """Best-effort last price without Pandas deprecation warnings."""
    try:
        v = t.fast_info.get("last_price", None)
        v = float(v)
        if math.isfinite(v) and v > 0:
            return v
    except Exception:
        pass
    try:
        h = t.history(period="5d")
        if hasattr(h, "empty") and not h.empty:
            close = h["Close"].dropna()
            if len(close) > 0:
                return float(close.iloc[-1])  # iloc avoids FutureWarning
    except Exception:
        pass
    return float("nan")

# ---------------- Expiration rule ----------------
def parse_exp_filter(raw: str, ticker) -> List[str]:
    """
    Simple rule:
      - Blank -> nearest only
      - Bare YYYY-MM-DD -> all expirations <= that date (inclusive)

    Kept for compatibility if you still use them:
      - all
      - next:N
      - YYYY-MM-DD:YYYY-MM-DD  (inclusive)
      - date:YYYY-MM-DD[,YYYY-MM-DD,...]
    """
    try:
        exps = list(ticker.options or [])
    except Exception:
        exps = []
    if not exps:
        return []

    s = (raw or "").strip().lower()
    if s == "":
        return exps[:1]

    # bare cutoff date → up to & including that date
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        cutoff = dt.date.fromisoformat(s)
        return [e for e in exps if dt.date.fromisoformat(e) <= cutoff]

    if s == "all":
        return exps
    if s.startswith("next:"):
        try:
            n = int(s.split(":", 1)[1])
        except Exception:
            n = 1
        return exps[:max(0, n)]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}", s):
        a, b = s.split(":", 1)
        A, B = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        return [e for e in exps if A <= dt.date.fromisoformat(e) <= B]
    if s.startswith("date:"):
        outs = []
        for tok in re.split(r"[,\s]+", s.split(":", 1)[1]):
            tok = tok.strip()
            if tok in exps:
                outs.append(tok)
        return outs or exps[:1]

    return exps[:1]

# ---------------- Strike filter (simple: all | range | single) ----------------
def parse_strike_filter(raw: str, spot: float, chain_strikes: Iterable[float]) -> Set[float]:
    strikes = sorted(set(float(x) for x in chain_strikes))
    if not strikes:
        return set()
    s = (raw or "").strip().lower()

    # all / blank → everything
    if s == "" or s == "all":
        return set(strikes)

    # range:LO-HI or LO-HI
    m = re.match(r"^(?:range:)?\s*([0-9]*\.?[0-9]+)\s*-\s*([0-9]*\.?[0-9]+)\s*$", s)
    if m:
        lo = float(m.group(1)); hi = float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return {k for k in strikes if lo <= k <= hi}

    # single strike (e.g., "145")
    try:
        v = float(s)
        return set(strikes).intersection({v})
    except Exception:
        # Unrecognized → fail-open to all to avoid missing data.
        return set(strikes)

# ---------- type parse ----------
def oc_type(s: str) -> str:
    s = (s or "").strip().lower()
    if s in ("call", "c", "calls"):
        return "call"
    if s in ("put", "p", "puts"):
        return "put"
    return "both"

# ---------- logging ----------
class AlertLog:
    def __init__(self, path=None):
        self.path = path
        if path and not os.path.isfile(path):
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(
                    ["timestamp", "kind", "ticker", "expiry", "type", "strike", "from", "to", "abs", "pct", "iv", "spread", "extra"]
                )

    def write(self, row):
        print(" | ".join(str(x) for x in row))
        if self.path:
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)

# ---------- spike scanner ----------
from dataclasses import dataclass

@dataclass
class SpikeConfig:
    tickers: list
    exp_filter: str
    kind: str
    strike_filter: str
    min_abs: float
    min_pct: float
    min_premium: float
    max_spread_pct: float
    cooldown_secs: int
    interval_secs: int
    workers: int
    log_path: str | None
    max_contracts_per_ticker: int
    verbose: bool

class SpikeScanner:
    def __init__(self, cfg: SpikeConfig, presets_dir: str):
        self.cfg = cfg
        self.presets_dir = presets_dir
        self.alert_log = AlertLog(cfg.log_path)
        self.prev = {}
        self.last_alert = {}

    def resolve_tickers(self):
        return parse_tickers_input(",".join(self.cfg.tickers), self.presets_dir)

    def run_forever(self):
        tks = self.resolve_tickers()
        if not tks:
            print("[ERROR] no tickers.")
            return
        print(
            f"[START] {len(tks)} tickers | type={self.cfg.kind} | ±{self.cfg.min_pct}% | ${self.cfg.min_abs} | "
            f"{self.cfg.interval_secs}s | exp={self.cfg.exp_filter} | minPrem=${self.cfg.min_premium} | "
            f"spread<={int(self.cfg.max_spread_pct*100)}% | cooldown={self.cfg.cooldown_secs}s | "
            f"strikes={self.cfg.strike_filter} | maxC={self.cfg.max_contracts_per_ticker}"
        )
        while True:
            t0 = time.time()
            try:
                self._scan_once(tks)
            except KeyboardInterrupt:
                print("\n[STOP]")
                return
            except Exception as e:
                print(f"[WARN] scan err: {e}")
            time.sleep(max(0, self.cfg.interval_secs - int(time.time() - t0)))

    def _scan_once(self, tickers):
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.workers)) as ex:
            for fu in as_completed([ex.submit(self._scan_one, tk) for tk in tickers]):
                try:
                    fu.result()
                except Exception as e:
                    print(f"[WARN] job failed: {e}")

    def _scan_one(self, tk):
        t = yf.Ticker(tk)
        spot = get_spot_price(t)

        exps = parse_exp_filter(self.cfg.exp_filter, t)
        if not exps:
            print(f"[WARN] {tk}: no expirations.")
            return

        for exp in exps:
            try:
                chain = t.option_chain(exp)
            except Exception as e:
                print(f"[WARN] {tk} chain({exp}) {e}")
                continue

            for side_name, df in (("call", chain.calls), ("put", chain.puts)):
                if self.cfg.kind != "both" and side_name != self.cfg.kind:
                    continue
                if df is None or len(df) == 0:
                    continue

                strikes_allowed = parse_strike_filter(self.cfg.strike_filter, spot, df["strike"].tolist())
                if not strikes_allowed:
                    continue

                df = df[df["strike"].isin(strikes_allowed)].copy()
                df["__prem"] = df.apply(choose_premium, axis=1)
                df = df[np.isfinite(df["__prem"]) & (df["__prem"] >= self.cfg.min_premium)].copy()
                if len(df) == 0:
                    continue

                df["__spread"] = df.apply(spread_pct, axis=1)
                df = df[df["__spread"] <= self.cfg.max_spread_pct].copy()
                if len(df) == 0:
                    continue

                if self.cfg.max_contracts_per_ticker > 0 and len(df) > self.cfg.max_contracts_per_ticker:
                    df = df.sort_values(by="__prem", ascending=False).head(self.cfg.max_contracts_per_ticker)

                for _, row in df.iterrows():
                    strike = float(row["strike"])
                    key = (tk, exp, side_name, strike)
                    new = float(row["__prem"])
                    old = self.prev.get(key, float("nan"))
                    self.prev[key] = new
                    if not math.isfinite(old):
                        continue
                    abs_move = new - old
                    if abs_move <= 0:
                        continue
                    pct_move = (abs_move / old) * 100.0 if old > 0 else float("inf")

                    # fire when EITHER threshold passes (percent or absolute)
                    if abs_move < self.cfg.min_abs and pct_move < self.cfg.min_pct:
                        continue

                    last_ts = self.last_alert.get(key, 0.0)
                    if time.time() - last_ts < self.cfg.cooldown_secs:
                        continue
                    self.last_alert[key] = time.time()

                    iv = safe_float(row.get("impliedVolatility"))
                    spr = float(row["__spread"])
                    extra = json.dumps(
                        {
                            "bid": safe_float(row.get("bid")),
                            "ask": safe_float(row.get("ask")),
                            "spot": spot if math.isfinite(spot) else None,
                            "volume": safe_float(row.get("volume")),
                            "oi": safe_float(row.get("openInterest")),
                        }
                    )
                    line = [
                        now_str(),
                        "SPIKE",
                        tk,
                        exp,
                        side_name.upper(),
                        strike,
                        f"{old:.2f}",
                        f"{new:.2f}",
                        f"{abs_move:.2f}",
                        f"{pct_move:.1f}%",
                        f"{iv:.4f}" if math.isfinite(iv) else "",
                        f"{spr:.2f}",
                        extra,
                    ]
                    self.alert_log.write(line)

# ---------- buyback ----------
@dataclasses.dataclass
class Contract:
    ticker: str
    kind: str
    strike: float
    expiry: str
    open_credit: float
    qty: int = 1
    note: str = ""

@dataclasses.dataclass
class BuybackConfig:
    contracts: List[Contract]
    targets: List[float]
    floor: float
    drop_pct_since_last: float
    interval_secs: int
    max_spread_pct: float
    log_path: str | None
    verbose: bool

class BuybackMonitor:
    def __init__(self, cfg: BuybackConfig):
        self.cfg = cfg
        self.alert_log = AlertLog(cfg.log_path)
        self.prev = {}

    def run_forever(self):
        print(
            f"[START] buyback: {len(self.cfg.contracts)} contracts | targets={self.cfg.targets} | "
            f"floor=${self.cfg.floor} | drop_since_last>={self.cfg.drop_pct_since_last}% | every {self.cfg.interval_secs}s"
        )
        while True:
            t0 = time.time()
            try:
                self._check_all()
            except KeyboardInterrupt:
                print("\n[STOP]")
                return
            except Exception as e:
                print(f"[WARN] loop err: {e}")
            time.sleep(max(0, self.cfg.interval_secs - int(time.time() - t0)))

    def _check_all(self):
        for c in self.cfg.contracts:
            try:
                self._check_one(c)
            except Exception as e:
                print(f"[WARN] {c.ticker} {c.expiry} {c.kind}{c.strike}: {e}")

    def _check_one(self, c: Contract):
        t = yf.Ticker(c.ticker)
        spot = get_spot_price(t)
        try:
            chain = t.option_chain(c.expiry)
        except Exception as e:
            print(f"[WARN] {c.ticker} chain({c.expiry}) err: {e}")
            return
        df = chain.calls if c.kind.upper() == "C" else chain.puts
        if df is None or len(df) == 0:
            print(f"[WARN] {c.ticker} {c.expiry} {c.kind}: empty chain")
            return
        row = df[df["strike"] == c.strike]
        if len(row) == 0:
            print(f"[WARN] {c.ticker} {c.expiry} {c.kind}: strike {c.strike} not found")
            return
        row = row.iloc[0].to_dict()
        prem = choose_premium(row)
        spr = spread_pct(row)
        iv = safe_float(row.get("impliedVolatility"))
        vol = safe_float(row.get("volume"))
        oi = safe_float(row.get("openInterest"))
        if not math.isfinite(prem) or prem < 0:
            print(f"[INFO] {c.ticker} {c.expiry} {c.kind}{c.strike}: premium N/A")
            return
        if spr > self.cfg.max_spread_pct:
            print(f"[INFO] {c.ticker} {c.expiry} {c.kind}{c.strike}: spread {spr:.2f} too wide")
            return
        key = (c.ticker, c.expiry, c.kind, c.strike)
        prev = self.prev.get(key, float("nan"))
        self.prev[key] = prem
        captured = (c.open_credit - prem) / c.open_credit * 100.0 if c.open_credit > 0 else 0.0
        ts = now_str()
        print(f"[{ts}] {c.ticker}=${spot:.2f}  {c.kind}{c.strike} {c.expiry}  prem={prem:.2f}  captured={captured:.1f}%  spread={spr:.2f}")
        extras = json.dumps({"spot": spot if math.isfinite(spot) else None})

        for tgt in self.cfg.targets:
            if captured >= float(tgt):
                self.alert_log.write(
                    [
                        ts,
                        f"BUYBACK-{int(tgt)}%",
                        c.ticker,
                        c.expiry,
                        c.kind,
                        c.strike,
                        f"{c.open_credit:.2f}",
                        f"{prem:.2f}",
                        f"{(c.open_credit - prem):.2f}",
                        f"{captured:.1f}%",
                        f"{iv:.4f}" if math.isfinite(iv) else "",
                        f"{spr:.2f}",
                        extras,
                    ]
                )
                break

        if prem <= self.cfg.floor:
            self.alert_log.write(
                [
                    ts,
                    "BUYBACK-FLOOR",
                    c.ticker,
                    c.expiry,
                    c.kind,
                    c.strike,
                    f"{c.open_credit:.2f}",
                    f"{prem:.2f}",
                    f"{(c.open_credit - prem):.2f}",
                    f"{captured:.1f}%",
                    f"{iv:.4f}" if math.isfinite(iv) else "",
                    f"{spr:.2f}",
                    extras,
                ]
            )

        if math.isfinite(prev) and prev > 0 and prem < prev:
            drop_pct = (prev - prem) / prev * 100.0
            if drop_pct >= self.cfg.drop_pct_since_last:
                self.alert_log.write(
                    [
                        ts,
                        f"BUYBACK-DROP{int(self.cfg.drop_pct_since_last)}%",
                        c.ticker,
                        c.expiry,
                        c.kind,
                        c.strike,
                        f"{prev:.2f}",
                        f"{prem:.2f}",
                        f"{(prev - prem):.2f}",
                        f"{drop_pct:.1f}%",
                        f"{iv:.4f}" if math.isfinite(iv) else "",
                        f"{spr:.2f}",
                        extras,
                    ]
                )

# ---------- positions helpers ----------
def load_positions_csv(path: str):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r, 1):
            try:
                out.append(
                    dict(
                        ticker=row["Ticker"].strip().upper(),
                        kind=row["Type"].strip().upper(),
                        strike=float(row["Strike"]),
                        expiry=row["Expiry"].strip(),
                        open_credit=float(row["OpenCredit"]),
                        qty=int(row.get("Qty", "1")),
                        note=row.get("Note", "").strip(),
                    )
                )
            except Exception as e:
                print(f"[WARN] positions row {i} skipped: {e}")
    return [Contract(**d) for d in out]

def parse_contract_expr(exprs: List[str]):
    out = []
    for ex in exprs:
        kv = {}
        for tok in re.split(r"[,\s]+", ex.strip()):
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k.strip().upper()] = v.strip()
        try:
            out.append(
                Contract(
                    ticker=kv["TICKER"].upper(),
                    kind=kv.get("TYPE", "C").upper(),
                    strike=float(kv["STRIKE"]),
                    expiry=kv["EXPIRY"],
                    open_credit=float(kv.get("OPEN", kv.get("OPENCREDIT", "0"))),
                    qty=int(kv.get("QTY", "1")),
                    note=kv.get("NOTE", ""),
                )
            )
        except Exception as e:
            print(f"[WARN] contract expr skipped: {ex} -> {e}")
    return out

# ---------- CLI ----------
def cmd_spike(args):
    presets_dir = os.path.join(os.path.dirname(__file__), "presets")
    os.makedirs(presets_dir, exist_ok=True)
    cfg = SpikeConfig(
        tickers=args.tickers,
        exp_filter=args.exp,
        kind=oc_type(args.type),
        strike_filter=args.strike_filter,
        min_abs=float(args.min_abs),
        min_pct=float(args.min_pct),
        min_premium=float(args.min_premium),
        max_spread_pct=float(args.max_spread),
        cooldown_secs=int(args.cooldown),
        interval_secs=int(args.interval),
        workers=int(args.workers),
        log_path=args.log,
        max_contracts_per_ticker=int(args.max_contracts),
        verbose=bool(args.verbose),
    )
    SpikeScanner(cfg, presets_dir).run_forever()

def cmd_buyback(args):
    contracts = []
    if args.positions:
        contracts.extend(load_positions_csv(args.positions))
    if args.contract:
        contracts.extend(parse_contract_expr(args.contract))
    if not contracts:
        print("[ERROR] No contracts. Use --positions and/or --contract ...")
        return
    cfg = BuybackConfig(
        contracts=contracts,
        targets=[float(x) for x in re.split(r"[,\s]+", args.targets.strip()) if x],
        floor=float(args.floor),
        drop_pct_since_last=float(args.drop_pct),
        interval_secs=int(args.interval),
        max_spread_pct=float(args.max_spread),
        log_path=args.log,
        verbose=bool(args.verbose),
    )
    BuybackMonitor(cfg).run_forever()

def build_parser():
    p = argparse.ArgumentParser(
        description="OptionSuite Fresh Start — spikes & buybacks",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("spike", help="Scan option premiums for sudden increases")
    ps.add_argument("--tickers", required=True, nargs="+", help="Tickers and/or preset tokens (prefix with @, e.g., @sp500)")
    ps.add_argument("--type", default="both", help="Option type: call, put, both")
    ps.add_argument("--exp", default="", help="Expirations: blank=nearest; YYYY-MM-DD=through that date; also supports next:N, date:..., range:...")
    ps.add_argument("--strike-filter", default="", help="Strikes: blank/all | 145 | 145-170 | range:145-170")
    ps.add_argument("--min-abs", default="0.10", help="Min absolute premium increase ($)")
    ps.add_argument("--min-pct", default="25", help="Min percent premium increase (%%)")
    ps.add_argument("--min-premium", default="0.20", help="Ignore contracts below this premium ($)")
    ps.add_argument("--max-spread", default="0.80", help="Max allowed spread fraction of mid (e.g., 0.8=80%%)")
    ps.add_argument("--cooldown", default="300", help="Per-contract alert cooldown (s)")
    ps.add_argument("--interval", default="60", help="Polling interval (s)")
    ps.add_argument("--workers", default="4", help="Parallel ticker workers")
    ps.add_argument("--max-contracts", default="200", help="Cap contracts per ticker after filters")
    ps.add_argument("--log", default=None, help="Append alerts to CSV")
    ps.add_argument("--verbose", action="store_true")
    ps.set_defaults(func=cmd_spike)

    pb = sub.add_parser("buyback", help="Monitor short options and alert when premium drops (captured)")
    pb.add_argument("--positions", default=None, help="CSV: Ticker,Type,Strike,Expiry,OpenCredit,Qty,Note")
    pb.add_argument("--contract", action="append", default=[], help="Inline: TICKER=AMD TYPE=C STRIKE=145 EXPIRY=2025-12-19 OPEN=0.85 QTY=1")
    pb.add_argument("--targets", default="80,85,90,95", help="Captured percent targets (comma-separated)")
    pb.add_argument("--floor", default="0.05", help="Floor premium ($)")
    pb.add_argument("--drop-pct", default="20", help="Drop %% since last check (runtime)")
    pb.add_argument("--interval", default="90", help="Polling interval (s)")
    pb.add_argument("--max-spread", default="0.80", help="Max allowed spread fraction")
    pb.add_argument("--log", default=None, help="Append alerts to CSV")
    pb.add_argument("--verbose", action="store_true")
    pb.set_defaults(func=cmd_buyback)
    return p

def main(argv=None):
    argv = argv or sys.argv[1:]
    p = build_parser()
    if not argv:
        p.print_help(sys.stderr)
        return 2
    args = p.parse_args(argv)
    if not hasattr(args, "func"):
        p.print_help(sys.stderr)
        return 2
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
