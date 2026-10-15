# OpenAlex Work IDs as a stable primary identifier — documented findings

Research date: **2026-10-14** (timezone Asia/Shanghai). API version observed: `17.1.0`.

## Source note (important)

`https://docs.openalex.org/...` no longer serves content — it **cross-origin redirects to `https://help.openalex.org`**, which is now the canonical docs site (rewritten structure: `/data/`, `/api/`, `/access/`, `/how-to/`, `/tutorials/`).

Every page also has a raw-markdown sibling: append `.md`. There is a plain-text page index at
`https://help.openalex.org/llms.txt`. I downloaded **all 125 pages** listed in that index into
`D:\Files\Project_Paper\_openalex_docs\all\` and grepped them as a set, so "not documented" claims below
mean "not present in any page the official index lists".

The previous official docs repo, `github.com/ourresearch/openalex-docs`, is **archived** (`archived: true`,
last push **2026-05-04**). It is still useful as the pre-migration historical record.

Local evidence copies: `D:\Files\Project_Paper\_openalex_docs\` (per-page) and `...\all\` (all 125 pages),
plus `RELEASE_NOTES.txt`, `old__work-object.md`, `old__faq.md`.

---

## 1. Which identifier fields appear in a Work's `ids` object?

**Exactly five keys: `openalex`, `doi`, `mag`, `pmid`, `pmcid`. There is no `ids.arxiv`.**

Source: <https://help.openalex.org/data/works/attributes/>
> "### `ids`
> *Object.* All known external identifiers for this work, as URIs where possible; keys for unknown IDs are omitted. Work-specific keys: `openalex`, `doi`, `mag` (Microsoft Academic Graph integer ID), `pmid` (PubMed), `pmcid` (PubMed Central)."

The generic definition adds that keys with no value are omitted:
<https://help.openalex.org/data/common-attributes/>
> "`ids` — *Object.* All known identifiers for this entity, expressed as URIs where possible, keyed by namespace — always `openalex`, plus whichever external IDs apply to the entity type (`doi`, `orcid`, `ror`, `issn_l`, `wikidata`, `mag`, …). Keys with no known value are omitted."

### arXiv is represented elsewhere, never in `ids`

| Where | Detail | Source |
|---|---|---|
| `indexed_in` | list value `arxiv` alongside `crossref`, `doaj`, `pubmed` | <https://help.openalex.org/data/works/attributes/#indexed_in> |
| Index entity | `https://openalex.org/indexes/arxiv` | <https://help.openalex.org/data/indexes/> |
| `locations[]` / `primary_location.source` | arXiv is source `S4306400194` ("arXiv (Cornell University)"), `type: repository` | <https://help.openalex.org/data/sources/> |
| `locations[].merge_key.arxiv` | "The keys used to match this location into a work: `doi`, `arxiv`, `pmid`, `title_author`." | <https://help.openalex.org/data/locations/> |
| arXiv DOI | `10.48550/arxiv.*` (a DataCite DOI) appears as a location's `doi`, not as an `ids` key | <https://help.openalex.org/data/works/dois/> |

**Live API check (2026-10-14)** — `https://api.openalex.org/works/W2146292423?select=id,ids,doi,type,indexed_in`:
```json
{"id":"https://openalex.org/W2146292423",
 "ids":{"openalex":"https://openalex.org/W2146292423","doi":"https://doi.org/10.1109/mcse.2011.37","mag":"2146292423"},
 "doi":"https://doi.org/10.1109/mcse.2011.37","type":"article",
 "indexed_in":["arxiv","crossref","datacite"]}
```
This work **is** indexed in arXiv and carries an arXiv location, yet its `ids` object has no `arxiv` key — direct
confirmation that arXiv presence does not populate `ids`.

### Has this changed recently? No — no documented change in ~3.5 years

- Current docs (above): five keys.
- Final archived version of the previous docs, `api-entities/works/work-object/README.md` (repo archived
  2026-05-04): same five — `doi`, `mag`, `openalex`, `pmid`, `pmcid`.
  <https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/README.md>
- **Oldest available revision** of that section (commit `4feaaed`, **2023-03-15**): same five. I verified this
  by fetching the file at that commit SHA; there is no `arxiv` key in the `ids` section at any revision.
- `RELEASE_NOTES.txt` (<https://openalex.s3.amazonaws.com/RELEASE_NOTES.txt>) has **no entry** mentioning
  `ids` fields or an arXiv identifier (it is quarterly, high-level).
- <https://help.openalex.org/api/deprecations/> does not mention `ids` or any arXiv ID field.

**Conclusion: `ids.arxiv` has never existed.** The arXiv identifier is only available indirectly — via
`indexed_in`, via the arXiv location's `id`/`native_id` (`pmh:oai:arXiv.org:…`), or via the arXiv DOI.

---

## 2. Are Work IDs stable over time? Merges, splits, redirects

### The docs contradict each other, and the "redirect" pages are wrong in practice

**Claim A — redirect / never break** (four pages):
- <https://help.openalex.org/data/common-attributes/#id>
  > "The canonical OpenAlex ID for this entity… **Stable; if the entity is later merged into another, this ID permanently redirects to the survivor.**"
- <https://help.openalex.org/data/#merged-entities>
  > "one ID becomes canonical and the other becomes a *merged-away* ID that permanently redirects to it. Requesting a merged-away ID returns the canonical entity, **so old IDs never break**."
- <https://help.openalex.org/api/get-single-entities/#merged-entities>
  > "When duplicate entities are merged, requesting the old ID returns a `301` redirect to the new one" … "Most HTTP clients follow redirects automatically, so you will receive the merged entity's data without extra handling." (Its worked example is an **author**: `A5092938886` → `A5006060960`.)
- <https://help.openalex.org/api/errors/>
  > "| `301` | Moved Permanently | Entity was merged; follow the redirect |"

**Claim B — 404, no redirect** (one page, and it matches reality):
- <https://help.openalex.org/access/sync/#deletions-and-merged-entities>
  > "**In the API:** a deleted or merged-away ID returns **404**. There is no redirect to the surviving record. (Special case: works of removed author profiles point to the null author `A9999999999`, and `A5317838346` marks deleted authors.)"

### Empirical test (2026-10-14) — Claim B wins

I pulled real deleted Work IDs from the actual deletion log and queried the live API:

- Source file: `https://openalex.s3.amazonaws.com/data/jsonl/works/deleted_ids.csv.gz`
  (first rows: `work_id,deleted_date` / `https://openalex.org/W10106787,2026-08-14` …)
- `W10106787`, `W11221645`, `W1325228`, `W2201389`, `W2221864982`, `W2293987438`, `W2428370538`,
  `W2468956189`, `W16008349`, `W166544274` → **all HTTP 404**, no `Location` header.
- Control `W2741809807` → HTTP 200.
- **The docs' own 301 example** `https://api.openalex.org/authors/A5092938886` → **HTTP 404**, no `Location` header.
- (Separately: the ID used in the docs' own `deleted_ids.csv.gz` *illustration*,
  `W4245566371`, returns **HTTP 200** — it is an illustrative example, not a real deletion. Do not use it as a test case.)

**Bottom line for the user's pipeline: do not rely on Work ID redirects. Treat a merged-away or deleted Work ID as dead.**

### What the docs tell clients to do

- Deletion log for works: `deleted_ids.csv.gz` at `{format}/works/deleted_ids.csv.gz` in each snapshot, two
  columns (`work_id`, `deleted_date`), cumulative, "about 160 MB compressed… tens of millions of rows".
  In every daily snapshot since 2026-08-15; in the free public bucket since the **2026-09-23** release.
  > "**Deletion is final.** A deleted ID stays deleted, and work IDs are never reused — a deleted ID will never point to a different work."
- **Merge trail** — trace a merge via location IDs:
  > "When two works are found to be duplicates and merged, the surviving work absorbs the other's locations. Each location carries an internal `id`… and that handle survives the merge." Table: "Work A in `deleted_ids.csv.gz`, and A's location `id`s now appear under work B → A was **merged into** B — repoint anything referencing A to B"; "…appear nowhere → A was **removed**".
- **Spot a merge before it lands:** "if two live records share the same DOI (works)… the one with the more recent `updated_date` is the canonical record that keeps receiving updates; the other has stopped updating and is on its way out."
- **No merged-ID → survivor mapping exists today:**
  > "The pre-Walden snapshot published a `merged_ids/` directory mapping merged IDs to their survivors. That mechanism ended with the 2025 Walden cutover (historical files preserved under `legacy-data/`, not updated). `deleted_ids.csv.gz` is its successor for **works** deletions, with location IDs providing the merge trail. What does **not** exist today: a merged-ID → survivor mapping, and deletion logs for authors, sources, institutions, or any other entity type."
- API sync filters surface nothing for dead IDs: "deleted or merged-away records just stop appearing (and their IDs 404), so reconcile your ID set against a snapshot periodically if deletions matter to you."
  All from <https://help.openalex.org/access/sync/#deletions-and-merged-entities> and
  <https://help.openalex.org/access/sync/#tracing-merges-location-ids-move-to-the-surviving-work>.

### Historical contrast (shows the rule changed)

Archived FAQ, <https://github.com/ourresearch/openalex-docs/blob/main/additional-help/faq.md>:
> "### Are OpenAlex IDs stable?
> Yes!\* The work associated with ID W1234 will keep the ID W1234.
> When we find duplicated works, authors, etc that already have assigned IDs, we merge them. **Merged entities will redirect to the proper entity in the API.** In the data snapshot, there is a directory which lists the IDs that have been merged.
> \*In July 2023, OpenAlex switched to a new, more accurate, author identification system, replaced all OpenAlex Author IDs with new ones. This is a very rare case in which we violate the rule of having stable IDs…"

So: pre-Walden the merged-ID directory and redirects existed; since the 2025 Walden cutover, works deletion/merging is
handled by `deleted_ids.csv.gz` + 404 + location-ID trail.

**Splits:** the docs discuss merges in detail but **do not describe a work-split mechanism or its ID
consequences** — not documented. (Release notes mention author-profile *splits* for authors, e.g. 2026-05-22
"continued cleanup of overmerged authors: 2.6M splinter profiles merged across 7 phases, affecting ~5M works" —
that is authors, not works.)

**Not verified / not documented:** whether the November 2025 Walden cutover re-minted Work IDs. The docs
describe the expansion corpus being "minted once, at the Walden cutover" and one `ingested_at` spike on
`2025-01-16`, but make **no statement** that pre-existing Work IDs survived (or did not survive) the cutover.
Anyone holding work IDs from before the cutover should verify them rather than assume.

---

## 3. arXiv preprints and versions

### One Work per paper — the preprint and the published article are the SAME Work

<https://help.openalex.org/data/works/#how-locations-become-works>:
> "OpenAlex starts from locations: copies of a work hosted at particular places. **A preprint on arXiv is one location; the same paper published in *Physical Review B* is a second; the accepted manuscript in a university repository is a third. The job is to recognize that all three are one work.**"
> "**Persistent identifiers.** If a location carries a DOI, PubMed ID, or arXiv ID that OpenAlex already knows, it belongs to that work. This is the certain path: **a shared PID is treated as proof.**"

<https://help.openalex.org/data/works/dois/> — worked example:
> "A paper posted to arXiv gets an arXiv DOI (`10.48550/arxiv.1102.1523`), then a new one when a journal publishes it (`10.1109/mcse.2011.37`). **Both point to the same work**, *The NumPy Array* (`W2146292423`)."

Live confirmation — `W2146292423` has 5 locations including
`pmh:oai:arXiv.org:1102.1523` (source arXiv, `version: submittedVersion`, `is_published: false`) **and**
`doi:10.1109/mcse.2011.37` (journal, `version: publishedVersion`), and its `type` is `article`.
A preprint DOI therefore finds the published work: `/works?filter=doi:10.48550/arxiv.1102.1523` returns `W2146292423`.

### The linking field is `locations`, at the location level — not a work-level "preprint-of" field

- `version` is an attribute of **locations**, not of works:
  <https://help.openalex.org/data/locations/#one-location-per-version>
  > "Each location carries a `version` field recording which of these it holds, following the DRIVER Guidelines standard: `publishedVersion` (most authoritative), `acceptedVersion`, then `submittedVersion`."
- `primary_location.version` is the work-level pointer: "Applies to the `primary_location` version; other `locations` may be earlier."
  <https://help.openalex.org/data/works/attributes/#publication_date>
- Convenience flags on each location: `is_accepted` ("`version` is `acceptedVersion` or `publishedVersion`"),
  `is_published` ("`version` is `publishedVersion`").
- Work type follows primary location: `preprint` = "An article whose primary location is a preprint repository such as arXiv, bioRxiv, medRxiv, SSRN, or Research Square."
  <https://help.openalex.org/data/work-types/>
- **There is no `is_preprint_of` field.** My regex search for `is_preprint_of|preprint_of` across all 125 official
  pages returned **zero matches**. There is no documented work-to-work preprint→published link field.
- **`related_works` is NOT that link.** It is defined as "OpenAlex IDs of algorithmically related works —
  recent papers sharing the most topics with this one."
  <https://help.openalex.org/data/works/attributes/#related_works>
  Do not use it to pair a preprint with its journal version.

### arXiv version numbering (v1, v2, …) — **not documented**

No page in the official index mentions arXiv version numbering, and no version suffix appears in any arXiv
location identifier. The documented location-ID form is per-paper:
> "`pmh:oai:arXiv.org:cond-mat/0404022` (an OAI-PMH record id, for repository copies)" — <https://help.openalex.org/data/locations/#id>

Live confirmation: the arXiv location on `W2146292423` is `pmh:oai:arXiv.org:1102.1523` with a bare
`native_id` (`oai:arXiv.org:1102.1523`) — no `v2`/`v3` suffix, and the work held exactly **one** such arXiv
location, alongside a separate DataCite arXiv-DOI location.

**Inference (labelled as such, not documented):** because arXiv's OAI-PMH identifier is per-paper and a shared
PID is "treated as proof" of one work, arXiv versions collapse onto one location / one Work rather than one
Work per version. The docs never state this explicitly, so treat it as a working assumption to verify on your
own sample, not as a citable fact.

---

## 4. Documented coverage — sources, and what is not covered

### Ingest sources

<https://help.openalex.org/data/how-its-built/#gather>:
> "The catalog was seeded by the discontinued Microsoft Academic Graph (MAG)… The core inputs we pull from today include:
> - [Crossref](https://www.crossref.org/) and [DataCite](https://datacite.org/), the DOI registration agencies
> - [ORCID](https://orcid.org/) and [ROR](https://ror.org/)…
> - [PubMed](https://pubmed.ncbi.nlm.nih.gov/) and [PubMed Central](https://www.ncbi.nlm.nih.gov/pmc/)
> - [DOAJ](https://doaj.org/) and [the ISSN International Centre](https://www.issn.org/)
> - The [Internet Archive](https://archive.org/details/GeneralIndex)
> - Aggregators and subject repositories: HAL, arXiv, Zenodo, Dergipark, OSTI, RePEc, and many more
> - Thousands of institutional repositories…
> - Parsing of 50M+ open access PDFs, journal landing pages, and direct publisher feeds
> - Corrections from users through community curation"

**Tracked "indexes" — exactly 5** (<https://help.openalex.org/data/indexes/>): `crossref`, `pubmed`,
`datacite`, `doaj`, `arxiv`. Repositories: "roughly 7,000 repository sources"
(<https://help.openalex.org/data/sources/repositories/>). Corpus sizes: core **320M+**, expansion **~190M**,
all **510M+** (<https://help.openalex.org/data/works/corpus/>).

### Explicit "no quality bar" — and the only non-English/Global South statement

<https://help.openalex.org/data/sources/#no-quality-bar-by-design>:
> "OpenAlex deliberately does not impose a quality bar on which sources it indexes — **its inclusion criteria are more like arXiv than Web of Science.** There are good reasons to index everything… and **excellent work is too often excluded from traditional indexes merely for being non-English or from the Global South.** … 'lower-quality' content can always be *filtered out* if it's included — it can't be added back if it's not."

This is a design rationale, **not** a coverage guarantee for specific languages or regions.

### Chinese-language literature: CNKI / Wanfang / VIP — **not documented as covered, and not documented as excluded**

I grepped all 125 official pages for `CNKI|Wanfang|维普` (and case-insensitive variants): **zero matches.**
- CNKI, Wanfang, and VIP are **not named anywhere in the OpenAlex docs** — neither as ingest sources nor in any
  coverage-limitation statement. There is no documented coverage statement either way.
- What *does* exist for China is journal **quality lists**, used as `listed_in` flags on sources, not as ingest
  pipelines (<https://help.openalex.org/data/source-lists/>):
  - `dongbi-a`/`-b`/`-c`/`-d` — "Dongbi Index", Dongbi Technology Data with the Chinese Academy of Medical
    Sciences; ~700 / ~2,900 / ~6,500 / ~2,500 sources; 2026-10-09 edition.
  - `ccf-a`/`-b`/`-c` — China Computer Federation recommended journals.
- Chinese is a supported detected language (`zh`), one of 180 languages
  (<https://help.openalex.org/data/languages/>).

**Correction to a common assumption, stated plainly: OpenAlex does not harvest CNKI/Wanfang/VIP.** That is an
inference from their total absence from the documented ingest paths plus the documented "get indexed" route —
labelled inference, since the docs never say so explicitly.

### The documented route to coverage (and its implication)

<https://help.openalex.org/how-to/getting-indexed/>:
> "OpenAlex indexes works… by harvesting the big scholarly indexes: Crossref, DataCite, PubMed, and others, plus thousands of repositories. So 'getting indexed' means getting your works into a place we already harvest."
> "For most journals that means registering DOIs through Crossref (or DataCite…)."

So a Chinese-language journal that registers DOIs with Crossref is covered; one that does not, and is not in a
harvested repository, has no documented path in.

**Explicit caveat about non-DOI coverage: not documented.** There is no page quantifying coverage of non-English
or non-DOI works. The nearest statements are the "no quality bar" rationale above and the no-DOI minting rule in §5.

---

## 5. Do Works with no DOI get OpenAlex IDs? Yes.

<https://help.openalex.org/how-to/repositories/#what-if-my-records-dont-match-any-work>:
> "Unmatched repository records aren't gone. **Any record with a DOI or a usable title is minted as a work of its own, with your repository as its only source**, and it sits in the core corpus like any other work."

<https://help.openalex.org/data/works/#how-locations-become-works>:
> "If nothing matches, the location seeds a new work of its own; **the only locations that don't are ones with neither a PID nor a usable title, which are dropped.**"

So an arXiv-only preprint (which nowadays usually *does* have a DataCite arXiv DOI anyway), a thesis, a
technical report or a dataset record gets a `W…` ID as long as it has a usable title. Work types confirm these
are first-class: `dissertation` ("A document submitted in completion of an academic degree or professional
qualification, such as a PhD or master's thesis"), `report` ("A technical report or working paper issued outside
the journal system… technical report, white paper, or government report"), `dataset`, `software`, `standard`,
`preprint` (<https://help.openalex.org/data/work-types/>).

### Documented caveats about such records

1. **They can be merged away later, and the old ID then 404s** — see §2. The whole merge/deletion apparatus
   applies to them.
2. **Some sit in a lower-quality, frozen corpus.** The expansion corpus: "~190M works added in the Walden update
   (primarily datasets and single-repository records)… **Data quality is lower but improving, and the set is
   static: no works have been added to it since the cutover.**" It is **excluded from API results by default**;
   include with `corpus=all`.
   > "many are bare repository or DataCite records with little more than a title and an identifier."
   <https://help.openalex.org/data/works/corpus/>
3. **A repository-minted DOI creates a duplicate instead of merging** — a documented failure mode:
   > "**If you mint your own DOIs for deposits of already-published papers, put the publisher's DOI in the record too.** A record whose only DOI is a repository-minted one looks to us like a different paper, so **it is minted as a separate work** instead of becoming a location on the published one."
   <https://help.openalex.org/data/sources/repositories/#matching-by-doi>
4. **No ID reuse, ever:** "A deleted ID stays deleted, and work IDs are never reused."
   <https://help.openalex.org/access/sync/#the-works-deletion-log-deleted_idscsvgz>

**Not documented:** whether such records are systematically re-keyed. Only the general merge/404 behavior is documented.

---

## 6. Refresh lag for arXiv content

**An arXiv-specific lag figure is NOT documented.** No page states how long after an arXiv posting the Work
appears. What *is* documented:

- **Repositories are harvested daily**, and arXiv is a repository-type source (`S4306400194`, type `repository`):
  <https://help.openalex.org/data/sources/repositories/#how-harvesting-runs>
  > "**Daily, every endpoint.** One run each day walks every registered endpoint (roughly 5,000 of them). There is no tiering or priority… a failing endpoint is retried every day until it works."
  > "**Incremental, resuming where it left off.** Each run asks for records changed since that endpoint's last successful harvest, overlapping by one day so nothing at the boundary is lost. Because we ask by datestamp, a deposit whose datestamp is in the past is not 'new' to us."
- **Deposit-to-visible latency (repository pipeline, generically):**
  <https://help.openalex.org/how-to/repositories/#how-often-do-you-harvest-and-how-long-until-a-new-deposit-shows-up>
  > "Daily. One run checks every registered endpoint for records changed since its last successful harvest (re-overlapping by a day so nothing at the boundary is missed). **A deposit made today is normally in OpenAlex within a day or two of harvest, once it has been parsed and matched.** A newly registered repository's first run fetches the entire back catalog in one pass; for a large or slow endpoint that can take days."
- **DOI-registrar ingest is continuous:**
  > "OpenAlex pulls records from these sources continuously — the database evolves daily." — <https://help.openalex.org/data/how-its-built/>
  and "over 320 million works in OpenAlex, with **tens of thousands added every day**" — <https://help.openalex.org/data/works/>
- **The end-to-end pipeline refreshes once per day:**
  > "Curations apply on the next end-to-end refresh of the pipeline, which runs once per day. So changes should appear within 24 hours." — <https://help.openalex.org/api/author-curation/>

**Practical reading (inference, labelled):** arXiv reaches OpenAlex by at least two paths — the OAI-PMH
repository harvest (daily, "within a day or two") and DataCite DOI registration for `10.48550/arXiv.*` DOIs
(continuous) — so expect roughly **1–2 days**, but the docs give no arXiv-specific SLA and no committed figure.
If you need a number for a paper, measure it (compare a work's arXiv location `ingested_at` against its
`publication_date`) rather than citing the docs.

---

## Pricing / quota model — user's premise confirmed, with two corrections

**Confirmed: the polite pool is gone.**
<https://help.openalex.org/api/deprecations/#historical-polite-pool>:
> "Before February 2026, OpenAlex used a 'polite pool' system where you could get higher rate limits by including `mailto=you@example.com` in your requests.
> **This system has been replaced by API keys.**
> - The `mailto` parameter is ignored
> - All users need an API key (free)"

**The model is a per-request credit/USD budget, not per-request invoicing.**
<https://help.openalex.org/access/pricing/>:
> "every account gets **$1 of API usage per day for free**… That's enough for most research and personal projects. The budget resets every midnight UTC"
- Pay-as-you-go prepaid usage in $1 increments, drawn down after the free daily budget, expiring 3 months
  after the most recent purchase.
- Annual plans: **Member $5,000/yr** ($20/day budget), **Member+ $10,000/yr** ($100/day + daily snapshot),
  **Partner from $20,000/yr** ($200+/day).

**Rate limits** (<https://help.openalex.org/api/authentication/#rate-limits>): 10 req/s with no key, 30 req/s
with a free key or prepaid usage, 50 req/s on an annual plan. Responses carry `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Credits-Used`, `X-RateLimit-Reset`; `meta` reports the cost of the call.

### Two corrections to "per-request billing"

1. **Some requests are free.** Single-entity GET is documented "**Cost:** Free"
   (<https://help.openalex.org/api/get-single-entities/>), and
   > "Error responses are free. Any `4xx` or `5xx` response costs 0 credits, and its `X-RateLimit-Credits-Used` header reports `0`."
   <https://help.openalex.org/api/errors/>
2. **A key is not strictly mandatory at low volume, despite the deprecations page.** <https://help.openalex.org/api/authentication/>
   > "OpenAlex data is free, and so is casual use of the API — you can make basic queries with no key at all."
   The keyless budget is **$0.10/day** (<https://help.openalex.org/access/example-costs/>) and is shared per IP.

### Observed live (2026-10-14) — corroborates the model

After ~15 anonymous requests I received HTTP 429 with:
```json
{"error":"Rate limit exceeded",
 "message":"Insufficient budget. This request has no API key, so it counts against the free daily budget
            shared by everyone on your network's IP address, and that budget is used up ($0 remaining;
            resets at midnight UTC). …",
 "costUsd":0.0001,"dailyRemainingUsd":0,"prepaidRemainingUsd":0,
 "creditsRequired":1,"creditsRemaining":0}
```
Note `costUsd: 0.0001` per request and the **per-IP shared** keyless budget — a practical hazard for shared
networks/CI.

---

## Explicitly flagged as unverified

| Item | Status |
|---|---|
| arXiv v1/v2 → separate Works? | **Not documented.** Inference only (§3). |
| Work IDs survived the Nov 2025 Walden cutover? | **Not documented.** Verify any pre-cutover IDs. |
| Work "split" mechanism and its ID consequences | **Not documented** (only author-profile splits appear, in release notes). |
| arXiv-specific ingestion lag in days | **Not documented.** Nearest statements are repository-generic (§6). |
| CNKI / Wanfang / VIP coverage | **Not documented either way**; absent from all 125 official pages. |
| Any explicit non-English / non-DOI coverage statistic | **Not documented.** |
| Doc pages claiming 404 vs 301 | **Contradictory**; live API matched the 404 page on 2026-10-14 (§2). |
| Whether `W4245566371` (docs' deletion-log example) is a real deletion | It returns HTTP 200 — treat the docs' example as illustrative. |
