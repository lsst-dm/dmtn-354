# The mppdb Database Service at the USDF

```{abstract}
mppdb is a large-scale SQL analytics database for Rubin catalog-level data, with
a TAP 1.1 service in front of it: some 120 billion rows across three databases on
a single ClickHouse server at the USDF, queryable in ADQL from ordinary VO tools.
It exists because Rubin produces catalog datasets faster than it acquires places
to analyse them — most immediately for Solar System Processing, which needs every
eligible source in one queryable table rather than scattered across dozens of
repositories. This note describes the system as
deployed: what it serves and where the data comes from, how to query it, how the
service is deployed on the Rubin Science Platform, how an operator keeps it
running, and what promotion to production would require. It is written for the people who will operate the
service and for the people who will use it.
```

## Scope and status

This note covers **the whole mppdb deployment at the USDF**, which has two
distinct parts:

- a **backend** on `sdfiana035` — the ClickHouse server holding the data, the
  ingest pipeline that loads it, and the catalog store that describes it. This
  runs inside an apptainer sandbox on a Rubin dev node and is where all writes
  happen.
- the **service**: the `mppdb` Phalanx application at
  <https://usdf-rsp-dev.slac.stanford.edu/mppdb>, a front-end-only deployment on
  the `usdf-rsp-dev` Rubin Science Platform. It queries the backend read-only and
  owns no data of its own.

**Why the split exists — it is a workaround, not a design.** The service belongs
on Phalanx because that is where the platform supplies what a user-facing service
needs: Gafaelfawr authentication and RSP identity, container build and lifecycle
management, ingress and TLS, secret management, and a deployment path with review
and rollback. The backend cannot follow it there, because the *data path* would
not survive the move. WekaFS is presented to this Kubernetes environment over
**NFS**, and NFS performance in that environment is far too poor to carry a
multi-terabyte ClickHouse server. So the backend runs where WekaFS is mounted
natively — a general-purpose interactive node — and the Kubernetes front-end
reaches it over the network.

:::{important}
**The ideal deployment is one containerized application** managed by Argo CD under
Phalanx, exactly as the service half already is — server, ingest and service
together, with no node to keep by hand. What blocks it is filesystem performance
in the Kubernetes environment, nothing about mppdb itself. If that data path ever
becomes performant, collapsing the two halves is the right end state; §12 lists
it. Until then, the node is load-bearing and should not be "tidied away" into the
cluster.
:::

The split matters because the two are operated differently and by different
means: the backend is a working tree and command-line tooling on a node, the
service is a Helm chart and an Argo CD application. **The unit of operation
between them is the catalog**: a publish on the backend changes what the service
should serve, and the service picks that up only when reloaded (§9).

What belongs to the service alone is its account universe — users, sessions, API
tokens, quotas and async-job state live in its own `state.db`. Nothing about
those is shared with the backend or with any future second front-end; they are
per-instance state by design.

Two properties are what make the system useful rather than merely present, and
both are demonstrated rather than asserted: datasets of tens of billions of rows
load in hours and can be **extended incrementally**, and queries against them
return in seconds **when the query matches how the dataset is ordered**. Both are
quantified later.

The system is a **pilot**. The backend runs on one node with no replication and
no backups, and does not survive a host reboot unattended. It is nonetheless in
real use, and the sections below describe what is deployed rather than what is
intended.

:::{important}
**Current status** callouts like this one mark something provisional, missing, or
known to be wrong. Believe the callout over the surrounding text.
:::

:::{warning}
**Numbers age quickly.** Row counts and table lists in this note were measured on
2026-08-23. The `ssp` database grew from ~22 billion to ~94 billion rows in the
six days before that date. Re-query rather than trusting a figure here; §9 says
how.
:::

## What the system is, and why it exists

Rubin generates substantial **catalog-level** datasets — prompt-processing
products, preliminary data releases, per-visit source tables — and has no
performant place to run analytics over them. They live as Butler collections and
file exports, which is right for pipeline processing and wrong for the question
"give me all the rows matching this, across everything, now". mppdb is that place:
a central, internal, large-scale SQL analytics database into which tabular
datasets are loaded and then queried.

**The immediate driver is Solar System Processing.** SSP must know the set of all
sources eligible to be linked into a newly discovered asteroid, or associated with
an already-known one. Today those sources are scattered across dozens of separate
repositories and collections; assembling them per query is impractical. mppdb
brings them together into one table that can be queried in seconds — which is the
difference between a pipeline that can ask the question and one that cannot.

The same need recurs elsewhere, which is why the design is a general analytics
database rather than an SSP-specific one. Three datasets are in scope:

| database | supports |
|---|---|
| `ssp` | Solar System Processing — the eligible-source working set |
| `ppdb` | prompt-processing analytics (the Prompt Products Database) |
| `dp2` *(planned)* | data-release analytics — DP2 products, loadable as soon as they exist |

The two halves of the system serve two different purposes. The **ClickHouse
backend** is the analytics engine: it is what makes a 40-billion-row table
queryable at all. The **service** puts a *standard* interface on top — TAP, so
that applications and existing VO tools can query it without bespoke clients —
and a web console on top of that, so a human can write a query, see a plot, and
get a sense of what is actually in these datasets without writing any code.

```{mermaid}
flowchart TB
  subgraph BACKEND["backend · sdfiana035 · apptainer sandbox"]
    ING["ingest: parquet / HATS / tapdump<br/>publishes data, then catalog"]
    CH[("ClickHouse 26.6<br/>mppdb · ppdb · ssp<br/>TAP_SCHEMA + registries")]
    ING --> CH
  end
  SVC["mppdb Phalanx app · usdf-rsp-dev<br/>/mppdb"]
  U["users: browser console · TOPCAT · pyvo"]
  CH -- "read-only, mppdb_ro" --> SVC
  SVC --> U
```

All writes happen on the backend node; the service only ever reads. That
asymmetry is what lets the service be redeployed, restarted or rolled back
without any risk to the data.

## Querying the service

### Endpoints

| | |
|---|---|
| Base URL | `https://usdf-rsp-dev.slac.stanford.edu/mppdb` |
| Web console | `/mppdb/ui/` |
| TAP sync / async | `/mppdb/sync`, `/mppdb/async` |
| Catalog state | `/mppdb/catalog` |
| Authentication | Rubin SSO via Gafaelfawr, scope `read:tap` |

`/tables`, `/capabilities`, `/availability` and `/catalog` are unauthenticated on
both; everything else needs a credential.

### Credentials

Rubin SSO authenticates you at the ingress, and mppdb provisions your account on
first sight — there is no separate signup and no password held by the service. For
external VO tools, mint an RSP token (`/settings/tokens/new`, scope `read:tap`
only) and keep it in a file:

```
(umask 077; cat > ~/.mppdb.token)   # paste the token, Enter, then Ctrl-D
```

That writes `~/.mppdb.token` mode 600 and keeps the token out of shell history.
The token is the credential for TOPCAT and pyvo alike.

### pyvo

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

In TOPCAT, use the base URL above as the TAP URL, and the token as the HTTP Basic
**username** with `x-oauth-basic` as the password (Gafaelfawr's convention, which
the ingress accepts; `Bearer` works too).

### Writing ADQL against this service

**Qualify every table name.** A table is addressed as `database.table` —
`ssp.dia_source_dp1`, `mppdb.DiaSource`, `ppdb.DiaObject`. Only the configured
default database resolves unqualified names, so qualifying always works and is
the habit to keep.

### What a query can prune on — and why it differs per dataset

This is the one thing worth understanding before writing a query here. Every table
is physically **sorted** on a key, and ClickHouse answers a query quickly when the
query's filter matches that key: it reads the relevant slice instead of the table.
The key is chosen per dataset, to match how that dataset is actually used — so the
same ADQL can be interactive against one database and a full scan against another,
and neither is a defect.

| tables | sorted on | fast | slow |
|---|---|---|---|
| `mppdb.DiaSource`, `DiaObject`, `DiaObjectLast`, `DiaForcedSource`, `SSSource`; all three `ppdb` tables | `hpix29` (spatial) | cone searches — a `CONTAINS(POINT(...), CIRCLE(...))` becomes a HEALPix range scan | large id-ordered sweeps |
| every `ssp` table | `sourceId` / `diaSourceId` / `id` | lookups and joins by source id, and by visit — visit is packed into the high bits of the id, so a visit is a contiguous range | cone searches, which scan the table |

**Why `ssp` is id-sorted and not spatial.** Because that is what Solar System
Processing asks of it: give me these sources, by identifier, for these visits.
That access pattern is a range scan on the sort key, which is the fastest thing
this engine does. A spatial ordering would serve cone searches instead and would
not help SSP at all. The spatial columns (`hpix29` and unit vectors) are still
computed and stored, so a cone search is *correct* — it is simply not *pruned*, and
on 18 billion rows that means minutes rather than milliseconds.

This is a per-dataset decision that can change. ClickHouse supports **projections**
— a second physical ordering of the same table — so a dataset that needs both
access patterns can have both, at the cost of storage and ingest time. Today
`ssp` has none because nothing needs them yet; the `mppdb` and `ppdb` tables are
spatially sorted because their use is positional; and `dp2`, when it lands, is
expected to be spatial. If an SSP use case starts needing cone searches at
interactive speed, adding a spatial projection is the intended answer rather than
a redesign.

Practically: use `TOP` while exploring, and prefer `/async` (a UWS job) over
`/sync` for anything that might be slow — async results are spooled and survive a
disconnect.

The pruning works because each table's registry carries a `physical` block binding
`ra`/`dec` to an `hpix29` column and unit vectors, which the query planner turns
into HEALPix ranges. That block is why the catalog is kept as documents and not
only as the VO-standard tables (§6).

## The data

Three databases are served, all physically ClickHouse databases on the one
server. Measured 2026-08-23:

| database | tables | rows | on disk | state |
|---|---|---|---|---|
| `ssp` | 11 served (+2 provenance, 1 view) | 93.77 B | 11.05 TiB | actively growing |
| `mppdb` | 12 | 25.74 B | 2.57 TiB | static since 2026-07 |
| `ppdb` | 3 | 48.70 M | 9.29 GiB | static since 2026-07-25 |
| **total** | | **~119.5 B** | **13.64 TiB** | |

The four largest tables are all in `ssp`: `source_daytime` (43.54 B rows,
4.37 TiB), `source_dp2` (20.66 B, 2.07 TiB), `source_nv` (17.99 B, 3.17 TiB) and
`source_nv_orphaned` (9.17 B, 938 GiB).

**`mppdb`** is the original science import: Rubin DP2 prompt products mapped onto
a *curated* registry generated from the vendored Felis `apdb.yaml` — datatypes,
units, UCDs and descriptions carried from Felis, with `hpix29`/`cx`/`cy`/`cz`
spatial columns, principal flags and foreign keys layered on. The three large DIA
tables were ingested from flat DP2 HATS exports with `mppdb ingest --from-hats`,
which maps columns onto that curated registry rather than generating a schema;
the small tables date to the original manual import. Uniquely, `mppdb` also has a
Parquet lake and manifest chain on `/data` — a legacy of the DuckDB engine era
that still backs snapshot semantics.

**`ppdb`** is a static import of the Rubin Prompt Products Database: a TAP dump of
`data-int.lsst.cloud/api/ppdbtap` loaded with `mppdb ingest-tapdump`.
ClickHouse-only — no lake, no manifests, no GC.

**`ssp`** is the solar-system working set: one table per export directory, each an
`acid import butler --split-by visit` export of per-visit parquet parts with
per-part manifests. Loaded with `mppdb ingest-parquet`, which *generates* the
schema from the parquet, computes the spatial columns from `ra`/`dec`, converts
NaN to NULL, and enforces type fidelity with a safe cast at staging — an
`int64`→`int32` overflow refuses loudly. Three products are actively maintained
(`source_daytime`, `source_nv`, `dia_source_prompt`) and grow by an append on the
export side followed by `ingest-parquet run --append`, an incremental path keyed
on immutable per-part manifests with a provenance ledger (`_ingest_parts`,
`_ingest_refs`) in the served database recording (visit, detector) → part and
maintaining the invariant `count(live) == sum(_ingest_parts.rows)` per table. The
other eight are frozen.

:::{warning}
**Two `ssp` tables are irreplaceable.** `source_nv_orphaned` and
`dia_source_2025lost` are recoveries of data whose Butler artifacts were deleted;
their export directories on `/sdf` are the only durable copies, and
`source_nv_orphaned` alone is 9.17 billion rows. These directories are primary
data, not a cache. See §11.
:::

**Publish mechanics, common to every ingest path:** load into a staging database,
verify counts, publish with an atomic multi-pair `RENAME TABLE`, then record
provenance. Readers never see a missing or half-loaded table. (`EXCHANGE TABLES`
and `CREATE OR REPLACE` are unavailable — `renameat2()` is unsupported on the
WekaFS data path.)

**Cadence:** `mppdb` and `ppdb` are static; `ssp` arrives in bursts — whenever a
nightly append actually appends, plus occasional metadata-only catalog publishes.
Loads run on `sdfiana035` only, because staging goes through the ClickHouse
server's node-local `user_files` directory.

:::{important}
A fourth database, **`dp2`** — thirteen DP2 release products, ~91.4 B rows — is
planned and approved, execution gated on the export completing. Ownership of
`ssp`'s ingest configs, loads and catalog content sits with the ssp-submit
project, not with the service.
:::

## Whether it works: ingest and query at scale

Two things had to be true for this to be useful at all, and both are load-bearing
enough to state with measurements rather than adjectives.

### Ingest must be fast, and incremental

A dataset of tens of billions of rows and tens of terabytes has to land in hours,
not weeks — and then be *extended* without reloading. Both hold today.

| stage | measured |
|---|---|
| parquet exports → ClickHouse (`ingest-parquet`, 64 workers) | **~200 M rows/min**, bounded by WekaFS read bandwidth rather than by ClickHouse |
| the full `ssp` rebuild (93.76 B rows, 11.05 TiB) | ≈ 8 hours of load time at that rate |
| incremental append | only the new per-visit parts are read; cost is proportional to what arrived, not to table size |

The incremental path is what makes the database sustainable rather than a
one-off: an append reads the immutable per-part manifests, loads only parts not
already recorded in the provenance ledger, and refuses rather than guesses if the
ledger cannot account for the table. Loading is therefore a nightly operation, not
a migration.

:::{important}
**Not yet measured here:** the upstream conversion times — Butler repository to
parquet or HATS export — which are the other half of a dataset's journey and are
performed by separate tooling. They should be added; the figures above cover only
the export→database stage.
:::

### Queries must return quickly at that scale

ClickHouse makes a 40-billion-row table interactively queryable when the query
matches how the table is physically ordered. That ordering is a **per-dataset
choice**, matched to how each dataset is actually used — see “What a query can prune on”
under Querying the service, which is the single most useful thing for a user of
this service to understand.

## The catalog store

Since 2026-08-22 the **catalog of record is the `TAP_SCHEMA.registries` table** in
ClickHouse, not files on disk. One row per served database holds the registry
document verbatim as YAML, its sha256, a global monotonic `generation`, and
publication provenance. The five standard TAP_SCHEMA tables (`schemas`, `tables`,
`columns`, `keys`, `key_columns`) are a **pure projection** of the store: every
publish rederives all five from every stored document and swaps them in with one
multi-pair rename.

Two consequences worth stating plainly. There is no longer any window in which a
TAP_SCHEMA table is absent. And an incomplete input can no longer delete a
schema — configuration is not an input to derivation, the store is.

The store keeps verbatim YAML rather than only the five tables because TAP_SCHEMA
is a lossy projection: the registry's `physical` block, which drives the spatial
pruning described in §3, has no column in the VO-standard tables.

**Generation semantics.** The generation is bumped only when the store actually
changes. A byte-identical publish is a store no-op that still rederives the five
tables — which makes `mppdb catalog publish` the documented repair for a damaged
projection.

**Publishing.** `mppdb catalog publish --schema X FILE` runs under an on-node
lock, sha-verifies the store, diffs the incoming document against the stored one,
**refuses any removal without `--allow-remove`**, writes the store with the
rename pattern, and rederives. `catalog diff` and `catalog show` are read-only.
Ingest publishes its own catalog — data first, catalog second, deliberately: a
crash between the steps leaves a loaded-but-unadvertised table, which
re-publishing heals, whereas the reverse order would advertise a table that does
not exist. The upsert is column-grain, so curated `description`/`unit`/`ucd`
values survive a re-ingest.

**How a front-end consumes it.** Under the ClickHouse engine the service loads
its catalog *only* from the store, at startup, refusing to boot on a missing or
sha-mismatched document rather than falling back to anything baked into its
image. At runtime the catalog is one immutable object behind a single reference;
`SIGHUP` rebuilds and rebinds it, **fail-safe** — a bad input keeps the
previously-served catalog, logs loudly, and does not exit. That is deliberately
the opposite of startup, where there is nothing good to keep serving.

`GET /catalog` reports the served generation and per-schema sha256s. It is what
makes both publishing and reloading verifiable rather than inferred, and it is
the basis of the currency check in §9.

## The two deployments

### The backend node

Everything that writes lives on `sdfiana035`, inside an apptainer sandbox whose
rootfs is a writable directory on WekaFS. It is here rather than in Kubernetes for
the reason given in §1: WekaFS reaches the cluster over NFS, which is far too slow
for the data path. The node is a deliberate compromise, not an accident of
history.

**ClickHouse** runs there as a plain daemon. There is no systemd in the
container and **no automatic restart after a host reboot** — bringing it back is a
manual, documented step, including a tmpfs directory that a reboot wipes. Its
`user_files` staging directory is node-local scratch, visible under two names for
one inode, which is why loaders may run inside or outside the container but never
off-node.

**The ingest and catalog tooling** runs from a git checkout at
`/root/projects/github.com/mjuric/mppdb`, kept on `main`, through its editable
venv; `git pull` is how that tooling is updated. This is where
`mppdb ingest-parquet`, `ingest --from-hats`, `ingest-tapdump` and
`mppdb catalog publish` are run, and where their configuration lives:
`deploy/usdf/mppdb.toml` carries the `[databases]` block — the *authorization
boundary* naming which ClickHouse databases are in play — alongside committed
scalars in `mppdb.env` and two git-ignored mode-600 secret files.

**Loads run on this node only**, because staging goes through the ClickHouse
server's node-local `user_files` directory. There are no credentials beyond being
on the node (§11).

### The Phalanx front-end, on usdf-rsp-dev

A front-end-only deployment: it queries the shared ClickHouse read-only and owns
no data. Defined as the `mppdb` application in Phalanx
(`applications/mppdb/`), currently image `ghcr.io/mjuric/mppdb:sha-35bd883`.

| Aspect | How |
|---|---|
| Auth | `GafaelfawrIngress`, scope `read:tap`; the service trusts the ingress-injected username header and provisions an account on first sight |
| Path | `/mppdb`, with the prefix stripped before the pod, which the app compensates for when generating URLs |
| Database credential | `mppdb_ro`, SELECT-only on `mppdb`, `ppdb`, `ssp`, `TAP_SCHEMA` and `system.parts` |
| State | a 20 GiB `wekafs` ReadWriteOnce volume at `/data` |
| Replicas | exactly **one**, `strategy: Recreate` — the state engine is single-writer, and two writers corrupt `state.db` |
| Secrets | hand-created Kubernetes secrets: `mppdb` (the database credential) and `mppdb-pull` (a registry token) |
| Catalog | loaded from the store at startup; refreshed by `SIGHUP`, never by redeploy |

:::{important}
The two secrets are created by hand because this deployment has no Vault access
yet. The chart carries a `useVaultSecret` flag; turning it on produces a
`VaultSecret` of the same name, so the cutover replaces them in place and changes
nothing else.
:::

## Operating the system

### Is it current? Store versus served

The service loads its catalog at startup and holds it until reloaded, so "is the
service serving the current catalog?" is a real question with a cheap answer:
compare what the store holds with what the service reports.

The service's side needs no credentials at all:

```
curl -s https://usdf-rsp-dev.slac.stanford.edu/mppdb/catalog
```

```json
{"generation": 2, "loaded_at": "…",
 "schemas": {"mppdb": "46b4daa8…", "ppdb": "bcc3d3f9…", "ssp": "edfb9aa7…"}}
```

The store's side is one query, answerable with the service's own read-only
database credential:

```sql
SELECT schema_name, generation, substring(sha256, 1, 10), published_at
FROM TAP_SCHEMA.registries ORDER BY schema_name
```

Equal generations and matching digests mean the service is current. A store
generation ahead of the served one means a publish has happened and the service
has not reloaded — which names the problem precisely rather than leaving it to be
inferred from a query that fails. On the backend node, `mppdb catalog show`
reports the same thing with provenance.

### After a catalog change

On the backend node, after any catalog change:

```
mppdb catalog publish --schema <s> <file>    # or an ingest, which publishes itself
mppdb catalog show                           # what the store now holds, with provenance
```

Then make the service pick it up:

```
kubectl -n mppdb exec deploy/mppdb -- mppdb reload --wait
```

`reload --wait` polls a target captured *before* the signal, so a concurrent
publish of another schema cannot fake a timeout, and its exit codes are
load-bearing: **0** landed, **2** refused (stale pidfile, wrong host), **3** timed
out — which means *unknown*, not failed. A refused reload leaves the served
generation unadvanced, so the same poll detects both outcomes.

:::{important}
Reloading is a per-front-end action with no coordinator. A publish makes the
store current; each front-end serves its last-loaded catalog until something
reloads it. Table **removals and renames** are the dangerous case, because a
front-end that has not reloaded advertises tables whose queries now fail while
its catalog still looks healthy — additions merely hide a new table.
:::

### Checking data and the service

After a data load, verify that served rows equal the export's declared rows
exactly, and that the ledger reconciles (`sum(_ingest_parts.rows)` equals the
live count, per table). Before an append, `ingest-parquet run <cfg> --append
--dry-run` shows the disk-versus-ledger delta and `--audit` finds torn parts and
stale run markers.

Row counts and sizes come from ClickHouse metadata, not from scanning:

```sql
SELECT database, formatReadableQuantity(sum(rows)),
       formatReadableSize(sum(bytes_on_disk))
FROM system.parts WHERE active GROUP BY database
```

The web console's sidebar uses exactly this, which is why the read-only user
needs `SELECT` on `system.parts`; without it the sidebar silently falls back to
showing column counts instead of row counts.

## Maintaining and upgrading

**The backend tooling** updates with `git pull` in its checkout; there is no
service there to restart, only the ClickHouse daemon, which is left alone.

**The service** upgrades by building an image from its branch, pinning
the `sha-<commit>` tag in `values-usdfdev.yaml`, and syncing the Argo CD
application. Two rules learned the hard way:

1. **Verify the mechanism, not just the result.** A configuration flip that
   silently does nothing can look exactly like success when the before and after
   states are identical. Confirm the mechanism directly — the config value inside
   the running pod, the log line, the `/catalog` generation — rather than
   inferring it from output that would match either way.
2. **Render a chart template before pushing it.** The repository's linters do not
   render Helm templates, so a structurally broken template passes lint and fails
   only at deploy.

**Dependency pins.** The container build pins third-party artifacts by checksum
against **per-release** URLs. A pin against an unversioned "latest" URL breaks on
every upstream release and teaches operators to ignore it; a pin against an
immutable URL means a mismatch is a real signal. When a pin must be bumped, two
independent fetches agreeing is the minimum evidence, and the reasoning belongs in
a comment for whoever bumps it next.

## Troubleshooting

Symptoms that have actually occurred, with causes and fixes.

A front-end advertises tables whose queries fail
: Its catalog predates a retirement. Compare the two `/catalog` outputs (§9.1),
  then `mppdb reload --wait`. The catalog looks healthy throughout, which is what
  makes this one nasty.

`mppdb reload --wait` reports a timeout (exit 3)
: Unknown, not failed — the reload may have landed. Check `/catalog`. On a
  deployment where a proxy strips a URL prefix, older builds polled the wrong
  local URL and always timed out; `--url http://127.0.0.1:8080` is the
  workaround, fixed upstream.

The service refuses to start, complaining about the catalog
: Under the ClickHouse engine there is no fallback: a missing, empty,
  unparseable, duplicated or sha-mismatched document aborts startup by design.
  Publish the catalog, then start.

Async queries fail in the browser with a network error
: The job URL was minted as `http://` while the page is `https://`, and the
  browser blocks the cross-scheme request. Behind a TLS-terminating proxy the
  service must be told to pin its advertised base URL rather than derive it from
  the request.

The web console loads but its assets 404
: The service is hosted under a URL prefix it does not know about. It must be
  told its public base URL so that generated asset and API paths carry the
  prefix.

Newly added UI files return 401 until a restart
: The anonymous-asset allowlist is read at startup. Restart in the same breath as
  any deploy that adds served UI files.

HTTP 403 fetching a file that is listed
: A permissions mismatch rather than a missing file — the serving process is not
  in the group the mode assumes.

`FILE_DOESNT_EXIST` deep inside a load
: The wrong `--user-files-root`. The CLI default matches neither the stock
  ClickHouse path nor this deployment's.

An `--append` refuses
: The provenance ledger cannot account for the table. Never force it; re-import
  wholesale.

## Known gaps

Ordered by how much they should worry a new owner.

1. **No durability story.** One node, one ClickHouse server, no replication, no
   backups of 13.64 TiB. For most tables the source parquet is a de facto
   backup — but `ssp.source_nv_orphaned` and `ssp.dia_source_2025lost` exist
   nowhere else than their export directories on `/sdf`, and those directories
   are irreplaceable primary data. Promotion requires a real backup commitment
   for them specifically.
2. **The backend does not survive a host reboot unattended.** ClickHouse must be
   started by hand, in a documented order, including a tmpfs directory a reboot
   wipes. Until it is, every query fails — the service comes back on its own but
   has nothing to read.
3. **NetworkPolicy is not enforced** on the `usdf-rsp-dev` cluster. The Phalanx
   front-end trusts an identity header injected by its ingress, which is only
   safe if nothing else can reach the pod; a NetworkPolicy is applied and
   correct, but an in-cluster pod was able to bypass it and forge the header.
   External access remains gated by Rubin SSO, so the exposure is in-cluster
   pod-to-pod — acceptable for a dev deployment, and a blocker for production.
4. **The node-trust model.** ClickHouse's `default` user — no password, restricted
   to the node — is the write credential for everything including the catalog
   store. Being on the node *is* the credential. That is a deliberate pilot
   decision, and it means any on-node process can write production data; it has
   already caused two incidents in testing, both caught and repaired the same
   day. Promotion requires real accounts for writers.
5. **Secrets are hand-created** on the Phalanx side, pending Vault access.
6. **A single service replica.** `state.db` is single-writer by design, so the
   service does not scale horizontally without upstream work.
7. **Catalog updates are partly manual.** `ingest-tapdump` does not publish to the
   store, so `ppdb` catalog updates are hand-run; `catalog publish` has no
   compare-and-set staleness guard, so a stale local document can silently revert
   curated metadata, mitigated today by operator convention.
8. **Single-operator knowledge.** The runbooks in the mppdb repository are good,
   but this note is the first document an outside operator could start from.

## What promotion to production requires

Not yet done, and listed as a handover checklist rather than a plan of record.

1. **A performant data path inside Kubernetes**, which is what would allow the
   whole system — ClickHouse, ingest and service — to become a single Phalanx
   application instead of a cluster front-end talking to a hand-kept node. Every
   other item on this list is smaller than this one, and several of them
   (unattended restart, node-trust, secret management) would disappear with it
   rather than needing separate solutions. It is not an mppdb work item: it needs
   WekaFS reachable from the cluster at native speed rather than over NFS.
2. **A durability commitment**, starting with the two irreplaceable `ssp` export
   directories, then a backup or replication story for the served databases.
3. **Unattended restart**: supervised startup of ClickHouse, the service and the
   ingress path, in the right order, without a human.
4. **Real ClickHouse accounts for writers**, so that presence on the node is no
   longer a credential.
5. **Confirm NetworkPolicy enforcement** on the hosting cluster, or replace the
   front-end's header trust with token verification it performs itself.
6. **Vault-managed secrets** on the Phalanx side, replacing the hand-created ones.
7. **Assign an owner**, and record the owning team and contact channels — this
   note, and the service pages, should name them.
8. **Alerting**: nothing currently reports a failed ingest, a stale service
   catalog, or a service that did not come back after a reboot.
