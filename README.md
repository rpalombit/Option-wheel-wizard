# OptionSuite — Options Trading Intelligence Toolkit

OptionSuite is a real-world options trading assistant designed to support income strategies such as the Wheel, covered calls, and cash-secured puts.  
The system combines option-chain analytics, buyback opportunity detection, real-time spike scanning (backend), and a growing multi-tab GUI built with Tkinter.

This repository contains the active CIS-260 development version, aligned with the production-grade tool being built for live trading use.

---

# 🚀 Features (Current Working State)

## ✅ **1. Preset Ticker Management**
- Load from `sp100`, `sp500`, `nas100`
- Manual ticker add/remove
- Ticker display panel
- Shared ticker list across modules

## ✅ **2. Spike Scanner (backend ready, GUI stub)**
Backend:
- Fully functional premium-spike detection engine  
GUI:
- Preset loading  
- Manual ticker control  
- Scanner Start/Stop buttons (stubbed)  
- Live logging  
Next step: wire GUI inputs into backend `SpikeScanner` configs

## ✅ **3. Buyback Monitor (partially integrated)**
### Working now:
- Manual contract entry  
- Multi-contract paste (CSV-style)  
- Contract list display  
- Recent alert feed (top 40 alerts)  
- Full logging to Logs tab  
- Options chain viewer via **yfinance**  
- Double-click chain row → auto-populates manual builder  
- Start/Stop Buyback engine thread framework

### Not yet wired:
- Final connection between GUI contracts → backend BuybackMonitor  
- Automatic use of presets in buyback (planned)  

## ✅ **4. Options Chain Viewer (via yfinance)**
- Fetches nearest expirations (stable fallback)  
- Displays:
  - Strike
  - Call bid/ask
  - Put bid/ask
  - Expiration
- Sortable columns  
- Ideal for quickly identifying workable strikes

## ✅ **5. Multi-Tab GUI (Tkinter)**
- **Scanner**  
- **Buyback**  
- **Wheel/CSP module** (placeholder)  
- **Logs** (full text stream)  
- Status bar  
- Dark theme support (`sv_ttk`)

---

# 📁 Project Structure

