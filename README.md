# OptionSuite — Real-Time Options Intelligence Toolkit  
---

## Overview  
OptionSuite is a Python-based options trading toolkit designed to support low-risk income strategies such as:

- Covered Calls  
- Cash-Secured Puts (CSP)  
- Wheel Strategy  
- Short-premium management (buybacks)

The project consists of:

1. **Spike Scanner** – looks for sudden premium spikes  
2. **Buyback Monitor** – manages short positions using configurable profit, floor, spread, and drop rules  
3. **Wheel / CSP Builder** – evaluates contracts, calculates financial metrics, and prepares trade candidates  
4. **Unified GUI (Tkinter)** – dark-mode compatible, multi-tab, threaded, stable interface

This is both the final project submission **and** the real tool I use in live trading.

---


---

## Features

### 1. Spike Scanner  
(Currently partially implemented — GUI functional, backend stubbed)  
- Load ticker presets  
- Add/remove tickers  
- Configure:  
  - Cooldown  
  - Minimum spike %  
  - Expiration window  
- Threaded scan loop (stubbed in GUI)  
- Real-time alert table with timestamps  

### 2. Buyback Monitor (Fully Implemented)  
Backend is fully operational via `BuybackMonitor` in `OptionSuite_FreshStart.py`.

Features include:  
- Parse `positions.csv`  
- Add manual contracts  
- Multi-line contract importer  
- Targets (% capture)  
- Floor price  
- Rapid drop detection (Δ%)  
- Spread detection  
- Interval-based scanning  
- Threaded continuous monitoring  
- Full GUI logging + recent alert feed  

### 3. Wheel / CSP Builder (Working MVP)  
- Fetch chain via **yfinance**  
- Select expiration + strike  
- Auto-calculate:  
  - Premium  
  - Delta  
  - Break-even  
  - ROC  
  - Annualized ROC  
  - Approx probability of assignment/call-away  
- Add contract directly into Buyback system  
- Copy trade summary  

---

## GUI Layout  
Tabs:  
1. **Scanner**  
2. **Buyback Monitor**  
3. **Wheel / CSP Builder**  
4. **Logs**

Supports **dark mode** if `sv_ttk` is installed.

---

## Installation  
### Requirements  
Python 3.10+
pip install yfinance pandas sv_ttk

### Running  
python OptionSuite_GUI_v5.py

### Notes  
- Place all files in the same directory  
- Presets belong in `/presets/`  
- `positions.csv` is optional but supported for Buyback  

---

## Capstone Objectives Achieved  

- Multi-module, real-world application  
- Fully custom Tkinter GUI with multiple tabs  
- Real-time multithreading  
- External data integration via yfinance  
- Custom financial math & logic  
- Clean modular architecture  
- Industry-style logging & UI feedback  

This exceeds standard course requirements by implementing a real, functional trading system.

---

## Future Enhancements  

- Full Spike Scanner backend  
- Volume & IV filters  
- Auto-save alerts to CSV  
- Rolling-strategy engine  
- Sentiment/news plugin  
- Wheel dashboard  
- Notification hooks  



For development questions: **@rpalombit (GitHub)**

