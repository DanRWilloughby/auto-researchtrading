# local-data/

Cached Coinbase market data for backtesting and analysis. Contents are
gitignored (large + regenerable from the exchange API).

## Files

| File | Interval | Coverage | Source |
|---|---|---|---|
| `BTC_30m.parquet` | 30-min OHLCV | 2023-06-01 → 2025-12-30 (~2.5 years, 45,313 candles) | CB Advanced Trade API |
| `ETH_30m.parquet` | 30-min | same range | CB |
| `SOL_30m.parquet` | 30-min | same range | CB |
| `BTC_1m.parquet` | 1-min OHLCV | 2025-07-29 → 2026-04-15 (~9 months, 323,316 candles) | CB |
| `ETH_1m.parquet` | 1-min | same range (~329K candles) | CB |
| `SOL_1m.parquet` | 1-min | 2025-08-15 → 2026-04-15 (~8 months, 253K candles; SOL data starts later) | CB |

### Coverage caveats

- **CB's public API retains 1-min candles for only ~270 days.** Fetching older than that returns empty. This is why the 1-min files cover ~9 months, not the full 2.5 years of 30m data.
- **SOL 1-min starts later** (2025-08-15 vs 07-29 for BTC/ETH) because CB's SOL perp didn't have complete 1-min data earlier than that (either the perp wasn't listed or data gaps exist).
- **A few thousand candles are missing** in each 1-min file — minutes with no trades show as gaps. This is normal for lower-volume markets and pre-2024 data. Pandas will handle them cleanly; analysis code should use `.resample('1min').ffill()` if you need strict minute-aligned coverage.

Schema: `timestamp` (ms since epoch, UTC), `open`, `high`, `low`, `close`, `volume`, `funding_rate`.

## Regenerating / extending

### Fetch 30-min history (~2.5 years)

```python
from engine.prepare import _download_coinbase_candles
import pandas as pd
from datetime import datetime, timezone

START = datetime(2023, 6, 1, tzinfo=timezone.utc)
END = datetime(2025, 12, 30, tzinfo=timezone.utc)

for coin in ['BTC', 'ETH', 'SOL']:
    df = _download_coinbase_candles(coin, '30m',
                                    int(START.timestamp()*1000),
                                    int(END.timestamp()*1000))
    df.to_parquet(f'local-data/{coin}_30m.parquet')
```

### Fetch 1-min history (~9 months back from now)

Use `/tmp/trading-check-refresh/fetch_1min_9mo.py` (chunked, resumable) or:

```python
# End up to 1h before now, start 260 days back to stay inside API retention
END = datetime.now(timezone.utc) - timedelta(hours=1)
START = END - timedelta(days=260)
```

Fetch takes ~10-30 min total (3 coins, chunked at 5 days per request).

## Uses

- Backtest scripts — run `engine/backtest.py --interval 30m` with data loaded from here
- Hybrid maker analysis — `hybrid_passive_9mo.py` and similar scripts use the 1-min files to simulate fills at N-min timeouts
- Entry-timing sweeps — compare prices at N min after signal to find optimal entry lag

## Why local (not VM)

The VM has an older 30-min cache at `/home/openclaw/auto-researchtrading/local-data/` (stops at 2025-12-30). Analysis happens on laptop; live trading happens on VM. The VM only needs what the strategy and shadow fetch on demand, not full backtest archives.

If the laptop data is lost, regenerate — it's not a crisis, just ~30 min of re-fetch.
