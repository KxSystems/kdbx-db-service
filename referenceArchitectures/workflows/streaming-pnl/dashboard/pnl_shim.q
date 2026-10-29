/ pnl_shim.q - makes the keyed pnlstate table queryable by KX Dashboards.
/ NOTE ON COMMENTS: never leave a line containing only "/" in this file. In q
/ that opens a BLOCK comment running until a line containing only "\", which
/ silently comments out the rest of the script - the process then starts with
/ no tables and no output at all.
/ WHY A SHIM IS NEEDED
/ A Dashboards connection is an ordinary kdb+ client: it opens a qIPC handle
/ and sends a q expression to evaluate. Two things rule out pointing it at the
/ DB Service directly, both established by testing:
/   1. the gateway rejects plain q. "select from pnlstate" makes it CLOSE the
/      handle; it requires its own (api;args;callback;opts) envelope.
/   2. Dashboards has no REST data source. dbtype is q | sql | influxdb |
/      presto, so /api/v0/query/simple is not reachable from a connection.
/ The Service Gateway client that ships with Dashboards (sgwClient.q_) was
/ also tried and does not apply: the gateway answers "Connection closed,
/ handshake was not terminated".
/ So this polls the gateway with the correct envelope and republishes the
/ result as plain in-memory tables that Dashboards can query with qSQL:
/   select from pnlstate
/   select from pnltotal
/ pnlstate is additionally KEYED, and keyed tables cannot be read through
/ .query.q at all, so .query.simple is the only route in either case.
/ Config (env vars, see the compose overlay):
/   PNLDASH_GW        gateway host:port for qIPC   default localhost:5040
/   PNLDASH_TABLE     source table                 default pnlstate
/   PNLDASH_INTERVAL  poll interval in ms          default 2000

gete:{[k;d] v:getenv k; $[0=count v; d; v]};
GW:      gete[`PNLDASH_GW;      "localhost:5040"];
SRC:     `$gete[`PNLDASH_TABLE; "pnlstate"];
INTERVAL:"J"$gete[`PNLDASH_INTERVAL; "2000"];

/ Empty tables up front, so a Dashboards grid gets a valid schema even before
/ the first poll lands instead of erroring on first render.
pnlstate:([] sym:`symbol$(); book:`symbol$(); position:`long$();
            avgcost:`float$(); markpx:`float$(); realized:`float$();
            unrealized:`float$(); totalpnl:`float$(); trades:`long$();
            lastupd:`timestamp$());

/ Portfolio roll-up, so the dashboard's total is a plain query rather than an
/ aggregation inside the component.
pnltotal:([] totalpnl:`float$(); realized:`float$(); unrealized:`float$();
            instruments:`long$(); books:`long$(); rows:`long$();
            lastpoll:`timestamp$());

gwh:0i; polls:0; failures:0;

connectGw:{
  if[gwh>0i; :gwh];
  h:@[hopen; `$":",GW; {[e] -1"  [shim] gateway connect failed: ",e; 0i}];
  if[h>0i; -1"  [shim] connected to gateway ",GW];
  gwh::h; h};

/ One .query.simple call in the 4-tuple form the gateway requires.
fetch:{[t]
  h:connectGw[];
  if[not h>0i; :(::)];
  r:@[h; (`.query.simple; (enlist`table)!enlist t; `; ()!());
        {[e] -1"  [shim] query failed: ",e; `err}];
  if[`err~r; @[hclose; gwh; {[x](::)}]; gwh::0i; :(::)];
  p:last r;                                 / response is (header; payload)
  $[98h=type p; p; (::)]};

.z.ts:{
  t:fetch SRC;
  if[(::)~t; failures+::1; :()];
  polls+::1;
  / Replace wholesale: ~75 rows, so a full re-read is simpler than deltas and
  / always internally consistent.
  pnlstate::t;
  pnltotal::enlist `totalpnl`realized`unrealized`instruments`books`rows`lastpoll!
    (sum t`totalpnl; sum t`realized; sum t`unrealized;
     count distinct t`sym; count distinct t`book; count t; .z.p);
  if[0=polls mod 30;
     -1"  [shim] poll ",(string polls)," ok, ",(string failures)," failed | ",
       (string count pnlstate)," rows, total P&L ",string first pnltotal`totalpnl];
  };

-1"[shim] starting";
-1"  source   : ",(string SRC)," via ",GW;
-1"  serving  : port ",string system"p";
-1"  interval : ",(string INTERVAL),"ms";
.z.ts[];
system "t ",string INTERVAL;
