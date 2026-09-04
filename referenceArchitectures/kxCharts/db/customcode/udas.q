log.info"Loading UDAs";

// Sample UDA Function
// .uda.da:{[table;startTS;endTS]
//     args:`table`startTS`endTS!(table;startTS;endTS);
//     res:.kxi.selectTable args;
//     .kxi.response.ok res
//     };

// Sample Aggregator UDA Function
// .uda.agg:{[tbls]
//     res: select O:first price, H:max price,  L:min price,  C:last price by id from raze tbls;
//     .kxi.response.ok res
//     };

// Sample Metadata
// metadata:.kxi.metaDescription["OHLC UDA"],
//     .kxi.metaParam[`name`type`isReq`description!(`table;-11h;1b;"Table to query")],
//    .kxi.metaReturn[`type`description!(98h;"OHLC")],
//     .kxi.metaMisc[enlist[`safe]!enlist 1b]

// Registering the UDA
// .kxi.registerUDA `name`query`aggregation`metadata!(`.uda.ohlc;`.uda.da;`.uda.agg;metadata);
