#!/usr/bin/env python3
"""
sim_marketdata.py - a throwaway simulated MARKET DATA simulator that
publishes bid/ask/last ticks into the KDB-X DB Service via the RT bus.

Counterpart: sim_oms.py, which publishes trades against the same universe.

All RT connection logic, the instrument universe and the env helpers live in
rt_common.py so both simulators share one copy - this file is only the
market-data simulation itself.

Not production code. Everything is env-var configurable.
"""

import math
import random
import sys
import time
from datetime import datetime, timezone

import rt_common as rtc

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# Target table in the DB Service. Must already exist - see README.md.
TABLE = rtc.env("MD_TABLE", "mdquote")

# RT client name. Also names the client's local log dir, <RT_DIR>/<name>* -
# keep the `dbs-` prefix so reset-db.sh style cleanup finds it.
RT_CLIENT_NAME = rtc.env("RT_CLIENT_NAME", "dbs-mdpub")

# Seconds between ticks per instrument. One full pass over the universe is
# published per interval, one row at a time.
TICK_INTERVAL = rtc.env_float("MD_TICK_INTERVAL", 1.0)

# Per-tick volatility and quoted spread, in basis points of price.
VOL_BPS = rtc.env_float("MD_VOL_BPS", 5.0)
SPREAD_BPS = rtc.env_float("MD_SPREAD_BPS", 2.0)

# Mean reversion strength (0 = pure random walk, 1 = snaps back to seed each
# tick). A small value keeps prices realistic over a long run instead of
# drifting off to zero or infinity.
MEAN_REVERT = rtc.env_float("MD_MEAN_REVERT", 0.02)

# Optional RNG seed for reproducible runs. Empty = non-deterministic.
SEED = rtc.env("MD_SEED", "")

# Print a progress line every N published rows (0 disables).
LOG_EVERY = rtc.env_int("MD_LOG_EVERY", 100)

# Reconnect / retry. See rt_common.RTPublisher and rt_common.pusher_alive().
RECONNECT_BASE = rtc.env_float("MD_RECONNECT_BASE", 1.0)
RECONNECT_MAX = rtc.env_float("MD_RECONNECT_MAX", 30.0)
RECONNECT_MAX_ATTEMPTS = rtc.env_int("MD_RECONNECT_MAX_ATTEMPTS", 0)
PUSHER_CHECK_SECS = rtc.env_float("MD_PUSHER_CHECK_SECS", 5.0)

# ---------------------------------------------------------------------------
# TICK GENERATION
# ---------------------------------------------------------------------------


class Instrument:
    """Per-symbol state for the random walk and the cumulative day volume."""

    def __init__(self, sym, seed_price):
        self.sym = sym
        self.ticker = rtc.full_ticker(sym)
        self.seed = seed_price
        self.mid = seed_price
        self.volume = 0
        self.trddate = None

    def tick(self, now):
        """Advance the price one step and return a row dict for the DB Service."""
        # Mean-reverting geometric random walk: a lognormal step (so prices
        # stay positive and moves scale with price) plus a gentle pull back
        # toward the seed price so a long run stays in a realistic range.
        sigma = VOL_BPS / 10_000.0
        shock = random.gauss(0.0, sigma)
        drift = MEAN_REVERT * math.log(self.seed / self.mid) if self.mid > 0 else 0.0
        self.mid = max(0.01, self.mid * math.exp(drift + shock))

        # Quoted spread, jittered 50%-150% of nominal, floored at one cent.
        spread = max(0.01, self.mid * (SPREAD_BPS / 10_000.0) * random.uniform(0.5, 1.5))
        bid = round(self.mid - spread / 2.0, 2)
        ask = round(self.mid + spread / 2.0, 2)
        if ask <= bid:  # rounding can collapse a very tight spread
            ask = round(bid + 0.01, 2)

        # Bid/ask bounce: the trade prints on whichever side got hit.
        # NB: the column is "lastpx", not "last" - "last" is a q reserved
        # word and the DB Service rejects it as a column name.
        lastpx = ask if random.random() < 0.5 else bid
        lastsize = random.choice([100, 200, 300, 500, 800, 1000, 1500, 2500])

        # Cumulative volume, reset on date rollover so it behaves like a
        # real session-to-date figure.
        trddate = now.date()
        if trddate != self.trddate:
            self.trddate = trddate
            self.volume = 0
        self.volume += lastsize

        return {
            "trddate": trddate,
            "ts": now,
            "sym": self.sym,
            "ticker": self.ticker,
            "exch": rtc.EXCH,
            "ccy": rtc.CCY,
            "bid": bid,
            "bidsize": random.choice([100, 200, 400, 600, 1000, 2000]),
            "ask": ask,
            "asksize": random.choice([100, 200, 400, 600, 1000, 2000]),
            "lastpx": lastpx,
            "lastsize": lastsize,
            "volume": self.volume,
        }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main():
    running = rtc.RunFlag()

    if SEED:
        random.seed(SEED)
        print(f"[feed] RNG seeded with {SEED!r}", flush=True)

    universe = rtc.build_universe()
    instruments = [Instrument(s, p) for s, p in universe.items()]
    print(
        f"[feed] simulating {len(instruments)} instruments -> table '{TABLE}' "
        f"every {TICK_INTERVAL}s: {', '.join(i.sym for i in instruments)}",
        flush=True,
    )

    pub = rtc.RTPublisher(
        client_name=RT_CLIENT_NAME,
        table=TABLE,
        reconnect_base=RECONNECT_BASE,
        reconnect_max=RECONNECT_MAX,
        reconnect_max_attempts=RECONNECT_MAX_ATTEMPTS,
    )
    pub.connect()
    print("[feed] publishing. Ctrl+C (or docker stop) to halt.", flush=True)

    published = 0
    dropped = 0
    next_pusher_check = time.monotonic() + PUSHER_CHECK_SECS
    try:
        while running:
            cycle_start = time.monotonic()

            # Periodically confirm the RT link is still up, and rebuild it if
            # not. Cheap (one /proc scan), so it runs on a short timer.
            if PUSHER_CHECK_SECS and cycle_start >= next_pusher_check:
                pub.ensure_alive()
                next_pusher_check = cycle_start + PUSHER_CHECK_SECS

            for inst in instruments:
                if not running:
                    break
                # Timestamp each row at generation time and publish it straight
                # away - one insert per tick, deliberately not batched.
                row = inst.tick(datetime.now(timezone.utc))
                if pub.insert(row):
                    published += 1
                    if LOG_EVERY and published % LOG_EVERY == 0:
                        print(
                            f"[feed] published {published} rows to {TABLE} "
                            f"({dropped} dropped)",
                            flush=True,
                        )
                else:
                    dropped += 1
            # Sleep the remainder of the interval so the tick rate stays
            # steady regardless of how long publishing took.
            time.sleep(max(0.0, TICK_INTERVAL - (time.monotonic() - cycle_start)))
    finally:
        pub.close()
        print(
            f"[feed] stopped after {published} rows published, {dropped} dropped",
            flush=True,
        )


if __name__ == "__main__":
    sys.exit(main())
