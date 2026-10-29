/ q weighted-average-cost P&L real-time engine (RTE). It consumes mdquote and
/ omstrade from RT, maintains keyed state incrementally, and publishes pnlstate
/ snapshots back to RT for DB Service to serve through its gateway.

.simQDir:$[""~getenv`SIM_Q_DIR;"/app/q";getenv`SIM_Q_DIR];
system "l ",.simQDir,"/common.q";

.pnl.emptyState:{[]
  `sym`book xkey ([]
    sym:`symbol$();
    book:`symbol$();
    position:`long$();
    avgcost:`float$();
    markpx:`float$();
    realized:`float$();
    unrealized:`float$();
    totalpnl:`float$();
    trades:`long$();
    lastupd:`timestamp$();
    lastpub:`timestamp$())
  };

.pnl.output:{[rows]
  output:delete lastpub from 0!rows;
  update
    avgcost:.sim.roundTo[10000f;avgcost],
    markpx:.sim.roundTo[10000f;markpx],
    realized:.sim.round2 realized,
    unrealized:.sim.round2 unrealized,
    totalpnl:.sim.round2 totalpnl,
    lastupd:.z.p
    from output
  };

.pnl.lookup:{[s;b]
  select from 0!.pnl.state where sym=s,book=b
  };

.pnl.markFor:{[sym;fallback]
  $[sym in key .pnl.marks;.pnl.marks sym;fallback]
  };

.pnl.setLastPublished:{[sym;book;timestamp]
  rows:.pnl.lookup[sym;book];
  if[count rows;
    row:update lastpub:timestamp from rows;
    .pnl.state::.pnl.state upsert `sym`book xkey row];
  };

.pnl.publishKey:{[sym;book;force]
  if[not .pnl.live;:()];
  rows:.pnl.lookup[sym;book];
  if[0=count rows;:()];
  if[not force;
    if[0<.pnl.publishIntervalNs;
      lastPublished:first rows`lastpub;
      due:null[lastPublished]|(.pnl.publishIntervalNs<="j"$.z.p-lastPublished);
      if[not due;:()];
      .pnl.setLastPublished[sym;book;.z.p]]];
  .sim.publish[.pnl.publisher;.pnl.table;.pnl.output rows];
  .pnl.published+:1;
  if[(0<.pnl.logEvery)&(0=.pnl.published mod .pnl.logEvery);
    total:sum exec totalpnl from 0!.pnl.state;
    .sim.log["pnl";string[.pnl.published]," upserts; total P&L ",string total]];
  };

.pnl.applyTrade:{[trade]
  sym:trade`sym;
  book:$[.pnl.useBook;trade`book;`];
  side:trade`side;
  if[null sym;:()];
  if[not side in `buy`sell;:()];
  if[null trade`qty;:()];
  if[null trade`price;:()];
  qty:"j"$trade`qty;
  price:"f"$trade`price;
  signed:$[side=`buy;qty;neg qty];
  current:.pnl.lookup[sym;book];
  old:$[count current;
    first current;
    `position`avgcost`markpx`realized`trades`lastpub!(0j;0f;0f;0f;0j;0Np)];
  oldPosition:old`position;
  oldCost:old`avgcost;
  realized:old`realized;
  tradeCount:old`trades;
  lastPublished:old`lastpub;

  newPosition:oldPosition+signed;
  sameDirection:(0=oldPosition)|((0<oldPosition)=0<signed);
  reducing:(not sameDirection)&(abs[signed]<=abs oldPosition);

  result:$[sameDirection;
    (newPosition;$[
      0=(abs oldPosition)+abs signed;
      0f;
      (((abs oldPosition)*oldCost)+((abs signed)*price))%((abs oldPosition)+abs signed)];realized);
    reducing;
    (newPosition;$[0=newPosition;0f;oldCost];realized+($[0<oldPosition;1f;-1f]*(price-oldCost)*abs signed));
    (newPosition;price;realized+($[0<oldPosition;1f;-1f]*(price-oldCost)*abs oldPosition))];

  newCost:result 1;
  newRealized:result 2;
  fallbackMark:$[0f<>old`markpx;old`markpx;price];
  mark:.pnl.markFor[sym;fallbackMark];
  unrealized:(mark-newCost)*newPosition;
  row:([]
    sym:enlist sym;
    book:enlist book;
    position:enlist newPosition;
    avgcost:enlist newCost;
    markpx:enlist mark;
    realized:enlist newRealized;
    unrealized:enlist unrealized;
    totalpnl:enlist newRealized+unrealized;
    trades:enlist tradeCount+1;
    lastupd:enlist .z.p;
    lastpub:enlist lastPublished);
  .pnl.state::.pnl.state upsert `sym`book xkey row;
  .pnl.tradeCount+:1;
  .pnl.publishKey[sym;book;0b];
  };

.pnl.markPosition:{[s;b;mark]
  current:.pnl.lookup[s;b];
  if[0=count current;:()];
  old:first current;
  unrealized:(mark-old`avgcost)*old`position;
  row:([]
    sym:enlist s;
    book:enlist b;
    position:enlist old`position;
    avgcost:enlist old`avgcost;
    markpx:enlist mark;
    realized:enlist old`realized;
    unrealized:enlist unrealized;
    totalpnl:enlist (old`realized)+unrealized;
    trades:enlist old`trades;
    lastupd:enlist .z.p;
    lastpub:enlist old`lastpub);
  .pnl.state::.pnl.state upsert `sym`book xkey row;
  .pnl.publishKey[s;b;0b];
  };

.pnl.applyQuote:{[quote]
  s:quote`sym;
  if[null s;:()];
  bid:"f"$quote`bid;
  ask:"f"$quote`ask;
  lastpx:"f"$quote`lastpx;
  mark:$[
    (.pnl.markMode=`bid)&not null bid;bid;
    (.pnl.markMode=`ask)&not null ask;ask;
    (.pnl.markMode=`lastpx)&not null lastpx;lastpx;
    (not null bid)&not null ask;0.5f*(bid+ask);
    0n];
  if[null mark;:()];
  .pnl.marks[s]:mark;
  holders:select sym,book from 0!.pnl.state where sym=s;
  {[m;x].pnl.markPosition[x`sym;x`book;m]}[mark;] each holders;
  .pnl.quoteCount+:1;
  };

.pnl.goLive:{[]
  .pnl.live::1b;
  stateKeys:select sym,book from 0!.pnl.state;
  {[row].pnl.publishKey[row`sym;row`book;1b]} each stateKeys;
  .sim.log["pnl";"replay complete after ",string[.pnl.replayed]," messages; snapshot published; now live"];
  };

.pnl.checkReplay:{[rows]
  if[.pnl.live;:()];
  if[not `ts in cols rows;.pnl.goLive[];:()];
  lagNs:"j"$.z.p-max rows`ts;
  if[lagNs<=.pnl.warmupNs;
    .pnl.goLive[];
    .pnl.replayed+:1];
  };

.pnl.onMessage:{[message;position]
  isQuote:.sim.isInsert[message;.pnl.quoteTable];
  isTrade:.sim.isInsert[message;.pnl.tradeTable];
  if[isQuote|isTrade;
    rows:message 2;
    .pnl.checkReplay rows;
    $[isQuote;.pnl.applyQuote each rows;.pnl.applyTrade each rows]];
  .pnl.position::position;
  };

.pnl.start:{[]
  .sim.loadRT[];
  .pnl.table::`$.sim.env[`PNL_TABLE;"pnlstate"];
  .pnl.quoteTable::`$.sim.env[`PNL_QUOTE_TABLE;"mdquote"];
  .pnl.tradeTable::`$.sim.env[`PNL_TRADE_TABLE;"omstrade"];
  .pnl.markMode::`$lower .sim.env[`PNL_MARK_PRICE;"mid"];
  .pnl.useBook::not "sym"~lower .sim.env[`PNL_KEY;"sym+book"];
  .pnl.publishIntervalNs::"j"$1e9*.sim.envFloat[`PNL_MIN_PUBLISH_INTERVAL;5f];
  .pnl.warmupNs::"j"$1e9*.sim.envFloat[`PNL_WARMUP_LAG;5f];
  .pnl.logEvery::.sim.envLong[`PNL_LOG_EVERY;100];
  .pnl.state::.pnl.emptyState[];
  .pnl.marks::(`symbol$())!`float$();
  .pnl.live::0b;
  .pnl.replayed::0j;
  .pnl.published::0j;
  .pnl.quoteCount::0j;
  .pnl.tradeCount::0j;
  .pnl.position::0j;

  client:.sim.env[`PNL_PUB_CLIENT_NAME;"dbs-q-pnlpub"];
  .pnl.publisherParams::.sim.publisherParams client;
  .pnl.publisher::.rt.pub .pnl.publisherParams;

  / RT topic filtering follows the KX RTE guidance to consume only the input
  / tables needed by this calculation. Keep the returned handle alive for the
  / lifetime of the process; replacing it with () unsubscribes cleanly.
  .pnl.subscription::.sim.subscribe[`.pnl.onMessage;(.pnl.quoteTable;.pnl.tradeTable)];
  .z.exit:{[code]
    .pnl.subscription::();
    .sim.stopPublisher[.pnl.publisherParams;.pnl.publisher;`.pnl.publisher];
    code
    };
  .sim.log["pnl";"P&L RTE subscribed to ",string[.pnl.quoteTable]," and ",string[.pnl.tradeTable],"; publishing ",string .pnl.table];
  };

if[0=count getenv`SIM_TEST_MODE;.pnl.start[]];
