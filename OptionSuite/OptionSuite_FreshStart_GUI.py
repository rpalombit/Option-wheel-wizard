#!/usr/bin/env python3
# OptionSuite_FreshStart_GUI.py — GUI log + Alerts pane + QoL upgrades (compact log, sound, trim)
from __future__ import annotations

import os, sys, threading, time, json, math, re, csv
from typing import List, Optional
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import sv_ttk  # optional dark theme (pip install sv-ttk)
except Exception:
    sv_ttk = None

# ---- engines ----
try:
    import OptionSuite_FreshStart as core
except Exception as e:
    raise SystemExit("Place OptionSuite_FreshStart.py next to this GUI file. " + str(e))

# ---- GUI logger ----
class GuiAlertLog(core.AlertLog):
    """
    Sends alerts to both the text log and the Alerts table.
    Keeps a row_store mapping Treeview iid -> full alert row so we can bridge on double-click.
    Supports compact logging + beep.
    """
    def __init__(
        self,
        text_widget: tk.Text,
        tree_widget: Optional[ttk.Treeview] = None,
        last_alert_cb=None,
        path: Optional[str] = None,
        compact_var: Optional[tk.BooleanVar] = None,
        sound_var: Optional[tk.BooleanVar] = None,
    ):
        super().__init__(path)
        self.text = text_widget
        self.tree = tree_widget
        self.last_alert_cb = last_alert_cb
        self.row_store = {}  # iid -> full row
        self.compact_var = compact_var
        self.sound_var = sound_var
        self.max_text_lines = 2000

    def _trim_text(self):
        try:
            lines = int(self.text.index("end-1c").split(".")[0])
            if lines > self.max_text_lines:
                self.text.delete("1.0", f"{lines - self.max_text_lines}.0")
        except Exception:
            pass

    def _maybe_beep(self):
        try:
            if not (self.sound_var and self.sound_var.get()):
                return
            # cross-platform soft beep
            self.text.bell()
            # try a Windows hint if available
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
        except Exception:
            pass

    def write(self, row: list):
        # Compose compact or full line
        def compact_line():
            try:
                ts, kind, tk_sym, exp, side, strike, pfrom, pto, absm, pct, iv, spr, extra = row
            except ValueError:
                vals = row[:12] if len(row) >= 12 else row + [""] * (12 - len(row))
                ts, kind, tk_sym, exp, side, strike, pfrom, pto, absm, pct, iv, spr = vals
            return (
                f"{ts}  {tk_sym}  {exp}  {(side or '').upper()} {strike}  "
                f"{pfrom}→{pto}  Δ${absm}  +{pct}  IV {iv}  SPR {spr}"
            )

        if self.compact_var and self.compact_var.get():
            line = compact_line()
        else:
            # full/raw row with extras
            line = " | ".join(str(x) for x in row)

        # Append to text log
        def _append_log():
            self.text.insert("end", line + "\n")
            self.text.see("end")
            self._trim_text()
        self.text.after(0, _append_log)

        # Append to Alerts table
        if self.tree:
            def _ins():
                try:
                    ts, kind, tk_sym, exp, side, strike, pfrom, pto, absm, pct, iv, spr, extra = row
                except ValueError:
                    vals = row[:12] if len(row) >= 12 else row + [""] * (12 - len(row))
                    ts, kind, tk_sym, exp, side, strike, pfrom, pto, absm, pct, iv, spr = vals
                short = (
                    ts,
                    tk_sym,
                    exp,
                    (side or "").upper(),
                    f"{float(strike):.2f}" if str(strike) else str(strike),
                    f"{pfrom}→{pto}",
                    absm,
                    pct,
                    iv,
                    spr,
                )
                kids = self.tree.get_children()
                tag = "odd" if (len(kids) % 2 == 1) else "even"
                iid = self.tree.insert("", "end", values=short, tags=(tag,))
                self.row_store[iid] = row

                # Keep table from growing unbounded
                max_rows = 800
                kids2 = self.tree.get_children()
                if len(kids2) > max_rows:
                    for i in range(0, len(kids2) - max_rows):
                        old = kids2[i]
                        self.tree.delete(old)
                        self.row_store.pop(old, None)
            self.text.after(0, _ins)

        self._maybe_beep()

        if self.last_alert_cb:
            self.last_alert_cb(row)

    def info(self, msg: str):
        self.text.after(0, lambda: (self.text.insert("end", f"[INFO] {msg}\n"), self.text.see("end"), self._trim_text()))

# ---- stoppable wrappers ----
class StoppableSpike(core.SpikeScanner):
    def __init__(self, cfg: core.SpikeConfig, presets_dir: str, gui_log: GuiAlertLog):
        super().__init__(cfg, presets_dir)
        self._stop = threading.Event()
        self.alert_log = gui_log

    def stop(self):
        self._stop.set()

    def _fmt_exp_label(self) -> str:
        s = (self.cfg.exp_filter or "").strip()
        if s == "":
            return "nearest"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return f"through {s}"
        return s

    def _fmt_strike_label(self) -> str:
        s = (self.cfg.strike_filter or "").strip()
        return s if s else "all"

    def run_gui_loop(self, tickers: List[str]):
        if not tickers:
            self.alert_log.info("No tickers resolved.")
            return

        # Friendly header in GUI log (not console)
        self.alert_log.info(
            f"Start: {len(tickers)} tickers | type={self.cfg.kind} | ±{self.cfg.min_pct}% | "
            f"${self.cfg.min_abs} | {self.cfg.interval_secs}s | exp={self._fmt_exp_label()} | "
            f"minPrem=${self.cfg.min_premium} | spread<={int(self.cfg.max_spread_pct*100)}% | "
            f"cooldown={self.cfg.cooldown_secs}s | strikes={self._fmt_strike_label()} | "
            f"maxC={self.cfg.max_contracts_per_ticker}"
        )

        # One-time: how many expirations got selected for first ticker
        try:
            import yfinance as yf
            t0 = yf.Ticker(tickers[0])
            picked = core.parse_exp_filter(self.cfg.exp_filter, t0)
            if picked:
                self.alert_log.info(f"Expirations selected: {len(picked)} ({picked[0]} ... {picked[-1]})")
            else:
                self.alert_log.info("Expirations selected: 0")
        except Exception as e:
            self.alert_log.info(f"Expirations: (could not inspect) {e}")

        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._scan_once(tickers)
            except Exception as e:
                self.alert_log.info(f"Scan error: {e}")
            wait = max(0, self.cfg.interval_secs - int(time.time() - t0))
            if self._stop.wait(wait):
                break

class StoppableBuyback(core.BuybackMonitor):
    def __init__(self, cfg: core.BuybackConfig, gui_log: GuiAlertLog):
        super().__init__(cfg)
        self._stop = threading.Event()
        self.alert_log = gui_log

    def stop(self):
        self._stop.set()

    def run_gui_loop(self):
        self.alert_log.info(
            f"Start buyback: {len(self.cfg.contracts)} contracts | targets={self.cfg.targets} | "
            f"floor=${self.cfg.floor} | drop_since_last>={self.cfg.drop_pct_since_last}% | "
            f"every {self.cfg.interval_secs}s"
        )
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._check_all()
            except Exception as e:
                self.alert_log.info(f"Buyback loop error: {e}")
            wait = max(0, self.cfg.interval_secs - int(time.time() - t0))
            if self._stop.wait(wait):
                break

# ---- app ----
class OptionSuiteApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OptionSuite — Fresh Start (GUI)")
        self.geometry("1200x760")
        self.minsize(1000, 620)
        self._style()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.scan_tab = ttk.Frame(nb)
        self.buy_tab = ttk.Frame(nb)
        nb.add(self.scan_tab, text="Market Scanner")
        nb.add(self.buy_tab, text="Buyback / Lookup")

        self._build_scan()
        self._build_buy()

        self.scan_thread = None
        self.buy_thread = None
        self.scan_runner: Optional[StoppableSpike] = None
        self.buy_runner: Optional[StoppableBuyback] = None
        self.last_alert_for_bridge = None

    def _style(self):
        try:
            self.tk.call("tk", "scaling", 1.15)
        except Exception:
            pass
        if sv_ttk:
            sv_ttk.set_theme("dark")
        else:
            s = ttk.Style(self)
            s.theme_use("clam")
            s.configure(".", background="#1e1e1e", foreground="#e6e6e6", fieldbackground="#2a2a2a", relief="flat")
            s.configure("TEntry", fieldbackground="#2a2a2a")
            s.configure("TCombobox", fieldbackground="#2a2a2a")
            s.configure("TNotebook", background="#1e1e1e")
            s.configure("TButton", padding=4)
            s.map("TButton", background=[("active", "#333")])
            s.configure("Treeview", rowheight=22, borderwidth=0)
            s.configure("Treeview.Heading", background="#2a2a2a", foreground="#e6e6e6")
            s.map("Treeview", background=[("selected", "#3c3f41")])

    # ---------- SCANNER ----------
    def _build_scan(self):
        root = self.scan_tab
        left = ttk.Frame(root)
        right = ttk.Frame(root)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right.pack(side="right", fill="both", expand=True, padx=8, pady=8)

        ttk.Label(left, text="Tickers (space/comma/line)").pack(anchor="w")
        self.scan_tickers = tk.Text(left, width=34, height=10)
        self.scan_tickers.pack(fill="x", pady=(0, 6))

        pres = ttk.Frame(left); pres.pack(fill="x", pady=4)
        ttk.Label(pres, text="Preset:").pack(side="left")
        self.scan_preset = ttk.Combobox(pres, values=self._scan_list_presets(), width=16, state="readonly")
        if self.scan_preset["values"]:
            self.scan_preset.current(0)
        self.scan_preset.pack(side="left", padx=6)
        ttk.Button(pres, text="Load", command=self._scan_load_preset).pack(side="left")

        grid = ttk.Frame(left); grid.pack(fill="x", pady=8)
        def L(r, c, t): ttk.Label(grid, text=t).grid(row=r, column=c, sticky="w", padx=2, pady=2)
        def E(r, c, var, w=10):
            e = ttk.Entry(grid, textvariable=var, width=w); e.grid(row=r, column=c, sticky="w", padx=2, pady=2); return e
        def C(r, c, var, vals):
            cb = ttk.Combobox(grid, textvariable=var, values=vals, width=10, state="readonly"); cb.grid(row=r, column=c, sticky="w", padx=2, pady=2); return cb

        # core knobs (visible)
        self.var_type = tk.StringVar(value="both")
        self.var_min_pct = tk.StringVar(value="25")
        self.var_min_prem = tk.StringVar(value="0.20")
        self.var_interval = tk.StringVar(value="60")
        self.var_exp = tk.StringVar(value="")      # blank by default; user types YYYY-MM-DD or leaves blank
        self.var_strike = tk.StringVar(value="")   # blank/all by default

        L(0, 0, "Option type");               C(0, 1, self.var_type, ["both", "call", "put"])
        L(1, 0, "Spike %");                    E(1, 1, self.var_min_pct)
        L(2, 0, "Min prem $");                 E(2, 1, self.var_min_prem)
        L(2, 2, "Interval s");                 E(2, 3, self.var_interval)
        L(3, 0, "Exps through (YYYY-MM-DD)");  E(3, 1, self.var_exp)
        L(3, 2, "Strike filter (blank/all | 145 | 145-170)"); E(3, 3, self.var_strike)

        # advanced (rarely changed)
        self.var_cooldown = tk.StringVar(value="300")
        self.var_workers = tk.StringVar(value="4")
        self.var_max_spread = tk.StringVar(value="0.80")
        self.var_maxc = tk.StringVar(value="200")
        self.var_min_abs = tk.StringVar(value="0.10")  # Abs move $ in Advanced

        adv_frame = ttk.LabelFrame(left, text="Advanced")
        af = ttk.Frame(adv_frame); af.pack(fill="x", padx=6, pady=6)
        def L2(r, c, t): ttk.Label(af, text=t).grid(row=r, column=c, sticky="w", padx=2, pady=2)
        def E2(r, c, var, w=10): ttk.Entry(af, textvariable=var, width=w).grid(row=r, column=c, sticky="w", padx=2, pady=2)

        L2(0, 0, "Cooldown s");    E2(0, 1, self.var_cooldown)
        L2(0, 2, "Workers");       E2(0, 3, self.var_workers)
        L2(1, 0, "Max spread");    E2(1, 1, self.var_max_spread)
        L2(1, 2, "Max contracts"); E2(1, 3, self.var_maxc)
        L2(2, 0, "Abs move $");    E2(2, 1, self.var_min_abs)

        adv_frame.pack_forget()
        def toggle_adv(btn=...):
            if adv_frame.winfo_manager():
                adv_frame.pack_forget(); btn.configure(text="Advanced ▾")
            else:
                adv_frame.pack(fill="x", pady=(2, 6)); btn.configure(text="Advanced ▴")

        bar = ttk.Frame(left); bar.pack(fill="x", pady=6)
        self.btn_adv = ttk.Button(bar, text="Advanced ▾", command=lambda: toggle_adv(self.btn_adv))
        self.btn_adv.pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Start Scan", command=self._scan_start).pack(side="left")
        ttk.Button(bar, text="Stop", command=self._scan_stop).pack(side="left", padx=6)
        ttk.Button(bar, text="Send last alert → Buyback", command=self._bridge_last_to_buy).pack(side="left", padx=6)

        # ---- Right side: Paned window with Log (top) and Alerts table (bottom) ----
        paned = tk.PanedWindow(right, orient=tk.VERTICAL, sashrelief="raised", bg="#1e1e1e", bd=0, sashwidth=6)
        paned.pack(fill="both", expand=True)

        # Log frame + toolbar
        frame_log = ttk.Frame(paned)
        logbar = ttk.Frame(frame_log)
        logbar.pack(fill="x", padx=2, pady=(0, 4))
        self.var_compact = tk.BooleanVar(value=True)
        self.var_sound = tk.BooleanVar(value=False)
        ttk.Checkbutton(logbar, text="Compact log", variable=self.var_compact).pack(side="left")
        ttk.Checkbutton(logbar, text="Sound on alert", variable=self.var_sound).pack(side="left", padx=10)

        self.scan_log = tk.Text(frame_log, wrap="none")
        yscroll_log = ttk.Scrollbar(frame_log, command=self.scan_log.yview)
        self.scan_log.configure(yscrollcommand=yscroll_log.set)
        self.scan_log.pack(side="left", fill="both", expand=True)
        yscroll_log.pack(side="right", fill="y")
        paned.add(frame_log)

        # Alerts frame (table)
        frame_alerts = ttk.LabelFrame(paned, text="Alerts")
        cols = ("TS","TICKER","EXP","TYPE","STRIKE","FROM→TO","ABS","PCT","IV","SPR")
        self.scan_alerts = ttk.Treeview(frame_alerts, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (150, 90, 110, 70, 90, 130, 70, 70, 80, 70)):
            self.scan_alerts.heading(c, text=c)
            self.scan_alerts.column(c, width=w, anchor="center")
        yscroll_tbl = ttk.Scrollbar(frame_alerts, command=self.scan_alerts.yview)
        self.scan_alerts.configure(yscrollcommand=yscroll_tbl.set)
        self.scan_alerts.pack(side="left", fill="both", expand=True)
        yscroll_tbl.pack(side="right", fill="y")

        # Alerts toolbar
        toolbar = ttk.Frame(frame_alerts)
        toolbar.pack(fill="x", padx=4, pady=4)
        ttk.Button(toolbar, text="Clear", command=self._alerts_clear).pack(side="left")
        ttk.Button(toolbar, text="Export CSV", command=self._alerts_export).pack(side="left", padx=6)

        paned.add(frame_alerts)
        self.after(200, lambda: paned.sash_place(0, 0, int(paned.winfo_height() * 0.55)))

        # Double-click on alert row → fill Buyback form
        self.scan_alerts.bind("<Double-1>", self._on_alert_double_click)

        # GUI logger that feeds both the text log and the alerts table
        self.scan_gui_log = GuiAlertLog(
            self.scan_log,
            tree_widget=self.scan_alerts,
            last_alert_cb=self._remember_last_alert,
            compact_var=self.var_compact,
            sound_var=self.var_sound,
        )
        try:
            self.scan_alerts.tag_configure("odd",  background="#232323")
            self.scan_alerts.tag_configure("even", background="#1e1e1e")
        except Exception:
            pass

    def _scan_list_presets(self):
        d = os.path.join(os.path.dirname(__file__), "presets")
        try:
            return [f for f in os.listdir(d) if f.lower().endswith(".txt")]
        except Exception:
            return []

    def _scan_load_preset(self):
        name = (self.scan_preset.get() or "").strip()
        if not name: return
        p = os.path.join(os.path.dirname(__file__), "presets", name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
            existing = self.scan_tickers.get("1.0", "end").strip()
            block = "\n".join(lines)
            self.scan_tickers.delete("1.0", "end")
            self.scan_tickers.insert("1.0", block if not existing else (existing + "\n" + block))
        except Exception as e:
            messagebox.showerror("Preset", f"Could not load preset: {e}")

    def _remember_last_alert(self, row: list):
        self.last_alert_for_bridge = row

    def _bridge_last_to_buy(self):
        if not self.last_alert_for_bridge:
            messagebox.showinfo("Bridge", "No alert yet to send.")
            return
        try:
            _, _kind, tk_sym, exp, side, strike, *_rest, extra = self.last_alert_for_bridge
        except Exception:
            return
        side = (side or "").upper()[:1]
        self.buy_one_ticker.set(tk_sym)
        self.buy_one_type.set(side)
        self.buy_one_strike.set(str(strike))
        self.buy_one_exp.set(exp)
        try:
            meta = json.loads(extra); spot = meta.get("spot")
            if spot: self._buy_log(f"[INFO] Bridge: {tk_sym} spot ~ {spot}")
        except Exception:
            pass
        self.nametowidget(self.scan_tab.master).select(self.buy_tab)

    def _on_alert_double_click(self, event):
        sel = self.scan_alerts.selection()
        if not sel: return
        iid = sel[0]
        row = getattr(self.scan_gui_log, "row_store", {}).get(iid)
        if not row: return
        self.last_alert_for_bridge = row
        self._bridge_last_to_buy()

    def _alerts_clear(self):
        for iid in self.scan_alerts.get_children():
            self.scan_alerts.delete(iid)
        if hasattr(self.scan_gui_log, "row_store"):
            self.scan_gui_log.row_store.clear()

    def _alerts_export(self):
        path = filedialog.asksaveasfilename(
            title="Export alerts to CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path: return
        cols = ("TS", "TICKER", "EXP", "TYPE", "STRIKE", "FROM→TO", "ABS", "PCT", "IV", "SPR")
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for iid in self.scan_alerts.get_children():
                    w.writerow(self.scan_alerts.item(iid, "values"))
            messagebox.showinfo("Export CSV", f"Saved {len(self.scan_alerts.get_children())} row(s) to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export CSV", str(e))

    def _scan_start(self):
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scan", "Already running.")
            return
        raw = self.scan_tickers.get("1.0", "end").strip().replace("\n", " ")
        tokens = [t for t in re.split(r"[,\s]+", raw) if t]
        cfg = core.SpikeConfig(
            tickers=tokens or ["AAPL"],
            exp_filter=self.var_exp.get().strip(),         # "" or YYYY-MM-DD or legacy next:N etc.
            kind=core.oc_type(self.var_type.get()),
            strike_filter=self.var_strike.get().strip(),   # "" (=all) | 145 | 145-170
            min_abs=float(self.var_min_abs.get() or 0.1),  # Advanced
            min_pct=float(self.var_min_pct.get() or 25),
            min_premium=float(self.var_min_prem.get() or 0.2),
            max_spread_pct=float(self.var_max_spread.get() or 0.8),
            cooldown_secs=int(self.var_cooldown.get() or 300),
            interval_secs=int(self.var_interval.get() or 60),
            workers=int(self.var_workers.get() or 4),
            log_path=None,
            max_contracts_per_ticker=int(self.var_maxc.get() or 200),
            verbose=False,
        )
        runner = StoppableSpike(cfg, presets_dir=os.path.join(os.path.dirname(__file__), "presets"), gui_log=self.scan_gui_log)
        self.scan_runner = runner
        tickers_resolved = runner.resolve_tickers()
        self.scan_gui_log.info(f"Starting scan on {len(tickers_resolved)} tickers...")

        def bg():
            try:
                runner.run_gui_loop(tickers_resolved)
            finally:
                self.scan_gui_log.info("Scan stopped.")
        self.scan_thread = threading.Thread(target=bg, daemon=True)
        self.scan_thread.start()

    def _scan_stop(self):
        if self.scan_runner:
            self.scan_runner.stop()

    # ---------- BUY/LOOKUP ----------
    def _build_buy(self):
        root = self.buy_tab
        top = ttk.Frame(root); top.pack(fill="x", padx=8, pady=6)

        ttk.Label(top, text="Underlying / OCC:").pack(side="left")
        self.buy_lookup_symbol = ttk.Entry(top, width=12); self.buy_lookup_symbol.pack(side="left", padx=6)
        ttk.Label(top, text="Expiration:").pack(side="left", padx=(8, 0))
        self.buy_lookup_exp = ttk.Combobox(top, width=14, state="readonly"); self.buy_lookup_exp.pack(side="left", padx=6)
        ttk.Label(top, text="Side:").pack(side="left")
        self.buy_lookup_side = ttk.Combobox(top, values=["calls", "puts"], width=8, state="readonly")
        self.buy_lookup_side.current(0); self.buy_lookup_side.pack(side="left", padx=6)
        ttk.Label(top, text="±Strikes").pack(side="left")
        self.buy_lookup_span = ttk.Entry(top, width=6); self.buy_lookup_span.insert(0, "10"); self.buy_lookup_span.pack(side="left", padx=6)

        ttk.Button(top, text="Lookup",  command=self._buy_lookup).pack(side="left", padx=8)
        ttk.Button(top, text="Refresh", command=self._buy_lookup_refresh).pack(side="left")
        ttk.Label(top, text="Auto s").pack(side="left", padx=(12,2))
        self.buy_auto_s = ttk.Entry(top, width=5); self.buy_auto_s.insert(0, "30"); self.buy_auto_s.pack(side="left", padx=2)
        ttk.Button(top, text="Start Auto", command=self._buy_start_auto).pack(side="left", padx=6)
        ttk.Button(top, text="Stop Auto",  command=self._buy_stop_auto).pack(side="left", padx=2)

        cols=("STRIKE","BID","ASK","MID","LAST","SPR","IV","VOL","OI","ITM","OCC")
        self.buy_table = ttk.Treeview(root, columns=cols, show="headings", height=16)
        for c in cols:
            self.buy_table.heading(c, text=c)
            self.buy_table.column(c, width=80 if c not in ("OCC","ITM") else 100, anchor="center")
        self.buy_table.pack(fill="both", expand=True, padx=8, pady=6)

        frm = ttk.Frame(root); frm.pack(fill="x", padx=8, pady=6)
        self.buy_one_ticker = tk.StringVar(); self.buy_one_type = tk.StringVar(value="C")
        self.buy_one_strike = tk.StringVar(); self.buy_one_exp = tk.StringVar()
        self.buy_one_open = tk.StringVar(value="0.50"); self.buy_one_qty = tk.StringVar(value="1")
        ttk.Label(frm,text="Ticker").grid(row=0,column=0,sticky="w")
        ttk.Entry(frm,textvariable=self.buy_one_ticker,width=10).grid(row=0,column=1,sticky="w",padx=4)
        ttk.Label(frm,text="Type").grid(row=0,column=2,sticky="w")
        ttk.Combobox(frm,textvariable=self.buy_one_type,values=["C","P"],width=4,state="readonly").grid(row=0,column=3,sticky="w",padx=4)
        ttk.Label(frm,text="Strike").grid(row=0,column=4,sticky="w")
        ttk.Entry(frm,textvariable=self.buy_one_strike,width=8).grid(row=0,column=5,sticky="w",padx=4)
        ttk.Label(frm,text="Expiry").grid(row=0,column=6,sticky="w")
        ttk.Entry(frm,textvariable=self.buy_one_exp,width=12).grid(row=0,column=7,sticky="w",padx=4)
        ttk.Label(frm,text="Open $").grid(row=0,column=8,sticky="w")
        ttk.Entry(frm,textvariable=self.buy_one_open,width=8).grid(row=0,column=9,sticky="w",padx=4)
        ttk.Label(frm,text="Qty").grid(row=0,column=10,sticky="w")
        ttk.Entry(frm,textvariable=self.buy_one_qty,width=6).grid(row=0,column=11,sticky="w",padx=4)
        ttk.Button(frm,text="Add row", command=self._buy_add_row).grid(row=0,column=12,sticky="w",padx=8)

        ctl = ttk.Frame(root); ctl.pack(fill="x", padx=8, pady=4)
        self.buy_targets = tk.StringVar(value="80,85,90,95")
        self.buy_floor   = tk.StringVar(value="0.05")
        self.buy_drop    = tk.StringVar(value="20")
        self.buy_interval= tk.StringVar(value="90")
        self.buy_max_spread = tk.StringVar(value="0.80")
        self.buy_positions_path = tk.StringVar(value="")
        ttk.Button(ctl,text="Load positions.csv", command=self._buy_load_positions).pack(side="left")
        ttk.Label(ctl,textvariable=self.buy_positions_path).pack(side="left", padx=6)
        ttk.Label(ctl,text="Targets").pack(side="left", padx=(16,2))
        ttk.Entry(ctl,textvariable=self.buy_targets,width=12).pack(side="left")
        ttk.Label(ctl,text="Floor $").pack(side="left", padx=(16,2))
        ttk.Entry(ctl,textvariable=self.buy_floor,width=8).pack(side="left")
        ttk.Label(ctl,text="Drop%").pack(side="left", padx=(16,2))
        ttk.Entry(ctl,textvariable=self.buy_drop,width=8).pack(side="left")
        ttk.Label(ctl,text="Interval s").pack(side="left", padx=(16,2))
        ttk.Entry(ctl,textvariable=self.buy_interval,width=8).pack(side="left")
        ttk.Label(ctl,text="Max spread").pack(side="left", padx=(16,2))
        ttk.Entry(ctl,textvariable=self.buy_max_spread,width=8).pack(side="left")
        ttk.Button(ctl,text="Start Auto", command=self._buy_monitor_start).pack(side="left", padx=8)
        ttk.Button(ctl,text="Stop Auto", command=self._buy_monitor_stop).pack(side="left")

        self.buy_log = tk.Text(root, wrap="none", height=8)
        self.buy_log.pack(fill="both", expand=False, padx=8, pady=(0,8))
        self.buy_gui_log = GuiAlertLog(self.buy_log)

        self._buy_positions_inline: List[core.Contract] = []
        self._buy_auto_timer = None

    # ---- buy helpers ----
    def _buy_log(self, s: str):
        self.buy_log.insert("end", s + "\n")
        self.buy_log.see("end")

    def _buy_load_positions(self):
        p = filedialog.askopenfilename(title="Select positions.csv", filetypes=[("CSV", "*.csv")])
        if p: self.buy_positions_path.set(p)

    def _buy_add_row(self):
        try:
            c = core.Contract(
                ticker=self.buy_one_ticker.get().strip().upper(),
                kind=self.buy_one_type.get().strip().upper(),
                strike=float(self.buy_one_strike.get()),
                expiry=self.buy_one_exp.get().strip(),
                open_credit=float(self.buy_one_open.get()),
                qty=int(self.buy_one_qty.get() or "1"),
                note="inline",
            )
        except Exception as e:
            messagebox.showerror("Add", f"Invalid fields: {e}")
            return
        self._buy_positions_inline.append(c)
        self._buy_log(f"[ADD] {c.ticker} {c.kind}{c.strike} {c.expiry} open={c.open_credit} qty={c.qty}")

    def _buy_fetch_expirations(self, tk_sym: str) -> List[str]:
        import yfinance as yf
        try: return list(yf.Ticker(tk_sym).options or [])
        except Exception: return []

    def _buy_lookup(self):
        tk_sym = self.buy_lookup_symbol.get().strip().upper()
        if not tk_sym: messagebox.showinfo("Lookup", "Enter a ticker."); return
        exps = self._buy_fetch_expirations(tk_sym)
        self.buy_lookup_exp["values"] = exps
        if exps and not self.buy_lookup_exp.get(): self.buy_lookup_exp.current(0)
        self._buy_lookup_refresh()

    def _buy_lookup_refresh(self):
        tk_sym = self.buy_lookup_symbol.get().strip().upper()
        exp = self.buy_lookup_exp.get().strip()
        side = self.buy_lookup_side.get().strip().lower()
        span = int(self.buy_lookup_span.get() or "10")
        if not (tk_sym and exp):
            self._buy_log("[INFO] Choose an expiration first."); return
        self.buy_table.delete(*self.buy_table.get_children())
        import yfinance as yf
        t = yf.Ticker(tk_sym)
        try:
            chain = t.option_chain(exp)
        except Exception as e:
            self._buy_log(f"[WARN] {tk_sym} chain({exp}) {e}"); return
        df = chain.calls if side == "calls" else chain.puts
        if df is None or len(df) == 0: return
        try:
            spot = float(t.fast_info['last_price']) if 'last_price' in t.fast_info else float(t.history(period="1d")['Close'].iloc[-1])
        except Exception:
            spot = float("nan")
        strikes = df["strike"].tolist()
        if math.isfinite(spot):
            center = min(strikes, key=lambda s: abs(s - spot)); idx = strikes.index(center)
            lo = max(0, idx - span); hi = min(len(strikes), idx + span + 1)
            subset = df.iloc[lo:hi].copy()
        else:
            subset = df.head(2 * span + 1).copy()
        for _, r in subset.iterrows():
            strike = float(r["strike"])
            bid = r.get("bid", float("nan")); ask = r.get("ask", float("nan")); last = r.get("lastPrice", float("nan"))
            mid = (bid + ask) / 2.0 if all(isinstance(x, float) and x > 0 for x in (bid, ask)) else float("nan")
            spr = (ask - bid) / mid if (isinstance(mid, float) and mid > 0 and isinstance(ask, float) and isinstance(bid, float)) else float("nan")
            iv = r.get("impliedVolatility", float("nan")); vol = r.get("volume", ""); oi = r.get("openInterest", ""); itm = r.get("inTheMoney", ""); occ = ""
            vals = (
                f"{strike:.2f}",
                f"{bid:.2f}" if isinstance(bid, float) and math.isfinite(bid) else "",
                f"{ask:.2f}" if isinstance(ask, float) and math.isfinite(ask) else "",
                f"{mid:.2f}" if isinstance(mid, float) and math.isfinite(mid) else "",
                f"{last:.2f}" if isinstance(last, float) and math.isfinite(last) else "",
                f"{spr:.2f}" if isinstance(spr, float) and math.isfinite(spr) else "",
                f"{iv:.4f}" if isinstance(iv, float) and math.isfinite(iv) else "",
                f"{int(vol)}" if isinstance(vol, (int, float)) and math.isfinite(float(vol)) else "",
                f"{int(oi)}" if isinstance(oi, (int, float)) and math.isfinite(float(oi)) else "",
                "✓" if itm else "",
                occ,
            )
            self.buy_table.insert("", "end", values=vals)

    def _buy_start_auto(self):
        try: s = int(self.buy_auto_s.get() or "30")
        except Exception: s = 30
        self._buy_stop_auto()
        def tick():
            self._buy_lookup_refresh()
            self._buy_auto_timer = self.after(s * 1000, tick)
        tick()

    def _buy_stop_auto(self):
        if getattr(self, "_buy_auto_timer", None):
            try: self.after_cancel(self._buy_auto_timer)
            except Exception: pass
            self._buy_auto_timer = None

    def _buy_monitor_start(self):
        if self.buy_thread and self.buy_thread.is_alive():
            messagebox.showinfo("Buyback", "Already running."); return
        contracts: List[core.Contract] = []
        if self.buy_positions_path.get():
            try: contracts.extend(core.load_positions_csv(self.buy_positions_path.get()))
            except Exception as e: messagebox.showerror("positions.csv", str(e)); return
        contracts.extend(self._buy_positions_inline)
        if not contracts:
            messagebox.showinfo("Buyback","Add inline rows and/or load positions.csv first."); return
        cfg = core.BuybackConfig(
            contracts=contracts,
            targets=[float(x) for x in re.split(r"[,\s]+", self.buy_targets.get().strip()) if x],
            floor=float(self.buy_floor.get() or 0.05),
            drop_pct_since_last=float(self.buy_drop.get() or 20),
            interval_secs=int(self.buy_interval.get() or 90),
            max_spread_pct=float(self.buy_max_spread.get() or 0.80),
            log_path=None, verbose=False)
        runner = StoppableBuyback(cfg, gui_log=self.buy_gui_log)
        self.buy_runner = runner; self._buy_log(f"[START] monitoring {len(contracts)} contract(s)")
        def bg():
            try: runner.run_gui_loop()
            finally: self._buy_log("[INFO] Buyback stopped.")
        self.buy_thread = threading.Thread(target=bg, daemon=True); self.buy_thread.start()

    def _buy_monitor_stop(self):
        if self.buy_runner: self.buy_runner.stop()

def main():
    OptionSuiteApp().mainloop()

if __name__ == "__main__":
    main()
