#!/usr/bin/env python3
"""
sim_oms.py - a throwaway OMS simulator that publishes TRADES into the KDB-X
DB Service via the RT bus.

Counterpart to sim_marketdata.py (market data). Both publish against the SAME
instrument universe, defined once in rt_common.py, so trades and quotes stay
coherent and an as-of join lines up:

    aj[`sym`ts; select from omstrade where ...; select from mdquote where ...]

All RT connection logic, the universe and the env helpers come from
rt_common.py - this file is only the trade simulation itself.

PRICE REFERENCING
-----------------
Trades are priced off whatever sim_marketdata is currently quoting. Note that
RT itself is a write-only message bus from a publisher's point of view - there
is no way to query current state back out of it. The live prices are instead
read from the DB Service gateway, which is where the RT stream lands, using
the repo's own Python client (dbservice_client). If that is unavailable or the
quote table is empty, it falls back to the static seed prices in rt_common.

Not production code. Everything is env-var configurable.
"""

import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import rt_common as rtc

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# Target table in the DB Service. Must already exist - see README.md.
TABLE = rtc.env("OMS_TABLE", "omstrade")

# RT client name. Also names the client's local log dir, <RT_DIR>/<name>* -
# distinct from the market-data publisher so the two do not share a log.
RT_CLIENT_NAME = rtc.env("RT_CLIENT_NAME", "dbs-omspub")

# Mean trades per second across the whole universe. Arrivals are a Poisson
# process (exponential gaps), so trades are randomly distributed in time
# rather than evenly spaced.
TRADES_PER_SEC = rtc.env_float("OMS_TRADES_PER_SEC", 3.0)

# Account / book tags to stamp trades with.
BOOKS = [
    b.strip()
    for b in rtc.env("OMS_BOOKS", "ALPHA,BETA,GAMMA,DELTA,MACRO").split(",")
    if b.strip()
] or ["ALPHA"]

# Trade sizes are drawn from these round lots.
LOTS = [100, 200, 300, 400, 500, 800, 1000, 1500, 2000, 5000]

# Slippage applied to the reference price, in basis points. Models the trade
# printing slightly away from the quote rather than exactly on it.
SLIPPAGE_BPS = rtc.env_float("OMS_SLIPPAGE_BPS", 1.0)

# --- Price reference -----------------------------------------------------
# "dbservice" = poll the gateway for live quotes (falls back to static on any
# failure); "static" = always use the seed prices from rt_common.
REF_SOURCE = rtc.env("OMS_REF_SOURCE", "dbservice").lower()

# DB Service gateway endpoint. The Helm deployment supplies the in-cluster
# service address; localhost is useful when running the process directly.
DBS_ENDPOINT = rtc.env("OMS_DBS_ENDPOINT", "http://localhost:8080")

# Quote table to price off, and how far back to look for a quote.
REF_TABLE = rtc.env("OMS_REF_TABLE", "mdquote")
REF_LOOKBACK_SECS = rtc.env_float("OMS_REF_LOOKBACK_SECS", 60.0)
REF_LIMIT = rtc.env_int("OMS_REF_LIMIT", 5000)

# How often to refresh the reference prices, in seconds.
#
# This is the dominant source of error between a trade price and the quote
# prevailing at that instant: the reference can be up to this stale, and
# mdquote moves MD_VOL_BPS (default 5bps) every MD_TICK_INTERVAL. With the
# default 2bps quoted spread, the market moves more than a full spread-width
# per tick, so no polling interval keeps most trades strictly inside the
# spread - see README "How close are trade prices to the quote?".
REF_REFRESH_SECS = rtc.env_float("OMS_REF_REFRESH_SECS", 1.0)

# Optional RNG seed for reproducible runs. Empty = non-deterministic.
SEED = rtc.env("OMS_SEED", "")

# Print a progress line every N published trades (0 disables).
LOG_EVERY = rtc.env_int("OMS_LOG_EVERY", 50)

# Reconnect / retry. See rt_common.RTPublisher and rt_common.pusher_alive().
RECONNECT_BASE = rtc.env_float("OMS_RECONNECT_BASE", 1.0)
RECONNECT_MAX = rtc.env_float("OMS_RECONNECT_MAX", 30.0)
RECONNECT_MAX_ATTEMPTS = rtc.env_int("OMS_RECONNECT_MAX_ATTEMPTS", 0)
PUSHER_CHECK_SECS = rtc.env_float("OMS_PUSHER_CHECK_SECS", 5.0)

# ---------------------------------------------------------------------------
# PRICE REFERENCE
# ---------------------------------------------------------------------------


class PriceReference:
    """
    Keeps a {sym: (bid, ask)} map to price trades off.

    Starts from the static seed prices in rt_common (so the very first trade
    is already sensible), then periodically refreshes from the live quote
    table via the DB Service gateway. Any failure just leaves the previous
    values in place - a trade feed that keeps running on slightly stale prices
    is far more useful here than one that stops.
    """

    def __init__(self, universe):
        # Synthetic opening spread around each seed price, used until the
        # first successful refresh (and forever if REF_SOURCE=static).
        self._quotes = {
            sym: (round(px * 0.9995, 2), round(px * 1.0005, 2))
            for sym, px in universe.items()
        }
        self._session = None
        self._live = False
        self._next_refresh = 0.0
        self._warned = False

        if REF_SOURCE != "dbservice":
            print(
                f"[ref] OMS_REF_SOURCE={REF_SOURCE!r} - using static seed prices only",
                flush=True,
            )

    def _connect(self):
        """Open a DB Service session lazily; None if the client is unusable."""
        if self._session is not None:
            return self._session
        try:
            # The repo's own client (see README "Python client"), rather than
            # hand-rolled REST calls.
            import dbservice_client as dbs

            self._session = dbs.Session(endpoint=DBS_ENDPOINT)
            print(f"[ref] DB Service session opened at {DBS_ENDPOINT}", flush=True)
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                print(
                    f"[ref] no DB Service session ({exc}); "
                    "falling back to static seed prices",
                    flush=True,
                )
                self._warned = True
            self._session = None
        return self._session

    def refresh(self, now_mono):
        """Refresh from the live quote table if the timer is due."""
        if REF_SOURCE != "dbservice" or now_mono < self._next_refresh:
            return
        self._next_refresh = now_mono + REF_REFRESH_SECS

        session = self._connect()
        if session is None:
            return

        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=REF_LOOKBACK_SECS)

        def timestamp(value):
            return value.strftime("%Y.%m.%dD%H:%M:%S.%f") + "000"

        try:
            resp = session.query_simple(
                table=REF_TABLE,
                startTS=timestamp(start),
                endTS=timestamp(end),
                sortCols=["ts"],
                limit=REF_LIMIT,
                return_as="json",
            )
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                print(f"[ref] quote refresh failed ({exc}); keeping previous", flush=True)
                self._warned = True
            return

        rows = resp.get("payload", resp) if isinstance(resp, dict) else resp
        if not isinstance(rows, list):
            return

        newest = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym, bid, ask, ts = (
                row.get("sym"),
                row.get("bid"),
                row.get("ask"),
                row.get("ts"),
            )
            # Skip the empty per-tier placeholder rows.
            if not sym or bid is None or ask is None or not ts:
                continue
            if sym not in newest or ts > newest[sym][0]:
                newest[sym] = (ts, float(bid), float(ask))

        if not newest:
            return

        for sym, (_ts, bid, ask) in newest.items():
            self._quotes[sym] = (bid, ask)

        if not self._live:
            self._live = True
            print(
                f"[ref] live prices from {REF_TABLE} for {len(newest)} instruments",
                flush=True,
            )

    def price(self, sym, side):
        """
        Trade price for a side: cross the spread (buy pays the ask, sell hits
        the bid), then apply a little slippage so prints are not always
        exactly on the quote.
        """
        bid, ask = self._quotes[sym]
        base = ask if side == "buy" else bid
        slip = base * (SLIPPAGE_BPS / 10_000.0) * random.uniform(-1.0, 1.0)
        return max(0.01, round(base + slip, 2))

    @property
    def is_live(self):
        return self._live


# ---------------------------------------------------------------------------
# TRADE GENERATION
# ---------------------------------------------------------------------------


def make_trade(sym, ref, now):
    """Build one trade row dict for the DB Service."""
    side = "buy" if random.random() < 0.5 else "sell"
    return {
        "trddate": now.date(),
        "ts": now,
        "sym": sym,
        # uuid4 -> q guid. Unique across restarts with no coordination.
        "tradeid": uuid.uuid4(),
        "side": side,
        "qty": random.choice(LOTS),
        "price": ref.price(sym, side),
        "book": random.choice(BOOKS),
        "exch": rtc.EXCH,
        "ccy": rtc.CCY,
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main():
    running = rtc.RunFlag()

    if SEED:
        random.seed(SEED)
        print(f"[oms] RNG seeded with {SEED!r}", flush=True)

    universe = rtc.build_universe()
    symbols = list(universe)
    print(
        f"[oms] simulating trades on {len(symbols)} instruments -> table "
        f"'{TABLE}' at ~{TRADES_PER_SEC}/s (Poisson): {', '.join(symbols)}",
        flush=True,
    )
    print(f"[oms] books: {', '.join(BOOKS)}", flush=True)

    ref = PriceReference(universe)

    pub = rtc.RTPublisher(
        client_name=RT_CLIENT_NAME,
        table=TABLE,
        reconnect_base=RECONNECT_BASE,
        reconnect_max=RECONNECT_MAX,
        reconnect_max_attempts=RECONNECT_MAX_ATTEMPTS,
    )
    pub.connect()
    print("[oms] publishing. Ctrl+C (or docker stop) to halt.", flush=True)

    published = 0
    dropped = 0
    next_pusher_check = time.monotonic() + PUSHER_CHECK_SECS
    try:
        while running:
            loop_mono = time.monotonic()

            # Keep the reference prices current, and confirm the RT link.
            ref.refresh(loop_mono)
            if PUSHER_CHECK_SECS and loop_mono >= next_pusher_check:
                pub.ensure_alive()
                next_pusher_check = loop_mono + PUSHER_CHECK_SECS

            # One trade, generated and published immediately - not batched.
            trade = make_trade(random.choice(symbols), ref, datetime.now(timezone.utc))
            if pub.insert(trade):
                published += 1
                if LOG_EVERY and published % LOG_EVERY == 0:
                    src = "live" if ref.is_live else "static"
                    print(
                        f"[oms] published {published} trades to {TABLE} "
                        f"({dropped} dropped, prices={src})",
                        flush=True,
                    )
            else:
                dropped += 1

            # Exponential gap => Poisson arrivals, i.e. randomly distributed
            # in time with mean rate TRADES_PER_SEC, rather than a fixed tick.
            if TRADES_PER_SEC > 0:
                time.sleep(random.expovariate(TRADES_PER_SEC))
    finally:
        pub.close()
        print(
            f"[oms] stopped after {published} trades published, {dropped} dropped",
            flush=True,
        )


if __name__ == "__main__":
    sys.exit(main())
