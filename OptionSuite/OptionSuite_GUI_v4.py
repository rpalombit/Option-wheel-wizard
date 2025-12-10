#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
import datetime as dt
from typing import List, Dict, Any, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Optional dark theme
try:
    import sv_ttk
except Exception:
    sv_ttk = None

# Backend core (your existing engine)
try:
    import OptionSuite_FreshStart as core
except Exception:
    core = None

# yfinance for options data
try:
    import yfinance as yf
except Exception:
    yf = None


# =====================================================
#  LOGGING ADAPTERS
# =====================================================
class LogRouter:
    """Generic text logger to the Logs tab."""

    def __init__(self, log_widget: tk.Text):
        self.log_widget = log_widget

    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", line + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")


class BuybackGuiLog:
    """Adapter so BuybackMonitor.alert_log writes into GUI log + recent alerts."""

    def __init__(self, log_widget: tk.Text, recent_widget: tk.Listbox, max_recent: int = 40):
        self.log_widget = log_widget
        self.recent_widget = recent_widget
        self.max_recent = max_recent

    def _write_line(self, line: str) -> None:
        # full log
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", line + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        # recent alerts
        self.recent_widget.insert("end", line)
        if self.recent_widget.size() > self.max_recent:
            self.recent_widget.delete(0)
        self.recent_widget.see("end")

    def info(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._write_line(f"[BUYBACK][{ts}] {msg}")

    def write(self, row: List[Any]) -> None:
        """Expected row: [ts, kind, ticker, exp, side, strike, p_open, p_now, abs_move, pct, iv, spr, ...]"""
        try:
            ts, kind, tk_sym, exp, side, strike, pfrom, pto, absm, pct, iv, spr = row[:12]
            side = (side or "").upper()
            line = (
                f"{ts}  {tk_sym} {exp} {side}{strike}  "
                f"{pfrom}→{pto}  Δ${absm}  {pct}  IV {iv}  SPR {spr}"
            )
        except Exception:
            line = " | ".join(str(x) for x in row)
        self._write_line(f"[BUYBACK] {line}")


# =====================================================
#  SPIKE LOGGER ADAPTER
# =====================================================
class GuiSpikeLogger:
    """
    Adapter so SpikeScanner alerts go to GUI Scanner tab + Logs tab.

    It must implement a .write(row) method compatible with core.AlertLog usage.
    """

    def __init__(self, gui: "OptionSuiteGUI"):
        self.gui = gui

    def _insert_row(self, vals: Tuple[Any, ...], log_line: str) -> None:
        # Insert into scanner alert table
        self.gui.alert_table.insert("", "end", values=vals)
        self.gui.logger.log(log_line)

    def write(self, row: List[Any]) -> None:
        """
        Expected core spike line format:
        [timestamp, 'SPIKE', tk, exp, type, strike, from, to, abs, pct, iv, spr, extra]
        """
        try:
            ts, kind, tk_sym, exp, oc_type, strike, old_s, new_s, abs_s, pct_s, iv_s, spr_s = row[:12]
        except Exception:
            # If anything weird, just ignore to avoid crashing GUI
            return

        # Values for Scanner Treeview:
        # columns = ("ticker", "strike", "exp", "premium", "pct", "volume", "time")
        vals = (
            tk_sym,
            strike,
            exp,
            new_s,     # premium now
            pct_s,     # % change
            "",        # volume not present in spike engine right now
            ts,
        )
        log_line = f"[Spike] {tk_sym} {exp} {oc_type}{strike} Δ{pct_s}"

        # Schedule on main thread (tkinter not thread-safe)
        try:
            self.gui.after(0, self._insert_row, vals, log_line)
        except Exception:
            # If GUI is closing, ignore
            pass


# =====================================================
#  STOPPABLE BUYBACK RUNNER
# =====================================================
class StoppableBuyback(core.BuybackMonitor if core else object):
    """Wraps core.BuybackMonitor with a stop event and GUI logging."""

    def __init__(self, cfg, gui_log: BuybackGuiLog):
        if core:
            super().__init__(cfg)
        self._stop = threading.Event()
        self.alert_log = gui_log

    def stop(self) -> None:
        self._stop.set()

    def run_gui_loop(self) -> None:
        if not core:
            self.alert_log.info("core (OptionSuite_FreshStart) not available; cannot run Buyback engine.")
            return

        self.alert_log.info(
            f"Start buyback: {len(self.cfg.contracts)} contracts | "
            f"targets={self.cfg.targets} | floor=${self.cfg.floor} | "
            f"drop_since_last>={self.cfg.drop_pct_since_last}% | "
            f"every {self.cfg.interval_secs}s"
        )

        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._check_all()
            except Exception as e:
                self.alert_log.info(f"Buyback loop error: {e}")
            elapsed = time.time() - t0
            wait = max(0, self.cfg.interval_secs - int(elapsed))
            if self._stop.wait(wait):
                break


# =====================================================
#  STOPPABLE SPIKE RUNNER
# =====================================================
class StoppableSpike:
    """
    Wraps core.SpikeScanner with a stop event and routes alerts into the GUI.
    """

    def __init__(self, cfg: core.SpikeConfig, gui_logger: GuiSpikeLogger):
        self.cfg = cfg
        self.gui_logger = gui_logger
        self._stop = threading.Event()

        # Resolve presets_dir from GUI if present
        gui = gui_logger.gui
        presets_dir = getattr(gui, "presets_dir", os.path.join(os.path.dirname(__file__), "presets"))

        self.engine = core.SpikeScanner(cfg, presets_dir=presets_dir)
        # Override engine's AlertLog with GUI logger
        self.engine.alert_log = gui_logger

    def stop(self) -> None:
        self._stop.set()

    def run_gui_loop(self) -> None:
        gui = self.gui_logger.gui
        try:
            tks = self.engine.resolve_tickers()
        except Exception as e:
            gui.logger.log(f"[Spike ERROR] resolve_tickers: {e}")
            return

        gui.logger.log(
            f"[Spike] Starting scanner on {len(tks)} tickers | "
            f"min_abs=${self.cfg.min_abs} | min_pct={self.cfg.min_pct}% | "
            f"exp_filter={self.cfg.exp_filter}"
        )

        while not self._stop.is_set():
            t0 = time.time()
            try:
                # only one pass per loop
                self.engine._scan_once(tks)
            except Exception as e:
                gui.logger.log(f"[Spike ERROR] scan: {e}")
            elapsed = time.time() - t0
            wait = max(0, self.cfg.interval_secs - int(elapsed))
            if self._stop.wait(wait):
                break

        gui.logger.log("[Spike] Scanner stopped.")


# =====================================================
#  OPTIONS CHAIN VIA YFINANCE
# =====================================================
def fetch_yf_options_chain(symbol: str, max_exps: int = 8) -> List[Dict[str, Any]]:
    """
    Use yfinance to pull options chain for up to max_exps expirations.

    Returns list of rows:
      {
        'strike': float,
        'call_bid': float or None,
        'call_ask': float or None,
        'put_bid': float or None,
        'put_ask': float or None,
        'exp': 'YYYY-MM-DD',
        'call_delta': float or None,
        'put_delta': float or None
      }
    """
    if yf is None:
        raise RuntimeError("yfinance is not installed. Run: pip install yfinance pandas")

    symbol = (symbol or "").strip().upper()
    if not symbol:
        return []

    t = yf.Ticker(symbol)
    expirations = t.options
    if not expirations:
        return []

    rows_map: Dict[Tuple[str, float], Dict[str, Any]] = {}

    def process_exp(exp_str: str) -> None:
        try:
            chain = t.option_chain(exp_str)
        except Exception:
            return
        calls = getattr(chain, "calls", [])
        puts = getattr(chain, "puts", [])
        # calls
        for _, row in calls.iterrows():
            strike = float(row.get("strike", 0.0))
            key = (exp_str, strike)
            r = rows_map.setdefault(
                key,
                {
                    "strike": strike,
                    "call_bid": None,
                    "call_ask": None,
                    "put_bid": None,
                    "put_ask": None,
                    "exp": exp_str,
                    "call_delta": None,
                    "put_delta": None,
                },
            )
            bid = row.get("bid")
            ask = row.get("ask")
            delta = row.get("delta")
            if bid is not None:
                r["call_bid"] = float(bid)
            if ask is not None:
                r["call_ask"] = float(ask)
            if delta is not None:
                try:
                    r["call_delta"] = float(delta)
                except Exception:
                    pass
        # puts
        for _, row in puts.iterrows():
            strike = float(row.get("strike", 0.0))
            key = (exp_str, strike)
            r = rows_map.setdefault(
                key,
                {
                    "strike": strike,
                    "call_bid": None,
                    "call_ask": None,
                    "put_bid": None,
                    "put_ask": None,
                    "exp": exp_str,
                    "call_delta": None,
                    "put_delta": None,
                },
            )
            bid = row.get("bid")
            ask = row.get("ask")
            delta = row.get("delta")
            if bid is not None:
                r["put_bid"] = float(bid)
            if ask is not None:
                r["put_ask"] = float(ask)
            if delta is not None:
                try:
                    r["put_delta"] = float(delta)
                except Exception:
                    pass

    for exp_str in expirations[:max_exps]:
        process_exp(exp_str)

    rows = list(rows_map.values())
    rows.sort(key=lambda r: (r["exp"], r["strike"]))
    return rows


def fetch_underlying_price(symbol: str) -> Optional[float]:
    """Best-effort yfinance spot price."""
    if yf is None:
        return None
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return None
    t = yf.Ticker(symbol)
    # Try fast_info
    try:
        fi = getattr(t, "fast_info", None)
        if fi and "lastPrice" in fi:
            return float(fi["lastPrice"])
        if fi and "last_price" in fi:
            return float(fi["last_price"])
    except Exception:
        pass
    # Fallback: last close
    try:
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


# =====================================================
#  MAIN GUI
# =====================================================
class OptionSuiteGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OptionSuite GUI v5 – Spike | Buyback | Wheel Builder")
        self.geometry("1600x950")

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.presets_dir = os.path.join(self.base_dir, "presets")

        # state
        self.tickers: List[str] = []  # global ticker list from presets/manual
        self.scan_thread: Optional[threading.Thread] = None
        self.scan_runner = None  # StoppableSpike instance

        self.buy_thread: Optional[threading.Thread] = None
        self.buy_runner: Optional[StoppableBuyback] = None

        # buyback conf vars
        self.buy_positions_path = tk.StringVar(value="")
        self.buy_targets = tk.StringVar(value="80,85,90,95")
        self.buy_floor = tk.StringVar(value="0.05")
        self.buy_drop = tk.StringVar(value="20")
        self.buy_interval = tk.StringVar(value="90")
        self.buy_max_spread = tk.StringVar(value="0.80")

        # manual contracts (raw core.parse_contract_expr strings)
        self.manual_contract_exprs: List[str] = []

        # options chain table state (Buyback tab)
        self.chain_rows: Dict[str, Dict[str, Any]] = {}  # Treeview iid -> row data
        self.chain_sort_reverse: Dict[str, bool] = {}

        # Wheel/CSP builder state
        self.builder_symbol_var = tk.StringVar(value="")
        self.builder_type_var = tk.StringVar(value="CSP")  # CSP or CC
        self.builder_exp_var = tk.StringVar(value="")
        self.builder_strike_var = tk.StringVar(value="")

        self.builder_underlying_var = tk.StringVar(value="-")
        self.builder_premium_var = tk.StringVar(value="-")
        self.builder_delta_var = tk.StringVar(value="-")
        self.builder_be_var = tk.StringVar(value="-")
        self.builder_collateral_var = tk.StringVar(value="-")
        self.builder_roc_var = tk.StringVar(value="-")
        self.builder_ann_roc_var = tk.StringVar(value="-")
        self.builder_prob_var = tk.StringVar(value="-")
        self.builder_summary_text: str = ""

        self.builder_chain_by_exp: Dict[str, List[Dict[str, Any]]] = {}
        self.builder_spot_cache: Dict[str, float] = {}

        if sv_ttk is not None:
            sv_ttk.set_theme("dark")

        self.build_menu_bar()
        self.build_header()
        self.build_tabs()
        self.build_status_bar()

    # ---------------- Menu ----------------
    def build_menu_bar(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Load positions.csv", command=self.buy_load_positions)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    # ---------------- Header ----------------
    def build_header(self) -> None:
        header = ttk.Frame(self, padding=10)
        header.pack(fill="x")

        ttk.Label(header, text="Preset:").pack(side="left", padx=5)
        self.preset_var = tk.StringVar()
        self.preset_dropdown = ttk.Combobox(header, width=20, textvariable=self.preset_var)
        self.preset_dropdown["values"] = ("sp100", "sp500", "nas100")
        self.preset_dropdown.pack(side="left", padx=5)

        ttk.Button(header, text="Load Preset", command=self.load_preset).pack(side="left", padx=5)

        ttk.Label(header, text="Add Ticker:").pack(side="left", padx=15)
        self.manual_ticker = tk.Entry(header, width=12)
        self.manual_ticker.pack(side="left", padx=5)
        ttk.Button(header, text="Add", command=self.add_manual_ticker).pack(side="left")

    # ---------------- Tabs ----------------
    def build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.scanner_tab = ttk.Frame(self.notebook)
        self.buyback_tab = ttk.Frame(self.notebook)
        self.wheel_tab = ttk.Frame(self.notebook)
        self.logs_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.scanner_tab, text="Scanner")
        self.notebook.add(self.buyback_tab, text="Buyback")
        self.notebook.add(self.wheel_tab, text="Wheel / CSP Builder")
        self.notebook.add(self.logs_tab, text="Logs")

        self.build_scanner_tab()
        self.build_buyback_tab()
        self.build_wheel_tab()
        self.build_logs_tab()

    def build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready.")
        lbl = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=5)
        lbl.pack(fill="x", side="bottom")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    # =====================================================
    #  SCANNER TAB
    # =====================================================
    def build_scanner_tab(self) -> None:
        outer = ttk.Frame(self.scanner_tab, padding=10)
        outer.pack(fill="both", expand=True)

        # left = ticker list
        left = ttk.Frame(outer)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="Current Tickers:").pack(anchor="w")
        self.ticker_listbox = tk.Listbox(left, height=25, width=20)
        self.ticker_listbox.pack(fill="y", pady=5)

        btn_frame = ttk.Frame(left)
        btn_frame.pack(fill="x", pady=5)
        ttk.Button(btn_frame, text="Remove Selected", command=self.remove_selected_ticker).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Clear All", command=self.clear_all_tickers).pack(fill="x", pady=2)

        # right = scanner controls + alert table
        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True, padx=10)

        control = ttk.Frame(right)
        control.pack(fill="x", pady=5)

        ttk.Label(control, text="Cooldown (sec):").pack(side="left", padx=5)
        self.cooldown_var = tk.StringVar(value="30")
        ttk.Entry(control, textvariable=self.cooldown_var, width=6).pack(side="left")

        ttk.Label(control, text="Min Spike %:").pack(side="left", padx=15)
        self.min_spike_var = tk.StringVar(value="10")
        ttk.Entry(control, textvariable=self.min_spike_var, width=6).pack(side="left")

        ttk.Label(control, text="Exp ≤ days:").pack(side="left", padx=15)
        self.exp_range_var = tk.StringVar(value="21")
        ttk.Entry(control, textvariable=self.exp_range_var, width=6).pack(side="left")

        ttk.Label(control, text=" " * 5).pack(side="left")
        ttk.Button(control, text="Start Scanner", command=self.start_scanner).pack(side="left", padx=5)
        ttk.Button(control, text="Stop Scanner", command=self.stop_scanner).pack(side="left", padx=5)

        table_frame = ttk.Frame(right)
        table_frame.pack(fill="both", expand=True, pady=10)

        columns = ("ticker", "strike", "exp", "premium", "pct", "volume", "time")
        self.alert_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=30)

        headers = ("Ticker", "Strike", "Exp", "Prem", "% Chg", "Vol", "Time")
        for col, label in zip(columns, headers):
            self.alert_table.heading(col, text=label)
            self.alert_table.column(col, width=90)

        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.alert_table.yview)
        self.alert_table.configure(yscroll=sb.set)
        self.alert_table.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

    # =====================================================
    #  BUYBACK TAB
    # =====================================================
    def build_buyback_tab(self) -> None:
        root = self.buyback_tab

        # positions.csv + global ticker helper
        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")

        ttk.Button(top, text="Load positions.csv", command=self.buy_load_positions).pack(side="left")
        self.buy_positions_label = ttk.Label(top, textvariable=self.buy_positions_path, width=60)
        self.buy_positions_label.pack(side="left", padx=6)

        ttk.Button(top, text="Copy Scanner Tickers → Helper", command=self.copy_scanner_to_buyback_helper).pack(
            side="left", padx=10
        )

        # Settings row
        settings = ttk.Frame(root, padding=10)
        settings.pack(fill="x")

        ttk.Label(settings, text="Targets %").pack(side="left")
        ttk.Entry(settings, textvariable=self.buy_targets, width=12).pack(side="left", padx=4)

        ttk.Label(settings, text="Floor $").pack(side="left", padx=(16, 2))
        ttk.Entry(settings, textvariable=self.buy_floor, width=8).pack(side="left")

        ttk.Label(settings, text="Drop %").pack(side="left", padx=(16, 2))
        ttk.Entry(settings, textvariable=self.buy_drop, width=8).pack(side="left")

        ttk.Label(settings, text="Interval s").pack(side="left", padx=(16, 2))
        ttk.Entry(settings, textvariable=self.buy_interval, width=8).pack(side="left")

        ttk.Label(settings, text="Max spread").pack(side="left", padx=(16, 2))
        ttk.Entry(settings, textvariable=self.buy_max_spread, width=8).pack(side="left")

        ttk.Button(settings, text="Start Buyback", command=self.buy_monitor_start).pack(side="left", padx=10)
        ttk.Button(settings, text="Stop Buyback", command=self.buy_monitor_stop).pack(side="left")

        # Recent alerts mini-feed
        recent_frame = ttk.LabelFrame(root, text="Recent Buyback Alerts", padding=5)
        recent_frame.pack(fill="x", padx=10, pady=5)
        self.recent_alerts_list = tk.Listbox(recent_frame, height=5)
        self.recent_alerts_list.pack(fill="x", expand=True)

        ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=5)

        # main area: left=options chain, right=manual builder + helper
        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        # LEFT: Options chain
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        fetch_row = ttk.Frame(left)
        fetch_row.pack(fill="x", pady=3)

        ttk.Label(fetch_row, text="Ticker:").pack(side="left")
        self.chain_ticker_var = tk.StringVar(value="")
        ttk.Entry(fetch_row, textvariable=self.chain_ticker_var, width=12).pack(side="left", padx=4)
        ttk.Button(fetch_row, text="Fetch Options", command=self.fetch_chain).pack(side="left", padx=4)

        columns = ("strike", "call_bid", "call_ask", "put_bid", "put_ask", "exp")
        self.chain_tree = ttk.Treeview(left, columns=columns, show="headings", height=20, selectmode="browse")
        headings = {
            "strike": "Strike",
            "call_bid": "Call Bid",
            "call_ask": "Call Ask",
            "put_bid": "Put Bid",
            "put_ask": "Put Ask",
            "exp": "Expiration",
        }
        for col in columns:
            self.chain_tree.heading(col, text=headings[col], command=lambda c=col: self.sort_chain_table(c))
            self.chain_tree.column(col, width=90, anchor="center")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.chain_tree.yview)
        self.chain_tree.configure(yscroll=vsb.set)
        self.chain_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self.chain_tree.bind("<Double-1>", self.on_chain_double_click)

        # RIGHT: manual builder + helper
        right = ttk.Frame(main)
        right.pack(side="left", fill="y", padx=10)

        ttk.Label(right, text="Manual Contract Entry").pack(anchor="w")

        form = ttk.Frame(right)
        form.pack(fill="x", pady=2)

        ttk.Label(form, text="Ticker").grid(row=0, column=0, sticky="w")
        self.manual_sym_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.manual_sym_var, width=10).grid(row=0, column=1, padx=3)

        ttk.Label(form, text="Type").grid(row=0, column=2, sticky="w")
        self.manual_type_var = tk.StringVar(value="CALL")
        type_box = ttk.Combobox(
            form, textvariable=self.manual_type_var, values=("CALL", "PUT"), width=6, state="readonly"
        )
        type_box.grid(row=0, column=3, padx=3)

        ttk.Label(form, text="Strike").grid(row=1, column=0, sticky="w")
        self.manual_strike_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.manual_strike_var, width=10).grid(row=1, column=1, padx=3)

        ttk.Label(form, text="Expiry").grid(row=1, column=2, sticky="w")
        self.manual_exp_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.manual_exp_var, width=12).grid(row=1, column=3, padx=3)

        ttk.Label(form, text="Entry $").grid(row=2, column=0, sticky="w")
        self.manual_open_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.manual_open_var, width=10).grid(row=2, column=1, padx=3)

        ttk.Button(right, text="Add Contract", command=self.add_manual_contract).pack(anchor="w", pady=4)

        # helper: scanner tickers snapshot
        ttk.Label(right, text="Scanner Ticker Helper:").pack(anchor="w", pady=(6, 0))
        self.buy_scanner_helper = tk.Listbox(right, height=5, width=30)
        self.buy_scanner_helper.pack(fill="x")
        self.buy_scanner_helper.bind("<Double-1>", self.on_helper_double_click)

        # multi-line paste
        ttk.Label(right, text="Paste multiple (symbol,exp,type,strike,open)").pack(anchor="w", pady=(8, 0))
        self.multi_text = tk.Text(right, width=40, height=6)
        self.multi_text.pack(fill="x")
        ttk.Button(right, text="Add All Lines", command=self.add_multi_contracts).pack(anchor="e", pady=3)

        # manual contract list
        ttk.Label(right, text="Current Manual Contracts:").pack(anchor="w", pady=(8, 0))
        self.manual_listbox = tk.Listbox(right, height=8, width=45)
        self.manual_listbox.pack(fill="x")

        lb_btns = ttk.Frame(right)
        lb_btns.pack(fill="x", pady=3)
        ttk.Button(lb_btns, text="Remove Selected", command=self.remove_manual_selected).pack(side="left")
        ttk.Button(lb_btns, text="Clear All", command=self.clear_manual_all).pack(side="left", padx=4)

    # ---------------- Wheel / CSP Builder Tab ----------------
    def build_wheel_tab(self) -> None:
        root = self.wheel_tab
        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)

        # TOP: ticker + fetch
        top = ttk.Frame(outer)
        top.pack(fill="x", pady=5)

        ttk.Label(top, text="Ticker:").pack(side="left")
        ttk.Entry(top, textvariable=self.builder_symbol_var, width=12).pack(side="left", padx=4)
        ttk.Button(top, text="Fetch Chain", command=self.builder_fetch_chain).pack(side="left", padx=4)

        ttk.Label(top, text="Type:").pack(side="left", padx=(20, 4))
        rb_frame = ttk.Frame(top)
        rb_frame.pack(side="left")
        ttk.Radiobutton(rb_frame, text="CSP (Put)", value="CSP", variable=self.builder_type_var,
                        command=self.builder_recalc).pack(side="left")
        ttk.Radiobutton(rb_frame, text="CC (Call)", value="CC", variable=self.builder_type_var,
                        command=self.builder_recalc).pack(side="left")

        # MID: exp + strike
        mid = ttk.Frame(outer)
        mid.pack(fill="x", pady=5)

        ttk.Label(mid, text="Expiration:").pack(side="left")
        self.builder_exp_combo = ttk.Combobox(mid, textvariable=self.builder_exp_var, width=18, state="readonly")
        self.builder_exp_combo.pack(side="left", padx=4)
        self.builder_exp_combo.bind("<<ComboboxSelected>>", lambda e: self.builder_on_exp_change())

        ttk.Label(mid, text="Strike:").pack(side="left", padx=(20, 4))
        self.builder_strike_combo = ttk.Combobox(mid, textvariable=self.builder_strike_var, width=12, state="readonly")
        self.builder_strike_combo.pack(side="left", padx=4)
        self.builder_strike_combo.bind("<<ComboboxSelected>>", lambda e: self.builder_recalc())

        ttk.Button(mid, text="Recalculate", command=self.builder_recalc).pack(side="left", padx=8)

        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=8)

        # BOTTOM: Metrics
        metrics = ttk.Frame(outer)
        metrics.pack(fill="x")

        left = ttk.Frame(metrics)
        left.pack(side="left", fill="x", expand=True, padx=(0, 10))
        right = ttk.Frame(metrics)
        right.pack(side="left", fill="x", expand=True)

        # Left metrics (price, premium, delta, BE, collateral)
        def row(parent, r, label, var):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(parent, textvariable=var).grid(row=r, column=1, sticky="w", pady=2, padx=(4, 0))

        row(left, 0, "Underlying Price:", self.builder_underlying_var)
        row(left, 1, "Premium (per share):", self.builder_premium_var)
        row(left, 2, "Delta:", self.builder_delta_var)
        row(left, 3, "Breakeven:", self.builder_be_var)
        row(left, 4, "Collateral (per contract):", self.builder_collateral_var)

        # Right metrics (ROC, annualized, probability)
        row(right, 0, "ROC % (per contract):", self.builder_roc_var)
        row(right, 1, "Annualized ROC %:", self.builder_ann_roc_var)
        row(right, 2, "Assignment / Call-away Prob:", self.builder_prob_var)

        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=8)

        # ACTIONS
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=5)

        ttk.Button(actions, text="Add to Buyback Monitor", command=self.builder_add_to_buyback).pack(side="left")
        ttk.Button(actions, text="Copy Summary to Clipboard", command=self.builder_copy_summary).pack(
            side="left", padx=10
        )

        # Info
        info = ttk.Label(
            outer,
            text="Workflow: Choose CSP/CC → Fetch chain → Select expiration & strike → review metrics → Add to Buyback.",
        )
        info.pack(anchor="w", pady=4)

    # ---------------- Logs Tab ----------------
    def build_logs_tab(self) -> None:
        frame = ttk.Frame(self.logs_tab)
        frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(frame, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        self.logger = LogRouter(self.log_text)
        self.buy_gui_log = BuybackGuiLog(self.log_text, self.recent_alerts_list)

    # =====================================================
    #  PRESETS + TICKERS
    # =====================================================
    def load_preset(self) -> None:
        preset = (self.preset_var.get() or "").strip()
        if not preset:
            messagebox.showwarning("Preset", "Choose a preset name first.")
            return

        path = os.path.join(self.presets_dir, preset + ".txt")
        if not os.path.exists(path):
            messagebox.showerror("Preset", f"Preset file not found:\n{path}")
            return

        try:
            with open(path, "r") as f:
                items = [
                    ln.strip().upper()
                    for ln in f
                    if ln.strip() and not ln.strip().startswith("#")
                ]
        except Exception as e:
            messagebox.showerror("Preset", f"Error loading preset:\n{e}")
            return

        added = 0
        for sym in items:
            if sym not in self.tickers:
                self.tickers.append(sym)
                added += 1
        self.refresh_ticker_display()
        self.logger.log(f"Loaded preset '{preset}' ({added} new symbols).")
        self.set_status(f"Preset '{preset}' loaded.")

    def add_manual_ticker(self) -> None:
        sym = self.manual_ticker.get().strip().upper()
        if not sym:
            return
        if sym not in self.tickers:
            self.tickers.append(sym)
            self.refresh_ticker_display()
            self.logger.log(f"Added ticker: {sym}")
            self.set_status(f"{sym} added.")
        else:
            self.logger.log(f"{sym} already present.")
        self.manual_ticker.delete(0, "end")

    def refresh_ticker_display(self) -> None:
        self.ticker_listbox.delete(0, "end")
        for sym in sorted(self.tickers):
            self.ticker_listbox.insert("end", sym)

    def remove_selected_ticker(self) -> None:
        selected = list(self.ticker_listbox.curselection())
        if not selected:
            return
        removed = []
        for idx in reversed(selected):
            sym = self.ticker_listbox.get(idx)
            removed.append(sym)
            if sym in self.tickers:
                self.tickers.remove(sym)
        self.refresh_ticker_display()
        if removed:
            self.logger.log("Removed: " + ", ".join(removed))
            self.set_status("Tickers removed.")

    def clear_all_tickers(self) -> None:
        if not self.tickers:
            return
        self.tickers.clear()
        self.refresh_ticker_display()
        self.logger.log("Cleared all tickers.")
        self.set_status("All tickers cleared.")

    # =====================================================
    #  SCANNER (WIRED TO SPIKE ENGINE)
    # =====================================================
    def start_scanner(self) -> None:
        if core is None:
            messagebox.showerror(
                "Scanner",
                "core module (OptionSuite_FreshStart.py) not found.\n"
                "Place it in the same folder as this GUI script.",
            )
            return

        if not self.tickers:
            messagebox.showwarning("Scanner", "Load a preset or add tickers first.")
            return

        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scanner", "Spike scanner is already running.")
            return

        # Read GUI controls
        try:
            cooldown = int(self.cooldown_var.get())
            min_pct = float(self.min_spike_var.get())
            max_days = int(self.exp_range_var.get())
        except Exception:
            messagebox.showerror("Scanner", "Invalid numeric values in scanner settings.")
            return

        cutoff_date = (dt.date.today() + dt.timedelta(days=max_days)).isoformat()

        # Build SpikeConfig from GUI
        cfg = core.SpikeConfig(
            tickers=self.tickers,
            exp_filter=cutoff_date,          # "exp ≤ days" converted to cutoff date
            kind="both",                     # both calls and puts for now
            strike_filter="",                # all strikes
            min_abs=0.01,                    # small floor; GUI focuses on % spike
            min_pct=min_pct,
            min_premium=0.10,
            max_spread_pct=0.80,
            cooldown_secs=cooldown,
            interval_secs=30,                # fixed 30s loop for demo
            workers=4,
            log_path=None,
            max_contracts_per_ticker=200,
            verbose=False,
        )

        gui_logger = GuiSpikeLogger(self)
        self.scan_runner = StoppableSpike(cfg, gui_logger)

        def bg():
            self.scan_runner.run_gui_loop()

        self.scan_thread = threading.Thread(target=bg, daemon=True)
        self.scan_thread.start()

        self.logger.log("[Scanner] Spike scanner started.")
        self.set_status("Spike Scanner running.")

    def stop_scanner(self) -> None:
        if self.scan_runner:
            self.scan_runner.stop()
            self.logger.log("[Scanner] Stop signal sent.")
            self.set_status("Spike Scanner stopping...")
        else:
            self.logger.log("[Scanner] No active scanner.")
            self.set_status("No active scanner.")

    # =====================================================
    #  BUYBACK HELPERS
    # =====================================================
    def buy_load_positions(self) -> None:
        path = filedialog.askopenfilename(
            title="Select positions.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self.buy_positions_path.set(path)
        self.logger.log(f"Loaded positions file: {path}")
        self.set_status("positions.csv loaded.")

    def copy_scanner_to_buyback_helper(self) -> None:
        """Manual button: copy global tickers into helper listbox on Buyback tab."""
        self.buy_scanner_helper.delete(0, "end")
        for sym in sorted(self.tickers):
            self.buy_scanner_helper.insert("end", sym)
        self.logger.log("[Buyback] Copied scanner tickers into helper list.")
        self.set_status("Scanner tickers copied into Buyback helper.")

    def on_helper_double_click(self, event) -> None:
        """Double-click on helper ticker → set as chain ticker and fetch options."""
        sel = self.buy_scanner_helper.curselection()
        if not sel:
            return
        sym = self.buy_scanner_helper.get(sel[0])
        self.chain_ticker_var.set(sym)
        self.fetch_chain()

    # =====================================================
    #  OPTIONS CHAIN IN BUYBACK
    # =====================================================
    def fetch_chain(self) -> None:
        sym = (self.chain_ticker_var.get() or "").strip().upper()
        if not sym:
            messagebox.showwarning("Options Chain", "Enter a ticker symbol first.")
            return

        if yf is None:
            messagebox.showerror(
                "Options Chain",
                "yfinance is not installed.\nInstall with:\n\npip install yfinance pandas",
            )
            return

        self.logger.log(f"[Chain] Fetching options for {sym} via yfinance...")
        self.set_status(f"Fetching options for {sym}...")
        self.chain_tree.delete(*self.chain_tree.get_children())
        self.chain_rows.clear()

        try:
            rows = fetch_yf_options_chain(sym)
        except Exception as e:
            messagebox.showerror("Options Chain", f"Error fetching options:\n{e}")
            self.set_status("Options fetch error.")
            return

        if not rows:
            messagebox.showinfo("Options Chain", f"No options data found for {sym}.")
            self.set_status("No options found.")
            return

        for i, r in enumerate(rows):
            iid = str(i)
            vals = [
                f"{r['strike']:.2f}",
                "" if r["call_bid"] is None else f"{r['call_bid']:.2f}",
                "" if r["call_ask"] is None else f"{r['call_ask']:.2f}",
                "" if r["put_bid"] is None else f"{r['put_bid']:.2f}",
                "" if r["put_ask"] is None else f"{r['put_ask']:.2f}",
                r["exp"],
            ]
            self.chain_tree.insert("", "end", iid=iid, values=vals)
            self.chain_rows[iid] = r

        self.logger.log(f"[Chain] Loaded {len(rows)} option rows for {sym}.")
        self.set_status(f"Options loaded for {sym}.")

    def sort_chain_table(self, col: str) -> None:
        items = list(self.chain_tree.get_children(""))
        if not items:
            return

        reverse = self.chain_sort_reverse.get(col, False)

        def parse_num(s: str) -> float:
            try:
                return float(s)
            except Exception:
                return float("inf") if reverse else float("-inf")

        index_map = {"strike": 0, "call_bid": 1, "call_ask": 2, "put_bid": 3, "put_ask": 4, "exp": 5}

        entries: List[Tuple[Any, str]] = []
        for iid in items:
            vals = self.chain_tree.item(iid, "values")
            idx = index_map[col]
            v = vals[idx]
            if col == "exp":
                key = v
            else:
                key = parse_num(v)
            entries.append((key, iid))

        entries.sort(key=lambda t: t[0], reverse=reverse)
        for index, (_, iid) in enumerate(entries):
            self.chain_tree.move(iid, "", index)

        self.chain_sort_reverse[col] = not reverse

    def on_chain_double_click(self, event) -> None:
        """Double-click a row → autofill manual contract fields."""
        sel = self.chain_tree.selection()
        if not sel:
            return
        iid = sel[0]
        row = self.chain_rows.get(iid)
        if not row:
            return

        sym = (self.chain_ticker_var.get() or "").strip().upper()
        self.manual_sym_var.set(sym)
        self.manual_strike_var.set(f"{row['strike']:.2f}")
        self.manual_exp_var.set(row["exp"])

        t = (self.manual_type_var.get() or "CALL").upper()
        if t.startswith("C"):
            price = row["call_ask"] if row["call_ask"] is not None else row["call_bid"]
        else:
            price = row["put_ask"] if row["put_ask"] is not None else row["put_bid"]
        if price is not None:
            self.manual_open_var.set(f"{price:.2f}")

    # =====================================================
    #  MANUAL CONTRACT EXPRESSIONS
    # =====================================================
    def _expr_from_fields(self) -> Optional[str]:
        sym = (self.manual_sym_var.get() or "").strip().upper()
        kind = (self.manual_type_var.get() or "CALL").upper()
        strike_s = (self.manual_strike_var.get() or "").strip()
        exp = (self.manual_exp_var.get() or "").strip()
        open_s = (self.manual_open_var.get() or "").strip()

        if not sym or not strike_s or not exp:
            messagebox.showwarning("Manual Contract", "Ticker, Strike, and Expiry are required.")
            return None
        try:
            float(strike_s)
        except Exception:
            messagebox.showerror("Manual Contract", "Strike must be a number.")
            return None
        if open_s:
            try:
                float(open_s)
            except Exception:
                messagebox.showerror("Manual Contract", "Entry price must be a number.")
                return None

        kind_code = "C" if kind.startswith("C") else "P"
        parts = [
            f"ticker={sym}",
            f"type={kind_code}",
            f"strike={strike_s}",
            f"expiry={exp}",
        ]
        if open_s:
            parts.append(f"open={open_s}")
        return " ".join(parts)

    def add_manual_contract(self) -> None:
        expr = self._expr_from_fields()
        if not expr:
            return
        self.manual_contract_exprs.append(expr)
        self.manual_listbox.insert("end", expr)
        self.logger.log(f"[Manual] Added contract: {expr}")
        self.set_status("Manual contract added.")

    def add_multi_contracts(self) -> None:
        text = self.multi_text.get("1.0", "end").strip()
        if not text:
            return
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        added = 0
        for ln in lines:
            # expected: symbol,exp,type,strike,open
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) < 4:
                continue
            sym = parts[0].upper()
            exp = parts[1]
            typ = (parts[2] or "CALL").upper()
            strike = parts[3]
            open_s = parts[4] if len(parts) > 4 else ""
            kind_code = "C" if typ.startswith("C") else "P"
            expr_parts = [
                f"ticker={sym}",
                f"type={kind_code}",
                f"strike={strike}",
                f"expiry={exp}",
            ]
            if open_s:
                expr_parts.append(f"open={open_s}")
            expr = " ".join(expr_parts)
            self.manual_contract_exprs.append(expr)
            self.manual_listbox.insert("end", expr)
            added += 1
        self.logger.log(f"[Manual] Added {added} contracts from pasted lines.")
        self.set_status(f"Added {added} manual contracts.")

    def remove_manual_selected(self) -> None:
        selection = list(self.manual_listbox.curselection())
        if not selection:
            return
        for idx in reversed(selection):
            self.manual_listbox.delete(idx)
            try:
                del self.manual_contract_exprs[idx]
            except IndexError:
                pass
        self.logger.log("[Manual] Removed selected contracts.")
        self.set_status("Manual contracts updated.")

    def clear_manual_all(self) -> None:
        if not self.manual_contract_exprs and not self.manual_listbox.size():
            return
        self.manual_contract_exprs.clear()
        self.manual_listbox.delete(0, "end")
        self.logger.log("[Manual] Cleared all manual contracts.")
        self.set_status("Manual contracts cleared.")

    # =====================================================
    #  BUYBACK ENGINE START/STOP
    # =====================================================
    def buy_monitor_start(self) -> None:
        if core is None:
            messagebox.showerror(
                "Buyback",
                "core module (OptionSuite_FreshStart.py) not found.\n"
                "Place it in the same folder as this GUI script.",
            )
            return
        if self.buy_thread and self.buy_thread.is_alive():
            messagebox.showinfo("Buyback", "Buyback monitor is already running.")
            return

        contracts: List[Any] = []

        # positions.csv contracts
        if self.buy_positions_path.get():
            try:
                contracts.extend(core.load_positions_csv(self.buy_positions_path.get()))
            except Exception as e:
                messagebox.showerror("positions.csv", f"Error loading positions.csv:\n{e}")
                return

        # manual contracts
        if self.manual_contract_exprs:
            try:
                extra = core.parse_contract_expr(self.manual_contract_exprs)
                contracts.extend(extra)
            except Exception as e:
                messagebox.showerror("Manual Contracts", f"Error parsing manual contracts:\n{e}")
                return

        if not contracts:
            messagebox.showinfo(
                "Buyback",
                "No contracts to monitor.\nLoad a positions.csv file or add manual contracts first.",
            )
            return

        # parse settings
        import re as _re

        try:
            targets = [
                float(x)
                for x in _re.split(r"[,\s]+", self.buy_targets.get().strip())
                if x
            ]
        except Exception:
            messagebox.showerror("Buyback", "Invalid Targets %. Use comma- or space-separated numbers.")
            return

        try:
            floor = float(self.buy_floor.get() or 0.05)
            drop_pct = float(self.buy_drop.get() or 20)
            interval_secs = int(self.buy_interval.get() or 90)
            max_spread = float(self.buy_max_spread.get() or 0.80)
        except Exception:
            messagebox.showerror("Buyback", "Invalid numeric fields (floor, drop, interval, spread).")
            return

        cfg = core.BuybackConfig(
            contracts=contracts,
            targets=targets,
            floor=floor,
            drop_pct_since_last=drop_pct,
            interval_secs=interval_secs,
            max_spread_pct=max_spread,
            log_path=None,
            verbose=False,
        )

        runner = StoppableBuyback(cfg, gui_log=self.buy_gui_log)
        self.buy_runner = runner
        self.logger.log(f"[Buyback] START monitoring {len(contracts)} contract(s).")
        self.set_status("Buyback monitor running.")

        def bg():
            try:
                runner.run_gui_loop()
            finally:
                self.logger.log("[Buyback] Buyback monitor stopped.")
                self.set_status("Buyback monitor stopped.")

        self.buy_thread = threading.Thread(target=bg, daemon=True)
        self.buy_thread.start()

    def buy_monitor_stop(self) -> None:
        if self.buy_runner:
            self.buy_runner.stop()
            self.logger.log("[Buyback] Stop signal sent.")
            self.set_status("Buyback stop requested.")
        else:
            self.logger.log("[Buyback] No active buyback.")
            self.set_status("No active buyback.")

    # =====================================================
    #  WHEEL / CSP BUILDER LOGIC
    # =====================================================
    def builder_fetch_chain(self) -> None:
        symbol = (self.builder_symbol_var.get() or "").strip().upper()
        if not symbol:
            messagebox.showwarning("Builder", "Enter a ticker symbol first.")
            return
        if yf is None:
            messagebox.showerror(
                "Builder",
                "yfinance is not installed.\nInstall with:\n\npip install yfinance pandas",
            )
            return

        self.logger.log(f"[Builder] Fetching options chain for {symbol}...")
        self.set_status(f"Fetching chain for {symbol}...")

        try:
            rows = fetch_yf_options_chain(symbol, max_exps=12)
        except Exception as e:
            messagebox.showerror("Builder", f"Error fetching options:\n{e}")
            self.set_status("Builder chain fetch error.")
            return

        if not rows:
            messagebox.showinfo("Builder", f"No options data found for {symbol}.")
            self.set_status("No chain data found.")
            return

        # group by expiration
        by_exp: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_exp.setdefault(r["exp"], []).append(r)
        for exp in by_exp:
            by_exp[exp].sort(key=lambda x: x["strike"])

        self.builder_chain_by_exp = by_exp
        exps_sorted = sorted(by_exp.keys())
        self.builder_exp_combo["values"] = exps_sorted

        if exps_sorted:
            self.builder_exp_var.set(exps_sorted[0])
            self.builder_on_exp_change()

        # underlying price
        spot = fetch_underlying_price(symbol)
        if spot is not None:
            self.builder_spot_cache[symbol] = spot
            self.builder_underlying_var.set(f"${spot:.2f}")
        else:
            self.builder_underlying_var.set("-")

        self.logger.log(f"[Builder] Loaded {len(rows)} rows across {len(by_exp)} expirations for {symbol}.")
        self.set_status(f"Builder: chain loaded for {symbol}.")

    def builder_on_exp_change(self) -> None:
        exp = self.builder_exp_var.get()
        if not exp or exp not in self.builder_chain_by_exp:
            self.builder_strike_combo["values"] = ()
            self.builder_strike_var.set("")
            self.builder_recalc()
            return

        chain = self.builder_chain_by_exp[exp]
        strikes = [f"{r['strike']:.2f}" for r in chain]
        self.builder_strike_combo["values"] = strikes
        if strikes:
            # pick closest-to-ATM by default
            symbol = (self.builder_symbol_var.get() or "").strip().upper()
            spot = self.builder_spot_cache.get(symbol)
            if spot is not None:
                closest = min(chain, key=lambda r: abs(r["strike"] - spot))
                self.builder_strike_var.set(f"{closest['strike']:.2f}")
            else:
                self.builder_strike_var.set(strikes[0])
        self.builder_recalc()

    def _builder_get_selected_row(self) -> Optional[Dict[str, Any]]:
        exp = self.builder_exp_var.get()
        if not exp or exp not in self.builder_chain_by_exp:
            return None
        chain = self.builder_chain_by_exp[exp]
        strike_s = self.builder_strike_var.get()
        if not strike_s:
            return None
        try:
            strike = float(strike_s)
        except Exception:
            return None
        for r in chain:
            if abs(r["strike"] - strike) < 1e-6:
                return r
        return None

    def _builder_mid_price(self, r: Dict[str, Any], is_call: bool) -> Optional[float]:
        if is_call:
            bid, ask = r.get("call_bid"), r.get("call_ask")
        else:
            bid, ask = r.get("put_bid"), r.get("put_ask")
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if ask is not None and ask > 0:
            return float(ask)
        if bid is not None and bid > 0:
            return float(bid)
        return None

    def _builder_delta(self, r: Dict[str, Any], is_call: bool) -> Optional[float]:
        if is_call:
            d = r.get("call_delta")
        else:
            d = r.get("put_delta")
        if d is None:
            return None
        try:
            return float(d)
        except Exception:
            return None

    def _builder_dte(self, exp_str: str) -> Optional[int]:
        try:
            y, m, d = [int(x) for x in exp_str.split("-")]
            exp_date = dt.date(y, m, d)
            today = dt.date.today()
            dte = (exp_date - today).days
            if dte < 0:
                return None
            return max(dte, 1)
        except Exception:
            return None

    def _approx_prob_from_delta(self, delta: Optional[float], is_put: bool) -> Optional[float]:
        if delta is None:
            return None
        d = abs(delta)
        p = d * 100.0
        return max(0.0, min(100.0, p))

    def _approx_prob_from_moneyness(self, S: float, K: float, is_put: bool) -> float:
        # crude heuristic if no delta
        if S <= 0:
            return 50.0
        m = (K - S) / S if is_put else (S - K) / S

        # distance in % from spot to strike
        dist = abs(m) * 100.0

        if is_put:
            # probability S < K
            if K < S:  # ITM put at start
                base = 70.0 + min(20.0, (S - K) / S * 100.0)
            else:  # OTM
                if dist > 25:
                    base = 10.0
                elif dist > 15:
                    base = 25.0
                elif dist > 8:
                    base = 40.0
                elif dist > 4:
                    base = 55.0
                else:
                    base = 65.0
        else:
            # probability S > K (call-away)
            if K < S:  # ITM call
                base = 75.0 + min(20.0, (S - K) / S * 100.0)
            else:
                if dist > 25:
                    base = 10.0
                elif dist > 15:
                    base = 25.0
                elif dist > 8:
                    base = 40.0
                elif dist > 4:
                    base = 55.0
                else:
                    base = 65.0

        return max(0.0, min(100.0, base))

    def builder_recalc(self) -> None:
        """Recompute all metrics based on builder state."""
        symbol = (self.builder_symbol_var.get() or "").strip().upper()
        if not symbol:
            self.builder_underlying_var.set("-")
            self.builder_premium_var.set("-")
            self.builder_delta_var.set("-")
            self.builder_be_var.set("-")
            self.builder_collateral_var.set("-")
            self.builder_roc_var.set("-")
            self.builder_ann_roc_var.set("-")
            self.builder_prob_var.set("-")
            self.builder_summary_text = ""
            return

        row = self._builder_get_selected_row()
        exp = self.builder_exp_var.get()
        if not row or not exp:
            self.builder_premium_var.set("-")
            self.builder_delta_var.set("-")
            self.builder_be_var.set("-")
            self.builder_collateral_var.set("-")
            self.builder_roc_var.set("-")
            self.builder_ann_roc_var.set("-")
            self.builder_prob_var.set("-")
            self.builder_summary_text = ""
            return

        is_csp = (self.builder_type_var.get() or "CSP").upper() == "CSP"
        is_call = not is_csp

        # spot
        spot = self.builder_spot_cache.get(symbol)
        if spot is None:
            spot = fetch_underlying_price(symbol)
            if spot is not None:
                self.builder_spot_cache[symbol] = spot

        if spot is not None:
            self.builder_underlying_var.set(f"${spot:.2f}")
        else:
            self.builder_underlying_var.set("-")

        strike = float(row["strike"])
        premium = self._builder_mid_price(row, is_call=is_call)
        delta = self._builder_delta(row, is_call=is_call)
        dte = self._builder_dte(exp)

        if premium is None:
            self.builder_premium_var.set("-")
        else:
            self.builder_premium_var.set(f"${premium:.2f}")

        if delta is None:
            self.builder_delta_var.set("-")
        else:
            self.builder_delta_var.set(f"{delta:+.2f}")

        # Collateral always K*100 (1 contract)
        collateral = strike * 100.0
        self.builder_collateral_var.set(f"${collateral:,.2f}")

        roc = None
        ann_roc = None
        be = None
        prob = None

        if premium is not None and spot is not None and dte is not None:
            if is_csp:
                # CSP logic
                be = strike - premium
                roc = (premium / strike) * 100.0 if strike > 0 else None
                ann_roc = roc * (365.0 / dte) if roc is not None else None
                prob = self._approx_prob_from_delta(delta, is_put=True)
                if prob is None:
                    prob = self._approx_prob_from_moneyness(spot, strike, is_put=True)
            else:
                # Covered Call logic
                upside = max(0.0, strike - spot)
                max_profit = upside + premium
                roc = (max_profit / spot) * 100.0 if spot > 0 else None
                ann_roc = roc * (365.0 / dte) if roc is not None else None
                prob = self._approx_prob_from_delta(delta, is_put=False)
                if prob is None:
                    prob = self._approx_prob_from_moneyness(spot, strike, is_put=False)

        if be is not None:
            self.builder_be_var.set(f"${be:.2f}")
        else:
            self.builder_be_var.set("-")

        if roc is not None:
            self.builder_roc_var.set(f"{roc:.2f}%")
        else:
            self.builder_roc_var.set("-")

        if ann_roc is not None:
            self.builder_ann_roc_var.set(f"{ann_roc:.2f}%")
        else:
            self.builder_ann_roc_var.set("-")

        if prob is not None:
            self.builder_prob_var.set(f"{prob:.1f}%")
        else:
            self.builder_prob_var.set("-")

        # Summary text
        typ = "CSP" if is_csp else "CC"
        side = "PUT" if is_csp else "CALL"
        dte_str = f"{dte}d" if dte is not None else "N/A"
        prem_str = f"${premium:.2f}" if premium is not None else "N/A"
        be_str = f"${be:.2f}" if be is not None else "N/A"
        roc_str = f"{roc:.2f}%" if roc is not None else "N/A"
        ann_str = f"{ann_roc:.2f}%" if ann_roc is not None else "N/A"
        prob_str = f"{prob:.1f}%" if prob is not None else "N/A"

        summary_lines = [
            f"{symbol} {typ} setup:",
            f"  Exp: {exp}  Strike: {strike:.2f}  Type: {side}",
            f"  Spot: ${spot:.2f}" if spot is not None else "  Spot: N/A",
            f"  Premium: {prem_str}",
            f"  Breakeven: {be_str}",
            f"  ROC: {roc_str}  Annualized: {ann_str}  Horizon: {dte_str}",
            f"  Assignment/Call-away probability: {prob_str}",
        ]
        self.builder_summary_text = "\n".join(summary_lines)

    def builder_add_to_buyback(self) -> None:
        """Build a contract expression and push to Buyback manual list."""
        symbol = (self.builder_symbol_var.get() or "").strip().upper()
        exp = (self.builder_exp_var.get() or "").strip()
        strike_s = (self.builder_strike_var.get() or "").strip()
        if not symbol or not exp or not strike_s:
            messagebox.showwarning(
                "Builder",
                "You must have a ticker, expiration, and strike selected before adding to Buyback.",
            )
            return

        try:
            float(strike_s)
        except Exception:
            messagebox.showerror("Builder", "Strike is not numeric.")
            return

        is_csp = (self.builder_type_var.get() or "CSP").upper() == "CSP"
        kind_code = "P" if is_csp else "C"

        # Use premium if we have one
        open_price = None
        prem_text = self.builder_premium_var.get()
        if prem_text and prem_text.startswith("$"):
            try:
                open_price = float(prem_text.replace("$", ""))
            except Exception:
                open_price = None

        parts = [
            f"ticker={symbol}",
            f"type={kind_code}",
            f"strike={strike_s}",
            f"expiry={exp}",
        ]
        if open_price is not None:
            parts.append(f"open={open_price:.2f}")
        expr = " ".join(parts)

        self.manual_contract_exprs.append(expr)
        self.manual_listbox.insert("end", expr)
        self.logger.log(f"[Builder] Added to Buyback: {expr}")
        self.set_status("Builder contract added to Buyback manual list.")
        messagebox.showinfo(
            "Builder",
            "Contract added to Buyback monitor list.\n\nStart the Buyback tab when ready.",
        )

    def builder_copy_summary(self) -> None:
        if not self.builder_summary_text:
            messagebox.showinfo("Builder", "No summary available yet. Fetch chain and select a contract first.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.builder_summary_text)
        self.logger.log("[Builder] Copied summary to clipboard.")
        self.set_status("Builder summary copied to clipboard.")

    # =====================================================
    #  MISC
    # =====================================================
    def show_about(self) -> None:
        messagebox.showinfo(
            "About OptionSuite",
            "OptionSuite GUI v5\n"
            "- Spike Scanner wired to core\n"
            "- Buyback monitor wired to core\n"
            "- Wheel/CSP Position Builder using yfinance.\n"
            "\nThis version is a working capstone-ready prototype.",
        )


# =====================================================
#  MAIN
# =====================================================
if __name__ == "__main__":
    app = OptionSuiteGUI()
    app.mainloop()
