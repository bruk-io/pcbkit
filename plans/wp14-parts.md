# WP14: parts, datasheets and extraction

Status: in progress on branch `wp14-parts`. Built: the Mouser client and cache,
`pcbkit.datasheets.judge`, and `pcbkit datasheet find` and `confirm` (Mouser, then
KiCad's symbols; records in `parts/`). Not built: part files, extraction, the evals,
more sources.

Before merging to main:

- `tools/datasheet_sources/parts.csv` has four part numbers taken from a real board
  (`EEH-ZA1E331P`, `SQD50P03-07_GE3`, `WSK25125L000FEA`, `PPTC221LFBN-RC`): swap them
  for generic ones and run the measurement and `rejudge.py` again.
- Add the CLAUDE.md line that tells parts (public datasheet facts) from board content.

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

## Where datasheet links come from (measured 2026-10-09)

`tools/datasheet_sources/measure.py` over the 52 parts in `parts.csv` (a quadruped
carrier's kinds of parts plus common ones, 19 manufacturers). Mouser was asked in 6
requests (10 part numbers each, exact, cached); each link was downloaded with pcbkit's
own User-Agent and judged by code: a PDF, with the family name in its first three pages.

| Outcome | Parts |
|---|---|
| Mouser link, right datasheet | 15 |
| At Mouser, no link | 34 |
| Not at Mouser | 3 (AO3401A, PPTC221LFBN-RC, 0ZCJ0050FF2E) |

- When Mouser gives a link it is right: 15 of 15 were PDFs of the right part, and none
  needed a browser's User-Agent. The links are on mouser.com.
- The gaps are everywhere: 34 misses across 18 manufacturers. Texas Instruments is 12 of
  them (every TI part), Vishay 5, ST 3, then one or two each.
- The cached answers hold no other link to the datasheet.
- A TI rule, `https://www.ti.com/lit/ds/symlink/<family>.pdf`, gave the right datasheet
  for 12 of 12, with pcbkit's User-Agent. But it needs the family (`INA226` from
  `INA226AIDGSR`), which was given by hand here; working it out from an orderable part
  number is its own problem.

### KiCad's stock symbols (measured 2026-10-09)

`tools/datasheet_sources/kicad_symbols.py` matches each part number to the longest
stock symbol name that starts it (KiCad 10.0.6, 22,756 symbols; lowercase `x` is a
wildcard, as in `MCP1700x-330xxTT`) and judges the symbol's `Datasheet` link the same
way. No distributor is asked.

| Outcome | All 52 | The 37 Mouser missed |
|---|---|---|
| Right datasheet | 20 | 15 |
| No symbol | 26 | 18 |
| Wrong datasheet | 2 | 2 |
| Link not a PDF | 3 | 1 |
| No usable link | 1 | 1 |

- Mouser and KiCad together give the right datasheet for 30 of 52 parts (Mouser 15,
  KiCad 20, both 5), with no new account and only local lookups beyond Mouser.
- KiCad covers 9 of the 12 TI parts, and the symbol name is the family (`INA226`,
  `TCA9548APWR`), which solves the family problem the TI rule had for those.
- Wrong datasheets: the ESP32-S3 dev board matched the bare chip's symbol, and
  `1N5819HW` (Diodes, SOD-123) matched Vishay's `1N5817` datasheet. Also
  `BAT54SLT1G` (onsemi) matched a Diodes datasheet for the same part type and passed
  the family check: a family check cannot tell manufacturers apart, so the check must
  also compare the manufacturer.
- Stale links: ICM-20948's goes to an HTML page now, MCP1700's gets a 403, and
  TXS0108E's has no `http://`. Infineon's failed certificate verification (not yet
  looked at: it may be this machine's Python).
- Still missing after both: 22 parts. Passives, connectors and discretes (10: Murata,
  Samsung, Panasonic, Kingbright, Bel Fuse, JST, Wurth, Sullins, Vishay shunt and
  TVS), Vishay MOSFETs (2), and ICs with no stock symbol (TPS54331, LM1117,
  SN74LVC1G17, TXS0108E, LSM6DSOX, MP1584, MAX17048, ICM-20948, 1N5819HW, the dev
  board).

So: Mouser first (31% of these parts, always right); a TI rule covers a third of the
misses if the family can be found; the other 22 misses spread over 17 manufacturers, too
thin for rules. KiCad's symbols next (local, and they give the family);
then a general fallback for the remaining 22, measured on exactly those: the open
jlcparts dataset, another distributor's or aggregator's API (Digi-Key, Nexar/Octopart),
or web search with the model, judged by the same checks plus a manufacturer check.

## Checking a datasheet is the part's

Measured on the 33 PDFs downloaded above: a check that the family name is in the PDF
passes wrong datasheets. `BSS138LT1G` got the old Fairchild BSS138 datasheet, not
onsemi's own, and passed; and once the family comes from the matched KiCad symbol, the
check only agrees with itself. The check must not depend on where the link came from.

A datasheet is **verified** for a part when its text has:

- the full orderable part number, letters and digits only, ignoring case, with at most
  2 characters missing at the end (reel and packaging codes: `CP2102N-A02-GQFN28` for
  `...GQFN28R`, `DS3231SN` for `DS3231SN#T&R`). The 1N5819HW and BSS138LT1G mistakes
  each miss 4. Small sample: watch it.
- the manufacturer's name, from a table of names and aliases in code (Texas
  Instruments/TI, onsemi/ON Semiconductor/Fairchild, ...), matched as a whole name. A
  single word is not enough: "diodes" is in every diode datasheet.

Passives and crystals (`RC0603FR-0710KL`, `ABM8-16.000MHZ-B2-T`) have series datasheets
that explain how the number is built rather than listing it, so they cannot be verified
this way: they are candidates until confirmed.

Measured with `pcbkit.datasheets.judge` (`tools/datasheet_sources/rejudge.py`, offline,
on the PDFs downloaded above, all pages):

| | Mouser | KiCad | Either |
|---|---|---|---|
| VERIFIED | 12 | 16 | 23 of 52 |
| CANDIDATE, part-number-missing | 1 | 5 | |
| CANDIDATE, part-number-built | 2 | 0 | |
| CANDIDATE, manufacturer-missing | 0 | 1 | |

- Every wrong datasheet found so far is a candidate, not verified: the dev board,
  1N5819HW, BSS138LT1G and BAT54SLT1G.
- Right datasheets left as candidates: `SS34-E3/57T` (Vishay prints `SS34`, not its
  `-E3/57T` packaging code) and `SM04B-SRSS-TB(LF)(SN)` (JST leaves off `(LF)(SN)`)
  get `part-number-missing`, whose next step says to find another source; confirming
  would be right. A table of each maker's packaging suffixes, in code, would fix
  both. `AO3401A`'s maker seems to be named only in the logo: `manufacturer-missing`,
  which rightly asks for a confirmation.
- The first version matched across the whole page with spaces removed, which would
  have joined `AO3401 Alpha` into `AO3401A`; the part number is now matched within one
  word, and a unit test holds it.

## The tool is opinionated, so the model need not remember

The rules above live in code. The model does only what needs judgement, and the tool
checks that too.

- `pcbkit datasheet find <MPN>` runs the whole chain (Mouser, KiCad's symbols, later
  sources), judges every candidate, and picks. The model never builds a URL, picks
  between candidates, or decides a match is close enough.
- Each answer is one of a closed set, with a reason code from a closed list and a
  `next:` line saying what to do: `VERIFIED`; `CANDIDATE` (`part-number-missing`,
  `manufacturer-missing`, `part-number-built`, `not-a-pdf`, `no-text`); `NOT FOUND`.
  The model follows `next:`.
- `pcbkit datasheet confirm <MPN> --page N --quote "..."` is how a candidate becomes
  `CONFIRMED`: the tool checks the quote is on that page and holds the part number, or
  for a built number the series and each segment. An opinion without evidence is
  refused.
- Later stages refuse what is not `VERIFIED` or `CONFIRMED`: extraction will not run on
  it, and `verify` checks the history. Skipping a step fails at the next one.
- Manufacturer aliases, the 2-character tolerance and the reason-to-next-step table are
  data in code, with tests.
- Every measured mistake is a test: 1N5819HW, BSS138LT1G, BAT54SLT1G, the ESP32-S3 dev
  board, and "diodes" as a manufacturer.
- The skill says: run `pcbkit datasheet find`, then do what `next:` says.

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
