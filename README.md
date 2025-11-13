# OptionSuite — Option Trading Intelligence Toolkit

OptionSuite is a real-world options trading assistant designed to improve income strategies such as the Wheel, covered calls, and cash-secured puts. It combines real-time market scanning, option-chain analytics, and automated buyback monitoring into one unified, extensible Python package.

This repository contains the development version of OptionSuite for CIS-260, aligned with the production tool I am actively building for real trading use.

---

## 🚀 Features

### 1. Spike Scanner
Scans option chains across multiple tickers and expirations, detecting:
- Premium spikes  
- IV changes  
- Spread anomalies  
- Per-contract cooldowns  

### 2. Buyback Monitor
Tracks short option positions and alerts when:
- Target percentages captured  
- Floor prices hit  
- Fast drops occur  

### 3. Unified GUI (Tkinter)
- Market Scanner tab  
- Buyback / Lookup tab  
- Live logs + alert table  
- Presets loader  
- CSV export  
- Thread-safe StoppableSpike & StoppableBuyback engines  

---

## 📁 Project Structure
OptionSuite/
├── OptionSuite_FreshStart.py
├── OptionSuite_FreshStart_GUI.py
├── presets/
│   ├── sp100.txt
│   ├── sp500.txt
│   └── nas100.txt
└── __init__.py

README.md
.gitignore

---

## 🛠 Next Phases

Planned improvements for the next development cycle:

- Auto-save alerts to timestamped CSV files  
- Add runtime status bar (last scan time, next scan countdown)  
- Highlight major spikes with color-coding in the GUI  
- Save GUI settings (persistent config file)  
- Optional chart visualizer (premium history, IV snapshots)  

---

## 🐍 Requirements

Python 3.11 recommended

Required libraries:
- yfinance  
- numpy  
- pandas  
- tkinter (built into Python on Windows)  

Install dependencies:
pip install yfinance numpy pandas

---

## 📅 Status (Nov 2025)

This repository was rebuilt from scratch using the full midterm working version as the foundation.  
Old pre-midterm files were removed, and the project was reorganized into a clean folder structure to prepare for the next development phase.
