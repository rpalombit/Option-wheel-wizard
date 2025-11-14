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
        'exp': 'YYYY-MM-DD'
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
                },
            )
            bid = row.get("bid")
            ask = row.get("ask")
            if bid is not None:
                r["call_bid"] = float(bid)
            if ask is not None:
                r["call_ask"] = float(ask)
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
                },
            )
            bid = row.get("bid")
            ask = row.get("ask")
            if bid is not None:
                r["put_bid"] = float(bid)
            if ask is not None:
                r["put_ask"] = float(ask)

    for exp_str in expirations[:max_exps]:
        process_exp(exp_str)

    rows = list(rows_map.values())
    rows.sort(key=lambda r: (r["exp"], r["strike"]))
    return rows


# =====================================================
#  MAIN GUI
# =====================================================
class OptionSuiteGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OptionSuite GUI v4.1")
        self.geometry("1500x950")

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.presets_dir = os.path.join(self.base_dir, "presets")

        # state
        self.tickers: List[str] = []  # global ticker list from presets/manual
        self.scan_thread: Optional[threading.Thread] = None
        self.scan_runner = None  # future Spike scanner

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

        # options chain table state
        self.chain_rows: Dict[str, Dict[str, Any]] = {}  # Treeview iid -> row data
        self.chain_sort_reverse: Dict[str, bool] = {}

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
        self.notebook.add(self.wheel_tab, text="Wheel / CSP")
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

    # ---------------- Wheel Tab ----------------
    def build_wheel_tab(self) -> None:
        ttk.Label(self.wheel_tab, text="Wheel / CSP planning (to be added).").pack(pady=20)

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
    #  SCANNER (STUB FOR NOW)
    # =====================================================
    def start_scanner(self) -> None:
        if not self.tickers:
            messagebox.showwarning("Scanner", "Load a preset or add tickers first.")
            return
        self.logger.log(f"[Scanner] Starting on {len(self.tickers)} tickers (stub).")
        self.set_status("Scanner started (stub).")

    def stop_scanner(self) -> None:
        self.logger.log("[Scanner] Stop requested (stub).")
        self.set_status("Scanner stopped (stub).")

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

    # =====================================================
    #  MISC
    # =====================================================
    def show_about(self) -> None:
        messagebox.showinfo(
            "About OptionSuite",
            "OptionSuite GUI v4.1\n"
            "Buyback monitor + options chain via yfinance.\n"
            "Scanner wiring to Spike engine next.",
        )


# =====================================================
#  MAIN
# =====================================================
if __name__ == "__main__":
    app = OptionSuiteGUI()
    app.mainloop()
