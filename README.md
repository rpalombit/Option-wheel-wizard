Option Wheel Wizard

This project is my CIS 260 Capstone, designed to help income traders manage covered calls and cash-secured puts. It has two main tools:

-Spike Scanner (option_spikes.py)

Scans option-chain data for sudden premium spikes.

Alerts with ticker, strike, expiration, and % change.

Helps identify opportunities to sell options at inflated premiums.


-Buyback Monitor (buyback.py)

Tracks short option positions from a positions.csv file.

Alerts when premiums drop significantly (e.g., −50% or below a target price).

Helps traders buy back options at favorable prices.


-Sample Data (positions.csv)

Example file format for tracking sold option positions.

Users can edit with their own trades.


-Requirements (requirements.txt)

Lists dependencies (yfinance, pandas, numpy).

Usage

Export option data (from ThinkorSwim, Yahoo Finance, etc.).

Run option_spikes.py to look for premium spikes.

Enter your short positions in positions.csv.

Run buyback.py to get buyback alerts.


Status:

Initial scripts uploaded
Midterm goal: finalize core logic and demonstrate with test data
Working: optional event awareness (earnings, dividends, macro news) and optimization
