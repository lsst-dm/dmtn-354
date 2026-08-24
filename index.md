# River: A Fast, Scalable, Evergreen SQL and TAP Database of Rubin Prompt, Data Release, Nightly Validation and Solar System Catalogs

```{abstract}
River is a SQL analytics database for Rubin catalog data, with a TAP 1.1 service
providing an ADQL/TAP API and a web UI over it. It holds **185 billion rows**
across three databases on one ClickHouse server at the USDF — the DP2 data
release, the Solar System Processing working set, and a Prompt Products snapshot
— and you query it from TOPCAT, pyvo, or its own web console. River is the
deployment; `mppdb` is the software it runs, which is why that name persists
throughout its configuration and tooling.

It is fast enough to use interactively at that scale. Indexed lookups and
sky-position cone searches return in **under a second**, aggregates over a
billion rows in **a few seconds**, and the slowest case measured — an
*unindexed* cone search across **18 billion rows**, where nothing prunes — in
**three to five minutes**. Ingest is on the same footing: a release-scale
dataset goes from a Butler repository to a queryable table in **hours**, at about
**200 million rows per minute** into ClickHouse, and nightly appends extend it
**incrementally**: a no-op nightly pass costs 24 s and a real append of 118.5
million rows about 9 minutes. River can be thought of as APDB, PPDB and Qserv
rolled into one, with update capability.

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

It is a pilot. It runs on one node, has no replication and no backups, and does
not come back on its own after a host reboot. It is in real use anyway. This note
describes what is deployed, not what is planned.

**Part I — Using the service** covers what is in the database and how to query
it. **Part II — Operations and internals** covers how it is built, how to run it,
and what would have to change for production.

:::{important}
**Current status** boxes like this one flag something provisional or known to be
wrong. Believe the box over the surrounding text.
:::

:::{warning}
Row counts here were measured on 2026-08-24. The `ssp` database grew from 22 to 94
billion rows in the preceding week. Re-query rather than trusting a number in this
note: `SELECT COUNT(*) FROM ssp.source_nv` is read from metadata and returns in
about 0.2 s.
:::

## Part I — Using the service

### Getting started

Open <https://usdf-rsp-dev.slac.stanford.edu/river/ui/>. Rubin SSO logs you in and
your account is created on first visit. Anyone who can log in to `usdf-rsp-dev`
can query.

Then use the console, which is built to be explored rather than documented:

- The **demo notebook** in your account, *Demo: Sky/Visit/Light Curve*, is the
  intended way in. Sixteen cells go from the whole sky down to one object's light
  curve. Notebooks here speak ADQL, not Python; cells run in order, can be named,
  and later cells can reference an earlier cell's result with `{{ }}`.
- The **schema browser** in the sidebar lists every database, table and column
  with units and descriptions. That is the reference for what a column means, so
  this note does not restate it.
- **Worked example queries ship in the console.** Start from those rather than
  from anything written here.

Two things about the console that are not visible from it: it runs **every query
asynchronously**, so console queries get the 3600 s limit rather than the 60 s
one; and the demo notebook is `dp2`-qualified, but you only get that version if
your notebook list is empty when you first look. An account created before
2026-08-24 still holds the old one, whose unqualified queries no longer resolve —
delete it and reload to be re-seeded.

### What is in it

| database | what it holds | rows |
|---|---|---|
| `ssp` | per-visit source catalogs for Solar System Processing — eleven tables, one per processing run | 93.77 B |
| `dp2` | the DP2 data release: thirteen tables, among them `Object`, `Source`, `DiaSource`, `ForcedSource` | 91.44 B |
| `ppdb` | a static snapshot of the Prompt Products Database | 48.70 M |

**Every table reference must be qualified** — `dp2.DiaSource`, `ssp.source_nv`.
There is no default database, so a bare `FROM DiaSource` is rejected with
`table 'DiaSource' must be schema-qualified`. That is deliberate: no one of these
is the natural home for an unqualified name.

`dp2` and `ssp` are organised differently, and it decides which of your queries
are fast. `dp2` is **sorted by sky position**, so cone and region searches are its
indexed access path. `ssp` is **sorted by id**, so id lookups and ranges are
indexed there and a cone search is a full scan — correct, but minutes rather than
milliseconds.

Two things in `dp2` will surprise you if you `SELECT *`: `Object` has **1,225
columns**, and `mpc_orbits` carries a JSON blob of roughly **5.7 KB per row**.
Name the columns you want.

:::{important}
**"DP2" names two different things here.** The `dp2` database is the DP2 data
release itself — thirteen products, ~91.4 B rows, the largest being
`ForcedSource`, `ForcedSourceOnDiaObject` and `Source`. Separately,
`ssp.source_dp2` and `ssp.dia_source_dp2` are per-visit *source tables extracted
from* DP2 for Solar System Processing, with different columns and a different
sort key. Same release, different shape, different purpose.

A third sense existed until 2026-08-24: a database called `mppdb`, a DP2
prerelease that everything queried while `dp2` was loading. It has been retired.
If you find a query or notebook referring to `mppdb.*`, it is from before the
cutover and needs requalifying to `dp2.*` — the prompt-product tables correspond,
except that `DiaObjectLast` is gone and `DiaObject` takes its role.
:::

### Limits

| limit | value |
|---|---|
| rows returned, default / maximum | **50,000** / 2,000,000 |
| query timeout, `/sync` / async | **60 s** / 3600 s |
| results kept | 7 days |

:::{warning}
**Truncation at 50,000 rows is reported as success**, not as an error — the
response carries an `OVERFLOW` status. If a result is exactly 50,000 rows, check
for it before believing that is the whole answer.
:::

Not supported, and the failures are cryptic enough to be worth listing:
`TAP_UPLOAD`; `WITH`/CTEs; correlated subqueries; `CASE`; `COUNTIF`; string
functions including `SUBSTRING`; `ORDER BY` on a select-list alias (write
`ORDER BY COUNT(*) DESC`, not `ORDER BY n_dia DESC`); and
`REGION`/`AREA`/`CENTROID`/`COORD1`/`COORD2`/`COORDSYS`.

Supported and easy to assume otherwise: joins, **including across databases**;
`GROUP BY` and `HAVING`; `POINT`, `CIRCLE`, `POLYGON`, `BOX`, `CONTAINS`,
`INTERSECTS`, `DISTANCE`; and the usual numeric functions. Fluxes are nJy, so
magnitudes are `-2.5 * LOG10(psfFlux) + 31.4`.

### Scripted access

The TAP endpoint is `https://usdf-rsp-dev.slac.stanford.edu/river`. Mint a token
at <https://usdf-rsp-dev.slac.stanford.edu/settings/tokens/new> with scope
`read:tap`. It is an RSP token; the service does not issue its own.

In **TOPCAT**, enter the endpoint as the TAP URL. TOPCAT will not prompt for
credentials until the service returns a 401, so the prompt appears after your
first action rather than up front. Give the token as the HTTP Basic *username*
with `x-oauth-basic` as the password.

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

**This is a pilot on one node.** If the host reboots, ClickHouse does not come
back on its own and every query fails until someone restarts it by hand. There is
no replication and no backup.

**Who to ask.** The service is run by the Solar System Pipelines group; mjuric
owns it today. Report a wrong column, a missing table or an outage there, or file
an issue against the mppdb repository.

`ssp.SubmittableSources`, a view over all eleven `ssp` tables, is not advertised
over TAP yet.

## Part II — Operations and internals

### Architecture

Two parts:

- a **backend** on `sdfiana035`: the ClickHouse server, the ingest tooling, and
  the catalog store. All writes happen here.
- the **service**: the `river` Phalanx application on `usdf-rsp-dev`. It provides
  the TAP API and the web UI. It reads the backend and owns no data.

```{mermaid}
flowchart TB
  subgraph BACKEND["backend · sdfiana035 · apptainer sandbox"]
    ING["ingest: parquet / HATS / tapdump<br/>publishes data, then catalog"]
    CH[("ClickHouse 26.6<br/>dp2 · ppdb · ssp<br/>TAP_SCHEMA + registries")]
    ING --> CH
  end
  SVC["river Phalanx app · usdf-rsp-dev<br/>/river"]
  U["users: console · TOPCAT · pyvo"]
  CH -- "read-only, mppdb_ro" --> SVC
  SVC --> U
```

**Why it is split.** The service runs on Phalanx because Phalanx supplies what a
user-facing service needs: Gafaelfawr authentication, container builds, ingress and
TLS, secret management, and a reviewable deploy path. The backend cannot run there.
WekaFS reaches this Kubernetes environment over NFS, and NFS is too slow for the
data path. So the backend runs on a node where WekaFS is mounted natively, and the
service reaches it over the network.

:::{important}
The ideal deployment is one containerized application under Argo CD — server,
ingest and service together, with no node to maintain by hand. Filesystem
performance in Kubernetes is what blocks it, not anything about mppdb. Until that
changes, the node is necessary and should not be moved into the cluster. See
*What production would require*.
:::

The unit of operation between the two parts is the catalog: a publish on the
backend changes what the service should serve, and the service picks it up only
when reloaded — see *Operating the service*.

### The data: provenance and lifecycle

**`mppdb`** was the original science import: DP2 prompt products — a
**prerelease** of the release now shipped as `dp2` — mapped onto a curated
registry generated from the vendored Felis `apdb.yaml`. **Retired from this
service on 2026-08-24**, superseded by `dp2`; the database still exists on the
backend and in the catalog store, but is no longer in this deployment's
`[databases]` block and so is not served. The description that follows is how it
was built, kept because the curation pattern is the one `dp2` should converge on.
Datatypes, units, Datatypes, units,
UCDs and descriptions come from Felis; `hpix29`/`cx`/`cy`/`cz` spatial columns,
principal flags and foreign keys were added. The three large DIA tables came from
flat DP2 HATS exports via `mppdb ingest --from-hats`, which maps columns onto that
registry rather than generating a schema. The small tables date to the original
manual import. `mppdb` also has a Parquet lake and manifest chain on `/data`, left
from the DuckDB era, which still backs snapshot semantics. Static since 2026-07.

**`ppdb`** is a static import of the Prompt Products Database: a TAP dump of
`data-int.lsst.cloud/api/ppdbtap` loaded with `mppdb ingest-tapdump`.
ClickHouse-only — no lake, no manifests, no GC. Unchanged since 2026-07-25.

**`ssp`** is the solar-system working set: one table per export directory, each an
`acid import butler --split-by visit` export of per-visit parquet parts with
per-part manifests. Loaded with `mppdb ingest-parquet`, which generates the schema
from the parquet, computes the spatial columns from `ra`/`dec`, converts NaN to
NULL, and safe-casts at staging — an `int64`→`int32` overflow fails loudly.

Three products are maintained and grow: `source_daytime`, `source_nv` and
`dia_source_prompt`. An append on the export side is followed by
`ingest-parquet run --append`, which reads the immutable per-part manifests and
loads only what is new. A provenance ledger in the served database
(`_ingest_parts`, `_ingest_refs`) maps (visit, detector) to part and holds the
invariant `count(live) == sum(_ingest_parts.rows)` per table. The other eight
products are frozen.

**Publishing, for every ingest path:** load into a staging database, verify counts,
publish with an atomic multi-pair `RENAME TABLE`, then record provenance. Readers
never see a missing or half-loaded table. (`EXCHANGE TABLES` and
`CREATE OR REPLACE` are unavailable: `renameat2()` is unsupported on the WekaFS
data path.)

**Cadence:** `dp2` and `ppdb` are static. `ssp` arrives in bursts, whenever a
nightly append actually appends, plus occasional metadata-only catalog publishes.
Loads run on `sdfiana035` only, because staging goes through the ClickHouse
server's node-local `user_files` directory.

:::{important}
**`dp2` shipped on 2026-08-24**: thirteen DP2 release tables, **91.44 B rows**,
Felis-named and `hpix29`-ordered — `Object`, `Source`, `DiaSource`, `DiaObject`,
`ForcedSource`, `ForcedSourceOnDiaObject`, `Visit`, `CcdVisit`, `SSObject`,
`SSSource`, `mpc_orbits`, `object_shear_all`, `isolated_star_stellar_motions`. The
catalog store now holds four documents at generation 15, and the primary
deployment serves all four.

**This deployment serves it as of 2026-08-24.** Whether a front-end serves `dp2`
is its own `[databases]` decision, per the authorization-boundary rule, and the
order is fixed: `GRANT SELECT ON dp2.* TO mppdb_ro` on the backend first, then a
`databases.dp2` entry in the chart, then a restart or reload. Skipping the grant
would leave a service configured for a database it cannot read. When `dp2` takes
taken over from `mppdb` as this note's reference database, and the timings were
re-measured against it on 2026-08-24.

`ssp`'s ingest configs, loads and catalog content are owned by the ssp-submit
project, not by the service.
:::

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
product takes **24 s**, and a real append of 940,680 refs / 118.5 M rows takes
about **9 min**.

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

- `dp2.*` and `ppdb.*` are sorted on **`hpix29`**, a HEALPix index, with
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
generation 20. Each query was run twice; both timings are given, because the gap
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
exactly 50,000 rows — the default `MAXREC` — so that row is a live instance of
the `OVERFLOW` truncation the service reports as success.

Full method, ids and reproduction details are in the control directory's
`notes/2026-08-24-mppdb-tap-measurements.md`.

### The catalog store

Since 2026-08-22 the catalog of record is the `TAP_SCHEMA.registries` table, not
files. One row per database holds the registry document verbatim as YAML, its
sha256, a monotonic `generation`, and publication provenance. The five standard
TAP_SCHEMA tables are a projection of the store: every publish rederives all five
from every stored document and swaps them in with one multi-pair rename.

Two results. There is no window where a TAP_SCHEMA table is absent. And an
incomplete input can no longer delete a schema, because configuration is not an
input to the derivation — the store is.

The store keeps the YAML, not just the five tables, because TAP_SCHEMA is a lossy
projection. The registry's `physical` block — the spatial binding and HEALPix depth
that drive cone-search pruning — has no column in the VO-standard tables.

**Generation** advances only when the store changes. A byte-identical publish is a
no-op that still rederives the five tables, which makes `catalog publish` the
repair for a damaged projection.

**Publishing.** `mppdb catalog publish --schema X FILE` takes an on-node lock,
sha-verifies the store, diffs the incoming document against the stored one, refuses
any removal without `--allow-remove`, writes the store with the rename pattern, and
rederives. `catalog diff` and `catalog show` are read-only. Ingest publishes its
own catalog, data first and catalog second: a crash between the two leaves a loaded
but unadvertised table, which republishing fixes, whereas the reverse order would
advertise a table that does not exist. The upsert is column-grain, so curated
`description`, `unit` and `ucd` values survive a re-ingest.

**How the service consumes it.** The service loads its catalog only from the store,
at startup, and refuses to boot on a missing or sha-mismatched document rather than
falling back to anything in its image. At runtime the catalog is one immutable
object behind a single reference. `SIGHUP` rebuilds and rebinds it, and is
fail-safe: bad input keeps the previous catalog, logs loudly, and does not exit.
Startup is deliberately the opposite, because there is nothing good to keep
serving. `GET /catalog` reports the served generation and per-schema digests, which
is what makes publishing and reloading checkable instead of assumed.

### The deployments

#### The backend node

Everything that writes lives on `sdfiana035`, inside an apptainer sandbox whose
root filesystem is a writable directory on WekaFS. It is here rather than in
Kubernetes for the reason given under *Architecture*.

**ClickHouse** runs there as a plain daemon. There is no systemd in the container
and no automatic restart after a host reboot; bringing it back is a documented
manual step, including a tmpfs directory that a reboot wipes. Its `user_files`
staging directory is node-local scratch, visible under two names for one inode,
which is why loaders may run inside or outside the container but never off-node.

**Ingest and catalog tooling** runs from a git checkout at
`/root/projects/github.com/mjuric/mppdb`, kept on `main`, through its editable
venv. `git pull` updates it. Configuration lives in `deploy/usdf/mppdb.toml` — the
`[databases]` block is the authorization boundary, naming which ClickHouse
databases are in play — plus committed scalars in `mppdb.env` and two git-ignored
mode-600 secret files.

#### The Phalanx application

Provides the TAP API and the web UI. Reads the backend, owns no data. Defined in
`applications/river/`, currently image `ghcr.io/mjuric/mppdb:sha-7570146`.

| aspect | how |
|---|---|
| auth | `GafaelfawrIngress`, scope `read:tap`; the service trusts the username header the ingress injects and creates the account on first sight |
| path | `/river`, prefix stripped before the pod; the app adds it back when generating URLs |
| database credential | `mppdb_ro`: SELECT on `dp2`, `mppdb`, `ppdb`, `ssp`, `TAP_SCHEMA`, `system.parts`. The `mppdb` grant is unused since the retirement but **deliberately retained while the new shape soaks**: revoking it is the one step that would turn rolling the retirement back from a chart sync into a chart sync plus a backend grant, and it costs nothing to keep (read-only, and the credential is only usable by clients that already reach the node) |
| state | 20 GiB `wekafs` ReadWriteOnce volume at `/data` |
| replicas | exactly one, `strategy: Recreate` — the state engine is single-writer and two writers corrupt `state.db` |
| secrets | hand-created: `river` (database credential) and `river-pull` (registry token) |
| catalog | loaded from the store at startup, refreshed by `SIGHUP`, never by redeploy |

:::{important}
The two secrets are hand-created because this deployment has no Vault access yet.
The chart has a `useVaultSecret` flag; turning it on produces a `VaultSecret` of the
same name, so the swap changes nothing else.
:::

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
This is not hypothetical. Before `dp2` was served, this deployment sat at
generation 2 against a store at 15 with all three of its digests matching the
store's current documents exactly: the thirteen intervening generations were
`dp2`'s per-table ingest publishes, none of which touched a schema it served.
Reloading would have changed nothing. Adding `dp2` is what moved it to 15.

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

The console's schema browser uses this, which is why the read-only user needs
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
: Unknown, not failed — the reload may have landed. Check `/catalog`. On
  deployments where a proxy strips a URL prefix, older builds polled the wrong
  local URL and always timed out; `--url http://127.0.0.1:8080` is the workaround,
  fixed upstream.

The service refuses to start, complaining about the catalog
: By design there is no fallback: a missing, empty, unparseable, duplicated or
  sha-mismatched document aborts startup. Publish the catalog, then start.

Async queries fail in the browser with a network error
: The job URL was minted as `http://` while the page is `https://`, and the browser
  blocks the cross-scheme request. Behind a TLS-terminating proxy the service must
  pin its advertised base URL rather than derive it from the request.

The console loads but its assets 404
: The service is hosted under a URL prefix it does not know about. It needs its
  public base URL so generated paths carry the prefix.

Newly added console files return 401 until a restart
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
