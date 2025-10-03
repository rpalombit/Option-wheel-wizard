import yfinance as yf
import time

# === User Inputs ===
ticker_input = input("Enter stock ticker to monitor (e.g. AAPL): ").upper()
option_type = input("Track Calls or Puts? (call/put): ").lower()
spike_threshold = 30  # percent
check_interval = 60  # seconds

# === Load Options Data ===
stock = yf.Ticker(ticker_input)
expirations = stock.options

if not expirations:
    print("No option data found for this ticker.")
    exit()

selected_expiry = expirations[0]  # soonest expiry
previous_prices = {}

print(f"\n📡 Monitoring {option_type.upper()}s for {ticker_input} (exp: {selected_expiry})")
print(f"🔔 Alerting on {spike_threshold}%+ spikes every {check_interval}s\n")

# === Main Loop ===
while True:
    try:
        chain = stock.option_chain(selected_expiry)
        options = chain.calls if option_type == "call" else chain.puts

        for _, row in options.iterrows():
            symbol = row['contractSymbol']
            price = row['lastPrice']
            volume = row['volume']

            if volume < 5 or price == 0:
                continue  # Skip inactive or illiquid

            if symbol in previous_prices:
                old_price = previous_prices[symbol]
                if old_price > 0:
                    change = ((price - old_price) / old_price) * 100
                    if change >= spike_threshold:
                        print(f"[ALERT] {symbol} spiked {change:.1f}% → ${old_price:.2f} → ${price:.2f}")
            previous_prices[symbol] = price

        time.sleep(check_interval)

    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped.")
        break
    except Exception as e:
        print(f"⚠️ Error: {e}")
        time.sleep(check_interval)