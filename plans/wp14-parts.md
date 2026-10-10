# WP14: parts, datasheets and extraction

Status: scoped, not started. Branch `wp14-parts`.

## Goal

Turn a part number into a verified, page-referenced part file that pcbkit can build
KiCad files from and that the checks can read, without hand-copying numbers out of PDFs.
Cache everything that can be fetched or recomputed, and measure the extraction instead of
trusting it: Haiku against Sonnet for reading, and Haiku against plain heuristics for
finding the right pages.

## Decisions so far

- **Our format is the source of truth.** A part file holds everything we know about a
  part; the KiCad symbol and fields are generated from it. The format holds nothing
  KiCad-specific (no `lib:Name` strings, no KiCad pin-type names as the canonical
  values), so other outputs stay possible, but only the KiCad output is built.
- **Footprints are named, not derived.** A part file names its package
  (`SOIC-8_3.9x4.9mm_P1.27mm`) and the output uses KiCad's stock footprint. Geometry is
  described only for a part with no standard package. Land patterns are IPC-7351 work
  that KiCad's own generator and librarians already do.
- **An independent cross-check stays.** A symbol generated from our pinout matches our
  pinout by construction, so where KiCad has a stock symbol or footprint for the part,
  `verify` compares ours against it.
- **Same repo, for now.** The library lives in `parts/`. Part files hold public datasheet
  facts, not board content; CLAUDE.md gets a line saying so.
- **Haiku 5.5 for extraction, if the numbers hold.** Each call is short and bounded, so
  it should stay well under 100k tokens. The eval decides.

## Pipeline

| Step | Who | What |
|---|---|---|
| `pcbkit parts lookup <MPN>` | code | Mouser Search API by part number: manufacturer, datasheet URL when Mouser has one, lifecycle, RoHS, stock, price breaks, lead time, suggested replacement. Key from `MOUSER_API_KEY`, never `pcbkit.toml`. |
| `pcbkit datasheet fetch <MPN> [url]` | code | Download the PDF; reject a landing page that is not a PDF; extract text and render page images per page; record URL, date and sha256. The URL comes from Mouser when it has one, else from the manufacturer's product page or the user. |
| Retrieval | code or model | Pick the pages each section needs (pinout, absolute max, operating conditions, ...). Method chosen by the retrieval eval below. |
| Extraction | model | One call per section with only its pages; writes into the part file. Every value carries its page and a short verbatim quote. |
| `pcbkit datasheet verify` | code | Schema; each quote appears on the page it cites; pinout and package against KiCad stock where it exists; a newer datasheet revision seen at the same URL. Values read off a figure have no quote: they are marked "read from figure N" for a person to check. |
| KiCad output | code | `.kicad_sym` from the pinout, with `Datasheet`, MPN and manufacturer fields filled in. Also fix `pcbkit/sch.py:422`, which writes `Datasheet` as `"~"` today. |

## Caching

| Layer | Key | Lifetime | Where |
|---|---|---|---|
| Mouser answers | MPN | Stable fields kept; stock, price and lead time refetched past an age limit (24 h to start). Each answer keeps its fetch date. | `~/.cache/pcbkit` |
| Datasheet PDFs | sha256 | Forever. A URL-to-hash map with dates shows when a URL starts serving a new revision. | `~/.cache/pcbkit` |
| Page text and images | (PDF hash, extractor, extractor version) | Until the extractor changes. | `~/.cache/pcbkit` |
| Part files | (PDF hash, schema version) | Not a cache: reviewed data, never thrown away or regenerated unasked. | `parts/` in git |

A board vendors the part files it uses, each with the PDF hash it came from, like a
lockfile. The board then builds, checks and passes CI with no network, no Mouser key and
no access to the library. Commands read the cache first and touch the network only on a
miss or stale stock; CI never calls Mouser. Confirm Mouser's rate limits from their docs
before relying on a number.

## Part file, first version

- Provenance: MPN, manufacturer, datasheet title, revision and date, URL, sha256.
- Package name, per orderable variant.
- Pinout per package: number, name, electrical type (power in, power out, input, output,
  bidirectional, open drain, passive, not connected), exposed pad.
- Absolute maximum ratings and recommended operating conditions, kept apart: value,
  min/typ/max, unit, conditions, page, quote.

Later: key electrical characteristics (logic thresholds against supply, drive, I2C
addresses), thermal (θJA, derating points from curves), reference implementations (below),
layout guidance, assembly (MSL, reflow, polarity marking).

## Reference implementations

Most datasheets show one or more typical application circuits, and an evaluation board
manual often has a full schematic and layout. They are the most useful thing to copy
when designing a part in, so the part file captures each one:

- what it is for and the conditions it was designed for (input range, output, load);
- the parts by role (input capacitor, feedback divider top, ...), each with value,
  rating, package where given, and the page and quote or figure it comes from;
- the connections, as a small netlist between the part's pins and those roles;
- the formulas that set the values (feedback divider, inductor, current sense), so a
  change of conditions can recompute them instead of copying numbers;
- the layout notes that go with it, and the figure number for a person to look at.

Outputs: a pcbkit design helper that adds the circuit to `design.py` with values worked
out for the board's conditions, and possibly a KiCad 9 design block. A check can then
compare a board's circuit around the part with its reference and report the
differences. Reading a netlist off a schematic drawing is vision work, so the extraction
eval scores it separately.

## The evals

Both evals score against a hand-made gold set, so they measure what users get, not what
the model says it did. The extraction prompt lives in one file that the plugin agent and
the eval script both read.

### Gold set

About six datasheets picked to differ: a simple IC (INA226), a dense one (PCA9685), a buck
regulator, a MOSFET, a connector, and one awkward one (scanned, or a pinout only as a
drawing). For each: the MPN, URL and sha256 (the PDF is not committed), the gold pages
for each section, and a hand-checked expected part file.

### Retrieval: which pages does each section need?

| Method | How |
|---|---|
| Heuristic | PDF bookmarks or table of contents, then heading matches ("Absolute Maximum Ratings", "Pin Configuration", ...). No model. |
| Haiku as retriever | Give Haiku each page's headings and first lines and ask which pages each section needs. |

Measures: recall of the gold pages at k (missing a page loses values silently), pages sent
on (cost of the extraction that follows), time, and cost (the heuristic is free, Haiku is
not).

### Extraction: does Haiku read the pages correctly?

Haiku and Sonnet run the same prompt on the same gold pages (retrieval held fixed, so the
two evals do not blur together), then end to end with the chosen retrieval.

| Measure | Why |
|---|---|
| Wrong values that pass `verify` | The key number: a real quote read wrongly (typical as maximum, units, conditions, absolute maximum as an operating limit). |
| Pinout errors | Read from drawings, so this tests vision; the costliest mistake on a board. |
| Missing values | Less bad than wrong ones, because they show. |
| Quote pass rate | Made-up or misquoted text. |
| Tokens, cost and time per datasheet | The case for Haiku. |

The results set the model per section: for example Haiku for tables and text, Sonnet for
pinout pages, and a Sonnet retry for any section `verify` rejects.

## Mouser Search API

From Mouser's Swagger specs (`https://api.mouser.com/api/docs/v1` and `.../v2`, read
2026-10-09) and one real call:

- v1: `search/partnumber`, `search/keyword`. v2: `search/partnumberandmanufacturer`,
  `search/keywordandmanufacturer`, `search/manufacturerlist` (their v1 forms are
  deprecated). All POST with JSON except the manufacturer list (GET); key in the
  `apiKey` query parameter; errors in an `Errors` array.
- Up to 10 part numbers per call, separated by `|`, each 3 to 40 characters; up to 50
  results. A manufacturer part number works in the `mouserPartNumber` field.
- `DataSheetUrl` came back empty for INA226AIDGSR: Mouser cannot be the only source of
  datasheet URLs.
- Prices come in the account's currency (CAD here). The cache keeps the raw response.
- Mouser's CDN (Akamai) answered urllib's default client with a redirect and a scripted
  client without a User-Agent with an HTML error page. The client sends a User-Agent and
  `Accept: application/json`, refuses redirects, and retries a non-JSON answer.
- A response header echoes the API key. Never log or store response headers, and never
  put the request URL (which holds the key) in an error.
- Rate limits are not in the specs, and the web page that may state them blocks scripts.
  Unconfirmed.

## Steps

1. Cache layers and `pcbkit parts lookup` (Mouser).
2. `pcbkit datasheet fetch`: PDF, page text, page images.
3. Part file schema and `verify` (schema and quotes first; KiCad stock comparison next).
4. The extraction prompt and a plugin agent with `model: haiku`.
5. One datasheet end to end through steps 1 to 4.
6. Gold set, then both evals. Their results decide retrieval and the model per section
   before anything else is built on them.
7. `parts/` library, vendoring into a board, `.kicad_sym` output, the `Datasheet` fix.
8. CLAUDE.md line on parts versus boards; docs page.

## Open questions

- **Text extraction library.** `pypdf` (pure Python, runs anywhere, weak on tables),
  `pdfplumber` (better tables), or `pdftotext` (good, but a non-Python dependency). The
  eval can compare them too, since retrieval and extraction both depend on the text.
- **Page images.** Renderer (`pdftoppm`, `pypdfium2`, ...) and resolution, which sets
  Haiku's token cost per page.
- **ECAD models.** Mouser's KiCad symbols and footprints come from SamacSys and seem to need
  a separate sign-in, not the Search API key. If they can be fetched, they are a second
  independent source for the pinout cross-check.
