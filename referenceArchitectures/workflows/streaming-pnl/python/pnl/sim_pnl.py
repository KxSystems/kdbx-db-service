#!/usr/bin/env python3
"""
sim_pnl.py - running P&L analytic, downstream of both simulated feeds.

Subscribes to RT for BOTH input streams and publishes its state back to RT as
a KEYED table, so each publish upserts the row for that key rather than
appending:

    mdquote  (market data)  --.
                               >--  sim_pnl  --> pnlstate  (keyed, upserted)
    omstrade  (fills)       --'

All RT connection logic - publisher, subscriber, message decoding, the
instrument universe - lives in common/rt_common.py and is shared with the two
upstream simulators. This file is only the P&L arithmetic and the wiring.

KEYED OUTPUT, VERIFIED
----------------------
Upsert is a property of the TABLE, not of the publish call: a table created
with `primaryKeys` upserts on matching keys, and the publisher still issues an
ordinary rt_helper.insert. Verified directly - publishing the same key twice
with different values left ONE row holding the second value.

Two constraints that came out of testing, both non-obvious:
  * the output table CANNOT be `partitioned`. That combination is rejected:
    "primaryKeys MUST include prtnCol 'ts' if specified" - and keying on a
    tick timestamp would defeat upsert entirely. It is `splayed_mem`
    (reference data, held in memory).
  * a keyed table is NOT readable via /api/v0/query/q, which errors with
    "Unexpected error (<table>) encountered executing .query.q". Use
    /api/v0/query/simple. See README.

P&L METHOD
----------
Weighted average cost, as specified - not FIFO or LIFO:
  * a trade that INCREASES the position recomputes the weighted-average cost
    over the combined old and new quantity;
  * a trade that REDUCES the position leaves the average cost untouched and
    books realised P&L on the reduced quantity;
  * realised P&L accumulates permanently and is never restated;
  * unrealised P&L is recomputed on every quote for whatever is still held.

Not production code. Everything is env-var configurable.
"""

import sys
import time
from datetime import datetime, timezone

import rt_common as rtc

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# Keyed output table. Must exist, with primaryKeys - see setup-tables.sh.
TABLE = rtc.env("PNL_TABLE", "pnlstate")

# Input streams. Both arrive on the same RT topic, so one subscription serves
# both and we filter by table name.
QUOTE_TABLE = rtc.env("PNL_QUOTE_TABLE", "mdquote")
TRADE_TABLE = rtc.env("PNL_TRADE_TABLE", "omstrade")

# RT client names. Publisher and subscriber are separate RT clients and must
# not share a name or a log directory.
PUB_CLIENT_NAME = rtc.env("PNL_PUB_CLIENT_NAME", "dbs-pnlpub")
SUB_CLIENT_NAME = rtc.env("PNL_SUB_CLIENT_NAME", "dbs-pnlsub")

# Which quote field to mark positions at: mid | bid | ask | lastpx.
# mid = (bid+ask)/2 is the conventional mark for unrealised P&L.
MARK_PRICE = rtc.env("PNL_MARK_PRICE", "mid").lower()

# Key granularity. omstrade carries a `book` column, so the default key is
# (sym, book) - P&L per instrument per book. Set to "sym" to aggregate all
# books into one row per instrument.
KEY_MODE = rtc.env("PNL_KEY", "sym+book").lower()

# Publish throttle: republish a given key at most this often.
#
# This is NOT cosmetic - it is required. Every publish to a keyed table bumps
# a GLOBAL REFERENCE VINTAGE that every DAP must catch up to before it will
# serve any query. Measured: publishing on every recalculation reached ~2,600
# upserts/sec, which advanced the vintage so far that queries failed with
#   rc=45 "DAP reference vintage 55214958 does not match global reference
#          vintage 60440983"
# and that broke queries for mdquote/omstrade too, not just this table.
# Keyed tables here are REFERENCE DATA - meant for occasional updates.
#
# State is recalculated on every single message regardless; this only limits
# how often each key's row is pushed to RT. At the default, ~75 keys / 5s is
# ~15 upserts/sec. Set to 0 to publish on every recalculation - expect the
# failure above.
MIN_PUBLISH_INTERVAL = rtc.env_float("PNL_MIN_PUBLISH_INTERVAL", 5.0)

# Warm-up: how close to "now" an incoming message must be for the stream to
# count as live, in seconds.
#
# Subscribing at position 0 replays the whole log so position and cost basis
# are rebuilt from every trade ever published. Those replayed messages carry
# their ORIGINAL timestamps, so comparing the envelope timestamp with the wall
# clock tells us whether we are still catching up. While catching up, state is
# updated but nothing is published; on reaching live traffic, one snapshot of
# every key is published and normal publishing begins. That avoids the
# 735k-upsert storm a naive replay produces.
WARMUP_LAG = rtc.env_float("PNL_WARMUP_LAG", 5.0)

# Print a progress line every N publishes (0 disables).
LOG_EVERY = rtc.env_int("PNL_LOG_EVERY", 100)

# Reconnect / retry, both sides.
RECONNECT_BASE = rtc.env_float("PNL_RECONNECT_BASE", 1.0)
RECONNECT_MAX = rtc.env_float("PNL_RECONNECT_MAX", 30.0)
RECONNECT_MAX_ATTEMPTS = rtc.env_int("PNL_RECONNECT_MAX_ATTEMPTS", 0)
PUSHER_CHECK_SECS = rtc.env_float("PNL_PUSHER_CHECK_SECS", 5.0)

# ---------------------------------------------------------------------------
# P&L STATE
# ---------------------------------------------------------------------------


class Position:
    """
    Weighted-average-cost P&L for one key (one instrument, or one
    instrument+book).

    Sign convention: position is net signed quantity - positive is long,
    negative is short. `avgcost` is the weighted-average cost of whatever is
    currently held, and is only recomputed when the position grows.
    """

    __slots__ = ("sym", "book", "position", "avgcost", "realized",
                 "unrealized", "markpx", "trades", "lastupd")

    def __init__(self, sym, book):
        self.sym = sym
        self.book = book
        self.position = 0          # net signed quantity
        self.avgcost = 0.0         # weighted-average cost of the open position
        self.realized = 0.0        # accumulates permanently, never restated
        self.unrealized = 0.0      # recomputed on every quote
        self.markpx = 0.0          # last mark price seen
        self.trades = 0
        self.lastupd = None

    def apply_trade(self, side, qty, price):
        """
        Fold one fill into the position.

        Three cases, which is where weighted-average differs from FIFO:

        1. OPENING or INCREASING (same sign, or from flat) - recompute the
           weighted-average cost across old and new quantity. No realised P&L.

        2. REDUCING or CLOSING (opposite sign, not past flat) - book realised
           P&L on the reduced quantity at the OLD average cost, and leave
           avgcost unchanged: the remaining inventory keeps the cost basis it
           already had. This is the specified behaviour.

        3. CROSSING through flat (opposite sign, larger than the position) -
           realise the whole existing position, then open the remainder on the
           other side at the trade price, which becomes the new average cost.
        """
        signed = qty if side == "buy" else -qty
        old_pos = self.position
        old_cost = self.avgcost

        if old_pos == 0 or (old_pos > 0) == (signed > 0):
            # --- case 1: opening or increasing -----------------------------
            new_pos = old_pos + signed
            # Weight by absolute quantity so it works for short positions too.
            total_abs = abs(old_pos) + abs(signed)
            if total_abs:
                self.avgcost = (abs(old_pos) * old_cost + abs(signed) * price) / total_abs
            self.position = new_pos

        elif abs(signed) <= abs(old_pos):
            # --- case 2: reducing or closing exactly -----------------------
            closed = abs(signed)
            # Long: gain is (exit - cost). Short: gain is (cost - exit).
            direction = 1.0 if old_pos > 0 else -1.0
            self.realized += direction * (price - old_cost) * closed
            self.position = old_pos + signed
            if self.position == 0:
                self.avgcost = 0.0     # flat: no basis to carry
            # else: avgcost deliberately unchanged

        else:
            # --- case 3: crossing through flat -----------------------------
            closed = abs(old_pos)
            direction = 1.0 if old_pos > 0 else -1.0
            self.realized += direction * (price - old_cost) * closed
            # Remainder opens a fresh position at the trade price.
            self.position = old_pos + signed
            self.avgcost = price

        self.trades += 1
        # A trade moves the mark too, so unrealised stays consistent even if
        # no quote arrives for a while.
        if self.markpx:
            self.remark(self.markpx)
        else:
            self.remark(price)

    def remark(self, markpx):
        """Recompute unrealised P&L at a new mark price."""
        self.markpx = markpx
        self.unrealized = (markpx - self.avgcost) * self.position

    @property
    def total(self):
        return self.realized + self.unrealized

    def row(self, now):
        """
        Row dict for the keyed output table. Key columns come FIRST, matching
        the primaryKeys order - the DB Service requires that ordering in the
        table definition, so keep the payload consistent with it.
        """
        return {
            "sym": self.sym,
            "book": self.book,
            "position": int(self.position),
            "avgcost": round(self.avgcost, 4),
            "markpx": round(self.markpx, 4),
            "realized": round(self.realized, 2),
            "unrealized": round(self.unrealized, 2),
            "totalpnl": round(self.total, 2),
            "trades": int(self.trades),
            "lastupd": now,
        }


class PnlBook:
    """All positions, keyed by (sym, book) or (sym,) depending on PNL_KEY."""

    def __init__(self, key_mode):
        self.by_key = {}
        self.by_sym = {}       # sym -> [keys], so a quote can find its holders
        self.use_book = key_mode != "sym"

    def _key(self, sym, book):
        return (sym, book if self.use_book else "")

    def get(self, sym, book):
        k = self._key(sym, book)
        pos = self.by_key.get(k)
        if pos is None:
            pos = Position(sym, k[1])
            self.by_key[k] = pos
            self.by_sym.setdefault(sym, []).append(k)
        return pos

    def holders(self, sym):
        """Existing keys for this sym - a quote only marks what already exists."""
        return [self.by_key[k] for k in self.by_sym.get(sym, ())]


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def mark_from_quote(row):
    """Pick the mark price out of a mdquote row per PNL_MARK_PRICE."""
    bid, ask = row.get("bid"), row.get("ask")
    if MARK_PRICE == "bid" and bid is not None:
        return float(bid)
    if MARK_PRICE == "ask" and ask is not None:
        return float(ask)
    if MARK_PRICE == "lastpx" and row.get("lastpx") is not None:
        return float(row["lastpx"])
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2.0
    return None


def main():
    running = rtc.RunFlag()

    book = PnlBook(KEY_MODE)
    print(
        f"[pnl] P&L analytic -> keyed table '{TABLE}' "
        f"(key={'sym+book' if book.use_book else 'sym'}, mark={MARK_PRICE})",
        flush=True,
    )
    print(f"[pnl] inputs: {QUOTE_TABLE} (quotes), {TRADE_TABLE} (trades)", flush=True)

    pub = rtc.RTPublisher(
        client_name=PUB_CLIENT_NAME,
        table=TABLE,
        reconnect_base=RECONNECT_BASE,
        reconnect_max=RECONNECT_MAX,
        reconnect_max_attempts=RECONNECT_MAX_ATTEMPTS,
    )
    pub.connect()

    stats = {"quotes": 0, "trades": 0, "published": 0, "dropped": 0}
    last_pub = {}
    # live=False means we are still replaying history: fold messages into
    # state, publish nothing.
    state = {"live": False, "replayed": 0}

    def publish(pos, force=False):
        """
        Upsert one key's current state.

        Suppressed entirely while replaying history (state["live"] is false),
        and otherwise throttled per key - see MIN_PUBLISH_INTERVAL for why
        that matters. `force` bypasses the throttle, for the go-live snapshot.
        """
        if not state["live"]:
            return
        now = time.monotonic()
        if MIN_PUBLISH_INTERVAL and not force:
            k = (pos.sym, pos.book)
            if now - last_pub.get(k, 0.0) < MIN_PUBLISH_INTERVAL:
                return
            last_pub[(pos.sym, pos.book)] = now
        if pub.insert(pos.row(datetime.now(timezone.utc))):
            stats["published"] += 1
            if LOG_EVERY and stats["published"] % LOG_EVERY == 0:
                tot = sum(p.total for p in book.by_key.values())
                print(
                    f"[pnl] {stats['published']} upserts to {TABLE} | "
                    f"{stats['trades']} trades, {stats['quotes']} quotes, "
                    f"{len(book.by_key)} keys, total P&L {tot:,.0f}",
                    flush=True,
                )
        else:
            stats["dropped"] += 1

    def go_live():
        """
        Switch from replay to live, publishing one snapshot of every key.

        Called the first time a message arrives that is close enough to now.
        The snapshot is forced past the throttle so the table reflects the
        whole replayed history immediately, rather than filling in key by key
        as each instrument next ticks.
        """
        state["live"] = True
        print(
            f"[pnl] caught up after {state['replayed']} replayed messages "
            f"({stats['trades']} trades, {stats['quotes']} quotes, "
            f"{len(book.by_key)} keys); publishing snapshot",
            flush=True,
        )
        for p in list(book.by_key.values()):
            publish(p, force=True)
        print(f"[pnl] snapshot published ({stats['published']} rows); now live", flush=True)

    def on_rows(table, rows, position, msg_ts):
        """Called per RT message, as it arrives - nothing is batched."""
        # Replay detection: replayed messages carry their original timestamps.
        if not state["live"]:
            if msg_ts is None:
                # Cannot tell - treat as live rather than never publishing.
                go_live()
            else:
                # Envelope timestamps are naive UTC.
                lag = (datetime.now(timezone.utc).replace(tzinfo=None) - msg_ts).total_seconds()
                if lag <= WARMUP_LAG:
                    go_live()
                else:
                    state["replayed"] += 1
                    if state["replayed"] % 50000 == 0:
                        print(
                            f"[pnl] replaying... {state['replayed']} messages, "
                            f"{lag/60:.1f} min behind, {len(book.by_key)} keys",
                            flush=True,
                        )

        if table == TRADE_TABLE:
            for r in rows:
                sym, qty, price = r.get("sym"), r.get("qty"), r.get("price")
                side = str(r.get("side", "")).lower()
                if not sym or qty is None or price is None or side not in ("buy", "sell"):
                    continue
                stats["trades"] += 1
                p = book.get(sym, str(r.get("book", "")))
                p.apply_trade(side, int(qty), float(price))
                publish(p)                      # recalculated by a trade

        elif table == QUOTE_TABLE:
            for r in rows:
                sym = r.get("sym")
                if not sym:
                    continue
                mark = mark_from_quote(r)
                if mark is None:
                    continue
                stats["quotes"] += 1
                # Only re-mark keys that actually hold this instrument;
                # a quote for something never traded has no P&L to report.
                for p in book.holders(sym):
                    p.remark(mark)
                    publish(p)                  # recalculated by a quote

    sub = rtc.RTSubscriber(
        client_name=SUB_CLIENT_NAME,
        tables=(QUOTE_TABLE, TRADE_TABLE),
        on_rows=on_rows,
        reconnect_base=RECONNECT_BASE,
        reconnect_max=RECONNECT_MAX,
    )

    # Keep the RT publish link healthy while the subscriber blocks. See
    # rt_common.pusher_alive() for why polling is the only reliable detector.
    def watchdog():
        if PUSHER_CHECK_SECS:
            pub.ensure_alive()

    try:
        print("[pnl] running. Ctrl+C (or docker stop) to halt.", flush=True)
        next_check = time.monotonic() + PUSHER_CHECK_SECS
        # run() blocks on the subscription; interleave the publisher watchdog
        # by letting it return control via the running flag.
        import threading

        t = threading.Thread(target=sub.run, args=(running,), daemon=True)
        t.start()
        while running:
            time.sleep(0.5)
            if PUSHER_CHECK_SECS and time.monotonic() >= next_check:
                watchdog()
                next_check = time.monotonic() + PUSHER_CHECK_SECS
    finally:
        pub.close()
        print(
            f"[pnl] stopped after {stats['published']} upserts "
            f"({stats['trades']} trades, {stats['quotes']} quotes, "
            f"{stats['dropped']} dropped)",
            flush=True,
        )


if __name__ == "__main__":
    sys.exit(main())
