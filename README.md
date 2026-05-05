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
python3 ScrapeFandom.py jedipedia@de --no-cache
python3 ScrapeFandom.py jedipedia@de --refresh-cache
```

The same export-cache flags are available through the pipeline:

```sh
uv run fandom-pipeline jedipedia@de --refresh-export-cache
uv run fandom-pipeline jedipedia@de --no-export-cache
```

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
```

The default output path is derived from the input file. For example, `pokemon@de.jsonl` writes `wordlists/pokemon_de.txt`.

The default cap is 250,000 entries. Lower it for a tighter seed list:

```sh
uv run fandom-wordlist harrypotter.jsonl wordlists/harrypotter-small.txt --max-base 50000
```

Multi-word terms are emitted as combinations and as their component words. For example, an accepted term like `Harry Potter` can produce entries such as `harry`, `potter`, `harrypotter`, `HarryPotter`, `harry-potter`, and `harry_potter`.

By default, the extractor excludes common English and German terms using the vendored lists in `dictionaries/`:

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
