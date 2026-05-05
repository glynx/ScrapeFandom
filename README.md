# Scrape Fandom
Fandom.com provides Wiki dumps at https://*.fandom.com/wiki/Special:Statistics, but most of the dumps are outdated, and require contacting an admin to produce a new dump.

This script scrapes Fandom.com for an updated Wiki dump. It scrapes the Special:AllPages to get a list of article names and requests a wiki dump from Special:Export. Instructions to get a corpus for natural language processing and training is provided.

English wikis use the plain slug. Localized wikis can be scraped with `slug@language`, for example `harry-potter@de`.

# Notes
The scraper uses Fandom's MediaWiki API to list pages and export XML. Chrome and Selenium are no longer required.

Install dependencies with `uv`:

```sh
uv sync
```

# Instructions
1. Install the Python dependencies:
```sh
uv sync
```
2. Run the pipeline to get `<target>.jsonl` and `wordlists/<target>.txt`.

Example
```sh
uv run fandom-pipeline harrypotter finalfantasy
```

For localized wikis, use `slug@language`:

```sh
uv run fandom-pipeline pokemon@de
```

This writes `pokemon@de.jsonl` and `wordlists/pokemon_de.txt`. Intermediate `pokemon@de.xml` and the WikiExtractor directory `pokemon@de/` are removed after a successful run.

Keep the intermediate XML and extraction directory when debugging or when you want to reuse them manually:

```sh
uv run fandom-pipeline pokemon@de --keep-artifacts
```

`./run-me.sh` is kept as a compatibility wrapper and calls the same pipeline through `uv`:

```sh
./run-me.sh pokemon@de
```

Scrape exports are cached per API batch in `/tmp/scrape-fandom/export-batches` by default. These files can grow large, so they are kept out of the home-directory cache. If Fandom returns a temporary `502` or similar error, rerun the same command and already exported batches will be reused as long as `/tmp` has not been cleaned:

```sh
python3 ScrapeFandom.py jedipedia@de
python3 ScrapeFandom.py actors@de call-of-duty@de
python3 ScrapeFandom.py jedipedia@de --no-cache
python3 ScrapeFandom.py jedipedia@de --refresh-cache
```

The same export-cache flags are available through the pipeline:

```sh
uv run fandom-pipeline jedipedia@de --refresh-export-cache
uv run fandom-pipeline jedipedia@de --no-export-cache
```

If you already have a scraped XML dump, convert it directly to JSONL:

```sh
uv run fandom-jsonl jedipedia@de.xml jedipedia@de.jsonl
```

This uses a temporary WikiExtractor directory and removes it afterwards. Add `--keep-extracted` if you want to keep that intermediate directory.

# Wiki Discovery
List candidate Fandom wikis with the argument to pass to `run-me.sh`:

```sh
uv run fandom-wikis
```

Filter by name or slug:

```sh
uv run fandom-wikis star
```

Filter by WikiLists language:

```sh
uv run fandom-wikis --language de
uv run fandom-wikis nintendo --language de
```

Verify candidates through each wiki's `api.php` before printing:

```sh
uv run fandom-wikis star --verify
```

Fetch page and article counts for the limited result set:

```sh
uv run fandom-wikis star --counts --limit 10
```

The WikiLists discovery data, verified metadata, and counts are cached in `~/.cache/scrape-fandom/fandom_wikis.json` for 30 days by default:

```sh
uv run fandom-wikis star --counts --refresh-cache
uv run fandom-wikis star --counts --cache-ttl-days 7
uv run fandom-wikis star --counts --no-cache
```

The discovery helper reads WikiLists through MediaWiki's API. By default it prints English wikis because the scraper is tuned for English Fandom sites.
For `--language de`, it also reads the German Das Wiki Wiki index because WikiLists' German section is very small.

# Wordlist
Generate one compact cracking base list from the JSONL output:

```sh
uv run fandom-wordlist harrypotter.jsonl
uv run fandom-wordlist pokemon@de.jsonl
uv run fandom-wordlist actors@de.jsonl call-of-duty@de.jsonl
```

The default output path is derived from each input file. For example, `pokemon@de.jsonl` writes `wordlists/pokemon_de.txt`, and `call-of-duty@de.jsonl` writes `wordlists/call-of-duty_de.txt`.

By default, the extractor does not cap the number of emitted entries. It keeps every candidate that passes the score, length, dictionary, and common-word filters. This is intentional for cracking seed lists because large wikis often contain low-frequency domain terms that are still valuable.

Use caps when you need a smaller, faster list:

```sh
uv run fandom-wordlist harrypotter.jsonl wordlists/harrypotter-small.txt --max-base 50000
```

The limit flags are:

```text
--max-base   final unique output entries
--max-words  ranked single-word entries before merging
--max-terms  ranked multi-word/phrase entries before merging
--max-names  ranked capitalized/name entries before merging
```

All of these default to `0`, which means unlimited. Prefer `--max-base` for a simple final-size limit. The bucket-specific limits are mainly useful when you want to bias the list toward or away from phrases, names, or single words.

The command prints a stats line before the final output path:

```text
stats: titles=376/431 names=1488/1488 terms=10178/10178 words=8750/8750 max-base=unlimited
```

For each bucket, the first number is what was selected after caps and the second number is what passed scoring. If both numbers are equal, caps are not reducing the output; dictionary, stopword, length, score, or common-word filters are responsible for the final size.

Multi-word terms are emitted as combinations and as their component words. For example, an accepted term like `Harry Potter` can produce entries such as `harry`, `potter`, `harrypotter`, `HarryPotter`, `harry-potter`, and `harry_potter`.

By default, the extractor is biased toward domain coverage. Any word that appears in a page title is kept, and the `wordfreq` common-word filter only suppresses very common English/German words (`--common-word-max 5.2`). Generic filler is still excluded with the vendored lists in `dictionaries/`:

```text
dictionaries/common-english.txt
dictionaries/common-german.txt
```

You can add more exclusion dictionaries with one word per line:

```sh
uv run fandom-wordlist harrypotter.jsonl wordlists/harrypotter \
  --exclude-file dictionaries/custom-english.txt \
  --exclude-file dictionaries/custom-german.txt
```

If a wiki has domain terms that look like common words, keep them explicitly:

```sh
uv run fandom-wordlist harrypotter.jsonl wordlists/harrypotter \
  --keep-file dictionaries/harrypotter.keep.txt
```

For an even broader list, raise or effectively disable the `wordfreq` filter:

```sh
uv run fandom-wordlist jedipedia@de.jsonl --common-word-max 999
```

To also disable the vendored common-word dictionaries:

```sh
uv run fandom-wordlist jedipedia@de.jsonl --no-default-excludes --common-word-max 999
```
