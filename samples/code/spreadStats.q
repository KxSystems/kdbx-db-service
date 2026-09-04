// spreadStats.q - .fx.spreadStats UDA: average bid/ask/spread and quote count
// by sym over a time range. Runs on the sample fxquote table.
//
// A UDA is two functions registered with the Service Gateway: a query function
// that runs on each Data Access process (once per storage tier), and an
// aggregation function that combines those partial results into the final one.
// Guide: https://code.kx.com/insights/api/database/uda/uda-creating.html

\d .fx

// Query - runs per DAP tier. Returns partial count + bid/ask SUMS grouped by
// sym (sums, not averages, so the aggregation can combine tiers correctly).
spreadStatsQuery:{[table;startTS;endTS]

  // .kxi.selectTable - read a table over a time range across the process's
  //   storage tiers, with optional server-side filter / group / aggregation.
  //   Doc: https://code.kx.com/insights/latest/api/database/uda/helper-functions.html
  //   Signature: .kxi.selectTable[args]  - args is a dictionary of:
  //     table    {symbol}        (required) table to read
  //     startTS  {timestamp}     inclusive start of the time range
  //     endTS    {timestamp}     exclusive end of the time range
  //     filter   {list}          functional where-clauses, e.g. enlist(within;`ts;(startTS;endTS))
  //     groupBy  {symbol|sym[]}  column(s) to group by
  //     agg      {dict}          derived / aggregated output columns
  //     (also accepts: sortCols, limit, fill, temporality, mmap)
  //   Returns: the selected table.
  t:.kxi.selectTable`table`startTS`endTS`filter!
      (table;startTS;endTS;enlist(within;`ts;(startTS;endTS)));

  // Functional select: count rows + sum bid/ask grouped by sym. 0! unkeys the
  // result so the per-tier partials concatenate cleanly in the aggregation.
  0!?[t;();([sym:`sym]);(n:(count;`i);bidSum:(sum;`bid);askSum:(sum;`ask))]
  }

// Aggregation - runs on the Aggregator. Receives the per-DAP partials as a LIST
// of tables (one per tier); raze + regroup by sym, then finalise the averages.
spreadStatsAgg:{[tbls]
  s:0!select n:sum n, bidSum:sum bidSum, askSum:sum askSum by sym from raze tbls;

  // .kxi.response.ok - wrap a successful payload in the response envelope the
  //   gateway expects (attaches success codes). Every UDA function must return
  //   through .kxi.response.ok (or .kxi.response.error[ac;ai;result] on failure).
  //   Doc: https://code.kx.com/insights/latest/api/database/uda/helper-functions.html
  //   Signature: .kxi.response.ok[result]  ->  (header;result)
  .kxi.response.ok select sym, avgBid:bidSum%n, avgAsk:askSum%n, avgSpread:(askSum-bidSum)%n, n from s
  }

\d .

// Metadata describes the UDA to the gateway (description, parameters, return,
// flags). Each .kxi.meta* helper returns one entry; they are joined with `,`
// into a list and handed to .kxi.registerUDA. `type` fields are q type numbers:
// negative = atom, positive = vector - e.g. -11h symbol, -12h timestamp, 98h table.
// Doc: https://code.kx.com/insights/latest/api/database/uda/uda-creating.html
metadata:

  // .kxi.metaDescription - human-readable description of the UDA.
  //   Signature: .kxi.metaDescription[d]   d {string}
  .kxi.metaDescription["Average bid/ask/spread and quote count by sym over a time range."],

  // .kxi.metaMisc - miscellaneous flags. safe=1b marks the API safe to retry
  //   after a failure.
  //   Signature: .kxi.metaMisc[m]   m {dict} - keys: safe {boolean}
  .kxi.metaMisc[([safe:1b])],

  // .kxi.metaParam - declare one UDA parameter; repeat once per parameter. The
  //   names must match the query function's arguments.
  //   Signature: .kxi.metaParam[p]   p {dict} - keys:
  //     name {symbol}  type {short|short[]}  isReq {boolean}
  //     default {any} (ignored when isReq)   description {string}
  .kxi.metaParam[`name`type`isReq`description!(`table;-11h;1b;"Table name.")],
  .kxi.metaParam[`name`type`isReq`description!(`startTS;-12h;1b;"Start time (inclusive).")],
  .kxi.metaParam[`name`type`isReq`description!(`endTS;-12h;1b;"End time (exclusive).")],

  // .kxi.metaReturn - declare the UDA's return type / description.
  //   Signature: .kxi.metaReturn[r]   r {dict} - keys: type {short|short[]}  description {string}
  .kxi.metaReturn`type`description!(98h;"Spread stats by sym.");

// .kxi.registerUDA - register the UDA with the Service Gateway, binding the API
//   name to its query + aggregation functions and metadata. Once registered it
//   is callable over REST at /api/v0/fx/spreadStats.
//   Doc: https://code.kx.com/insights/latest/api/database/uda/uda-creating.html
//   Signature: .kxi.registerUDA[det]   det {dict} - keys:
//     name {symbol}         API name, e.g. `.fx.spreadStats
//     query {symbol}        DAP-side query function name
//     aggregation {symbol}  Aggregator-side function name (optional)
//     metadata {list}       output of the .kxi.meta* helpers (optional)
.kxi.registerUDA `name`query`aggregation`metadata!(
  `.fx.spreadStats;`.fx.spreadStatsQuery;`.fx.spreadStatsAgg;metadata);
