# mppdb: SQL and TAP Analytics for Rubin Catalogs at the USDF

```{abstract}
mppdb is a SQL analytics database for Rubin catalog data, with a TAP 1.1 service
in front of it. It holds about 120 billion rows across three databases on one
ClickHouse server at the USDF, and you query it in ADQL from TOPCAT, pyvo, or its
own web console. It exists to give Solar System Processing — and Rubin catalog QA
generally — somewhere to run SQL across a whole dataset. The first half of this
note is for people using the service; the second half is for people running it.
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
Row counts here were measured on 2026-08-24. The `ssp` database grew from 22 to 94
billion rows in the preceding week. Re-query rather than trusting a number in this
note: `SELECT COUNT(*) FROM ssp.source_nv` is read from metadata and returns in
about 0.2 s.
:::

# Part I — Using the service

## What mppdb holds, and why it exists

There are more Rubin catalog datasets than there are places to run SQL against
them. They live in Butler collections and file exports. That works for pipelines;
it does not work for ad-hoc queries across a whole dataset. The immediate driver
was Solar System Processing, which needs every source that could be linked into an
asteroid discovery in one queryable table rather than spread across dozens of
collections — some of which no longer exist upstream.

The service gives you two things: a **TAP 1.1 interface**, so standard VO tools
work without anything bespoke, and a **web console** for finding out what is
actually in these datasets.

Three databases are queryable today:

| database | what it holds | rows |
|---|---|---|
| `mppdb` | DP2 **prerelease** prompt products: DIA sources and objects, solar-system objects, MPC orbits | 25.74 B |
| `ssp` | per-visit source catalogs for Solar System Processing — eleven tables, one per processing run | 93.77 B |
| `ppdb` | a static snapshot of the Prompt Products Database — `DiaObject`, `DiaSource`, `DiaForcedSource` | 48.70 M |

A fourth, `dp2`, is **being loaded and is not queryable yet** — `FROM dp2.Source`
fails with `unknown schema 'dp2'`. It is not granted to the service's read-only
user until the load finishes.

:::{important}
**"DP2" means three different things here.** `mppdb` is a *prerelease* DP2 import
and is what everything below queries. `dp2` is the *final* release, still loading.
`ssp.source_dp2` and `ssp.dia_source_dp2` are per-visit source tables extracted
from DP2.

`dp2` is **not** simply a bigger `mppdb`: it is thirteen DP2 release products
(~91.4 B rows) whose three largest are `ForcedSource`, `ForcedSourceOnDiaObject`
and `Source` — data-release tables that `mppdb` does not have at all. Where the
two do overlap, on the DIA prompt products, the row counts are comparable and the
columns differ slightly. Timings in this note were measured against `mppdb` and
should be re-measured once `dp2` is queryable.
:::

## Getting started

Open <https://usdf-rsp-dev.slac.stanford.edu/mppdb/ui/>. Rubin SSO logs you in and
your account is created on first visit. Anyone who can log in to `usdf-rsp-dev`
can query.

The console has a **schema browser** in the left sidebar: it lists every database,
its tables and their columns with units and descriptions. It is the fastest way to
answer "what is this column", and it is the only place some of that information
exists.

**Start with the demo notebook.** Every account gets one, *Demo:
Sky/Visit/Light Curve*: sixteen cells that begin with the whole sky, sort down to
one active object, pull its light curve, then plot every detection from a single
visit. Notebooks here speak ADQL, not Python; cells run top to bottom and remember
what came before, and you can add plot and markdown cells.

Cells can also feed each other. Give a cell a name in its header, then reference
its result from a later cell with `{{ }}`. If a cell named `many` returned a table:

```text
SELECT ra, dec, band, visit, midpointMjdTai, psfFlux, psfFluxErr
FROM mppdb.DiaSource
WHERE diaObjectId = {{ many.diaObjectId[0] }}
```

**The console runs every query asynchronously**, so console queries get the 3600 s
budget rather than the 60 s one. The 60 s limit below applies when you call `/sync`
yourself, including `pyvo`'s `search()`.

:::{important}
The demo queries `mppdb` today. When `dp2` is loaded it will move there, largely
unchanged, except that `DiaObjectLast` goes away and `DiaObject` takes over its
role.
:::

For scripted access, mint a token at
<https://usdf-rsp-dev.slac.stanford.edu/settings/tokens/new> with scope
`read:tap`, then save it:

```
(umask 077; cat > ~/.mppdb.token)   # paste the token, press Enter, then Ctrl-D
```

A token minted here is a Rubin RSP token scoped to `read:tap`; the service does
not issue its own. Your preferences, quotas and job history, however, live in this
service and are not shared with the rest of the RSP.

## Finding your way around the tables

### `ssp` — eleven tables, one per processing run

Which table you want depends on which processing run you care about. The row
counts do not tell you that, and picking the biggest is a trap.

| table | rows | cols | id | position | time | flux |
|---|---|---|---|---|---|---|
| `source_daytime` | 43.54 B | 23 | `id` | **`coord_ra`/`coord_dec`** | `mjd` | **instrumental only** |
| `source_dp2` | 20.66 B | 21 | `sourceId` | `ra`/`dec` | `mjd` | `psfFlux` |
| `source_nv` | 17.99 B | 64 | `sourceId` | `ra`/`dec` | `mjd` | `psfFlux` |
| `source_nv_orphaned` | 9.17 B | 20 | `sourceId` | `ra`/`dec` | `mjd` | `psfFlux` |
| `dia_source_dp2_v30_0_0` | 1.26 B | 96 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |
| `dia_source_dp2` | 1.00 B | 96 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |
| `dia_source_prompt` | 118 M | 96 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |
| `dia_source_rfl` | 16.9 M | 89 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |
| `dia_source_dp1_48666` | 5.3 M | 87 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |
| `dia_source_dp1` | 3.09 M | 89 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |
| `dia_source_2025lost` | 70 | 18 | `diaSourceId` | `ra`/`dec` | `midpointMjdTai` | `psfFlux` |

**These tables are not interchangeable.** Three differences will each break a
query you copied from one table to another:

- `source_daytime` uses **`coord_ra`/`coord_dec`**; every other table uses
  `ra`/`dec`. A cone search written for `source_nv` fails on `source_daytime`
  with `UNKNOWN_COLUMN`.
- The `source_*` tables timestamp with `mjd`; the `dia_source_*` tables use
  `midpointMjdTai`.
- **`source_daytime` has no calibrated flux at all** — only
  `base_PsfFlux_instFlux` and `slot_CalibFlux_instFlux`, which are
  *instrumental*, not nJy. Averaging "flux" across `ssp` tables mixes units
  silently.

:::{warning}
**Quality flags exist in almost none of these tables.** `detect_isPrimary` is in
**`source_nv` only**. So in `source_dp2` (20.66 B, the obvious pick for "final
DP2") and `source_daytime` (43.54 B, the biggest table here) **you cannot cut on
primary-ness at all** — deblended parent/child duplicates cannot be removed.

| table | `detect_isPrimary` | `sky_source` | `pixelFlags_*` |
|---|---|---|---|
| `source_nv` | yes | yes | 12 |
| `source_daytime` | — | yes | — |
| `source_dp2` | — | yes | — |
| `source_nv_orphaned` | — | — | — |
| `dia_source_*` (most) | — | — | 18–20 |
| `dia_source_2025lost` | — | — | — |
:::

`dia_source_dp2_v30_0_0` is a **superseded prerelease** and is *larger* than
`dia_source_dp2`, the final release. Choosing by row count gets you the wrong data
with no warning.

`ssp.source_dp2` holds 20.66 B rows while `dp2.Source` holds 17.57 B. They are
nominally the same data from the same release; the difference is not explained
anywhere, so treat `ssp.source_dp2` as the SSP working copy rather than as an
authoritative count of DP2 sources.

Every table's full description — naming the collection and run it came from — is
in the schema browser and in `TAP_SCHEMA.tables`.

### `mppdb` — twelve tables, four of them empty

| table | rows | |
|---|---|---|
| `DiaForcedSource` | 24.26 B | forced photometry at every object position |
| `DiaSource` | 1.00 B | single-epoch difference-image detections |
| `DiaObject` | 232 M | objects, **with photometry**, version-history rows |
| `DiaObjectLast` | 232 M | objects, current version only, positions only |
| `SSSource` | 8.14 M | per-detection solar-system quantities and ephemeris residuals |
| `DetectorVisitProcessingSummary` | 5.15 M | per-visit, per-detector image quality |
| `mpc_orbits` | 1.51 M | MPC orbital elements |
| `SSObject` | 299 K | solar-system objects, phase-curve fits (80 columns) |
| `DiaObject_To_Object_Match` | **0** | empty |
| `current_identifications` | **0** | empty |
| `numbered_identifications` | **0** | empty |
| `metadata` | **0** | empty |

:::{warning}
**Four tables are empty.** They are advertised, they have schemas, and they
return nothing. In particular `numbered_identifications` is where asteroid numbers
and names would live, so **you cannot look an object up by number or name** —
`(24) Themis` is not findable. Use the unpacked provisional designation
(`mpc_orbits.designation`, `SSSource.designation`), e.g. `'2002 FJ36'`.
:::

**`DiaObject` versus `DiaObjectLast`** matters more than the names suggest.
`DiaObjectLast` has ten columns — ids, position, source counts — and **no
photometry**. `DiaObject` has 87, including `u_psfFluxMean` through
`y_psfFluxMean` and their errors. So object-level photometry QA must use
`DiaObject`.

`DiaObject` carries version history: `validityStartMjdTai`/`validityEndMjdTai`,
and several rows per `diaObjectId`. Joining it without filtering on validity
multi-counts objects. The fix is one predicate — `validityEndMjdTai` is NULL on
the current version:

```sql
WHERE validityEndMjdTai IS NULL
```

**`DetectorVisitProcessingSummary`** is the per-visit, per-detector image-quality
table — 52 columns including `seeing` (arcsec), `skyBg` (adu), `zeroPoint`,
`psfSigma`, `astromOffsetMean`, `nPsfStar`. It is what you join against when
asking whether a detection anomaly tracks the observing conditions.

### Column meanings

`mppdb` and `ppdb` columns carry units, UCDs and descriptions from Felis, visible
in the schema browser and in `TAP_SCHEMA.columns`:

```sql
SELECT column_name, datatype, unit, ucd, description
FROM TAP_SCHEMA.columns WHERE table_name = 'mppdb.DiaSource'
```

Fluxes are **nJy**, so magnitudes are `-2.5 * LOG10(psfFlux) + 31.4`.

:::{important}
**`ssp` columns have no descriptions or units** — 655 columns, all blank. The
names come from the source parquet exports, which are the Butler
`sourceTable`/`diaSourceTable` columns, so the Science Pipelines schema is the
reference for what they mean. Table-level descriptions do exist.
:::

Two things about values, both of which change what a comparison means:

- **NaN became NULL on ingest.** Anywhere a pipeline wrote NaN, `ssp` holds NULL.
  `AVG` skips NULLs, and `x > 0` is neither true nor false for them.
- **Flags are nullable booleans.** `isDipole = 0` works, as does `= 1`; but a NULL
  flag satisfies neither, so `WHERE isDipole = 0` silently drops rows where the
  flag was never set.

## Writing queries

**Qualify table names**: `mppdb.DiaSource`, `ssp.source_nv`, `ppdb.DiaObject`.
Unqualified names resolve to **`mppdb`** — `FROM DiaObjectLast` works,
`FROM source_nv` does not.

### What is fast

Each table is physically sorted on one key. A query is fast when its filter
matches that key, because the engine reads a slice instead of scanning. In terms
of what you write:

| this prunes | on |
|---|---|
| `WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', …)) = 1` | `mppdb.*` and `ppdb.*` — sorted by sky position |
| `WHERE <idcol> = …` or `BETWEEN` on the id | all `ssp` tables — sorted by id |
| a join on **both** `visit` and `detector` | `mppdb.DiaSource` × `DetectorVisitProcessingSummary` |
| `COUNT(*)` with no `WHERE` | anything — read from metadata, not scanned |

Everything else scans. Scanning is not always slow — `GROUP BY band` over
`DiaSource` is a 2.2 s scan, because `band` is a low-cardinality column and the
table is 1 B rows — but it does not prune, and the cost grows with the table:

| this scans | measured |
|---|---|
| cone search on `ssp.*` | **311 s** for a 0.1° cone on `source_nv` (18 B rows, 46,616 matched). Correct, but nothing prunes it — **async only** |
| `WHERE visit = …` on `ssp.*` | 5.1 s on `source_nv`. There is a `visit` column, but the table is organised by id |
| a join on `visit` alone | fans out across all detectors; the same query joined on `visit` **and** `detector` took 2.8 s where the `visit`-only version hit the 60 s limit |

The id column differs per `ssp` table — `id`, `sourceId` or `diaSourceId`, per the
table above.

**Getting an id range from a visit.** Ids are packed per visit, but the packing is
not documented, and ADQL here has no bitwise operators to unpack it. Ask the
database once:

```sql
SELECT MIN(sourceId) AS lo, MAX(sourceId) AS hi
FROM ssp.source_nv WHERE visit = 2026051200854
```

That costs one scan (4.5 s), and then every query against that visit is indexed:

```sql
SELECT COUNT(*) FROM ssp.source_nv
WHERE sourceId BETWEEN 26278442525786120 AND 26278442624354608
  AND detect_isPrimary = 1
```

**0.1 s**, versus 5.1 s for the same count written as `WHERE visit = …`. Keep the
range if you are going to ask more than one question about a visit.

You never write `hpix29` yourself. It is the spatial sort key, it is hidden from
the schema browser deliberately, and `CONTAINS(...)` is what puts it to work.

### Limits you will hit

| limit | value |
|---|---|
| rows returned, default | **50,000** |
| rows returned, maximum | 2,000,000 |
| sync query timeout | **60 s** (`/sync` and `pyvo`'s `search()`) |
| async job timeout | 3600 s (everything the console runs) |
| results kept | 7 days |

:::{warning}
**Truncation at 50,000 rows is reported as success**, not as an error — the
response carries an `OVERFLOW` status. If an aggregate returns exactly 50,000
rows, check for it before believing that is the whole answer. In `pyvo` it is on
the result's `QUERY_STATUS` info; in the console the job listing flags the job as
overflowed. Raise `MAXREC` up to 2,000,000, or aggregate server-side instead.
:::

**`TOP n` without `ORDER BY` gives you whichever rows the engine reaches first**,
not a sample. That is fine for looking at column values and wrong for anything
statistical.

**Not supported.** If a query fails on one of these, it is the service, not you:

- **`TAP_UPLOAD`** — no uploading a table to join against. For small match lists
  an explicit `id IN (…)` works; a few thousand ids is fine, and past that the
  query text itself becomes the problem.
- **`WITH` / common table expressions**, and **correlated subqueries**.
- **`CASE` expressions** and **`COUNTIF`** — so conditional counts need either two
  queries or a `WHERE`-filtered one.
- **`ORDER BY` on a select-list alias** — write `ORDER BY COUNT(*) DESC`, not
  `ORDER BY n_dia DESC`, which fails as `UNKNOWN_COLUMN`.
- **String functions** — no `SUBSTRING`, no concatenation. You cannot slice a
  designation server-side.
- **`REGION`, `AREA`, `CENTROID`, `COORD1`, `COORD2`, `COORDSYS`**, and
  region-in-region `CONTAINS`.

**Supported**, and easy to miss: joins, including **across databases**
(`ssp.dia_source_dp1 JOIN mppdb.SSSource` works); `GROUP BY`, `HAVING`,
aggregates; the geometry constructors `POINT`, `CIRCLE`, `POLYGON` and `BOX`, the
predicates `CONTAINS` and `INTERSECTS`, and the scalar `DISTANCE`; and the
numeric functions `ABS CEILING DEGREES EXP FLOOR LOG LOG10 MOD PI POWER RADIANS
RAND ROUND SQRT TRUNCATE ACOS ASIN ATAN ATAN2 COS COT SIN TAN`. `CAST` is
restricted to scalar types.

### Examples

All timings below were measured on this service against `mppdb`.

**Nightly solar-system linkage** — how many detections were linked to a
solar-system object each night and band. This is the "is SSP working" query.
`ssObjectId` is `0` for unlinked detections, not NULL, so `COUNT(ssObjectId)`
would count everything. 7.5 s, 365 rows:

```sql
SELECT FLOOR(midpointMjdTai) AS night, band, COUNT(*) AS n_linked
FROM mppdb.DiaSource
WHERE ssObjectId != 0
GROUP BY FLOOR(midpointMjdTai), band
ORDER BY FLOOR(midpointMjdTai), band
```

Overall, 8,136,150 of 1,000,825,975 detections are linked — 0.81%, and it matches
`SSSource`'s row count exactly. Drop the `WHERE` for the denominator per night.

**What a table actually covers** — rows and time span per band, 2.2 s:

```sql
SELECT band, COUNT(*) AS n, MIN(midpointMjdTai) AS t0, MAX(midpointMjdTai) AS t1
FROM mppdb.DiaSource GROUP BY band ORDER BY band
```

**Image quality against detection counts** — which visit/detector pairs threw
anomalous numbers of r-band detections, and does it track seeing or sky
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

**Objects with photometry in a cone** — `DiaObject`, current versions only.
20.0 s, 23,536 rows:

```sql
SELECT diaObjectId, ra, dec, nDiaSources,
       g_psfFluxMean, r_psfFluxMean, i_psfFluxMean
FROM mppdb.DiaObject
WHERE validityEndMjdTai IS NULL
  AND CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', 53.13, -28.10, 0.5)) = 1
```

**Solar-system astrometric residuals against the ephemeris** — the standard SSP QA
plot. 4.6 s, 732 rows:

```sql
SELECT ss.diaSourceId, ds.midpointMjdTai, ds.band,
       ss.ephOffsetAlongTrack, ss.ephOffsetCrossTrack, ss.phaseAngle
FROM mppdb.SSSource AS ss
JOIN mppdb.DiaSource AS ds ON ss.diaSourceId = ds.diaSourceId
WHERE ss.designation = '2002 FJ36'
ORDER BY ds.midpointMjdTai
```

**Detections per band with quality cuts**, 10.4 s. `reliability` is the
real/bogus classifier score, declared nullable but with no NULLs today:

```sql
SELECT band, COUNT(*) AS n_detections, AVG(snr) AS mean_snr
FROM mppdb.DiaSource
WHERE reliability > 0.9 AND isDipole = 0
GROUP BY band
ORDER BY band
```

:::{warning}
That cut is aggressive, not a default. Across the whole table, `reliability > 0.9`
keeps 16.3 M of 1.0 B detections — 1.6%; `> 0.5` keeps 26.3 M. In g band the note's
cuts take 175.7 M detections down to 1.4 M. Pick a threshold deliberately and say
which one you used. Note also that `reliabilityVersion` exists, so scores are not
comparable across runs with different classifier models, and that `reliability`
does not remove streaks or edge artifacts — the `pixelFlags_*` columns do that.
:::

**A single visit from `ssp`**, using the id range from above. 0.2 s:

```sql
SELECT TOP 5 sourceId, ra, dec, mjd, psfFlux, band
FROM ssp.source_nv
WHERE sourceId BETWEEN 26278442525786120 AND 26278442624354608
  AND detect_isPrimary = 1
```

**A forced-photometry light curve** for one object:

```sql
SELECT midpointMjdTai, band, psfFlux, psfFluxErr
FROM mppdb.DiaForcedSource
WHERE diaObjectId = 744858242361853537
ORDER BY midpointMjdTai
```

**Orbital elements in bulk**, a rough main-belt cut — a selection, not a
definition of the main belt:

```sql
SELECT TOP 1000 designation, a, q, e, i, argperi, node, epoch_mjd
FROM mppdb.mpc_orbits
WHERE a BETWEEN 2.1 AND 3.3 AND e < 0.25
```

:::{warning}
**809,795 of `mpc_orbits`' 1,505,545 rows have NULL `a`** — 54%. Any cut on `a`
drops them silently. Check `COUNT(*)` with and without the element predicate
before drawing a conclusion about population sizes.
:::

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

In **pyvo** — note `run_async`, not `search`, for anything that might take more
than a minute:

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

**This is a pilot on one node.** If the host reboots, ClickHouse does not come
back on its own and every query fails until someone restarts it by hand. There is
no replication and no backup of the databases.

**Who to ask.** The service is run by the Solar System Pipelines group; mjuric
owns it today. Report a wrong column, a missing table or an outage there, or file
an issue against the mppdb repository. A named on-call owner is one of the things
production would need and does not have yet.

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
