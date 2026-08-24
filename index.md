# mppdb: SQL and TAP Analytics for Rubin Catalogs at the USDF

```{abstract}
mppdb is a SQL analytics database for Rubin catalog data, with a TAP 1.1 service
in front of it. It holds about 120 billion rows across three databases on one
ClickHouse server at the USDF, and you query it in ADQL from TOPCAT, pyvo, or its
own web console. It exists because Rubin produces catalog datasets faster than it
gains places to analyse them. The first half of this note is for people using the
service; the second half is for people running it.
```

## Scope and status

The service runs at <https://usdf-rsp-dev.slac.stanford.edu/mppdb>. Anyone who can
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
Row counts here were measured on 2026-08-23. The `ssp` database grew from 22 to 94
billion rows in the six days before that. Re-query rather than trusting a number
in this note; *Operating the service* shows how.
:::

# Part I — Using the service

## What mppdb holds, and why it exists

Four databases on one ClickHouse server, queried in ADQL:

| database | what it holds | rows |
|---|---|---|
| `ssp` | per-visit source catalogs for Solar System Processing — eleven tables, one per processing run | 93.77 B |
| `mppdb` | DP2 **prerelease** prompt products: DIA sources and objects, solar-system objects, MPC orbits | 25.74 B |
| `ppdb` | a static snapshot of the Prompt Products Database — `DiaObject`, `DiaSource`, `DiaForcedSource` | 48.70 M |
| `dp2` | the final DP2 release — **being loaded now**, will take over from `mppdb` | 86.55 B, 3 of 13 tables |

:::{important}
**"DP2" means three different things here**, which is worth untangling once.
`mppdb` is a *prerelease* DP2 import and is what everything below queries today.
`dp2` is the *final* release, loading as this is written; it is similar to `mppdb`
— comparable row counts, some columns different and fewer — and will replace it.
Separately, `ssp.source_dp2` and `ssp.dia_source_dp2` are per-visit source tables
extracted from DP2. Timings in this note were measured against `mppdb` and should
be re-run once `dp2` is in place.
:::

Rubin produces catalog datasets faster than it gains places to analyse them. They
live in Butler collections and file exports, which suits pipelines and not
questions like "give me every row matching this, across everything, now". The
immediate driver was Solar System Processing, which needs every source that could
be linked into an asteroid discovery in one queryable table rather than spread
across dozens of collections — some of which no longer exist upstream.

The service on top does two things: it provides a **TAP 1.1 interface**, so
standard VO tools work without anything bespoke, and a **web console** for finding
out what is actually in these datasets.

## Getting started

Open <https://usdf-rsp-dev.slac.stanford.edu/mppdb/ui/>. Rubin SSO logs you in and
your account is created on first visit. Anyone who can log in to `usdf-rsp-dev`
can query.

**Start with the demo notebook.** Every account gets one, *Demo:
Sky/Visit/Light Curve*: sixteen cells that begin with the whole sky, sort down to
one active object, pull its light curve, then plot every detection from a single
visit. Notebooks here speak ADQL, not Python; cells run top to bottom and remember
what came before, and you can add plot and markdown cells.

They also support `{{ }}` references, which feed a value from one cell into the
next query. If a cell named `many` returned a table, the next cell can say:

```text
SELECT ra, dec, band, visit, midpointMjdTai, psfFlux, psfFluxErr
FROM mppdb.DiaSource
WHERE diaObjectId = {{ many.diaObjectId[0] }}
```

:::{important}
The demo queries `mppdb` today. When `dp2` is loaded it will move there, largely
unchanged, except that `DiaObjectLast` goes away and `DiaObject` takes over its
role. Examples below that use `mppdb.DiaObjectLast` are tied to the current
prerelease import for the same reason.
:::

For scripted access, mint a token at
<https://usdf-rsp-dev.slac.stanford.edu/settings/tokens/new> with scope
`read:tap`, then save it:

```
(umask 077; cat > ~/.mppdb.token)   # paste the token, press Enter, then Ctrl-D
```

## Finding your way around the tables

### `ssp` — eleven tables, one per processing run

Which table you want depends entirely on which processing run you care about. The
row counts do not tell you that, and picking the biggest is a trap.

| table | rows | what it is |
|---|---|---|
| `source_daytime` | 43.54 B | single-visit star sources, LSSTCam Daytime AP reprocessing |
| `source_dp2` | 20.66 B | single-visit sources, final DP2 release |
| `source_nv` | 17.99 B | single-visit star sources, nightly validation |
| `source_nv_orphaned` | 9.17 B | nightlyValidation-era sources recovered from scratch FITS; no upstream copy |
| `dia_source_dp2_v30_0_0` | 1.26 B | DIA sources from a DP2 **prerelease — superseded** |
| `dia_source_dp2` | 1.00 B | DIA sources, final DP2 release |
| `dia_source_prompt` | 118 M | DIA sources, LSSTCam prompt processing |
| `dia_source_rfl` | 16.9 M | DIA sources, DRP FL reprocessing (`w_2025_19`) |
| `dia_source_dp1_48666` | 5.3 M | earlier DP1 test processing — the run the first asteroids were found in |
| `dia_source_dp1` | 3.09 M | DIA sources, final DP1 release |
| `dia_source_2025lost` | 70 | recovered from a submitted ADES file; no upstream copy |

:::{warning}
Note rows five and six. `dia_source_dp2_v30_0_0` is a **superseded prerelease** and
is *larger* than `dia_source_dp2`, the final release. Choosing by row count gets
you the wrong data with no warning.
:::

Every table's full description — naming the collection and run it came from — is in
the schema browser and in `TAP_SCHEMA.tables`.

### `mppdb` — twelve tables

`DiaSource`, `DiaObject`, `DiaObjectLast`, `DiaForcedSource`,
`DiaObject_To_Object_Match`, `DetectorVisitProcessingSummary`, `SSObject`,
`SSSource`, `mpc_orbits`, `current_identifications`, `numbered_identifications`,
`metadata`.

Three of those repay knowing about:

**`DiaObject` versus `DiaObjectLast`.** `DiaObject` is version *history*: it
carries `validityStartMjdTai` and `validityEndMjdTai`, and can hold several rows
per `diaObjectId`. Joining it without filtering on validity silently multi-counts
objects. `DiaObjectLast` holds one row per object. Use `DiaObjectLast` unless you
specifically want history.

**`DetectorVisitProcessingSummary`** is the per-visit, per-detector image-quality
table — 52 columns including `seeing`, `skyBg`, `zeroPoint`, `psfSigma`,
`astromOffsetMean`, `nPsfStar`. It is what you join against when asking whether a
detection anomaly tracks the observing conditions.

**`mpc_orbits.designation` is the unpacked provisional designation** (e.g.
`2008 AB`), not a number or a name. Numbers and names live in
`numbered_identifications` (`permid`, `iau_designation`, `iau_name`), so looking up
"(24) Themis" needs a join.

`DiaObject_To_Object_Match` maps `diaObjectId` to `objectId` — but no `Object`
table is loaded here, so the other side of that join does not exist yet.

### Column meanings

`mppdb` and `ppdb` columns carry units, UCDs and descriptions from Felis; the
schema browser and `TAP_SCHEMA.columns` show them:

```sql
SELECT column_name, datatype, unit, ucd, description
FROM TAP_SCHEMA.columns WHERE table_name = 'mppdb.DiaSource'
```

:::{important}
**`ssp` columns have no descriptions or units** — 655 columns, all blank. The names
come straight from the source parquet exports, which are the Butler
`sourceTable`/`diaSourceTable` columns, so the Science Pipelines schema is the
reference for what they mean. This includes the quality flags
(`detect_isPrimary`, `sky_source`, `calib_psf_used`, `pixelFlags_*`,
`invalidPsfFlag`), which behave as they do in the pipelines.
:::

## Writing queries

**Qualify table names**: `mppdb.DiaSource`, `ssp.source_nv`, `ppdb.DiaObject`.
Only the default database resolves unqualified names.

### What is fast

Each table is physically sorted on one key. A query is fast when its filter
matches that key, because the engine reads a slice instead of the whole table. In
terms of what you write:

| this is fast | on |
|---|---|
| `WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', …)) = 1` | `mppdb.*` and `ppdb.*` spatial tables — they are sorted by sky position |
| `WHERE <idcol> = …` or `BETWEEN` on the id | all `ssp` tables — sorted by id |
| `COUNT(*)` with no `WHERE` | anything — it is read from metadata, not scanned |
| `GROUP BY band`, `WHERE band = 'r'` | anything — `band` is a low-cardinality column |
| a join on **both** `visit` and `detector` | `mppdb.DiaSource` × `DetectorVisitProcessingSummary` |

| this is slow | why |
|---|---|
| any cone search on `ssp.*` | the spatial columns exist so the query is *correct*, but nothing prunes it: expect a full scan, minutes on tens of billions of rows |
| `WHERE visit = …` on `ssp.*` | there is a `visit` column but the table is not organised by it, so this scans. The fast path is an explicit id range, and ADQL here has no bitwise or integer-division functions to compute one from a visit number — so in practice, filter by id or accept the scan |
| a join on `visit` alone | fans out across all detectors; the same query joined on `visit` **and** `detector` finished in 2.8 s where the `visit`-only version hit the 60 s limit |

The id column differs per `ssp` table: `id` for `source_daytime`, `sourceId` for
`source_nv`, `source_dp2` and `source_nv_orphaned`, `diaSourceId` for all seven
`dia_source_*` tables.

You never write `hpix29` yourself. It is the spatial sort key, it is hidden from
the schema browser deliberately, and `CONTAINS(...)` is what puts it to work.

Measured against the live service, on `mppdb`:

| query | time |
|---|---|
| cone search, `DiaObjectLast`, radius 0.5°, `TOP 1000` with no `ORDER BY` | 0.2 s — this is time to the *first* 1000 rows, not the full cone |
| `COUNT(*)` on `ssp.source_nv` (17.99 B rows) | 0.2 s — metadata, not a scan |
| id-ordered lookup, `ssp.dia_source_dp1` | 0.1 s |
| visit/detector join with `GROUP BY`, 704 rows out | 2.8 s |
| `GROUP BY band` with `AVG(snr)` over `DiaSource`, no cuts | 11.9 s |
| the same aggregate with quality cuts | 10.4 s |

### Limits you will hit

| limit | value |
|---|---|
| rows returned, default | **50,000** |
| rows returned, maximum | 2,000,000 |
| sync query timeout | **60 s** |
| async job timeout | 3600 s |
| results kept | 7 days |

:::{warning}
**Truncation at 50,000 rows is reported as success**, not as an error — the
response carries an `OVERFLOW` status. If you run an aggregate and get exactly
50,000 rows back, check for it before believing that is the whole answer.
:::

Use `/sync` for interactive work and `/async` for anything that might exceed 60
seconds — an async job is queued, its results are spooled, and it survives a
disconnect. In pyvo that is `run_async` rather than `search`.

**Not supported here**, and worth knowing before you assume you made a mistake:

- **`TAP_UPLOAD`** — you cannot upload a table to join against. There is no
  workaround through the service today.
- **`WITH` / common table expressions** — rejected as `UNSUPPORTED_ADQL`.
- **Correlated subqueries.**
- **`ORDER BY` on a select-list alias** — write `ORDER BY COUNT(*) DESC`, not
  `ORDER BY n_dia DESC`, which fails as `UNKNOWN_COLUMN`.
- **`REGION`, `AREA`, `CENTROID`, `COORD1`, `COORD2`, `COORDSYS`**, and
  region-in-region `CONTAINS` — point-in-region only.
- Functions are an allowlist; `CAST` is restricted to scalar types.

Joins, `GROUP BY`, `HAVING`, aggregates and point-in-region `CONTAINS` all work.

### Examples

**Image quality against detection counts** — which visit/detector pairs threw
anomalous numbers of r-band DIA detections, and does it track seeing or sky
background? 2.8 s, 704 rows:

```sql
SELECT s.visit, s.detector, v.seeing, v.skyBg, v.zeroPoint, COUNT(*) AS n_dia
FROM mppdb.DiaSource AS s
JOIN mppdb.DetectorVisitProcessingSummary AS v
  ON s.visit = v.visit AND s.detector = v.detector
WHERE s.band = 'r'
GROUP BY s.visit, s.detector, v.seeing, v.skyBg, v.zeroPoint
HAVING COUNT(*) > 5000
ORDER BY COUNT(*) DESC
```

**A cone search** — fast, because `DiaObjectLast` is sorted by position:

```sql
SELECT TOP 1000 diaObjectId, ra, dec, nDiaSources, lastDiaSourceMjdTai
FROM mppdb.DiaObjectLast
WHERE CONTAINS(POINT('ICRS', ra, dec),
               CIRCLE('ICRS', 53.13, -28.10, 0.5)) = 1
```

**Detections per band, with quality cuts.** Without the cuts the mean is dominated
by dipoles, streaks and edge artifacts — in g band the cuts take 175.7 M
detections down to 1.4 M:

```sql
SELECT band, COUNT(*) AS n_detections, AVG(snr) AS mean_snr
FROM mppdb.DiaSource
WHERE reliability > 0.9 AND isDipole = 0
GROUP BY band
ORDER BY band
```

**A forced-photometry light curve** for one object:

```sql
SELECT midpointMjdTai, band, psfFlux, psfFluxErr
FROM mppdb.DiaForcedSource
WHERE diaObjectId = 744858242361853537
ORDER BY midpointMjdTai
```

**Solar-system astrometric residuals against the ephemeris** — the standard SSP QA
plot. Run this async; it exceeded the sync limit when tested:

```sql
SELECT ss.diaSourceId, ds.midpointMjdTai, ds.band,
       ss.ephOffsetAlongTrack, ss.ephOffsetCrossTrack, ss.phaseAngle
FROM mppdb.SSSource AS ss
JOIN mppdb.DiaSource AS ds ON ss.diaSourceId = ds.diaSourceId
WHERE ss.designation = '2008 AB'
ORDER BY ds.midpointMjdTai
```

**Orbital elements for a numbered object**, which needs the identification join:

```sql
SELECT n.permid, n.iau_name, o.a, o.q, o.e, o.i, o.epoch_mjd
FROM mppdb.mpc_orbits AS o
JOIN mppdb.numbered_identifications AS n
  ON n.unpacked_primary_provisional_designation = o.designation
WHERE n.permid = '24'
```

A crude main-belt box, if you want elements in bulk — a selection, not a
definition:

```sql
SELECT TOP 1000 designation, a, q, e, i, argperi, node, epoch_mjd
FROM mppdb.mpc_orbits
WHERE a BETWEEN 2.1 AND 3.3 AND e < 0.25
```

The console ships several of these ready to run.

## Getting results out

Results come back as **VOTable, CSV or Parquet** — pass `FORMAT=votable`, `csv` or
`parquet` on `/sync`, or when fetching an async job's result. Those three are all
there is: `FORMAT=fits`, `tsv` and `json` are rejected. Note that
`/capabilities` advertises only the VOTable serialisations, so a strict VO client
may not offer you CSV or Parquet even though the service will serve them.

Parquet is the one to use for anything large — it is typed and compact, and
`pandas.read_parquet` or `pyarrow` will read it directly.

The console's Download menu mints a **capability link** for a completed job: a
long random URL that `curl`, `wget` or TOPCAT can fetch with no token and no
session. It is good for 8 hours, can be reused within that window, and dies if
the service restarts. Convenient for handing a result to a collaborator; not
something to put in a script that has to keep working.

There is also a **Simple Cone Search** endpoint if you have an SCS client:

```
/scs?RA=53.13&DEC=-28.10&SR=0.05
/scs/DiaObjectLast?RA=53.13&DEC=-28.10&SR=0.05
```

The table name is **unqualified and `mppdb`-only** — `/scs/mppdb.DiaObjectLast`
fails with `unknown SCS table`, which is a confusing message for what is really a
naming rule. Omit it and you get `DiaObjectLast`. `SR` is capped at **5 degrees**,
and cones on `DiaSource` are **disabled** on this deployment because the table is
too large to cone through. For anything SCS will not do, use ADQL with
`CONTAINS`.

## TOPCAT and pyvo

The TAP endpoint is `https://usdf-rsp-dev.slac.stanford.edu/mppdb`.

In **TOPCAT**: enter that as the TAP URL. TOPCAT will not ask for credentials
until the service returns a 401, so expect the prompt to appear after your first
action rather than up front. Give your token as the HTTP Basic *username*, with
`x-oauth-basic` as the password.

In **pyvo**:

```python
import pyvo, requests
from pathlib import Path

tok = Path.home() / ".mppdb.token"
if tok.stat().st_mode & 0o077:
    raise SystemExit(f"{tok} is readable by others - run: chmod 600 {tok}")
session = requests.Session()
session.headers["Authorization"] = "Bearer " + tok.read_text().strip()
service = pyvo.dal.TAPService(
    "https://usdf-rsp-dev.slac.stanford.edu/mppdb", session=session)

job = service.run_async("SELECT TOP 10 * FROM ssp.dia_source_dp1")
print(job.to_table())
```

## Things to know

Your account, tokens, quotas and job history belong to this service; a token
minted here works here.

`ssp.SubmittableSources`, a view over all eleven `ssp` tables, is not advertised
over TAP yet.

Async jobs are per-user, with quotas on concurrency and spool space. The console
lists yours.

# Part II — Operations and internals

## Architecture

Two parts:

- a **backend** on `sdfiana035`: the ClickHouse server, the ingest tooling, and
  the catalog store. All writes happen here.
- the **service**: the `mppdb` Phalanx application on `usdf-rsp-dev`. It reads the
  backend and owns no data.

```{mermaid}
flowchart TB
  subgraph BACKEND["backend · sdfiana035 · apptainer sandbox"]
    ING["ingest: parquet / HATS / tapdump<br/>publishes data, then catalog"]
    CH[("ClickHouse 26.6<br/>mppdb · ppdb · ssp<br/>TAP_SCHEMA + registries")]
    ING --> CH
  end
  SVC["mppdb Phalanx app · usdf-rsp-dev<br/>/mppdb"]
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

## The data: provenance and lifecycle

**`mppdb`** is the original science import: DP2 prompt products — a **prerelease**
of the release now loading as `dp2` — mapped onto a curated registry generated
from the vendored Felis `apdb.yaml`. Datatypes, units,
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

**Cadence:** `mppdb` and `ppdb` are static. `ssp` arrives in bursts, whenever a
nightly append actually appends, plus occasional metadata-only catalog publishes.
Loads run on `sdfiana035` only, because staging goes through the ClickHouse
server's node-local `user_files` directory.

:::{important}
A fourth database, `dp2` — thirteen DP2 release products, about 91.4 B rows — is
**loading now**: the three largest tables are in (`ForcedSource` 44.73 B,
`ForcedSourceOnDiaObject` 24.26 B, `Source` 17.57 B, 86.55 B together as of
2026-08-24) and the remaining ten are pending. It is not yet granted to the
service's read-only user, so it is not queryable through TAP. When it is complete
it takes over from `mppdb`, and the timings in this note should be re-measured
against it. `ssp`'s ingest configs, loads and catalog content are owned by the
ssp-submit project, not by the service.
:::

## Ingest and query performance

Two things had to be true: datasets of this size must load in hours and be
extendable incrementally, and queries must return quickly. Both hold.

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
  is the honest upstream unit. Two products bracket it: `object_forced_source`,
  44.7 G rows / 912 GB in 127 min; `source`, 17.6 G rows / 9.76 TB in 419 min.

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

## The catalog store

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

## The deployments

### The backend node

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

### The Phalanx application

Reads the backend, owns no data. Defined in `applications/mppdb/`, currently image
`ghcr.io/mjuric/mppdb:sha-35bd883`.

| aspect | how |
|---|---|
| auth | `GafaelfawrIngress`, scope `read:tap`; the service trusts the username header the ingress injects and creates the account on first sight |
| path | `/mppdb`, prefix stripped before the pod; the app adds it back when generating URLs |
| database credential | `mppdb_ro`: SELECT on `mppdb`, `ppdb`, `ssp`, `TAP_SCHEMA`, `system.parts` |
| state | 20 GiB `wekafs` ReadWriteOnce volume at `/data` |
| replicas | exactly one, `strategy: Recreate` — the state engine is single-writer and two writers corrupt `state.db` |
| secrets | hand-created: `mppdb` (database credential) and `mppdb-pull` (registry token) |
| catalog | loaded from the store at startup, refreshed by `SIGHUP`, never by redeploy |

:::{important}
The two secrets are hand-created because this deployment has no Vault access yet.
The chart has a `useVaultSecret` flag; turning it on produces a `VaultSecret` of the
same name, so the swap changes nothing else.
:::

## Operating the service

### Is the service serving the current catalog?

The service loads its catalog at startup and holds it until reloaded, so this is a
real question with a cheap answer. Compare what the store holds with what the
service reports.

The service side needs no credentials:

```
curl -s https://usdf-rsp-dev.slac.stanford.edu/mppdb/catalog
```

```json
{"generation": 2, "loaded_at": "…",
 "schemas": {"mppdb": "46b4daa8…", "ppdb": "bcc3d3f9…", "ssp": "edfb9aa7…"}}
```

The store side is one query, using the service's own read-only credential:

```sql
SELECT schema_name, generation, substring(sha256, 1, 10), published_at
FROM TAP_SCHEMA.registries ORDER BY schema_name
```

Equal generations and matching digests mean the service is current. A store
generation ahead of the served one means a publish happened and the service has not
reloaded. On the backend node, `mppdb catalog show` reports the same with
provenance.

### After a catalog change

On the backend node:

```
mppdb catalog publish --schema <s> <file>    # or an ingest, which publishes itself
mppdb catalog show
```

Then make the service pick it up:

```
kubectl -n mppdb exec deploy/mppdb -- mppdb reload --wait
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

### Checking data and the service

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

## Maintaining and upgrading

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

## Troubleshooting

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

## Known gaps

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

## What production would require

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
