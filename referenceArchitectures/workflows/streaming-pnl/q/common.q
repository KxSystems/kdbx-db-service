/ Shared configuration and RT helpers for the q streaming P&L workloads.

.sim.env:{[name;fallback]
  v:trim getenv name;
  $[0=count v;fallback;v]
  };

.sim.envLong:{[name;fallback]
  raw:.sim.env[name;""];
  if[0=count raw;:fallback];
  parsed:@[{[v]"F"$v};raw;{[error]0n}];
  if[null parsed;
    .sim.log["warn";(string name)," is not an integer; using ",string fallback];
    :fallback];
  "j"$$[parsed<0f;ceiling parsed;floor parsed]
  };

.sim.envFloat:{[name;fallback]
  raw:.sim.env[name;""];
  if[0=count raw;:fallback];
  parsed:@[{[v]"F"$v};raw;{[error]0n}];
  if[null parsed;
    .sim.log["warn";(string name)," is not a number; using ",string fallback];
    :fallback];
  parsed
  };

.sim.seedRandom:{[name]
  raw:.sim.env[name;""];
  if[0=count raw;:()];
  codes:"j"$raw;
  seed:abs sum (1+til count codes)*codes;
  system "S ",string seed;
  .sim.log["random";(string name)," set; generated values are repeatable"];
  };

.sim.normal:{[n]
  u1:1e-12|n?1f;
  u2:n?1f;
  sqrt[-2f*log u1]*cos[(2f*acos -1f)*u2]
  };

.sim.roundTo:{[s;v]
  (1f%s)*"j"$s*v
  };

.sim.round2:{.sim.roundTo[100f;x]};

.sim.log:{[component;message]
  -1 "[",component,"] ",message;
  };

.sim.defaultSymbols:`AAPL`MSFT`NVDA`AMZN`GOOGL`META`TSLA`JPM`V`XOM`UNH`JNJ`WMT`PG`MA;
.sim.defaultPrices:232.50 415.20 128.40 186.30 168.90 512.60 248.70 214.30 282.40 118.60 528.10 158.20 79.40 168.50 476.80;

.sim.seedFor:{[sym]
  index:.sim.defaultSymbols?sym;
  $[index<count .sim.defaultSymbols;.sim.defaultPrices index;.sim.round2 20f+480f*first 1?1f]
  };

.sim.parseInstrument:{[entry]
  entry:trim entry;
  parts:trim each ":" vs entry;
  price:$[1<count parts;@[{[v]"F"$v};last parts;{[error]0n}];0n];
  if[not null price;:(`$":" sv -1 _ parts;price)];
  sym:`$entry;
  (sym;.sim.seedFor sym)
  };

.sim.universe:{[]
  raw:.sim.env[`SIM_SYMBOLS;""];
  if[0=count raw;raw:.sim.env[`MD_SYMBOLS;""]];
  if[0=count raw;:(.sim.defaultSymbols;.sim.defaultPrices)];
  entries:trim each "," vs raw;
  entries:entries where 0<count each entries;
  if[0=count entries;:(.sim.defaultSymbols;.sim.defaultPrices)];
  parsed:.sim.parseInstrument each entries;
  (first each parsed;last each parsed)
  };

.sim.ticker:{[sym;suffix]
  `$((string sym),$[0=count suffix;"";".",suffix])
  };

.sim.loadRT:{[]
  rtPath:.sim.env[`RT_QPK_DIR;"/opt/kx/app/rt"];
  previous:system "cd";
  system "cd ",rtPath;
  system "l startq.q";
  system "cd ",previous;
  };

.sim.publisherParams:{[client]
  endpoints:trim each "," vs .sim.env[`RT_PUBLISH_ENDPOINTS;"localhost:5002"];
  endpoints:endpoints where 0<count each endpoints;
  if[0=count endpoints;endpoints:enlist "localhost:5002"];
  endpoints:{":",x} each endpoints;
  `path`stream`publisher_id`cluster!(
    .sim.env[`RT_DIR;"/tmp/rt"];
    .sim.env[`RT_TOPIC;"data"];
    client;
    endpoints)
  };

.sim.publisher:{[client]
  .rt.pub .sim.publisherParams client
  };

.sim.subscribe:{[callback;topics]
  config:`stream`position`callback`filter!(
    .sim.env[`RT_STREAM_ID;.sim.env[`RT_TOPIC;"data"]];
    .sim.envLong[`RT_SUB_POSITION;0];
    callback;
    "," sv string each topics);
  .rt.sub config
  };

.sim.stopPublisher:{[params;publisher;publisherName]
  .[.rt.pub_flush;(params;publisher);{[error]
    .sim.log["warn";"RT publisher flush failed: ",string error]}];
  .[.rt.pub_stop;(params;publisherName);{[error]
    .sim.log["warn";"RT publisher shutdown failed: ",string error]}];
  };

.sim.isInsert:{[message;table]
  if[0h<>type message;:0b];
  if[3>count message;:0b];
  if[not (first message) in `.b`upd;:0b];
  if[not table~message 1;:0b];
  98h=type message 2
  };

.sim.publish:{[publisher;table;rows]
  publisher (`upd;table;rows);
  };
