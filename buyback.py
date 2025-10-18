# buyback_unified.py
# Single or CSV-portfolio buyback monitor for short options.
# Deps: yfinance (required), win10toast (optional for Windows toasts)
# CSV schema (case-insensitive headers): Ticker,Strike,Premium,Expiry,Type,Qty
#   - Premium = your OPEN CREDIT per contract (e.g., 10.00 for $1,000 credit)
#   - Type = call|put
#   - Qty optional (defaults 1)

import sys, os, csv, time, argparse, datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import yfinance as yf

# ---------- Notifications ----------
_toaster = None
try:
    from win10toast import ToastNotifier
    _toaster = ToastNotifier()
except Exception:
    _toaster = None

def notify(title: str, body: str, enable_toast: bool = True):
    # terminal bell
    try:
        print("\a", end=""); sys.stdout.flush()
    except Exception:
        pass
    # Windows toast
    if enable_toast and _toaster:
        try:
            _toaster.show_toast(title, body, duration=5, threaded=True)
        except Exception:
            pass

# ---------- Helpers ----------
def chicago_now() -> dt.datetime:
    return dt.datetime.now()

def mid_price(bid, ask, last) -> Optional[float]:
    try:
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        if last is not None and last > 0:
            return round(last, 2)
    except Exception:
        pass
    return None

def get_underlying_price(t: yf.Ticker) -> Optional[float]:
    # fast_info path
    try:
        fi = getattr(t, "fast_info", None)
        if fi:
            p = None
            if hasattr(fi, "get"):
                p = fi.get("last_price") or fi.get("lastTradePrice")
            else:
                p = getattr(fi, "last_price", None) or getattr(fi, "lastTradePrice", None)
            if p is not None:
                return float(p)
    except Exception:
        pass
    # .info fallback
    try:
        info = t.info
        p = info.get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    # history fallback
    try:
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None

def dte_days(exp_yyyy_mm_dd: str) -> int:
    try:
        exp_date = dt.datetime.strptime(exp_yyyy_mm_dd, "%Y-%m-%d").date()
        return (exp_date - dt.date.today()).days
    except Exception:
        return 0

def approx_equal(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol

def parse_targets(targets_str: str) -> List[int]:
    levels: List[int] = []
    for x in str(targets_str).split(","):
        x = x.strip().rstrip("%")
        if x.isdigit():
            levels.append(int(x))
    return sorted(set(levels))

# ---------- Alert logging ----------
def append_alert(csv_path: str, rowdict: Dict[str, Any]):
    if not csv_path:
        return
    new_file = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rowdict.keys()))
        if new_file:
            w.writeheader()
        w.writerow(rowdict)

# ---------- Single-contract mode ----------
@dataclass
class TargetHit:
    name: str
    triggered: bool = False

def run_single(ticker: str, opt_type: str, strike: float, exp: str,
               open_credit: float, poll: int, targets_str: str,
               abs_threshold: Optional[float], quiet: bool,
               alerts_csv: Optional[str], toast_enabled: bool):
    stock = yf.Ticker(ticker)
    is_call = (opt_type == "call")
    levels = parse_targets(targets_str)
    targets = { f"{lvl}%": TargetHit(f"{lvl}%") for lvl in levels }
    if abs_threshold is not None:
        targets[f"≤ ${abs_threshold:.2f}"] = TargetHit(f"≤ ${abs_threshold:.2f}")

    print(f"[START] Monitoring {ticker} {exp} {strike:g} {opt_type.upper()} | open credit ${open_credit:.2f}")
    dte = dte_days(exp)
    print(f"Targets: {', '.join(targets.keys()) or '(none)'} | Poll: {poll}s | DTE: {dte}")

    while True:
        try:
            if dte_days(exp) < 0:
                print("[END] Option expired; stopping monitor.")
                break

            chain = stock.option_chain(exp)
            table = chain.calls if is_call else chain.puts
            row = table.loc[table['strike'].apply(lambda s: approx_equal(float(s), float(strike)))]
            if row.empty:
                if not quiet:
                    print(f"[{chicago_now()}] Contract not found at strike {strike}.")
                time.sleep(poll); continue

            bid  = None if row['bid'].isna().iloc[0] else float(row['bid'].iloc[0])
            ask  = None if row['ask'].isna().iloc[0] else float(row['ask'].iloc[0])
            last = None if row['lastPrice'].isna().iloc[0] else float(row['lastPrice'].iloc[0])
            mark = mid_price(bid, ask, last)
            if mark is None:
                if not quiet:
                    print(f"[{chicago_now()}] No price yet (bid={bid} ask={ask} last={last}).")
                time.sleep(poll); continue

            u = get_underlying_price(stock)
            captured = max(0.0, (open_credit - mark) / open_credit)
            captured_pct = int(round(captured * 100))
            head = (f"[{chicago_now()}]  {ticker}=${u:.2f}  " if u is not None else f"[{chicago_now()}]  ")
            head += f"{ticker} {exp} {strike:g} {opt_type.upper()}  mid=${mark:.2f}  captured={captured_pct}%  DTE={dte_days(exp)}"

            reasons: List[str] = []
            # percent-captured ladder
            for lvl in levels:
                if captured_pct >= lvl:
                    label = f"{lvl}% captured"
                    # print each ladder level once
                    if label not in reasons:  # local de-dupe in this loop
                        reasons.append(label)
            # absolute floor
            if abs_threshold is not None and mark <= abs_threshold:
                reasons.append(f"ABS ≤ ${abs_threshold:.2f}")

            if reasons:
                msg = head + "  >>> ALERT: " + " | ".join(reasons)
                print(msg)
                notify("Buyback target hit (single)", msg, enable_toast=toast_enabled)
                append_alert(alerts_csv or "", {
                    "ts": str(chicago_now()), "mode": "single", "ticker": ticker, "type": opt_type,
                    "exp": exp, "strike": strike, "mid": mark, "underlying": u if u is not None else "",
                    "open_credit": open_credit, "captured_pct": captured_pct, "reasons": " | ".join(reasons)
                })
            else:
                if not quiet:
                    print(head)

            time.sleep(poll)
        except KeyboardInterrupt:
            print("\n[STOP] Monitor interrupted by user."); break
        except Exception as e:
            print(f"[ERROR] {e}"); time.sleep(max(5, poll))

# ---------- CSV portfolio mode ----------
def read_positions_csv(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        for row in r:
            row_norm = { (k or "").strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() }
            # map to canonical keys
            out.append({
                "ticker": row_norm.get("ticker","").upper(),
                "strike": float(row_norm.get("strike","0") or 0),
                "premium": float(row_norm.get("premium","0") or 0),  # open credit
                "expiry": row_norm.get("expiry",""),
                "type": (row_norm.get("type","call") or "call").lower(),
                "qty": int(float(row_norm.get("qty","1") or 1)),
            })
    return [r for r in out if r["ticker"] and r["strike"] and r["premium"] and r["expiry"]]

def group_by_ticker_exp(positions: List[Dict[str, Any]]) -> Dict[Tuple[str,str], List[Dict[str, Any]]]:
    g: Dict[Tuple[str,str], List[Dict[str, Any]]] = {}
    for p in positions:
        key = (p["ticker"], p["expiry"])
        g.setdefault(key, []).append(p)
    return g

def portfolio_once(df: List[Dict[str, Any]], poll: int,
                   abs_floor: Optional[float], pct_drop_alert: Optional[float],
                   targets_str: str, quiet: bool, alerts_csv: Optional[str],
                   toast_enabled: bool):
    # Build tickers once
    tickers: Dict[str, yf.Ticker] = {}
    for tk in {r["ticker"] for r in df}:
        tickers[tk] = yf.Ticker(tk)

    # Parse ladder
    levels = parse_targets(targets_str)

    # Group by (ticker, expiry) to reuse option_chain calls
    grouped = group_by_ticker_exp(df)
    for (tk, exp), rows in grouped.items():
        try:
            stock = tickers[tk]
            chain = stock.option_chain(exp)
        except Exception as e:
            if not quiet:
                print(f"[{chicago_now()}] Failed to fetch chain for {tk} {exp}: {e}")
            continue

        # prefetch underlying once per ticker pass
        u = get_underlying_price(stock)
        u_txt = f"{tk}=${u:.2f}  " if u is not None else ""

        for r in rows:
            try:
                strike = r["strike"]
                is_call = (r["type"] == "call")
                table = chain.calls if is_call else chain.puts
                sub = table.loc[table['strike'].apply(lambda s: approx_equal(float(s), float(strike)))]
                if sub.empty:
                    if not quiet:
                        print(f"[{chicago_now()}] {u_txt}{tk} {exp} {strike:g} {r['type'].upper()}  (contract not found)")
                    continue

                bid  = None if sub['bid'].isna().iloc[0] else float(sub['bid'].iloc[0])
                ask  = None if sub['ask'].isna().iloc[0] else float(sub['ask'].iloc[0])
                last = None if sub['lastPrice'].isna().iloc[0] else float(sub['lastPrice'].iloc[0])
                mark = mid_price(bid, ask, last)
                if mark is None:
                    if not quiet:
                        print(f"[{chicago_now()}] {u_txt}{tk} {exp} {strike:g} {r['type'].upper()}  (no price yet)")
                    continue

                entry = float(r["premium"])
                captured = max(0.0, (entry - mark) / entry) if entry > 0 else 0.0
                captured_pct = int(round(captured * 100))
                base = f"[{chicago_now()}]  {u_txt}{tk} {exp} {strike:g} {r['type'].upper()}  mid=${mark:.2f}  captured={captured_pct}%  DTE={dte_days(exp)}"

                # ---- triggers ----
                reasons: List[str] = []

                # 1) percent-captured ladder (NEW in portfolio mode)
                for lvl in levels:
                    if captured_pct >= lvl:
                        reasons.append(f"{lvl}% captured")

                # 2) absolute floor
                if abs_floor is not None and mark <= abs_floor:
                    reasons.append(f"ABS ≤ ${abs_floor:.2f}")

                # 3) % drop from entry (e.g., 50% off)
                if pct_drop_alert is not None and entry > 0:
                    drop = (entry - mark) / entry * 100.0
                    if drop >= pct_drop_alert:
                        reasons.append(f"{pct_drop_alert:.0f}% drop from entry")

                if reasons:
                    msg = base + "  >>> ALERT: " + " | ".join(reasons)
                    print(msg)
                    notify("Buyback (portfolio)", msg, enable_toast=toast_enabled)
                    append_alert(alerts_csv or "", {
                        "ts": str(chicago_now()), "mode": "csv", "ticker": tk, "type": r["type"],
                        "exp": exp, "strike": strike, "mid": mark, "underlying": u if u is not None else "",
                        "open_credit": entry, "captured_pct": captured_pct, "reasons": " | ".join(reasons),
                        "qty": r.get("qty", 1)
                    })
                else:
                    if not quiet:
                        print(base)

            except Exception as ie:
                print(f"[ERROR] {tk} {exp} {r.get('type','').upper()} {r.get('strike','')}: {ie}")

# ---------- CLI / entry ----------
def interactive_single() -> Dict[str, Any]:
    print("\n=== Buyback Monitor Wizard (Single) ===")
    ticker = (input("Ticker [AMD]: ") or "AMD").upper()
    typ    = (input("Type call/put [call]: ") or "call").lower()
    strike = float(input("Strike [290]: ") or 290)
    exp    = input("Expiration YYYY-MM-DD [2026-02-20]: ") or "2026-02-20"
    open_c = float(input("Open credit per contract (e.g., 10.00): "))
    poll   = int(input("Poll seconds [60]: ") or 60)
    targets = input("Percent-captured ladder [50,65,75,85,90]: ") or "50,65,75,85,90"
    floor  = input("Absolute floor (blank to disable) [0.20]: ") or "0.20"
    abs_thr = float(floor) if floor.strip() != "" else None
    quiet  = (input("Quiet mode? alerts only y/N: ") or "n").lower().startswith("y")
    return dict(ticker=ticker, opt_type=typ, strike=strike, exp=exp,
                open_credit=open_c, poll=poll, targets=targets,
                abs_threshold=abs_thr, quiet=quiet)

def main():
    ap = argparse.ArgumentParser(description="Unified Buyback Monitor (single or CSV portfolio)")
    # Single
    ap.add_argument("--ticker")
    ap.add_argument("--type", choices=["call","put"], default="call")
    ap.add_argument("--strike", type=float)
    ap.add_argument("--exp")
    ap.add_argument("--open-credit", type=float)
    # Common
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--targets", default="50,65,75,85,90")
    ap.add_argument("--abs-threshold", type=float, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--alerts-csv", default="buyback_alerts.csv")
    ap.add_argument("--no-toast", action="store_true")
    # Portfolio
    ap.add_argument("--from-csv", help="Path to positions.csv for portfolio mode")
    ap.add_argument("--pct-drop-alert", type=float, default=None, help="Alert if premium has dropped by this % from entry (e.g., 50)")
    args = ap.parse_args()

    # Portfolio mode
    if args.from_csv:
        if not os.path.exists(args.from_csv):
            print(f"[ERROR] CSV not found: {args.from_csv}")
            sys.exit(1)
        positions = read_positions_csv(args.from_csv)
        if not positions:
            print("[ERROR] No valid rows found in CSV. Expect headers: Ticker,Strike,Premium,Expiry,Type,Qty")
            sys.exit(1)
        print(f"[START] Portfolio mode — {len(positions)} position(s) | Poll: {args.poll}s")
        while True:
            try:
                portfolio_once(
                    positions, args.poll, args.abs_threshold, args.pct_drop_alert,
                    args.targets, args.quiet, args.alerts_csv, toast_enabled=(not args.no-toast)
                )
                time.sleep(args.poll)
            except KeyboardInterrupt:
                print("\n[STOP] Portfolio monitor interrupted by user.")
                break
            except Exception as e:
                print(f"[ERROR] portfolio loop: {e}")
                time.sleep(max(5, args.poll))
        return

    # Single mode (flags or wizard)
    flags_used = any(getattr(args, k) is not None for k in ("ticker","strike","exp","open_credit"))
    if flags_used:
        missing = [name for name, val in (("ticker",args.ticker),("strike",args.strike),("exp",args.exp),("open_credit",args.open_credit)) if val is None]
        if missing:
            ap.error(f"Missing required flags for single mode: {', '.join(missing)}")
        run_single(
            args.ticker, args.type, float(args.strike), args.exp, float(args.open_credit),
            args.poll, args.targets, args.abs_threshold, args.quiet, args.alerts_csv, toast_enabled=(not args.no_toast)
        )
    else:
        w = interactive_single()
        run_single(
            w["ticker"], w["opt_type"], float(w["strike"]), w["exp"], float(w["open_credit"]),
            w["poll"], w["targets"], w["abs_threshold"], w["quiet"], args.alerts_csv, toast_enabled=(not args.no_toast)
        )

if __name__ == "__main__":
    main()
