#!/usr/bin/env python3
"""
rt_common.py - shared plumbing for the throwaway feed simulators in this
repository (sim_marketdata.py, sim_oms.py).

Extracted so both simulators use ONE copy of the RT connection logic and ONE
instrument universe. Nothing in here is specific to market data or to trades;
per-feed behaviour lives in the individual simulator files.

The publisher uses the KX Python RT client:

    rtpy.rt_helper.RTParams(config_url='file://<rtconfig.json>')
    h, rc = rtpy.rt_helper.start(params)
    rtpy.rt_helper.insert(h, '<table>', kx.toq([{...row...}]))
"""

import json
import os
import random
import signal
import tempfile
import threading
import time
from datetime import datetime

import pykx as kx
from rtpy import Subscriber, rt_helper

# ---------------------------------------------------------------------------
# ENV HELPERS
# ---------------------------------------------------------------------------


def env(name, default):
    """Read an env var, treating empty/whitespace as unset."""
    v = os.environ.get(name)
    return default if v is None or not v.strip() else v.strip()


def env_float(name, default):
    try:
        return float(env(name, str(default)))
    except ValueError:
        print(f"[warn] {name} is not a number, using default {default}", flush=True)
        return default


def env_int(name, default):
    try:
        return int(float(env(name, str(default))))
    except ValueError:
        print(f"[warn] {name} is not a number, using default {default}", flush=True)
        return default


def env_list(name):
    """Read a comma-separated environment variable as a list."""
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# SHARED RT CONNECTION DEFAULTS
# ---------------------------------------------------------------------------

# RT publisher ingress ("push server") connection. RT_PUBLISH_ENDPOINTS is
# used for a replicated RT deployment; RT_HOST/RT_PORT remain as a single-node
# fallback.
RT_HOST = env("RT_HOST", "localhost")
RT_PORT = env("RT_PORT", "5002")
RT_PUBLISH_ENDPOINTS = env_list("RT_PUBLISH_ENDPOINTS")

# RT topic. This must match RT_SINK on the target RT deployment.
RT_TOPIC = env("RT_TOPIC", "data")

# Where the RT client keeps its local replay logs. rtpy defaults to /tmp/rt.
RT_DIR = env("RT_DIR", "/tmp/rt")

# rtpy console log level: error | warn | info | debug | trace
RT_LOG_LEVEL = env("RT_LOG_LEVEL", "error")

# --- subscriber side ------------------------------------------------------
# RT subscription ports:
#   5003  rt_subscription   (sub_port)
#   5001  pull_server       (repl_port)
# Note the endpoint format differs from the publisher's: the subscriber wants
# "host:port", whereas the publisher config uses ":host:port".
RT_SUB_PORT = env_int("RT_SUB_PORT", 5003)
RT_REPL_PORT = env_int("RT_REPL_PORT", 5001)
RT_STREAM_ID = env("RT_STREAM_ID", "data")
RT_TOPIC_PREFIX = env("RT_TOPIC_PREFIX", "rt-")
RT_REPLICAS = env_int("RT_REPLICAS", 1)
RT_SUB_ENDPOINTS = env_list("RT_SUB_ENDPOINTS")

# Log position to subscribe from. 0 replays the entire stream from the
# beginning, which is what a stateful analytic wants - it rebuilds position
# and cost basis from every trade ever published. There is no "tail only"
# sentinel: position is a size_t, so -1 raises OverflowError.
RT_SUB_POSITION = env_int("RT_SUB_POSITION", 0)

# Seconds to wait for rt_helper.stop() during shutdown before abandoning it.
#
# Measured: stop() can block indefinitely - NOT only when RT is unreachable,
# but even against a healthy, reachable RT. Called inline it can consume the
# workload's full termination grace period and lose the shutdown summary.
# See RTPublisher.close().
#
# Set to 0 to skip stop() altogether.
RT_STOP_TIMEOUT = env_float("RT_STOP_TIMEOUT", 3.0)

# ---------------------------------------------------------------------------
# SHARED INSTRUMENT UNIVERSE
# ---------------------------------------------------------------------------

# One fixed universe for ALL simulators, so market data and trades stay
# coherent: 15 large-cap US names with roughly realistic seed prices.
DEFAULT_UNIVERSE = {
    "AAPL": 232.50,
    "MSFT": 415.20,
    "NVDA": 128.40,
    "AMZN": 186.30,
    "GOOGL": 168.90,
    "META": 512.60,
    "TSLA": 248.70,
    "JPM": 214.30,
    "V": 282.40,
    "XOM": 118.60,
    "UNH": 528.10,
    "JNJ": 158.20,
    "WMT": 79.40,
    "PG": 168.50,
    "MA": 476.80,
}

# Exchange/market suffix, exchange code and currency, applied to every symbol
# by both simulators. The ticker is formed as "<sym>.<suffix>", e.g. AAPL.US -
# a neutral convention rather than any particular vendor's.
TICKER_SUFFIX = env("SIM_TICKER_SUFFIX", "US")
EXCH = env("SIM_EXCH", "US")
CCY = env("SIM_CCY", "USD")


def build_universe():
    """
    Return {sym: seed_price}, from SIM_SYMBOLS or the built-in default.

    SIM_SYMBOLS is a comma-separated list where each entry is TICKER or
    TICKER:SEEDPRICE, e.g.  "AAPL:232.5,MSFT:415.2,VOD LN:71.3".
    A ticker with no seed price falls back to the built-in price if known,
    otherwise a random price in [20, 500).

    Set SIM_SYMBOLS to the SAME value for every simulator (the compose overlay
    uses a YAML anchor to guarantee this) so trades reference instruments that
    actually have market data. MD_SYMBOLS is still honoured as a fallback for
    backwards compatibility with the original standalone sim_marketdata.
    """
    raw = env("SIM_SYMBOLS", "") or env("MD_SYMBOLS", "")
    if not raw:
        return dict(DEFAULT_UNIVERSE)

    universe = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        # Split on the LAST colon so tickers containing a colon still work.
        if ":" in entry:
            sym, _, price_s = entry.rpartition(":")
            sym = sym.strip()
            try:
                universe[sym] = float(price_s)
                continue
            except ValueError:
                # Not a price after all - treat the whole entry as a ticker.
                sym = entry
        else:
            sym = entry
        universe[sym] = DEFAULT_UNIVERSE.get(sym, round(random.uniform(20, 500), 2))

    if not universe:
        print("[warn] SIM_SYMBOLS parsed to nothing, using default universe", flush=True)
        return dict(DEFAULT_UNIVERSE)
    return universe


def full_ticker(sym):
    """Composite ticker for a symbol, e.g. AAPL -> 'AAPL.US'."""
    return f"{sym}.{TICKER_SUFFIX}" if TICKER_SUFFIX else sym


# ---------------------------------------------------------------------------
# RT PUBLISHER
# ---------------------------------------------------------------------------

# Name of RT's background replicator process. /proc/<pid>/comm is truncated to
# 15 characters, so "push_client_static" shows up as "push_client_sta".
PUSHER_COMM_PREFIX = "push_client_sta"


def pusher_alive():
    """
    Is RT's background push client still running?

    This matters because of how the RT client is actually built. It is
    store-and-forward, NOT a synchronous socket:

      * rt_helper.start() returns rc=0 immediately even if RT is
        unreachable - it does not verify connectivity.
      * rt_helper.insert() appends to a LOCAL replay log under RT_DIR. It
        succeeds even when RT is down, because nothing is sent inline.
      * a separate `push_client_static` process, forked by librt_helper, ships
        that local log to RT and handles its own retries.

    So a dropped RT connection never surfaces as an exception from insert().
    What happens instead is that push_client_static retries for ~5 minutes,
    logs "Unable to connect to <host> after 300001ms, exiting...", and exits.
    Measured behaviour: librt_helper then respawns it and keeps cycling
    indefinitely, so RT self-heals and nothing is lost - the replay log is
    positional, so a fresh pusher resumes from the offset in
    RT_DIR/<client>/state.0.

    This check is therefore a BACKSTOP for the case where the pusher stays
    dead, not the primary reconnect mechanism.

    NOTE: this scans the whole PID namespace, so it assumes one publisher per
    container, as deployed by this chart.
    """
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/comm") as fh:
                    if fh.read().strip().startswith(PUSHER_COMM_PREFIX):
                        return True
            except OSError:
                continue  # process exited between listdir and open
    except OSError:
        # No procfs (not Linux). Can't tell, so don't trigger a reconnect loop.
        return True
    return False


class RTPublisher:
    """
    Owns an RT handle for one publisher and knows how to rebuild it when the
    link to RT drops.

    Unlike the original embedded version, everything is passed in rather than
    read from module globals, so several simulators can share this class while
    publishing to different tables under different RT client names.

    Two independent failure paths are covered:
      1. start()/insert() raising - a genuine local error (bad config,
         unwritable rt_dir). Handled by backoff + retry.
      2. the background push client exiting and staying gone. Handled by
         ensure_alive(). See pusher_alive() for why that is the real signal.
    """

    def __init__(
        self,
        client_name,
        table,
        host=None,
        port=None,
        topic=None,
        rt_dir=None,
        log_level=None,
        reconnect_base=1.0,
        reconnect_max=30.0,
        reconnect_max_attempts=0,
        label="feed",
        endpoints=None,
    ):
        self.client_name = client_name
        self.table = table
        self.host = host or RT_HOST
        self.port = port or RT_PORT
        self.topic = topic or RT_TOPIC
        self.endpoints = (
            endpoints or RT_PUBLISH_ENDPOINTS or [f"{self.host}:{self.port}"]
        )
        self.rt_dir = rt_dir or RT_DIR
        self.log_level = log_level or RT_LOG_LEVEL
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.reconnect_max_attempts = reconnect_max_attempts
        self.label = label  # only used to prefix log lines
        self._handle = None
        self._cfg_path = self._write_config()

    def _write_config(self):
        """
        Generate the rtconfig.json that rt_helper reads, from our settings.

        Shape is taken verbatim from samples/feed/rtconfig.json:
          name    - RT client name (also names the local log dir)
          topics  - maps the logical stream name ("insert") to the RT topic
          insert  - maps that stream name to the list of RT endpoints, each a
                    q-style handle string ":host:port"
        """
        cfg = {
            "name": self.client_name,
            "useSslRt": False,
            "topics": {"insert": self.topic},
            "insert": {
                "insert": [f":{endpoint.lstrip(':')}" for endpoint in self.endpoints]
            },
        }
        # Written to a real file because rt_helper only accepts a config URL.
        fd, path = tempfile.mkstemp(prefix=f"rtconfig-{self.client_name}-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh)
        print(f"[rt] config {path}: {json.dumps(cfg)}", flush=True)
        return path

    def connect(self):
        """(Re)establish the RT handle, retrying with exponential backoff."""
        attempt = 0
        backoff = self.reconnect_base
        while True:
            attempt += 1
            try:
                params = rt_helper.RTParams(
                    config_url="file://" + self._cfg_path,
                    rt_dir=self.rt_dir,
                    console_log_level=self.log_level,
                )
                self._handle, rc = rt_helper.start(params)
                print(
                    f"[rt] connected to {','.join(self.endpoints)} "
                    f"(topic={self.topic}, client={self.client_name}, rc={rc})",
                    flush=True,
                )
                return
            except Exception as exc:  # noqa: BLE001 - any failure means "retry"
                self._handle = None
                if self.reconnect_max_attempts and attempt >= self.reconnect_max_attempts:
                    raise RuntimeError(
                        f"giving up connecting to RT after {attempt} attempts"
                    ) from exc
                print(
                    f"[rt] connect attempt {attempt} failed ({exc}); "
                    f"retrying in {backoff:.1f}s",
                    flush=True,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max)

    def insert(self, row):
        """
        Publish a single row (a dict) immediately - no batching, one insert
        per generated event.

        Note this is an append to the local RT replay log, not a network send;
        the background push client forwards it. Exceptions here therefore mean
        a local problem, so reconnect and retry the row once.

        Returns True if published, False if the row was dropped.
        """
        payload = kx.toq([row])
        try:
            rt_helper.insert(self._handle, self.table, payload)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[rt] insert failed ({exc}); reconnecting", flush=True)
            self.close()
            self.connect()
            try:
                rt_helper.insert(self._handle, self.table, payload)
                return True
            except Exception as exc2:  # noqa: BLE001
                # Drop this event rather than block the feed; the next one will
                # try again on the fresh handle.
                print(f"[rt] insert failed again ({exc2}); dropping row", flush=True)
                return False

    def ensure_alive(self):
        """Restart the RT client if its background push client has died."""
        if pusher_alive():
            return
        print("[rt] push client is gone - RT link is down, restarting", flush=True)
        self.close()
        self.connect()

    def close(self):
        """
        Best-effort teardown, bounded by RT_STOP_TIMEOUT. Safe to call on an
        already-dead handle.

        rt_helper.stop() runs on a daemon thread rather than inline, because
        measurement shows it can block indefinitely - not only when RT is
        unreachable, but even against a healthy RT. Inline, that made every
        `docker stop` hang for the full grace period and exit 137, losing the
        shutdown summary line.

        Abandoning stop() is safe: insert() has already appended each row to
        the on-disk replay log, and that log is positional, so the pusher
        resumes from RT_DIR/<client>/state.0 on the next start. Nothing that
        was published is lost. The thread is a daemon, so it cannot keep the
        interpreter alive once main() returns.
        """
        if self._handle is None:
            return
        # Clear the handle up front so a concurrent insert() cannot use it
        # while the stop thread is working on it.
        handle, self._handle = self._handle, None

        if RT_STOP_TIMEOUT <= 0:
            print("[rt] RT_STOP_TIMEOUT=0, skipping stop()", flush=True)
            return

        done = threading.Event()

        def _stop():
            try:
                rt_helper.stop(handle)
            except Exception as exc:  # noqa: BLE001
                print(f"[rt] stop failed, ignoring ({exc})", flush=True)
            finally:
                done.set()

        threading.Thread(target=_stop, name="rt-stop", daemon=True).start()
        if done.wait(RT_STOP_TIMEOUT):
            print("[rt] stopped cleanly", flush=True)
        else:
            print(
                f"[rt] stop() did not return within {RT_STOP_TIMEOUT}s - "
                "abandoning it; the replay log is on disk, nothing is lost",
                flush=True,
            )


# ---------------------------------------------------------------------------
# RT SUBSCRIBER
# ---------------------------------------------------------------------------


def decode_message(payload):
    """
    Decode one raw RT message into (table_name, [row dicts], envelope_ts).

    rtpy hands on_message a memoryview of a q-IPC-serialised envelope. Its
    shape, established by inspection of a live stream:

        [timestamp, ::, ::, 'publisher-id', ['.b', '<table>', [ {row}, ... ]]]

    so element 0 is the publish timestamp and element 4 is
    (topic; table; rows). Returns (None, [], None) for anything that is not a
    table insert - the same stream also carries control messages
    (`_schemaChange`, `_reload`, `_prtnEnd`) and publisher registrations.

    The envelope timestamp is what lets a consumer distinguish REPLAYED
    history from live traffic: subscribing at position 0 delivers the whole
    log as fast as it can be read, and those messages carry their original
    (old) timestamps.

    kx.deserialize(...).py() works in UNLICENSED pykx, which matters because
    these containers run unlicensed. Indexing the pykx object directly does
    NOT (it raises LicenseException), which is why this converts to native
    Python with .py() first and indexes that.
    """
    try:
        m = kx.deserialize(bytes(payload)).py()
    except Exception:  # noqa: BLE001 - a malformed frame must not kill the feed
        return None, [], None
    if not isinstance(m, list) or len(m) < 5:
        return None, [], None
    msg_ts = m[0] if isinstance(m[0], datetime) else None
    inner = m[4]
    if not isinstance(inner, list) or len(inner) < 3:
        return None, [], msg_ts
    table = inner[1]
    rows = inner[2]
    if not isinstance(table, str) or not isinstance(rows, list):
        return None, [], msg_ts
    return table, [r for r in rows if isinstance(r, dict)], msg_ts


class RTSubscriber:
    """
    Subscribes to an RT stream and calls `on_rows(table, rows, position, msg_ts)` for
    every table insert, as it arrives.

    Both upstream feeds publish to the SAME topic (`RT_TOPIC_PREFIX` +
    `RT_STREAM_ID`, i.e. "rt-data"), so ONE subscription sees both mdquote
    and omstrade; `tables` filters to the ones wanted. Confirmed on a live
    stream: 158,895 mdquote and 31,293 omstrade messages on one subscription.

    Reconnect: rtpy's Subscriber is a context manager with no retry of its
    own, so run() wraps it in a backoff loop and resumes from the last
    position seen, so a reconnect does not replay or skip.
    """

    def __init__(
        self,
        client_name,
        tables,
        on_rows,
        host=None,
        sub_port=None,
        repl_port=None,
        stream_id=None,
        topic_prefix=None,
        replicas=None,
        position=None,
        reconnect_base=1.0,
        reconnect_max=30.0,
        endpoints=None,
    ):
        self.client_name = client_name
        self.tables = set(tables)
        self.on_rows = on_rows
        self.host = host or RT_HOST
        self.sub_port = sub_port or RT_SUB_PORT
        self.repl_port = repl_port or RT_REPL_PORT
        self.endpoints = (
            endpoints or RT_SUB_ENDPOINTS or [f"{self.host}:{self.sub_port}"]
        )
        self.stream_id = stream_id or RT_STREAM_ID
        self.topic_prefix = topic_prefix or RT_TOPIC_PREFIX
        self.replicas = replicas or RT_REPLICAS
        self.position = RT_SUB_POSITION if position is None else position
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.seen = 0
        self.dispatched = 0

    def _on_message(self, pos, mv):
        # Remember where we got to, so a reconnect resumes here.
        self.position = pos
        self.seen += 1
        table, rows, msg_ts = decode_message(mv)
        if table is None or table not in self.tables or not rows:
            return
        self.dispatched += 1
        try:
            self.on_rows(table, rows, pos, msg_ts)
        except Exception as exc:  # noqa: BLE001
            # A bug in the handler must not tear down the subscription.
            print(f"[sub] handler error on {table}: {exc!r}", flush=True)

    @staticmethod
    def _on_event(event_type, pos, next_pos):
        # BADTAIL / BADMSG / RESET / SKIPFORWARD / LOGROLL - informational.
        print(f"[sub] event {event_type} pos={pos} next={next_pos}", flush=True)

    def run(self, running):
        """
        Block, dispatching messages until `running` goes false. Reconnects
        with exponential backoff, resuming from the last position seen.
        """
        backoff = self.reconnect_base
        while running:
            try:
                # Endpoint must be "host:port" - a bare hostname is rejected.
                print(
                    f"[sub] subscribing to {','.join(self.endpoints)} "
                    f"(topic={self.topic_prefix}{self.stream_id}, "
                    f"client={self.client_name}, from position {self.position})",
                    flush=True,
                )
                with Subscriber(
                    endpoints=self.endpoints,
                    stream_id=self.stream_id,
                    topic_prefix=self.topic_prefix,
                    replicas=self.replicas,
                    sub_port=self.sub_port,
                    repl_port=self.repl_port,
                    client_name=self.client_name,
                    position=self.position,
                    on_message=self._on_message,
                    on_event=self._on_event,
                ):
                    print("[sub] subscribed", flush=True)
                    backoff = self.reconnect_base  # a good connection resets it
                    # Messages arrive on rtpy's own thread via the callback;
                    # just stay alive.
                    while running:
                        time.sleep(0.2)
                return
            except Exception as exc:  # noqa: BLE001
                if not running:
                    return
                print(
                    f"[sub] subscription failed ({exc}); retrying in {backoff:.1f}s",
                    flush=True,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max)


# ---------------------------------------------------------------------------
# SHUTDOWN
# ---------------------------------------------------------------------------


class RunFlag:
    """
    SIGTERM/SIGINT-aware run flag for clean container shutdown.
    """

    def __init__(self):
        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, signum, _frame):
        print(f"[feed] signal {signum} received, stopping", flush=True)
        self._running = False

    def __bool__(self):
        return self._running
