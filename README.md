# OptionSuite (Former Option-wheel-wizard) — Real-Time Options Trading Intelligence Toolkit

OptionSuite is an evolving trading assistant designed to support income-focused options strategies such as the Wheel, covered calls, and cash-secured puts. The goal is to provide a single, unified tool that helps traders identify high-value opportunities, manage short options effectively, and understand risk and return more clearly.

This repository contains the current development build for my CIS-260 capstone, which is also the live foundation of the real trading tool I use and refine.

---

## Current Features (Nov 2025)

###  Unified GUI (Tkinter)
A clean, tab-based interface including:

- **Spike Scanner**
- **Buyback Monitor**
- **Wheel/CSP Position Builder**
- **Logs** (centralized event history)

The layout is unified and ready for full backend wiring.  
Dark-theme support included (sv_ttk if installed).

---

## 1. Spike Scanner (GUI Framework Complete)
The scanner tab is fully built and includes:

- Ticker list loading (manual + presets: SP-100, SP-500, NAS-100)
- Minimum spike % filter
- Cooldown timer
- Expiration filtering controls
- Start/Stop controls
- Live logging

**Backend wiring begins next week** (scanner engine already built in backend file).

---

## 2. Buyback Monitor (Fully Implemented)
The Buyback engine **is fully functional** and includes:

- Manual contract entry (Ticker, Type, Strike, Exp, Credit, Qty)
- Automatic premium analysis (mid-price fallback)
- Spread % detection
- Floor price detection
- Capture % detection (80%, 90%, etc.)
- Fast drop detection since last check
- Chain-wide collapse scanning (optional)
- Cooldowns and alert grouping
- Logs integrated with GUI

This is the first module that is **fully wired** and ready for live Monday use.

---

##  3. Wheel / CSP Position Builder (Working MVP)
The Wheel/CSP builder supports:

- Auto-fetching expirations
- Auto-fetching strikes for selected expiration
- Auto-calculating:
  - Break-even
  - Return on collateral (ROC)
  - Annualized ROC
  - Delta-based assignment risk
  - Estimated premium
  - Collateral requirements

This is the start of a much larger Wheel intelligence system.

---

## Project Structure

OptionSuite/
├── OptionSuite_FreshStart_UPDATED.py # Backend engine (Spike + Buyback + helpers)
├── OptionSuite_GUI_v4.py # Full GUI (Scanner, Buyback, Wheel Builder)
├── presets/
│ ├── sp100.txt
│ ├── sp500.txt
│ └── nas100.txt
└── NEXT_PHASE_PLAN.md

---

## Next Phase (Dec 2025)
The next development cycle is focused on wiring the full system and adding trader-grade intelligence:

###  Short-Term Goals (Active)
- Wire the Spike Scanner backend into the GUI alerts table
- Add auto-color-coding for large spikes and collapses
- Add auto-save for alerts to CSV
- Add improved logging without forcing tab switches

### Medium-Term Goals
- Expand Wheel/CSP Builder into a full strike-analysis tool:
  - Best strike selector
  - Delta filters
  - ROC filters
  - Roll-down/out modeling
  - Probability of profit
- Integrate Buyback alerts into Wheel workflow (optional)

### Long-Term Vision
Once core modules are finished:
- Real-time auto-refresh
- News/premarket sentiment integration
- Position tracking dashboard
- Rolling optimization engine
- Alerts export and mobile notification hooks

---

## Status (Nov 2025)
The repository was rebuilt from scratch and is now organized, stable, and ready for the next development phases. Core backend logic (Spike engine + Buyback engine) is complete, and the new GUI v4 integrates all three modules into a unified interface.
