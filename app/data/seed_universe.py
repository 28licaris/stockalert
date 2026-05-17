"""
Curated 100-ticker seed universe for bulk historical backfill and lake
archiving.

The list is **pinned and deterministic** — same input, same output every run.
It's NOT a watchlist (the watchlist drives live streaming and is mutable per-
user); it's the *training universe* the lake archive is populated against so
backtests and feature-engineering experiments always have a consistent floor
of data available.

Composition (100 tickers, 11 sector buckets):
  - 15 mega-cap tech
  - 10 semiconductors
  - 10 broad-market ETFs (SPY/QQQ/IWM family)
  - 10 sector ETFs (SPDR Select Sectors XL*)
  - 10 commodity & metals ETFs (gold, silver, platinum, palladium, oil, gas,
       broad commodities, agriculture, copper miners)
  - 5  bond & volatility ETFs
  - 8  financials (banks, payments)
  - 8  healthcare (pharma, insurers, medical devices)
  - 8  consumer (staples + discretionary leaders)
  - 8  industrials & energy majors
  - 8  high-volume retail momentum names

Design choices:
  - **Curated, not data-driven.** A static list is reproducible, friendly to
    backtests, and avoids drift week-over-week. Top-volume churn (meme stocks,
    new IPOs) is handled separately by the streaming watchlist.
  - **Categories are enums-as-strings** so they survive JSON/Parquet
    round-trips for downstream analytics without an extra mapping table.
  - **No futures / no options.** Polygon Flat Files for those products live
    under different prefixes and use different schemas; this module is
    stocks/ETFs only.

To consume from a service::

    from app.data.seed_universe import SEED_UNIVERSE, symbols_by_category

    # All 100 tickers as a flat list
    all_symbols = [t.symbol for t in SEED_UNIVERSE]

    # Just the commodity/metals ETFs
    metals = symbols_by_category("commodity_metals")
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SeedTicker:
    """One row of the seed universe. Immutable so callers can safely cache."""
    symbol: str
    category: str
    description: str


# Stable category identifiers (snake_case). Treat as enum values; downstream
# code, dashboards and analytics may filter on these.
CATEGORIES: tuple[str, ...] = (
    "mega_cap_tech",
    "semiconductors",
    "broad_etf",
    "sector_etf",
    "commodity_metals",
    "bond_volatility_etf",
    "financials",
    "healthcare",
    "consumer",
    "industrials_energy",
    "momentum",
)


# Order within each bucket is mostly market-cap descending. The whole list is
# stable across releases so the lake archive doesn't have to re-key partitions.
_TICKERS: tuple[SeedTicker, ...] = (
    # ───────── mega-cap tech (15) ─────────
    SeedTicker("AAPL",  "mega_cap_tech", "Apple Inc."),
    SeedTicker("MSFT",  "mega_cap_tech", "Microsoft Corp."),
    SeedTicker("GOOGL", "mega_cap_tech", "Alphabet Inc. (Class A)"),
    SeedTicker("AMZN",  "mega_cap_tech", "Amazon.com Inc."),
    SeedTicker("META",  "mega_cap_tech", "Meta Platforms Inc."),
    SeedTicker("NVDA",  "mega_cap_tech", "NVIDIA Corp."),
    SeedTicker("TSLA",  "mega_cap_tech", "Tesla Inc."),
    SeedTicker("AVGO",  "mega_cap_tech", "Broadcom Inc."),
    SeedTicker("ORCL",  "mega_cap_tech", "Oracle Corp."),
    SeedTicker("ADBE",  "mega_cap_tech", "Adobe Inc."),
    SeedTicker("CRM",   "mega_cap_tech", "Salesforce Inc."),
    SeedTicker("NFLX",  "mega_cap_tech", "Netflix Inc."),
    SeedTicker("AMD",   "mega_cap_tech", "Advanced Micro Devices Inc."),
    SeedTicker("IBM",   "mega_cap_tech", "International Business Machines Corp."),
    SeedTicker("CSCO",  "mega_cap_tech", "Cisco Systems Inc."),

    # ───────── semiconductors (10) ─────────
    SeedTicker("INTC",  "semiconductors", "Intel Corp."),
    SeedTicker("QCOM",  "semiconductors", "Qualcomm Inc."),
    SeedTicker("MU",    "semiconductors", "Micron Technology Inc."),
    SeedTicker("AMAT",  "semiconductors", "Applied Materials Inc."),
    SeedTicker("LRCX",  "semiconductors", "Lam Research Corp."),
    SeedTicker("KLAC",  "semiconductors", "KLA Corp."),
    SeedTicker("TXN",   "semiconductors", "Texas Instruments Inc."),
    SeedTicker("ASML",  "semiconductors", "ASML Holding N.V."),
    SeedTicker("TSM",   "semiconductors", "Taiwan Semiconductor Mfg. Co."),
    SeedTicker("MRVL",  "semiconductors", "Marvell Technology Inc."),

    # ───────── broad-market ETFs (10) ─────────
    SeedTicker("SPY",   "broad_etf", "SPDR S&P 500 ETF Trust"),
    SeedTicker("QQQ",   "broad_etf", "Invesco QQQ Trust (Nasdaq-100)"),
    SeedTicker("IWM",   "broad_etf", "iShares Russell 2000 ETF"),
    SeedTicker("DIA",   "broad_etf", "SPDR Dow Jones Industrial Average ETF"),
    SeedTicker("VTI",   "broad_etf", "Vanguard Total Stock Market ETF"),
    SeedTicker("VOO",   "broad_etf", "Vanguard S&P 500 ETF"),
    SeedTicker("EFA",   "broad_etf", "iShares MSCI EAFE ETF"),
    SeedTicker("EEM",   "broad_etf", "iShares MSCI Emerging Markets ETF"),
    SeedTicker("VXUS",  "broad_etf", "Vanguard Total International Stock ETF"),
    SeedTicker("ACWI",  "broad_etf", "iShares MSCI ACWI ETF"),

    # ───────── sector ETFs (10, SPDR Select Sectors) ─────────
    SeedTicker("XLF",   "sector_etf", "Financial Select Sector SPDR Fund"),
    SeedTicker("XLE",   "sector_etf", "Energy Select Sector SPDR Fund"),
    SeedTicker("XLK",   "sector_etf", "Technology Select Sector SPDR Fund"),
    SeedTicker("XLI",   "sector_etf", "Industrial Select Sector SPDR Fund"),
    SeedTicker("XLY",   "sector_etf", "Consumer Discretionary Select Sector SPDR Fund"),
    SeedTicker("XLP",   "sector_etf", "Consumer Staples Select Sector SPDR Fund"),
    SeedTicker("XLV",   "sector_etf", "Health Care Select Sector SPDR Fund"),
    SeedTicker("XLU",   "sector_etf", "Utilities Select Sector SPDR Fund"),
    SeedTicker("XLB",   "sector_etf", "Materials Select Sector SPDR Fund"),
    SeedTicker("XLRE",  "sector_etf", "Real Estate Select Sector SPDR Fund"),

    # ───────── commodity & metals ETFs (10) ─────────
    SeedTicker("GLD",   "commodity_metals", "SPDR Gold Shares"),
    SeedTicker("SLV",   "commodity_metals", "iShares Silver Trust"),
    SeedTicker("IAU",   "commodity_metals", "iShares Gold Trust"),
    SeedTicker("PPLT",  "commodity_metals", "abrdn Physical Platinum Shares ETF"),
    SeedTicker("PALL",  "commodity_metals", "abrdn Physical Palladium Shares ETF"),
    SeedTicker("USO",   "commodity_metals", "United States Oil Fund (WTI crude)"),
    SeedTicker("UNG",   "commodity_metals", "United States Natural Gas Fund"),
    SeedTicker("DBC",   "commodity_metals", "Invesco DB Commodity Index Tracking Fund"),
    SeedTicker("DBA",   "commodity_metals", "Invesco DB Agriculture Fund"),
    SeedTicker("COPX",  "commodity_metals", "Global X Copper Miners ETF"),

    # ───────── bond & volatility ETFs (5) ─────────
    SeedTicker("TLT",   "bond_volatility_etf", "iShares 20+ Year Treasury Bond ETF"),
    SeedTicker("HYG",   "bond_volatility_etf", "iShares iBoxx High Yield Corporate Bond ETF"),
    SeedTicker("LQD",   "bond_volatility_etf", "iShares iBoxx Investment Grade Corporate Bond ETF"),
    SeedTicker("SHY",   "bond_volatility_etf", "iShares 1-3 Year Treasury Bond ETF"),
    SeedTicker("VIXY",  "bond_volatility_etf", "ProShares VIX Short-Term Futures ETF"),

    # ───────── financials (8) ─────────
    SeedTicker("JPM",   "financials", "JPMorgan Chase & Co."),
    SeedTicker("BAC",   "financials", "Bank of America Corp."),
    SeedTicker("WFC",   "financials", "Wells Fargo & Co."),
    SeedTicker("GS",    "financials", "Goldman Sachs Group Inc."),
    SeedTicker("MS",    "financials", "Morgan Stanley"),
    SeedTicker("BLK",   "financials", "BlackRock Inc."),
    SeedTicker("V",     "financials", "Visa Inc."),
    SeedTicker("MA",    "financials", "Mastercard Inc."),

    # ───────── healthcare (8) ─────────
    SeedTicker("UNH",   "healthcare", "UnitedHealth Group Inc."),
    SeedTicker("JNJ",   "healthcare", "Johnson & Johnson"),
    SeedTicker("LLY",   "healthcare", "Eli Lilly and Co."),
    SeedTicker("PFE",   "healthcare", "Pfizer Inc."),
    SeedTicker("ABBV",  "healthcare", "AbbVie Inc."),
    SeedTicker("MRK",   "healthcare", "Merck & Co. Inc."),
    SeedTicker("ABT",   "healthcare", "Abbott Laboratories"),
    SeedTicker("BMY",   "healthcare", "Bristol-Myers Squibb Co."),

    # ───────── consumer (8, staples + discretionary leaders) ─────────
    SeedTicker("WMT",   "consumer", "Walmart Inc."),
    SeedTicker("HD",    "consumer", "Home Depot Inc."),
    SeedTicker("COST",  "consumer", "Costco Wholesale Corp."),
    SeedTicker("MCD",   "consumer", "McDonald's Corp."),
    SeedTicker("NKE",   "consumer", "Nike Inc."),
    SeedTicker("SBUX",  "consumer", "Starbucks Corp."),
    SeedTicker("KO",    "consumer", "Coca-Cola Co."),
    SeedTicker("PEP",   "consumer", "PepsiCo Inc."),

    # ───────── industrials & energy (8) ─────────
    SeedTicker("BA",    "industrials_energy", "Boeing Co."),
    SeedTicker("CAT",   "industrials_energy", "Caterpillar Inc."),
    SeedTicker("GE",    "industrials_energy", "GE Aerospace"),
    SeedTicker("XOM",   "industrials_energy", "Exxon Mobil Corp."),
    SeedTicker("CVX",   "industrials_energy", "Chevron Corp."),
    SeedTicker("RTX",   "industrials_energy", "RTX Corp."),
    SeedTicker("LMT",   "industrials_energy", "Lockheed Martin Corp."),
    SeedTicker("COP",   "industrials_energy", "ConocoPhillips"),

    # ───────── momentum (8, high-volume retail favorites) ─────────
    SeedTicker("PLTR",  "momentum", "Palantir Technologies Inc."),
    SeedTicker("COIN",  "momentum", "Coinbase Global Inc."),
    SeedTicker("GME",   "momentum", "GameStop Corp."),
    SeedTicker("AMC",   "momentum", "AMC Entertainment Holdings Inc."),
    SeedTicker("SOFI",  "momentum", "SoFi Technologies Inc."),
    SeedTicker("HOOD",  "momentum", "Robinhood Markets Inc."),
    SeedTicker("RIVN",  "momentum", "Rivian Automotive Inc."),
    SeedTicker("LCID",  "momentum", "Lucid Group Inc."),
)


# Public, immutable handle. Wrapping in tuple makes accidental mutation
# (`SEED_UNIVERSE.append(...)`) raise instead of silently corrupting the list.
SEED_UNIVERSE: tuple[SeedTicker, ...] = _TICKERS

# Convenience constants. Computed once at import.
SEED_SYMBOLS: tuple[str, ...] = tuple(t.symbol for t in SEED_UNIVERSE)
SEED_COUNT: int = len(SEED_UNIVERSE)


def symbols_by_category(category: str) -> list[str]:
    """
    Return the symbols belonging to one of the ``CATEGORIES`` buckets.

    Unknown category strings return ``[]`` so callers that build filters from
    untrusted input (CLI args, query params) don't crash.
    """
    return [t.symbol for t in SEED_UNIVERSE if t.category == category]


def category_counts() -> dict[str, int]:
    """How many seed tickers each bucket contains. Useful for diagnostics."""
    counts: dict[str, int] = {c: 0 for c in CATEGORIES}
    for t in SEED_UNIVERSE:
        counts[t.category] = counts.get(t.category, 0) + 1
    return counts
