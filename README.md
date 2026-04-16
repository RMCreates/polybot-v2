# Polymarket Bot

An automated research and trading bot for Polymarket prediction markets — fetches live market data, scores signals via pluggable providers, enforces tiered position sizing and risk limits, and executes orders through the Polymarket CLOB API with a FastAPI dashboard for monitoring. Paper trading mode is the default; live trading requires explicit opt-in via `LIVE_TRADING=true` after validated performance thresholds are met.
