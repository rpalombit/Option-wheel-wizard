import yfinance as yf
import pandas as pd
import time
import os

# === Load active covered call positions ===
filename = "positions.csv"

if not os.path.exists(filename):
    print(f"⚠️ File '{filename}' not found. Please create it with columns: Ticker,Strike,Premium,Expiry")
    exit()

print(f"📂 Monitoring active positions from {filename}...\n")

spike_threshold = -50  # alert when premium drops 50%+
check_interval = 60  # check every 60 seconds

positions_df = pd.read_csv(filename)

while True:
    try:
        for _, row in positions_df.iterrows():
            ticker = row['Ticker'].upper()
            strike = float(row['Strike'])
            entry_price = float(row['Premium'])
            expiry = row['Expiry']

            stock = yf.Ticker(ticker)
            chain = stock.option_chain(expiry)
            calls = chain.calls
            match = calls[calls['strike'] == strike]

            if match.empty:
                continue

            current_price = match['lastPrice'].values[0]
            volume = match['volume'].values[0]
            symbol = match['contractSymbol'].values[0]

            if volume < 1 or current_price == 0:
                continue

            change = ((current_price - entry_price) / entry_price) * 100
            if change <= spike_threshold:
                print(f"[BUYBACK ALERT] {symbol} dropped {abs(change):.1f}% → ${entry_price:.2f} → ${current_price:.2f}")
                print(f"    📉 Consider buying back. Vol: {volume} | Exp: {expiry}\n")

        time.sleep(check_interval)

    except KeyboardInterrupt:
        print("\n🛑 Buyback monitor stopped.")
        break
    except Exception as e:
        print(f"⚠️ Error: {e}")
        time.sleep(check_interval)
