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

**Part I — Using the service** (§2–§7) covers what is in the database and how to
query it. **Part II — Operations and internals** (§8–§17) covers how it is built,
how to run it, and what would have to change for production.

:::{important}
**Current status** boxes like this one flag something provisional or known to be
wrong. Believe the box over the surrounding text.
:::

:::{warning}
Row counts here were measured on 2026-08-23. The `ssp` database grew from 22 to 94
billion rows in the six days before that. Re-query rather than trusting a number
in this note; §13 shows how.
:::

# Part I — Using the service

## What mppdb is for

Rubin produces large catalog datasets — prompt-processing products, preliminary
data releases, per-visit source tables. They live in Butler collections and file
exports. That works for pipelines and not for questions like "give me every row
matching this, across everything, now". mppdb answers those questions.

The immediate reason it exists is Solar System Processing. SSP needs the set of
all sources that could be linked into a new asteroid discovery, or associated with
a known one. Those sources sit in dozens of separate repositories and collections,
and some no longer exist upstream at all. mppdb puts them in one table you can
query in seconds.

Two of the `ssp` tables are recoveries: `source_nv_orphaned` holds 17,053 visits
rebuilt from scratch FITS files after the nightlyValidation datasets were deleted
from both repositories, and `dia_source_2025lost` holds 70 observations rebuilt
from a submitted ADES file. The FITS files and that PSV file remain the canonical
copies and need protecting (§16); mppdb makes them queryable, it does not replace
them.

The same need shows up elsewhere, so the database is general rather than
SSP-specific. Three datasets are in scope:

| database | what it is for |
|---|---|
| `ssp` | Solar System Processing — the eligible-source working set |
| `ppdb` | developing the SSP daily data products pipeline |
| `dp2` *(planned)* | data-release analytics, loadable as soon as DP2 exists |

`ppdb` is there to support the pipeline that will produce `sssource`, `ssobject`
and `nearby_sso` for the Prompt Products Database, developed at
[`mjuric/ssp`](https://github.com/mjuric/ssp). That pipeline does not use this
database yet, so `ppdb` currently has no active reader.

**The service on top does two things.** It provides a **TAP 1.1 interface**, so
applications and standard VO tools query the database without anything bespoke.
And it provides a **web console** for exploring what is in the database — write a
query, see the table, draw a plot — which is how you find out what these datasets
actually contain without writing a program.

## Getting started

Open <https://usdf-rsp-dev.slac.stanford.edu/mppdb/ui/>. Rubin SSO logs you in;
your account is created on first visit. There is no signup and no password held by
the service.

**Start with the demo notebook.** Every account gets one, called *Demo:
Sky/Visit/Light Curve*. It is the fastest way to see what the service does: sixteen
cells that begin with the whole sky, sort down to one active object, pull its light
curve, and then plot every detection from a single visit. It also shows the feature
that makes these notebooks more than a query box — `{{ }}` references, which feed a
value from one cell's results into the next cell's query, so a chain of queries
reads like an argument instead of a series of copy-pastes.

The console also has a query editor, a schema browser listing every table with its
row count, and worked examples you can run and edit: a cone search, per-band
detection counts, light curves, solar-system objects, and MPC orbital elements.

Notebooks here speak ADQL rather than Python. Cells run top to bottom and remember
what came before, and you can add plot cells and markdown cells alongside the
queries.

For scripted access you need a token. Mint one in the RSP token page
(`/settings/tokens/new`, scope `read:tap` only) and save it:

```
(umask 077; cat > ~/.mppdb.token)   # paste the token, press Enter, then Ctrl-D
```

That writes `~/.mppdb.token` readable only by you, and keeps the token out of your
shell history.

## What is in the database

Measured 2026-08-23:

| database | tables | rows | on disk |
|---|---|---|---|
| `ssp` | 11 | 93.77 B | 11.05 TiB |
| `mppdb` | 12 | 25.74 B | 2.57 TiB |
| `ppdb` | 3 | 48.70 M | 9.29 GiB |

`mppdb` holds DP2 prompt products: `DiaSource`, `DiaObject`, `DiaObjectLast`,
`DiaForcedSource`, `SSObject`, `SSSource`, `mpc_orbits` and related tables, with
Felis datatypes, units and descriptions. `ppdb` holds the three PPDB tables.
`ssp` holds per-visit source catalogs, one table per export — the four largest
are `source_daytime` (43.54 B rows), `source_dp2` (20.66 B), `source_nv`
(17.99 B) and `source_nv_orphaned` (9.17 B).

The console's schema browser is the fastest way to see columns, units and
descriptions. `SELECT * FROM TAP_SCHEMA.tables` gives the same thing in SQL.

## Writing queries

**Qualify table names.** Write `mppdb.DiaSource`, `ssp.source_nv`,
`ppdb.DiaObject`. Only the default database resolves unqualified names, so
qualifying always works.

**Use `TOP` while exploring**, and prefer async for anything that might be slow.
A sync query returns in the request; an async one becomes a job whose results are
spooled and survive a disconnect. In pyvo that is `run_async` instead of `search`.

### What is fast, and why it differs per table

Each table is physically sorted on one key. A query is fast when its filter
matches that key, because the engine reads a slice instead of the whole table. The
key is chosen per dataset to match how that dataset is used.

| tables | sorted on | fast | slow |
|---|---|---|---|
| `mppdb.DiaSource`, `DiaObject`, `DiaObjectLast`, `DiaForcedSource`, `SSSource`; all `ppdb` tables | `hpix29` (position) | cone searches | large sweeps by id |
| all `ssp` tables | `sourceId` / `diaSourceId` / `id` | lookups by id, and by visit — visit sits in the id's high bits, so one visit is one contiguous range | cone searches |

Measured against the live service:

| query | time |
|---|---|
| cone search, `mppdb.DiaObjectLast`, radius 0.5°, 1000 rows | **0.2 s** |
| `COUNT(*)` on `ssp.source_nv` (17.99 B rows) | **0.2 s** |
| id-ordered lookup, `ssp.dia_source_dp1` | **0.1 s** |
| `GROUP BY band` with `AVG(snr)` over `mppdb.DiaSource` | **11.9 s** |

A cone search on an `ssp` table is a different matter: the spatial columns exist,
so the query is correct, but nothing prunes it and the engine scans the table.
Expect minutes on tens of billions of rows.

`ssp` is sorted by id because that is what its main user asks for. The SSP
submission tooling sends batches of 10⁴–10⁶ `(collection, id)` pairs, joins them
server-side against a staged temporary table, and selects 19 columns. Its only
whole-table work is offline verification, which goes visit by visit — served by
the same ordering. It issues no spatial queries.

This can change. ClickHouse supports projections, a second physical ordering of
the same table, so a dataset that needs both patterns can have both at the cost of
storage and ingest time. Nothing needs one today. If a use case starts needing
fast cone searches over `ssp`, a projection is the answer rather than a redesign.

### Examples

A cone search — fast, because `mppdb.DiaObjectLast` is spatially sorted:

```sql
SELECT TOP 1000 diaObjectId, ra, dec, nDiaSources, lastDiaSourceMjdTai
FROM mppdb.DiaObjectLast
WHERE CONTAINS(POINT('ICRS', ra, dec),
               CIRCLE('ICRS', 53.13, -28.10, 0.5)) = 1
```

Detections per band, with mean signal-to-noise — a full aggregate, 11.9 s:

```sql
SELECT band, COUNT(*) AS n_detections, AVG(snr) AS mean_snr
FROM mppdb.DiaSource
GROUP BY band
ORDER BY band
```

A forced-photometry light curve for one object:

```sql
SELECT midpointMjdTai, band, psfFlux, psfFluxErr
FROM mppdb.DiaForcedSource
WHERE diaObjectId = 744858242361853537
ORDER BY midpointMjdTai
```

Main-belt orbits from the MPC elements table:

```sql
SELECT TOP 1000 designation, a, q, e, i, argperi, node, epoch_mjd
FROM mppdb.mpc_orbits
WHERE a BETWEEN 2.1 AND 3.3 AND e < 0.25
```

The console ships these and others, ready to run.

## TOPCAT and pyvo

The TAP endpoint is `https://usdf-rsp-dev.slac.stanford.edu/mppdb`.

In **TOPCAT**, enter that as the TAP URL. For authentication use your token as the
HTTP Basic *username* with `x-oauth-basic` as the password.

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

**Your account is local to this service.** Users, sessions, tokens, quotas and job
history live in this deployment. A token minted here works here.

**One table is missing from TAP.** `ssp.SubmittableSources` is a view over all
eleven `ssp` tables, used by the SSP submission portal, which reads ClickHouse
directly. It is absent from `/tables` by oversight rather than by design and is
expected to be added. If you compare `/tables` against the database and find one
extra object, that is why.

**Async jobs and results are per-user**, with quotas on concurrency and spool
space. The console shows your jobs.

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
changes, the node is necessary and should not be moved into the cluster. See §17.
:::

The unit of operation between the two parts is the catalog: a publish on the
backend changes what the service should serve, and the service picks it up only
when reloaded (§13).

## The data: provenance and lifecycle

**`mppdb`** is the original science import: DP2 prompt products mapped onto a
curated registry generated from the vendored Felis `apdb.yaml`. Datatypes, units,
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
planned and approved, waiting on the export. `ssp`'s ingest configs, loads and
catalog content are owned by the ssp-submit project, not by the service.
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
Kubernetes for the reason in §8.

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
: Its catalog predates a retirement. Compare store and served (§13.1), then
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
8. **`ssp.SubmittableSources` is not advertised over TAP** (§7), an omission rather
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
