/ q market-data simulator. Publishes mdquote rows to RT once per interval.
/ Its simulation and publication behavior mirrors sim_marketdata.py.

.simQDir:$[""~getenv`SIM_Q_DIR;"/app/q";getenv`SIM_Q_DIR];
system "l ",.simQDir,"/common.q";

.md.tick:{[]
  countRows:count .md.symbols;
  today:.z.D;
  if[today<>.md.tradeDate;
    .md.tradeDate::today;
    .md.volume::countRows#0j];

  shocks:.md.volBps*0.0001f*.sim.normal countRows;
  drift:.md.meanRevert*log .md.seeds%.md.mids;
  .md.mids::0.01f|.md.mids*exp drift+shocks;

  spreads:0.01f|.md.mids*.md.spreadBps*0.0001f*(0.5f+countRows?1f);
  bids:.sim.round2 .md.mids-0.5f*spreads;
  asks:.sim.round2 .md.mids+0.5f*spreads;
  collapsed:where asks<=bids;
  if[count collapsed;asks[collapsed]:bids[collapsed]+0.01f];

  hitAsk:0=countRows?2;
  lastpx:?[hitAsk;asks;bids];
  lastsize:.md.lastSizes[countRows?count .md.lastSizes];
  .md.volume::.md.volume+lastsize;

  rows:([]
    trddate:countRows#today;
    ts:countRows#.z.p;
    sym:.md.symbols;
    ticker:.md.tickers;
    exch:countRows#.md.exchange;
    ccy:countRows#.md.currency;
    bid:bids;
    bidsize:.md.quoteSizes[countRows?count .md.quoteSizes];
    ask:asks;
    asksize:.md.quoteSizes[countRows?count .md.quoteSizes];
    lastpx:lastpx;
    lastsize:lastsize;
    volume:.md.volume);

  / Python publishes each instrument as its own RT event. Preserve that
  / message shape rather than batching the whole cycle into one insert.
  {[rows;i]
    row:update ts:.z.p from rows enlist i;
    .sim.publish[.md.publisher;.md.table;row]
    }[rows;] each til countRows;

  .md.published+::countRows;
  if[(0<.md.logEvery)&(0=.md.published mod .md.logEvery);
    .sim.log["marketdata";"published ",string[.md.published]," rows to ",string .md.table]];
  };

.md.start:{[]
  .sim.loadRT[];
  .sim.seedRandom `MD_SEED;
  universe:.sim.universe[];
  .md.symbols::first universe;
  .md.seeds::last universe;
  .md.mids::.md.seeds;
  .md.volume::count[.md.symbols]#0j;
  .md.tradeDate::.z.D;

  suffix:.sim.env[`SIM_TICKER_SUFFIX;"US"];
  .md.tickers::.sim.ticker[;suffix] each .md.symbols;
  .md.exchange::`$.sim.env[`SIM_EXCH;"US"];
  .md.currency::`$.sim.env[`SIM_CCY;"USD"];
  .md.table::`$.sim.env[`MD_TABLE;"mdquote"];
  .md.volBps::.sim.envFloat[`MD_VOL_BPS;5f];
  .md.spreadBps::.sim.envFloat[`MD_SPREAD_BPS;2f];
  .md.meanRevert::.sim.envFloat[`MD_MEAN_REVERT;0.02f];
  .md.logEvery::.sim.envLong[`MD_LOG_EVERY;100];
  .md.lastSizes::100 200 300 500 800 1000 1500 2500;
  .md.quoteSizes::100 200 400 600 1000 2000;
  .md.published::0j;

  client:.sim.env[`RT_CLIENT_NAME;"dbs-q-mdpub"];
  .md.publisherParams::.sim.publisherParams client;
  .md.publisher::.rt.pub .md.publisherParams;
  .z.exit:{[code]
    .sim.stopPublisher[.md.publisherParams;.md.publisher;`.md.publisher];
    code
    };
  intervalMs:1|"j"$1000f*.sim.envFloat[`MD_TICK_INTERVAL;1f];
  .sim.log["marketdata";"publishing ",string[count .md.symbols]," instruments to ",string .md.table];
  .z.ts:.md.tick;
  system "t ",string intervalMs;
  .md.tick[];
  };

if[0=count getenv`SIM_TEST_MODE;.md.start[]];
