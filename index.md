# River: A Fast, Scalable, Evergreen SQL and TAP Database of Rubin Prompt, Data Release, Nightly Validation and Solar System Catalogs

```{abstract}
River is a SQL analytics database for Rubin catalog data, with a
[TAP 1.1](https://www.ivoa.net/documents/TAP/) service providing an
[ADQL](https://www.ivoa.net/documents/ADQL/)/TAP API and a web UI over it. It
holds 185 billion rows across three databases (updated nightly with new data
from AP and NV pipelines) on one
[ClickHouse](https://clickhouse.com/) server at the USDF — the DP2 data release,
the Solar System Processing working set, and a Prompt Products snapshot — and you
query it from [TOPCAT](https://www.star.bris.ac.uk/~mbt/topcat/),
[pyvo](https://pyvo.readthedocs.io/en/latest/), or its own web UI.

It is generally fast enough to enable exploratory data analysis at scale.
Indexed lookups and sky-position cone searches return in under a second,
aggregates over a billion rows in a few seconds, and the slowest case
measured — an *unindexed* cone search across 18 billion rows, where the whole
table has to be read — in three to five minutes. Ingest is on the same footing:
a release-scale dataset goes from a Butler repository to a queryable table in
hours, at about
200 million rows per minute into ClickHouse, and nightly appends extend it
incrementally in minutes — nightly appends read freshly-written, cache-resident
data, which is what makes them cheap. River can be thought of as APDB, PPDB and
[Qserv](https://qserv.lsst.io/) rolled into one, with update capability.

It exists to give Solar System Processing — and Rubin catalog QA generally —
somewhere to run SQL across a whole dataset before it reaches an official
database, and somewhere to query the ones that never will. Two of these
databases back production work: `ssp` is what the solar-system group's MPC
submissions are drawn from, and `ppdb` is where the solar-system tables
delivered to Rubin's official Prompt Products Database will be built. The intent
is therefore to keep the contents current — refreshed at least daily, and in
near-real time as each night's data arrives. The first half of this note is for
people using the service; the second half is for people running it.
```

## Scope and status

The service runs at <https://usdf-rsp-dev.slac.stanford.edu/river>. Anyone who can
log in to `usdf-rsp-dev` can query it.

It is a pilot. Its database is one ClickHouse server with no replication and no
backups, and in its current placement it does not come back on its own after a
host reboot. It is in real use anyway. This note
describes what is deployed, not what is planned.

**Part I — Using the service** covers what is in the database and how to query
it. **Part II — Operations and internals**, which is a draft for the service
operator only, covers how it is built, how to run it, and what would have to
change for production.

## Part I — Using the service

### Getting started

Open <https://usdf-rsp-dev.slac.stanford.edu/river/ui/>. Rubin SSO logs you in and
your account is created on first visit. Anyone who can log in to `usdf-rsp-dev`
can query.

Then use the web UI, which is built to be explored rather than documented:

- The **demo notebook** in your account, *Demo: Sky/Visit/Light Curve*, is the
  intended way in. Sixteen cells go from the whole sky down to one object's light
  curve. These are **ADQL notebooks**, not Jupyter: a cell holds a query, not
  Python. Cells run in order, can be named, and later cells can reference an
  earlier cell's result with `{{ }}`.
- The **schema browser** in the sidebar lists every database, table and column
  with units and descriptions. That is the reference for what a column means, so
  this note does not restate it.
- **Worked example queries ship with it.** Start from those rather than from
  anything written here.

:::{figure} _static/river-ui.png
:alt: The River web UI: a schema browser listing 27 tables on the left, an ADQL notebook in the centre with query cells and results inline, and an all-sky density plot rendered from 232 million rows.
:width: 100%
:name: fig-river-ui

The River web UI. Left: the schema browser, listing all 27 tables across the
three databases with row counts. Centre: an ADQL notebook — markdown and query
cells with results inline. The plot cell shown renders an all-sky HEALPix density
map of all 232,004,216 `dp2.DiaObject` rows server-side, in 107 s; plot cells run
over every row rather than a sample. Top left: the Python and TOPCAT access
panels.
:::

One thing about the ADQL notebooks that is not visible from them: they run
**every query asynchronously**, so a notebook query gets the 3600 s limit rather
than the 60 s in force for the synchronous TAP API (see the Limits section
below).

### What is in it

| database | what it holds | rows |
|---|---|---|
| `dp2` | the DP2 data release: thirteen tables, among them `Object`, `Source`, `DiaSource`, `ForcedSource` | 91.44 B |
| `ppdb` | a static snapshot of the Prompt Products Database | 48.70 M |
| `ssp` | per-visit source catalogs for Solar System Processing — eleven tables, one per processing run | 93.77 B |

`dp2` and `ppdb` are static. Three of the `ssp` tables — `source_daytime`,
`source_nv` and `dia_source_prompt` — are **updated daily**, so a query against
them today can return rows that were not there yesterday. The other eight `ssp`
tables are frozen.

**Every table reference must be qualified in ADQL queries you write** —
`dp2.DiaSource`, `ssp.source_nv`.
There is no default database, so a bare `FROM DiaSource` is rejected with
`table 'DiaSource' must be schema-qualified`. That is deliberate: no one of these
is the natural home for an unqualified name.

`dp2` and `ssp` are organised differently, and that decides which of your queries
are fast. `dp2` is **sorted by sky position**, so spatial searches are fast.
`ssp` is **sorted by id**, so lookups and ranges on the id are fast, but a
spatial search has to read the whole table — correct, and minutes rather than
milliseconds.

Two things in `dp2` will surprise you if you `SELECT *`: `Object` has **1,225
columns**, and `mpc_orbits` carries a JSON blob of roughly **5.7 KB per row**.
Name the columns you want.

### Limits

Sync and async are governed by different limits, and the gap is four orders of
magnitude — so which one you are using matters more than any individual number.
**ADQL notebooks always run async.**

| limit | `/sync` (and pyvo `search()`) | async (ADQL notebooks, pyvo `run_async()`) |
|---|---|---|
| rows returned, default | **50,000** | **1,000,000,000** |
| rows returned, maximum | 2,000,000 | 1,000,000,000 |
| query timeout | **60 s** | 3600 s |
| results kept | — | 7 days |

:::{warning}
**On `/sync`, truncation at 50,000 rows is reported as success**, not as an
error — the response carries an `OVERFLOW` status. If a sync result is exactly
50,000 rows, check for it before believing that is the whole answer. Note that
`/capabilities` advertises only these sync figures, so a VO client may show you
50,000 / 2,000,000 and give no hint that async is a billion.
:::

Supported: joins, **including across databases**; `GROUP BY` and `HAVING`;
`POINT`, `CIRCLE`, `POLYGON`, `BOX`, `CONTAINS`, `INTERSECTS`, `DISTANCE`; and the
usual numeric functions. Fluxes are nJy, so magnitudes are
`-2.5 * LOG10(psfFlux) + 31.4`.

Not currently supported: `TAP_UPLOAD`; `WITH`/CTEs; correlated subqueries;
`CASE`; `COUNTIF`; string functions including `SUBSTRING`; `ORDER BY` on a select-list alias (write
`ORDER BY COUNT(*) DESC`, not `ORDER BY n_dia DESC`); and
`REGION`/`AREA`/`CENTROID`/`COORD1`/`COORD2`/`COORDSYS`.


### Python and API access

The TAP endpoint is `https://usdf-rsp-dev.slac.stanford.edu/river`. Mint a token
at <https://usdf-rsp-dev.slac.stanford.edu/settings/tokens/new> with scope
`read:tap`. It is an RSP token; the service does not issue its own.

In **pyvo**, use `run_async` rather than `search` for anything that might take
more than a minute:

```python
import pyvo, requests
from pathlib import Path

tok = Path.home() / ".river.token"      # chmod 600
session = requests.Session()
session.headers["Authorization"] = "Bearer " + tok.read_text().strip()
service = pyvo.dal.TAPService(
    "https://usdf-rsp-dev.slac.stanford.edu/river", session=session)

job = service.run_async("SELECT TOP 10 * FROM ssp.dia_source_dp1")
print(job.to_table())
```

In **TOPCAT**, enter the endpoint as the TAP URL. TOPCAT will not prompt for
credentials until the service returns a 401, so the prompt appears after your
first action rather than up front. Give the literal `x-oauth-basic` as the HTTP
Basic *username* and your token as the *password*.

Results come back as **VOTable, CSV or Parquet**; `fits`, `tsv` and `json` are
rejected, and `/capabilities` advertises only the VOTable serialisations, so a
strict VO client may not offer you the other two.

There is also a **Simple Cone Search** endpoint at `/scs?RA=&DEC=&SR=`, which
resolves against `dp2` and defaults to `dp2.DiaObject`. Its table name is given
**unqualified** — `/scs/DiaObject`, not `/scs/dp2.DiaObject`, which fails with
`unknown SCS table`. `SR` is capped at **5°**, and cones on `DiaSource` are
disabled because the table is too large to cone through. For anything SCS will
not do, use ADQL with `CONTAINS`.

### Things to know

**This is a pilot on one database server.** If the host it runs on reboots,
ClickHouse does not come back on its own and every query fails until someone
restarts it by hand. There is no replication and no backup. The server can be
moved between two hosts, and each move is an outage of a few minutes during
which queries fail.

**Who to ask.** The service is run by the Solar System Pipelines group; mjuric
owns it today. Report a wrong column, a missing table or an outage there, or file
an issue against the mppdb repository.

`ssp.SubmittableSources` is a view over the `ssp` tables, and is served over TAP
like any other table. Its spatial pruning is weak, so a small cone on it takes
seconds rather than the 0.1 s of the base tables.

## Part II — Operations and internals (DRAFT)

:::{danger}
**Do not run any command in Part II unless you are the service operator.** That
is currently **mjuric**, and only one person can hold it: the service is a single
replica with a single-writer state database, and the backend accepts a publish
from one node only.

The commands here are not read-only diagnostics. They restart the service, swap
what every user's queries resolve against, and in some cases delete data that has
no backup. Several are irreversible, and one of them — deleting a namespace —
destroys a volume whose reclaim policy discards it. Running them concurrently
with the operator is how you corrupt state rather than merely duplicate work.

This part is also **draft**: it describes a pilot that is still changing, and
commands may lag the deployment. If you need something done here, ask the
operator rather than reproducing it.
:::

### Architecture

River is one [Phalanx](https://phalanx.lsst.io/) application, `river-next`, on
`usdf-rsp-dev`, plus two SDF hosts outside the cluster:

- the **front-end**: the TAP API and the web UI, served at `/river`. It reads
  ClickHouse and owns no catalog data.
- the **ClickHouse server**: version 26.8 LTS, one server, with its data
  directory on SDF's sdfdata Weka filesystem. The chart defines it as a
  single-replica StatefulSet. It currently runs instead on **`sdfiana032`**, the
  same image, configuration and data directory, reached through the native Weka
  client. The pod is the fallback. A chart value, `clickhouse.backend: pod |
  external`, says which side the front-end uses.
- the **ingest host**, **`sdfiana031`**: every ingest and catalog publish runs
  there, from SDF, never in the cluster.

```{mermaid}
flowchart TB
  U["users: web UI · TOPCAT · pyvo"]
  subgraph RSP["usdf-rsp-dev · Phalanx app river-next"]
    FE["River front-end<br/>/river"]
    POD[("ClickHouse 26.8 pod<br/>fallback, 0 replicas")]
  end
  N032[("ClickHouse 26.8 · sdfiana032<br/>serving")]
  subgraph WEKA["sdfdata Weka"]
    DATA[("data directory<br/>.owner · heartbeats")]
    UF["user-files staging"]
  end
  ING["ingest + catalog publish<br/>sdfiana031"]
  U --> FE
  FE -- "read-only, mppdb_ro" --> N032
  FE -. "backend: pod" .-> POD
  N032 -- "native Weka client" --> DATA
  POD -. "NFS 4.1 via Weka gateway" .-> DATA
  ING -- "admin, HTTP 8123" --> N032
  ING -- "Parquet shards" --> UF
```

**Why the server sits outside its own pod.** Phalanx supplies what a
user-facing service needs: [Gafaelfawr](https://gafaelfawr.lsst.io/)
authentication, container builds, ingress and TLS, secret management, and a
reviewable deploy path. ClickHouse was moved into the same application so that
it, too, is declared in the chart and restarts itself. But the cluster's nodes
reach sdfdata over NFS 4.1 through the Weka gateway, not through the native Weka
client. Over NFS, steady-state reads are as fast as the previous server's on its
native mount, or faster, while the first access to each file is slow, an occasional `open()` hangs
for minutes, and a clean shutdown takes about ten minutes (see *Storage path: NFS
against native Weka*). So the same server runs on an SDF host with the native
client, and the pod stays ready to take over. Both use one data directory, and
the fencing described in *Failover and fencing* makes sure only one of them
runs.

:::{important}
The ideal deployment is the one the chart already describes: everything under
[Argo CD](https://argo-cd.readthedocs.io/en/stable/), with no host to maintain
by hand. What blocks it is the storage path, not anything about mppdb: the pod
needs native Weka access to sdfdata, through a CSI storage class or a native
client on the cluster's nodes. Until then the failover host is the better place
for the server. See *What production would require*.
:::

The unit of operation between ingest and the front-end is the catalog: a
publish on the ingest host changes what the service should serve, and the
front-end picks it up on its next poll of the store, within about a minute —
see *Operating the service*.

### The data: provenance and lifecycle

**`dp2`** is the DP2 data release: thirteen tables, **91.44 B rows** — `Object`,
`Source`, `DiaSource`, `DiaObject`, `ForcedSource`, `ForcedSourceOnDiaObject`,
`Visit`, `CcdVisit`, `SSObject`, `SSSource`, `mpc_orbits`, `object_shear_all`,
`isolated_star_stellar_motions`. [Felis](https://felis.lsst.io/)-named and
`hpix29`-ordered, so spatial
searches are fast. It is loaded from flat HATS exports with
`mppdb ingest --from-hats`, which maps columns onto a curated registry rather
than generating a schema: datatypes, units, UCDs and descriptions come from
Felis, while the `hpix29`/`cx`/`cy`/`cz` spatial columns, the principal flags and
the foreign keys are added by curation. Static.

**`ppdb`** is a snapshot of the Prompt Products Database — `DiaObject`,
`DiaSource`, `DiaForcedSource`, 48.70 M rows — taken 2026-07-25 from a TAP dump
of `data-int.lsst.cloud/api/ppdbtap` and loaded with `mppdb ingest-tapdump`.
ClickHouse-only: no Parquet lake, no manifests, no GC. Static.

**`ssp`** is the Solar System Processing working set: eleven tables, **93.77 B
rows**, one per export directory, each an `acid import butler --split-by visit`
export of per-visit Parquet parts with per-part manifests. It is loaded with
`mppdb ingest-parquet`, which generates the schema from the Parquet, computes the
spatial columns from `ra`/`dec`, converts NaN to NULL, and safe-casts at staging
so an `int64`→`int32` overflow fails loudly rather than wrapping. Sorted by id,
with no spatial projection — a deliberate choice, since this working set is
queried by identifier and by visit rather than by position.

Three `ssp` products grow: `source_daytime`, `source_nv` and
`dia_source_prompt`. An append on the export side is followed by
`ingest-parquet run --append`, which reads the immutable per-part manifests and
loads only what is new. A provenance ledger in the served database,
`_ingest_parts` and `_ingest_refs`, maps (visit, detector) to part and holds the
invariant `count(live) == sum(_ingest_parts.rows)` per table. The other eight
products are frozen. `ssp`'s ingest configs, loads and catalog content belong to
the ssp-submit project, not to this service.

**Publishing, for every ingest path:** load into a staging database, verify
counts, publish with an atomic multi-pair `RENAME TABLE`, then record provenance.
Readers never see a missing or half-loaded table. (`EXCHANGE TABLES` and
`CREATE OR REPLACE` are unavailable: `renameat2()` is unsupported on the WekaFS
data path.)

**Cadence.** `dp2` and `ppdb` are static. `ssp` arrives in bursts, whenever a
nightly append actually appends, plus occasional metadata-only catalog publishes.
Every load runs on the designated ingest host, `sdfiana031`. Ingest writes
Parquet shards into a staging directory on sdfdata, beside the data directory,
and ClickHouse reads them with `file()`: the same directory is the server's
`user_files_path` on either side, and the loader names it by its host path with
`--user-files-root`.

**Adding a database to this service** is two chart changes: a `SELECT` grant
for `mppdb_ro` in the chart's ClickHouse user definitions, and a
`databases.<db>` entry for the front-end. The users and their grants are
declared in the chart, not issued as SQL on the server. The `[databases]` block
is the authorization boundary — a database absent from it is not served,
whatever the store holds — and doing the front-end half without the grant leaves
the service configured for a database it cannot read. While the server runs on
the failover host, its configuration is a snapshot exported from the chart, so a
grant change also needs a fresh export, which in turn needs the server stopped
(see *Maintaining and upgrading*).

### Ingest and query performance

Two things had to be true: datasets of this size must load in hours and be
extendable incrementally, and queries must return quickly. Both hold.

#### Ingest performance

A dataset moves in two stages, Butler to export and export to ClickHouse. Both
were measured on this hardware:

| stage | measured |
|---|---|
| Butler → HATS export (`acid import butler`, DP2, 2026-08-23) | 91.44 G rows / 13.78 TB in **7.0 h**, six 128-core hosts, one dataset each |
| export → ClickHouse (`ingest-parquet`, 64 workers, one host) | **~200 M rows/min**, limited by WekaFS read bandwidth, not by ClickHouse |
| full `ssp` rebuild (93.76 B rows, 11.05 TiB) | about **8 h** of load time |

So a release-scale dataset goes from Butler to a queryable table in hours at each
stage. Do not turn that into a rate comparison between the stages:

- **Per host the rates differ about sixfold.** The export's 217 M rows/min is six
  hosts together; per host it is ~36 M rows/min against the load step's ~200 M on
  one. The export also does more work — read, shard, spill, re-read, spatially
  sort, write, build a margin cache. One product pushed 8.8 TB through the shuffle
  before writing 9.76 TB.
- **Rows per minute is the wrong unit upstream.** It varies 60x across the DP2
  products (5.8–352 M rows/min), almost entirely with row width: one table is 1,225
  columns, another 29. Byte rate varies only about 4x (5.8–23.3 GB/min), so GB/min
  is the honest upstream unit. The two fastest illustrate the difference:
  `object_forced_source` at 44.7 G rows / 912 GB in 127 min is the fastest by rows
  (352 M rows/min, 7.2 GB/min), while `source` at 17.6 G rows / 9.76 TB in 419 min
  is the fastest by bytes (42 M rows/min, 23.3 GB/min). The low ends of both
  ranges belong to the narrow, small products.

**Incremental extension works at both stages**, which is what makes this
sustainable. Downstream, an append loads only parts absent from the ledger and
fails rather than guessing if the ledger cannot account for the table. Upstream,
the flat per-visit exports do the same: a no-op nightly run over a 70,946-group
product takes **24 s**, and a real append (940,680 refs / 118.5 M rows) costs
**minutes when the input artifacts are resident in the storage cache, and
roughly 45x more when they are not** (measured ~3,900 vs ~90 artifacts/s on the
same product and collections; the same one-era rebuild took 7,848 s cold and
171 s warm). A nightly append over recently-written data is the warm case by
construction — that is why nightly extension is cheap — but the flat rate does
not transfer to re-reading old eras. For the many-small-file prompt products
the conserved quantity is **artifacts per second**, not rows: two eras
differing 31x in rows per artifact ran at nearly identical artifact rates, so
per-file latency, not bandwidth, sets the pace there — the opposite regime
from the bulk exports above.

:::{important}
HATS exports are one-off per release. There is no append mode for a HATS
collection, so re-importing means a full rebuild, and the 7 hours above is the cost
of a release rather than of a night. Only the flat per-visit exports are
incremental.
:::

**Where the time goes** — inference, not a profile; the export was not
instrumented. The bulk case is WekaFS-bandwidth-bound, like the load step. The
many-small-artifacts case is registry-latency-bound instead: one product took 92
minutes for a fifth of another's bytes because it was 183,169 datastore artifacts,
one registry query each. That effect is upstream-only. Very wide tables are CPU-
and writer-memory-bound.

These figures come from one day, with a warm-ish filesystem, six concurrent
imports sharing one WekaFS namespace, and inherited rather than tuned partitioning.
A dedicated run on a quiet filesystem would be faster by an unmeasured margin.
Every row count matched an independent earlier import of the same collection, and
all thirteen products succeeded first time. Per-catalog `acid-import-log.yaml`
files under the DP2 export directory record each run's arguments, worker counts,
totals and elapsed time.

#### Query performance

The other half of the requirement: queries must return quickly at these row
counts. What makes that possible is that each table is physically sorted on the
key its queries filter by, so the engine reads a slice rather than scanning.

- `dp2.*` and `ppdb.*` are sorted on **`hpix29`**, a [HEALPix](https://healpix.sourceforge.io/) index, with
  `cx`/`cy`/`cz` alongside. ADQL `CONTAINS(POINT(...), CIRCLE(...))` is rewritten
  onto that index, so a cone reads only the relevant ranges. `hpix29` is hidden
  from the schema browser deliberately — users never write it.
- The `ssp` tables are sorted on their id column (`id`, `sourceId` or
  `diaSourceId`, depending on the table) and carry **no spatial projection**.
  This is a deliberate choice: the SSP working set is queried by identifier and by
  visit, not by position, and a spatial projection over 94 B rows would cost
  storage and load time for queries nobody makes. The consequence is that a cone
  search on `ssp` is a full scan, which is correct but slow.

Measured through the deployed service against `dp2`, 2026-08-24, at catalog
generation 20, when the server was the previous one on `sdfiana035` (see
*Storage path* below for how the current server compares). Each query was run twice; both timings are given, because the gap
between them is the point.

| query | first | repeat | rows |
|---|---|---|---|
| `COUNT(*)` on `ssp.source_nv` (18 B rows) | 0.1 s | 0.1 s | metadata, not a scan |
| id-range query on `ssp.source_nv`, one visit, `detect_isPrimary` | 0.1 s | 0.1 s | 705,581 |
| `MIN`/`MAX` `sourceId` for one visit | 0.1 s | 0.1 s | the range lookup |
| `WHERE visit = …` count on `ssp.source_nv` | 0.1 s | 0.1 s | 791,503 |
| cone on `dp2.DiaObject`, 0.5°, `TOP 1000` no `ORDER BY` | 0.7 s | 0.1 s | 1,000 |
| cone on `dp2.DiaObject` + per-band photometry, 0.5° | 0.2 s | 0.2 s | 50,000 — **truncated** |
| `GROUP BY band` with `COUNT`/`MIN`/`MAX` on `dp2.DiaSource` | 0.9 s | 0.9 s | 6 |
| `GROUP BY band` with `AVG(snr)`, no cuts | 5.0 s | 0.9 s | 6 |
| the same with `reliability > 0.9 AND isDipole = 0` | 2.1 s | 1.2 s | 6 |
| nightly linkage, `WHERE ssObjectId != 0`, grouped | 0.9 s | 0.9 s | 365 |
| `SSSource` × `DiaSource` residuals, one designation | 0.8 s | 0.7 s | 732 |
| `DiaSource` × `CcdVisit`, joined on `visit` **and** `detector` | 2.6 s | 2.6 s | 704 |
| **cone on `ssp.source_nv`, 0.1°, fresh sky position** | **190.6 s** | — | 20,691 — async only |

:::{warning}
**These numbers are page-cache sensitive, and only the last row is reliably
cold.** ClickHouse's query cache is disabled here (`use_query_cache = 0`), so
repetition is not memoisation — it is the operating system holding the byte
ranges a previous query read. The effect is not subtle. The same `ssp` cone
measured **311 s** the first time and **0.1 s** on every re-run at the same
centre, then went straight back over the 60 s sync ceiling at a centre nobody had
touched.

So a benchmark that reuses one sky position measures the cache and reports a
service roughly 600x faster than a user experiences. `notes/bench-tap.sh` in the
control directory randomises the cone centre for exactly this reason, which is
why the last row is trustworthy: 190.6 s at one fresh position, 311 s at another,
the difference being how many rows each region holds. **A first-touch `ssp` cone
costs three to five minutes.**

The whole-table aggregates are warm even in the "first" column, having been run
repeatedly across a day, so read them as a floor rather than a characteristic
cost. A truly cold measurement needs the page cache dropped on the ClickHouse
host, which is not ours to do.
:::

Three things there are worth an operator's attention. A join on `visit` alone
fans out across all detectors and exceeds the sync limit, where the same join on
`visit` **and** `detector` returns in under three seconds — so a "the service is
slow" report is often a join-key problem. The 0.1–0.7 s cones are genuine, and
they are what the `hpix29` sort key buys: the same shape of query against `ssp`,
which has no spatial ordering, is the 190 s row. And the photometry cone returned
exactly 50,000 rows — the sync default `MAXREC` — so that row is a live instance
of the `OVERFLOW` truncation the service reports as success. The harness runs on
`/sync`; the same query from an ADQL notebook would have returned the lot.

Full method, ids and reproduction details are in the control directory's
`notes/2026-08-24-mppdb-tap-measurements.md`.

#### Storage path: NFS against native Weka

The same data has now been served by three servers, which isolates what the
storage path costs:

- **old**: ClickHouse 26.6 on `sdfiana035`, the previous server, with sdfdata
  mounted natively. Measured 2026-10-04.
- **pod**: ClickHouse 26.8 in the `river-next` pod, reaching sdfdata over NFS 4.1
  through the Weka gateway. Measured 2026-10-04.
- **sdfiana032**: the same 26.8 binary and configuration, with sdfdata mounted
  natively. Measured 2026-10-05.

All three read the same parts — the migration hard-linked them — with the same
queries and cone centres. Queries went directly to ClickHouse from `sdfiana031`,
not through TAP, one at a time, with `max_threads=64`, the query cache off and
output discarded. Timings are server-side medians in seconds. "Cold" means every
ClickHouse cache was dropped and each cone used a fresh sky position; the
operating system's page cache could not be dropped from the pod.

| query | mode | old | pod (NFS) | sdfiana032 (native) |
|---|---|---|---|---|
| `dp2.Source` `GROUP BY band` (17.6 B rows) | cold / warm | 50.2 / 27.5 | 27.1 / 12.8 | 19.3 / 10.7 |
| `dp2.ForcedSource` aggregate (44.7 B rows) | cold / warm | 43.4 / 43.2 | 90.0 / 17.3 | 30.8 / 15.8 |
| `ssp.source_daytime` aggregate (43.5 B rows) | cold / warm | 69.9 / 69.3 | 87.4 / 65.8 | 51.7 / 25.5 |
| `dp2.Object` scan, three magnitude cuts | cold / warm | 8.8 / 1.73 | 1.9 / 1.02 | 2.3 / 0.88 |
| `dp2.Object` 10° colour-cut cone | cold / warm | 16.8 / 0.42 | 0.94† / 0.28 | 3.8 / 0.24 |
| **`dp2.Object` 1° cone, 50 columns** | cold / warm | 0.92 / 0.11 | **56.7** / 0.16 | 0.61 / 0.11 |
| `DiaSource` × `DiaObject` join, 1° cone | cold / warm | 1.31 / 0.17 | 2.72 / 0.19 | 0.57 / 0.15 |
| `ppdb.DiaSource` point lookup by id | cold / warm | 0.14 / 0.088 | 0.26 / 0.185 | 0.14 / 0.09 |
| `ssp.SubmittableSources` full scan, 16 columns (93.8 B rows, 7.96 TiB) | cold | 1327.7 | 959.1 | 851.8 |
| id lookup on `ssp.SubmittableSources`, 1,400 ids (client wall time) | cold / warm | 2.0–3.0 / 1.9 | 2.7–4.0 / 2.05 | 1.9–3.1 / 1.7 |
| clean server shutdown | | — | 586 (idle) | 7 |

† The pod's colour-cut columns were already in its node's page cache from
earlier use.

Three findings follow.

**Over NFS, the cost falls on the first access to each file.** The 1° cone reads
50 `dp2.Object` columns that nothing had touched since a restart. In the pod it
took about 57 s on each of three runs, to read 14 MiB through 432 file opens,
with 562 s of thread time spent waiting to load marks; the previous server on
its native mount took 0.92 s. Once a file has been read it is fast. The
first-access case appeared in real use too: the first validation query from the
solar-system tooling after cutover took 42.9 s, with 2,599 file opens for
154 MiB, against 0.9 s for the identical rerun. Wide tables are where this
hurts, because their rarely-read columns pay it again on every node and after
every page-cache eviction.

**Over NFS, an `open()` occasionally hangs for minutes.** On 2026-10-04, after
cutover, one ClickHouse thread sat in `openat()` from 22:00:44 to 22:13:52 UTC,
blocked in the kernel's NFS client (`nfs_set_open_stateid_locked`), and every
`dp2.Object` read waited behind it until all were released in the same second.
Of that query's 788 s, 11.1 s was disk reading. The user's async query hit the
front-end's 300 s timeout and failed. A second stall at 23:35 that day held a
planning-only `EXPLAIN ESTIMATE` for 8 minutes, again in a single NFS open, with
16,018 file opens and 4.5 s of real disk time.

**Native Weka removes both.** On `sdfiana032` the same 26.8 server ran the cold
wide cone in 0.61 s against 56.7 s, was the fastest of the three on every large
scan, and finished the full `SubmittableSources` scan in 851.8 s
(9.6 GiB/s) against 959.1 s in the pod and 1327.7 s on the previous server. The
hardware is not identical — the pod runs on an EPYC 7702P inside a 64-core,
384 GiB limit, while `sdfiana032` is an EPYC 7713P with 128 cores, 503 GiB and
no limit, shared with other work — so part of the advantage may be the CPU.
The first-access penalty is not a hardware effect: the pod's warm timings are
close to the native host's. This is why the server runs on `sdfiana032`, and
why native Weka access from the cluster is the fix to pursue.

Steady-state performance over NFS is not the problem: warm, the pod was as fast
as the previous server or faster, 0.4–1.0 times its time on the large scans.
Point lookups carry about 0.1 s more per query in the pod with no disk wait,
which is CPU, version or configuration rather than storage.

Full method and raw data are in the control directory's
`notes/2026-10-04-river-old-vs-new-benchmark.md` and
`notes/2026-10-05-river-failover-rehearsal.md`.


### The catalog store

The catalog of record is the `TAP_SCHEMA.registries` table, not files. One row
per database holds the registry document verbatim as YAML, its
sha256, a monotonic `generation`, and publication provenance. The five standard
TAP_SCHEMA tables are a projection of the store: every publish rederives all five
from every stored document and swaps them in with one multi-pair rename.

Two properties follow. There is never a window in which a TAP_SCHEMA table is
absent. And an incomplete configuration cannot delete a schema, because
configuration is not an input to the derivation — the store is.

The store keeps the YAML, not just the five tables, because TAP_SCHEMA is a lossy
projection. The registry's `physical` block — the spatial binding and HEALPix depth
that make spatial searches fast — has no column in the VO-standard tables.

**Generation** advances only when the store changes. A byte-identical publish is a
no-op that still rederives the five tables, which makes `catalog publish` the
repair for a damaged projection.

**Publishing.** `mppdb catalog publish --schema X FILE` takes the publish lock,
sha-verifies the store, diffs the incoming document against the stored one, refuses
any removal without `--allow-remove`, writes the store with the rename pattern, and
rederives. `catalog diff` and `catalog show` are read-only. Ingest publishes its
own catalog, data first and catalog second: a crash between the two leaves a loaded
but unadvertised table, which republishing fixes, whereas the reverse order would
advertise a table that does not exist. The upsert is column-grain, so curated
`description`, `unit` and `ucd` values survive a re-ingest.

:::{warning}
**`principal` does not survive a re-ingest.** The column-grain merge does not
preserve it, and ingest does not set it, so re-ingesting a table drops its
principal flags — which is what SCS uses to find a row identifier and to define
VERB=2. A rebuild therefore needs the flags re-applied as a deliberate step.
:::

**The locks.** Two `flock`s serialize writers: the publish lock, which a second
publisher waits on, and a per-table ingest lock, which a second ingest of the
same table is refused. A `flock` excludes only processes on one machine, so both
are pinned to one designated ingest host: `MPPDB_INGEST_HOST=sdfiana031`, with
the lock files in a local directory named by `MPPDB_LOCK_DIR`. Run from any
other host, a publish or ingest refuses. This replaced the earlier rule, which
compared the caller's host with the ClickHouse server's — a check that a server
in a pod, whose host name is the pod's, can never pass.

**How the service consumes it.** The service loads its catalog only from the store,
at startup, and refuses to boot on a missing or sha-mismatched document rather than
falling back to anything in its image. At runtime the catalog is one immutable
object behind a single reference. A reload rebuilds and rebinds it, and is
fail-safe: bad input keeps the previous catalog, logs loudly, and does not exit.
Startup is deliberately the opposite, because there is nothing good to keep
serving.

**Reloads are automatic.** The service polls the store's `generation` every 60 s
(`MPPDB_CATALOG_POLL_S`; 0 disables it) with one small query, and reloads when
it changes. Poll errors keep the current catalog and are logged only when the
state changes, so a long backend outage does not flood the log; a failed reload
backs off before retrying. Measured on 2026-10-01, an unannounced publish was
served 49 s later with no one touching the service. `SIGHUP` still triggers the
same reload by hand. `GET /catalog` reports the served generation and per-schema digests, which
is what makes publishing and reloading checkable instead of assumed.

### The deployments

Everything below is the Phalanx application `river-next`, in the namespace of
the same name, defined in `applications/river-next/`, except for what runs on
the two SDF hosts. Argo CD currently deploys it from the Phalanx feature branch
the failover work was built on, not from `main`.

#### ClickHouse

One server, ClickHouse **26.8 LTS**, from the upstream image pinned by digest.
A long-term-support line gets a year of fixes on one version. The upgrade from
26.6 is one-way: 26.6 may not read parts that 26.8 has written.

| aspect | how |
|---|---|
| data | `…/river-next/clickhouse` on sdfdata Weka, mounted at `/var/lib/clickhouse` on both sides |
| staging | `…/river-next/user-files` beside it, the server's `user_files_path` |
| identity | the data directory's owning uid and gid, on both sides; the pod never chowns the volume |
| pod | StatefulSet `river-next-clickhouse`, one replica, 64 CPU / 384 GiB, on the RSP node pool; `max_server_memory_usage` is 0.9 of the memory limit and `max_threads` is set to the CPU limit, since the container sees all 128 host cores |
| failover host | `sdfiana032`, an unprivileged Apptainer instance of the same image, with the configuration exported byte-identical from the chart, including the pod's memory cap |
| pod health | a liveness probe that reads a file ClickHouse writes in its data directory as well as pinging, so a lost mount restarts the container; a 30-minute startup allowance for metadata loading |
| clients | a ClusterIP service for the front-end; a MetalLB `LoadBalancer` service on port 8123, on SLAC's private network, for ingest and direct clients on SDF hosts. The failover host serves the same ports itself |
| users | `admin` (writers), `mppdb_ro` (the front-end), `ssp_xmatch` (a direct client with read access to `ssp`), all with password hashes from a Secret, plus a passwordless `default` restricted to localhost, which the `ssp.visit_exposure_dict` dictionary loads as |
| switch | `clickhouse.backend: pod` (the default) or `external`. `external` scales the StatefulSet to zero, without deleting it or its claim, and points the front-end at `clickhouse.external.host`. Nothing else changes |

The data volume is a claim on the `sdf-data-rubin` storage class, whose PVs are a
hostPath of the whole `/sdf/data/rubin` filesystem with a `Delete` reclaim
policy. Its PV was patched to `Retain` seconds after it bound, and must stay so
before anything deletes the claim.

**The discovery file.** Clients do not follow the server through forwarding:
they change host name. Every switch rewrites the `river:` line of
`/sdf/home/m/mjuric/.clickhouse.host`, one `name:host[:port]` per line, port
8123 when absent, written atomically and leaving every other line as it is.
`river-ch-failover start` writes `sdfiana032`'s name once the server answers;
`point-to-pod` writes the LoadBalancer address only once the pod answers.
Clients in containers must read the absolute path, since their `$HOME` differs.

#### The ingest host

Ingest and catalog publishes run on `sdfiana031`, as `admin` over HTTP to
whichever side the discovery file names. The tooling runs from a checkout of the mppdb repository there, with
`MPPDB_INGEST_HOST`, `MPPDB_LOCK_DIR` and `MPPDB_CLICKHOUSE_*` set in the
environment, and `--user-files-root` naming the staging directory by its host
path. The `MPPDB_CLICKHOUSE_*` variables override `deploy/usdf/mppdb.toml`, which
still describes the previous single-host layout; its `[databases]` block remains
the list of databases in play.

#### The Phalanx front-end

Provides the TAP API and the web UI. Reads ClickHouse, owns no catalog data.
Currently image `ghcr.io/mjuric/mppdb:sha-f0d957e`.

| aspect | how |
|---|---|
| auth | `GafaelfawrIngress`, scope `read:tap`; the service trusts the username header the ingress injects and creates the account on first sight |
| path | `/river`, prefix stripped before the pod; the app adds it back when generating URLs. The chart's default is `/river-next`, the validation path |
| database credential | `mppdb_ro`: SELECT on `dp2`, `ppdb`, `ssp` and `TAP_SCHEMA`, `dictGet` on `ssp.visit_exposure_dict`, plus `system.one` and `system.parts`. Nothing more — the grant matches the served set exactly |
| state | 20 GiB `wekafs` ReadWriteOnce volume at `/data`, and a separate 2 TiB volume for async results |
| replicas | exactly one, `strategy: Recreate` — the state engine is single-writer and two writers corrupt `state.db` |
| secrets | hand-created: `river-next` (database credential), `river-next-pull` (registry token) and `river-next-clickhouse` (the ClickHouse users' password hashes) |
| catalog | loaded from the store at startup, then reloaded whenever its 60 s poll sees the store's generation change |

:::{important}
The secrets are hand-created because this deployment has no Vault access yet.
Each template has a `useVaultSecret` flag; turning it on produces a `VaultSecret`
of the same name, so the swap changes nothing else. The failover host reads the
same password hashes from a mode-600 file outside the repository.
:::

### Failover and fencing

The pod and `sdfiana032` run one server on one data directory. Two servers on
one ClickHouse data directory corrupt it silently, so everything about the
switch is built to make sure the other side is provably gone before a side
starts. The design is the same shell code on both sides: a guard, and a wrapper
around the server process. In the pod the guard is an init container and the
wrapper is the container's command; on `sdfiana032` the wrapper is the Apptainer
instance's process. Both are present whichever backend the chart selects, so a
manual scale-up of the pod during a failover still meets the guard.

**State on disk**, in the data directory:

- **`.owner`**, one line, `<state> <ident> <UTC time>`, where `state` is `pod`,
  `sdfiana032` or `released`. It is always written atomically. A missing file
  means `pod`; an empty one is refused everywhere.
- **Heartbeats**, `.heartbeat-pod` and `.heartbeat-sdfiana032`, touched every
  30 s by the running side's wrapper for as long as its server lives, and
  removed when it exits. A heartbeat older than 120 s is stale.
- **The claim lock**, `.owner.lock/`, held only while a side checks the rules
  and writes `.owner`. `mkdir` is atomic from both the NFS and the native
  client. A `holder` file records who holds it, and is checked before and after
  every write.

**The wrapper** runs the guard, writes `<side> <ident>`, starts the server with
a heartbeat beside it, and forwards SIGTERM. **Only a clean exit of the server,
seen by the wrapper in the same container or instance, writes `released`.** A
non-zero exit leaves the owner line naming that side, so that side alone may
restart itself.

**The guard's start rules**, all of which must hold:

1. the owner line says `released`, or names this side — never the other side;
2. the other side's heartbeat is absent or stale;
3. the other side's server does not answer `GET /ping` within 3 s. This fails
   closed: only a network failure counts as no answer, and any HTTP reply, even
   an error, means alive;
4. on the failover host only, the host's short name is `sdfiana032`.

The claim then re-reads `.owner` 2 s after writing it and refuses unless it
finds exactly the line it wrote. Nothing in the start path calls `kubectl`, so
the failover host's start can run unattended.

**Why stale signals never cross sides.** A server hung in a stalled NFS `open()`
can resume writing when the mount recovers; on 2026-10-03 a fenced client's
process lived on for five hours. A stale heartbeat and an unanswered ping are
exactly what such a server looks like, so they are necessary for a cross-side
start but never sufficient. The fence is `released`, and only a clean exit
writes it. The two override paths — `river-ch-failover start --force-takeover`
and `release-stale` — are interactive only, and require positive proof that the
side named in `.owner` has no live server: for the pod, `kubectl` showing the
StatefulSet at zero replicas **and** no pod at all, where a pod in
`Terminating` counts as present; for `sdfiana032`, a process check over `ssh`
showing no server or instance. An unreachable host is refused unless the
operator confirms, after SDF does, that it is down. A pod stuck in `Terminating`
on a stalled mount is not force-deleted: that removes the API object, not the
process.

The guard does not stop two servers of the *same* side, since rule 1 passes for
both. The realistic case is a force-deleted `Terminating` pod whose process
lives on beside its replacement, which is why force-deleting is ruled out.

#### The rehearsal

The switch was rehearsed on 2026-10-05, 03:44–04:28 UTC: pod mode, a switch to
`sdfiana032`, the benchmarks above, and a switch back, with no override at any
step.

- **The first switch failed safe.** It came about 90 s after the pod had
  started, while the server was still loading tables and scheduling merges. Its
  shutdown ran past the 300 s grace period and the pod was SIGKILLed, so no
  `released` was written; the owner line still named the pod and `start` would
  have refused. Recovery was `backend: pod` again: the pod reclaimed the
  directory under the same-side rule and was ready in 24 s, with the data
  intact and no detached parts.
- **A clean shutdown over NFS is slow.** With the server idle and merges
  stopped, the second attempt shut down cleanly but took **586 s**, most of it in
  shutting down storages. The grace period is now 1800 s, and a switch away from
  the pod is preceded by letting the table loader finish, stopping merges and
  TTL merges, and flushing logs.
- **On `sdfiana032`** the server was serving 16 s after `start`; `/river` was
  back after an outage of 10.5 minutes. The same server stopped cleanly there in
  **7 s**, and the switch back to the pod was an outage of about 2 minutes.

The front-end crash-loops while its ClickHouse is down, and recovers within
about a minute of the server answering.

The production switch followed on 2026-10-05 at 08:20 UTC. The pod had been
settled for hours, shut down cleanly in about 10 s, and the outage was about
70 s.

### The move to Kubernetes

Until October 2026 the server was ClickHouse 26.6 in an Apptainer instance on
`sdfiana035`, and the front-end was a separate Phalanx application, `river`. The
move put ClickHouse into the Phalanx application and upgraded it, without
re-ingesting anything.

**Approach.** ClickHouse parts are immutable: a merge or mutation writes a new
part and removes the old one. Two servers can therefore share a part through
hard links, and removing one link leaves the other intact — the mechanism
`FREEZE` itself relies on. The new data directory was put on the same sdfdata
filesystem as the old one so that links were possible.

**Migration, 2026-10-03 (Pacific time).** With ingest paused and no unfinished
mutations on the old server:

| step | result |
|---|---|
| schema | 111 objects recreated on the new server from the old server's `SHOW CREATE`, in 12 s |
| stop merges, freeze | merges stopped on the old server, then `FREEZE` of 82 tables in 3 minutes: **818 parts, 196,811,301,743 rows, 32.05 TiB**. The freeze manifest was the authoritative part list from then on |
| link | from an SDF host, not a pod, since a hard link cannot cross the pod's two mounts: 440,461 large files hard-linked, 5,772 small per-part metadata files copied so no inode that a server might rewrite is shared; 9 min 52 s |
| attach | merges stopped on the new server too, then all 818 parts attached in 24 minutes. Attaching renames a part on 26.8, so everything after this matched parts by `hash_of_all_files`. The old server's query logs went into a separate `system_2606` database |
| verify | against the manifest, in 3 minutes: every part present by hash and no extras, row counts identical, the catalog store at the same generation, the dictionary loading |

**Verification beyond the tool.** A black-box harness compared the two
servers part by part and row by row (431 checks passing), ran a fixed query set
through `/river` and the new front-end with identical results (12 of 12), and
checked operations: both front-ends at the same catalog generation, the users
authenticating, a real ingest with the new lock settings, the direct clients'
results, and the liveness probe restarting a pod whose check was made to fail.
The upgrade itself was reviewed against the 33 backward-incompatible changelog
entries of 26.7 and 26.8, none of which affected River, and mppdb's live test
suite passed against 26.8. The whole procedure had been rehearsed first on a
scratch database on the real servers.

The old server could not be made read-only for writers — its users are defined
in XML, where SQL `REVOKE` does not apply — so instead nothing was published
until cutover, and the full comparison ran once more immediately before it:
429 checks passing and none failing, so no stray write had reached the old
server.

**Cutover, 2026-10-04, 21:20–21:36 UTC.** Both front-ends were scaled to zero,
and the old front-end's user state was copied into the new one's volumes:
`state.db`, checkpointed and integrity-checked, and the async spool — 218 files,
44 GiB, in 8 min 18 s, every sha256 identical. That carried over 20 users, 34
threads and 70 jobs. The old front-end's ingresses were removed and the
new application took over `/river`. The outage was 16 minutes. The failover to
`sdfiana032` was built after cutover, once the NFS findings came in.

**Rollback and decommission.** The old server was kept, with merges stopped and
the freeze in place, so that rollback was possible: scale the new front-end to
zero, restore the old ingresses and scale the old front-end back up. Anything
users did after cutover would be left behind, and anything written to the new
server since would have to be redone on the old one, which cannot read parts
26.8 writes. Decommissioning is in progress and goes in a fixed
order: `UNFREEZE` on the old server first, because it needs the source tables
and works only while that server runs; then the old `river` application, whose
volumes were set to `Retain` before cutover; then the Apptainer instance. The
old data directory and the leftover databases that were deliberately not
migrated are deleted last, by hand.

### Operating the service

#### Is the service serving the current catalog?

The service loads its catalog at startup and holds it until reloaded, so this is a
real question with a cheap answer. Compare what the store holds with what the
service reports.

The service side needs no credentials:

```
curl -s https://usdf-rsp-dev.slac.stanford.edu/river/catalog
```

```json
{"generation": 20, "loaded_at": "…",
 "schemas": {"dp2": "43ebc9db…", "ppdb": "6d4cc61d…", "ssp": "3d76160e…"}}
```

The store side is one query, using the service's own read-only credential:

```sql
SELECT schema_name, generation, substring(sha256, 1, 10), published_at
FROM TAP_SCHEMA.registries ORDER BY schema_name
```

Equal generations and matching digests mean the service is current. On the backend
node, `mppdb catalog show` reports the same with provenance.

**Read the digests, not the generation.** `generation` is global and monotonic
across the whole store, so publishing *any* schema advances it for everyone. A
service can be several generations behind while serving byte-identical content,
because the publishes that moved the counter touched schemas it does not serve.
A gap of a dozen generations is unremarkable: a single database's ingest
publishes one table at a time, advancing the counter once per table without
touching any other schema's document.

The practical rule: a digest mismatch on a served schema means reload now; a
generation gap with matching digests means a reload would change nothing, and
deferring it is the safer choice, since a reload also picks up anything else that
has changed.

#### After a catalog change

On the backend node:

```
mppdb catalog publish --schema <s> <file>    # or an ingest, which publishes itself
mppdb catalog show
```

Then make the service pick it up:

```
kubectl -n river exec deploy/river -- mppdb reload --wait
```

`reload --wait` polls a target captured before the signal, so a concurrent publish
of another schema cannot fake a timeout. Its exit codes matter: 0 landed, 2 refused
(stale pidfile, wrong host), 3 timed out — which means unknown, not failed. A
refused reload leaves the served generation unadvanced, so the same poll detects
both.

:::{important}
Reloading is per-service and nothing coordinates it. A publish makes the store
current; the service serves its last-loaded catalog until reloaded. Removals and
renames are the dangerous case: a service that has not reloaded advertises tables
whose queries now fail, while its catalog still looks healthy. Additions only hide
a new table.
:::

#### Checking data and the service

After a load, check that served rows equal the export's declared rows exactly, and
that the ledger reconciles — `sum(_ingest_parts.rows)` equals the live count, per
table. Before an append, `ingest-parquet run <cfg> --append --dry-run` shows the
disk-versus-ledger delta and `--audit` finds torn parts and stale run markers.

Row counts and sizes come from metadata, not from scanning:

```sql
SELECT database, formatReadableQuantity(sum(rows)),
       formatReadableSize(sum(bytes_on_disk))
FROM system.parts WHERE active GROUP BY database
```

The web UI's schema browser uses this, which is why the read-only user needs
`SELECT` on `system.parts`. Without it the browser silently shows column counts
instead of row counts.

### Maintaining and upgrading

**Backend tooling** updates with `git pull`. There is no service to restart there,
only the ClickHouse daemon, which is left alone.

**The service** upgrades by building an image from its branch, pinning the
`sha-<commit>` tag in `values-usdfdev.yaml`, and syncing the Argo CD application.
Two rules learned the hard way:

1. **Check the mechanism, not just the result.** A config change that silently does
   nothing looks exactly like success when before and after are identical. Confirm
   the mechanism directly — the value inside the running pod, the log line, the
   `/catalog` generation — not output that would match either way.
2. **Render a chart template before pushing it.** The repository's linters do not
   render Helm templates, so a broken template passes lint and fails at deploy.

**Dependency pins.** The container build pins third-party artifacts by checksum
against per-release URLs. A pin against an unversioned "latest" URL breaks on every
upstream release and teaches operators to ignore it; against an immutable URL, a
mismatch means something real. When a pin must be bumped, two independent fetches
agreeing is the minimum evidence, and the reasoning belongs in a comment.

### Troubleshooting

Symptoms that have happened, with causes.

The service advertises tables whose queries fail
: Its catalog predates a retirement. Compare store and served (*Is the service
  serving the current catalog?*), then
  `mppdb reload --wait`. The catalog looks healthy throughout, which is what makes
  this one hard to notice.

`mppdb reload --wait` times out (exit 3)
: Unknown, not failed — the reload may have landed. Check `/catalog`. The tool
  derives its poll URL by joining the *public* path prefix to a *local* host, so
  where a proxy strips that prefix it polls `http://127.0.0.1:8080/river/catalog`
  while the pod serves `/catalog`, and never gets an answer. Pass
  `--url http://127.0.0.1:8080` to poll the path the pod actually serves. Still
  required on the deployed image.

The service refuses to start, complaining about the catalog
: By design there is no fallback: a missing, empty, unparseable, duplicated or
  sha-mismatched document aborts startup. Publish the catalog, then start.

Async queries fail in the browser with a network error
: The job URL is minted as `http://` while the page is `https://`, and the browser
  blocks the cross-scheme request. Behind a TLS-terminating proxy the service must
  pin its advertised base URL rather than derive it from the request.

The web UI loads but its assets 404
: The service is hosted under a URL prefix it does not know about. It needs its
  public base URL so generated paths carry the prefix.

Newly added web UI files return 401 until a restart
: The anonymous-asset allowlist is read at startup. Restart in the same step as any
  deploy that adds served files.

HTTP 403 fetching a file that is listed
: A permissions mismatch, not a missing file — the serving process is not in the
  group the mode assumes.

`FILE_DOESNT_EXIST` deep inside a load
: The wrong `--user-files-root`. The CLI default matches neither the stock
  ClickHouse path nor this deployment's.

An `--append` refuses
: The ledger cannot account for the table. Do not force it; re-import wholesale.

### Known gaps

Ordered by how much they should worry a new owner.

1. **No durability story.** One node, one ClickHouse server, no replication, no
   backups of 13.64 TiB. Most databases can be rebuilt from their exports. The
   exception is the two recovered `ssp` products, whose Butler datasets are gone:
   their canonical copies are the scratch FITS files and the ADES/PSV file they
   were rebuilt from. Those need an explicit protection commitment, separately from
   and more urgently than a backup story for the served databases.
2. **The backend does not survive a host reboot.** ClickHouse must be started by
   hand, in a documented order, including a tmpfs directory a reboot wipes. Until
   it is, every query fails: the service returns on its own but has nothing to
   read.
3. **NetworkPolicy is not enforced** on `usdf-rsp-dev`. The service trusts an
   identity header injected by its ingress, which is only safe if nothing else can
   reach the pod. A NetworkPolicy is applied and correct, but an in-cluster pod
   bypassed it and forged the header. External access is still gated by Rubin SSO,
   so the exposure is pod-to-pod inside the cluster — acceptable for dev, a blocker
   for production.
4. **The node-trust model.** ClickHouse's `default` user — no password, restricted
   to the node — is the write credential for everything, including the catalog
   store. Being on the node is the credential. That is a deliberate pilot decision,
   and it means any on-node process can write production data; it has already
   caused two incidents in testing, both repaired the same day. Production needs
   real accounts for writers.
5. **Secrets are hand-created** on the service side, pending Vault access.
6. **One service replica.** `state.db` is single-writer, so the service does not
   scale horizontally without upstream work.
7. **Catalog updates are partly manual.** `ingest-tapdump` does not publish to the
   store, so `ppdb` catalog updates are hand-run. `catalog publish` has no
   compare-and-set staleness guard, so a stale local document can revert curated
   metadata; operator convention is the current mitigation.
8. **`ssp.SubmittableSources` is not advertised over TAP**, an omission rather
   than a decision.
9. **Single-operator knowledge.** The mppdb repository's runbooks are good, but
   this note is the first document an outside operator could start from.

### What production would require

A handover checklist, not a plan of record.

1. **A performant data path inside Kubernetes.** This is what would let the whole
   system become one Phalanx application instead of a cluster front-end talking to
   a hand-maintained node. Several other items here — unattended restart,
   node-trust, secret management — would disappear with it rather than needing
   separate fixes. It is not an mppdb work item: it needs WekaFS reachable from the
   cluster at native speed rather than over NFS.
2. **A durability commitment**, starting with the canonical FITS and PSV files
   behind the two recovered `ssp` products, then a backup or replication story for
   the served databases.
3. **Unattended restart** of ClickHouse and the service, in the right order,
   without a human.
4. **Real ClickHouse accounts for writers**, so that being on the node stops being
   a credential.
5. **Confirm NetworkPolicy enforcement**, or replace the service's header trust
   with token verification it performs itself.
6. **Vault-managed secrets**, replacing the hand-created ones.
7. **An owner**, named here and on the service pages.
8. **Alerting.** Nothing reports a failed ingest, a stale catalog, or a backend
   that did not come back after a reboot.
