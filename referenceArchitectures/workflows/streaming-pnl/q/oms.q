/ q OMS simulator. Reads live mdquote rows from DB Service and publishes
/ omstrade rows to RT.

.simQDir:$[""~getenv`SIM_Q_DIR;"/app/q";getenv`SIM_Q_DIR];
system "l ",.simQDir,"/common.q";

.oms.warn:{[message]
  if[not .oms.warned;
    .sim.log["ref";message];
    .oms.warned::1b];
  };

.oms.closeGateway:{[]
  if[.oms.gatewayHandle>0i;@[hclose;.oms.gatewayHandle;{[error]()}]];
  .oms.gatewayHandle::0i;
  };

.oms.gatewayError:{[error]
  .oms.lastError::$[10h=type error;error;string error];
  `gatewayError
  };

.oms.connectGateway:{[]
  if[.oms.gatewayHandle>0i;:.oms.gatewayHandle];
  .oms.lastError::"";
  handle:@[hopen; `$":",.oms.gateway;.oms.gatewayError];
  if[`gatewayError~handle;
    .oms.warn["no DB Service gateway connection (",.oms.lastError,"); using static seed prices"];
    :0i];
  if[-6h<>type handle;
    .oms.warn["no DB Service gateway connection (",.oms.lastError,"); using static seed prices"];
    :0i];
  if[not handle>0i;
    .oms.warn["no DB Service gateway connection (",.oms.lastError,"); using static seed prices"];
    :0i];
  .oms.gatewayHandle::handle;
  .sim.log["ref";"DB Service gateway connected at ",.oms.gateway];
  handle
  };

.oms.refreshFromGateway:{[]
  if[not "dbservice"~.oms.refSource;:()];

  now:.z.p;
  if[(not null .oms.lastRefresh)&(.oms.refreshNs>"j"$now-.oms.lastRefresh);:()];
  .oms.lastRefresh::now;

  handle:.oms.connectGateway[];
  if[not handle>0i;:()];

  args:`table`startTS`endTS`sortCols`limit!(
    .oms.quoteTable;
    now-"n"$.oms.lookbackNs;
    now;
    enlist "ts";
    .oms.refLimit);
  result:@[handle;(`.query.simple;args;`;()!());.oms.gatewayError];
  if[`gatewayError~result;
    .oms.warn["quote refresh failed (",.oms.lastError,"); keeping previous prices"];
    .oms.closeGateway[];
    :()];
  if[(0h<>type result)|(2>count result);
    .oms.warn["quote refresh returned an unexpected response; keeping previous prices"];
    :()];

  header:first result;
  if[99h=type header;
    if[`rc in key header;
      if[0<>header`rc;
        message:$[`ai in key header;
          $[10h=type header`ai;header`ai;string header`ai];
          "DB Service query failed"];
        .oms.warn["quote refresh failed (",message,"); keeping previous prices"];
        :()]]];

  rows:last result;
  if[98h<>type rows;:()];
  rows:select sym,ts,bid,ask from rows where not null sym,not null ts,not null bid,not null ask;
  if[0=count rows;:()];

  rows:`sym`ts xasc rows;
  latest:select last bid,last ask by sym from rows;
  .oms.quotes::.oms.quotes upsert latest;
  if[not .oms.live;
    .oms.live::1b;
    .sim.log["ref";"live prices from ",string[.oms.quoteTable]," for ",string[count latest]," instruments"]];
  };

.oms.refreshError:{[error]
  .oms.lastError::$[10h=type error;error;string error];
  .oms.warn["quote refresh failed (",.oms.lastError,"); keeping previous prices"];
  ()
  };

.oms.refresh:{[]
  @[{x[]};.oms.refreshFromGateway;.oms.refreshError]
  };

.oms.nextDelayMs:{[]
  if[not 0f<.oms.rate;:1000j];
  u:1e-12|first 1?1f;
  1|"j"$1000f*neg[log u]%.oms.rate
  };

.oms.tick:{[]
  .oms.refresh[];

  sym:first 1?.oms.symbols;
  side:first 1?`buy`sell;
  quote:.oms.quotes sym;
  base:$[side=`buy;quote`ask;quote`bid];
  slip:base*.oms.slippageBps*0.0001f*((2f*first 1?1f)-1f);
  price:0.01f|.sim.round2 base+slip;

  rows:([]
    trddate:enlist .z.D;
    ts:enlist .z.p;
    sym:enlist sym;
    tradeid:1?0Ng;
    side:enlist side;
    qty:enlist first 1?.oms.lots;
    price:enlist price;
    book:enlist first 1?.oms.books;
    exch:enlist .oms.exchange;
    ccy:enlist .oms.currency);

  .sim.publish[.oms.publisher;.oms.table;rows];
  .oms.published+:1;
  if[(0<.oms.logEvery)&(0=.oms.published mod .oms.logEvery);
    source:$[.oms.live;"live";"static"];
    .sim.log["oms";"published ",string[.oms.published]," trades to ",string[.oms.table]," (prices=",source,")"]];
  system "t ",string .oms.nextDelayMs[];
  };

.oms.start:{[]
  .sim.loadRT[];
  .sim.seedRandom `OMS_SEED;
  universe:.sim.universe[];
  .oms.symbols::first universe;
  seeds:last universe;
  opening:([]sym:.oms.symbols;bid:.sim.round2[seeds*0.9995f];ask:.sim.round2[seeds*1.0005f]);
  .oms.quotes::`sym xkey opening;

  .oms.table::`$.sim.env[`OMS_TABLE;"omstrade"];
  .oms.quoteTable::`$.sim.env[`OMS_REF_TABLE;"mdquote"];
  .oms.refSource::lower .sim.env[`OMS_REF_SOURCE;"dbservice"];
  .oms.gateway::.sim.env[`OMS_DBS_QIPC_ENDPOINT;"localhost:5040"];
  .oms.lookbackNs::"j"$1e9*.sim.envFloat[`OMS_REF_LOOKBACK_SECS;60f];
  .oms.refLimit::.sim.envLong[`OMS_REF_LIMIT;5000];
  .oms.refreshNs::"j"$1e9*.sim.envFloat[`OMS_REF_REFRESH_SECS;1f];
  .oms.exchange::`$.sim.env[`SIM_EXCH;"US"];
  .oms.currency::`$.sim.env[`SIM_CCY;"USD"];
  bookNames:trim each "," vs .sim.env[`OMS_BOOKS;"ALPHA,BETA,GAMMA,DELTA,MACRO"];
  bookNames:bookNames where 0<count each bookNames;
  .oms.books::$[count bookNames;`$bookNames;enlist `ALPHA];
  .oms.lots::100 200 300 400 500 800 1000 1500 2000 5000;
  .oms.slippageBps::.sim.envFloat[`OMS_SLIPPAGE_BPS;1f];
  .oms.logEvery::.sim.envLong[`OMS_LOG_EVERY;50];
  .oms.published::0j;
  .oms.live::0b;
  .oms.warned::0b;
  .oms.gatewayHandle::0i;
  .oms.lastError::"";
  .oms.lastRefresh::0Np;

  if[not "dbservice"~.oms.refSource;
    .sim.log["ref";"OMS_REF_SOURCE=",.oms.refSource,"; using static seed prices only"]];

  client:.sim.env[`RT_CLIENT_NAME;"dbs-q-omspub"];
  .oms.publisherParams::.sim.publisherParams client;
  .oms.publisher::.rt.pub .oms.publisherParams;
  .z.exit:{[code]
    .sim.stopPublisher[.oms.publisherParams;.oms.publisher;`.oms.publisher];
    .oms.closeGateway[];
    code
    };

  .oms.rate::.sim.envFloat[`OMS_TRADES_PER_SEC;3f];
  .sim.log["oms";"publishing to ",string[.oms.table],"; prices from DB Service table ",string .oms.quoteTable];
  .z.ts:.oms.tick;
  .oms.tick[];
  };

if[0=count getenv`SIM_TEST_MODE;.oms.start[]];
