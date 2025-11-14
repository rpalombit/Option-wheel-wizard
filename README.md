OptionSuite — Options Trading Intelligence Toolkit

OptionSuite is a real-world options trading assistant designed to support income strategies such as the Wheel, covered calls, and cash-secured puts. The system combines option-chain analytics, buyback opportunity detection, real-time spike scanning (backend), and a growing multi-tab GUI built with Tkinter.

This repository contains the active CIS-260 development version, aligned with the production-grade tool being built for live trading use.

Features (Current Working State)

Preset Ticker Management:

Load from sp100, sp500, nas100

Manual ticker add or remove

Shared ticker list across all modules

Spike Scanner (backend ready, GUI stub):
Backend: fully functional spike detection engine
GUI: preset loading, manual ticker control, Start and Stop buttons (not wired yet), live logging
Next step: wire the GUI settings into the backend SpikeScanner

Buyback Monitor (partially integrated):
Working now:

Manual contract entry

Multi-contract paste

Contract list display

Recent alerts feed (shows last 40 alerts)

Full logging in the Logs tab

Options chain viewer using yfinance

Double-clicking a chain row fills the contract builder

Start and Stop buyback thread structure is set up

Not wired yet:

Passing GUI contracts into the BuybackMonitor engine

Using presets inside the Buyback module

Options Chain Viewer (via yfinance):

Fetches nearest options expirations

Displays strike, call bid, call ask, put bid, put ask, expiration

Columns are sortable

Helps user quickly build accurate contract entries

Multi-Tab GUI (Tkinter):
Tabs: Scanner, Buyback, Wheel/CSP (placeholder), Logs
Includes a status bar and optional dark theme

Project Structure

Option-wheel-wizard/

OptionSuite/

OptionSuite_FreshStart.py (backend: spike and buyback engines)

OptionSuite_GUI_v4.py (current GUI)

presets/

sp100.txt

sp500.txt

nas100.txt

NEXT_PHASE_PLAN.md
README.md
.gitignore

Running the Application

Requirements:
Python 3.11 recommended

Install libraries:
pip install yfinance numpy pandas

Launch the GUI from the repo root:
python OptionSuite/OptionSuite_GUI_v4.py

The GUI opens with the following tabs:
Scanner
Buyback
Wheel/CSP
Logs

Next Development Phases

Immediate:

Wire Buyback GUI contracts into the backend BuybackMonitor

Link presets into the Buyback module

Connect the Spike Scanner GUI to the backend engine

Add alert color coding and a Clear Logs button

Medium-Term:

Save GUI preferences

Add advanced options-chain filtering

Create a combined alert dashboard for Scanner and Buyback

Export alerts to CSV files

Long-Term:

Full Wheel strategy module

Contract notebook and tracking system

Event-aware scanning (earnings, IV behavior)

High-volume multi-ticker scanning

Status (Nov 2025)

The project was rebuilt from scratch and reorganized into a clean structure. The GUI v4 introduced major improvements in layout and usability.

The spike backend is fully functional and ready to be wired into the GUI.

The buyback system is built and partially connected (manual contracts, options viewer, logging, alerts, threading).

Preset system and logging system are fully working.

The repository now has:

Clean file organization

Updated modules

An active development roadmap

OptionSuite is ready for the next phase of feature integration.
