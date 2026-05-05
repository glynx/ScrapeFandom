import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests


USER_AGENT = "ScrapeFandom/1.0"
WIKILISTS_API = "https://list.fandom.com/api.php"
DAS_WIKI_WIKI_DE_API = "https://wikis.fandom.com/de/api.php"
DEFAULT_CACHE = Path.home() / ".cache" / "scrape-fandom" / "fandom_wikis.json"
DEFAULT_CACHE_TTL_DAYS = 30


@dataclass(frozen=True)
class WikiCandidate:
    name: str
    slug: str
    url: str
    language: str
    scrape_arg: str
    pages: int | None = None
    articles: int | None = None


class WikiLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href")
        if href and "fandom.com" in href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        text = " ".join("".join(self._text).split())
        if text:
            self.links.append((text, self._href))
        self._href = None
        self._text = []


def fetch_wikilists_html(session: requests.Session) -> str:
    response = session.get(
        WIKILISTS_API,
        params={
            "action": "parse",
            "page": "List of wikias",
            "prop": "text",
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["parse"]["text"]["*"]


def get_wikilists_html(
    session: requests.Session,
    cache: dict,
    cache_ttl_days: int,
    refresh_cache: bool,
    no_cache: bool,
) -> tuple[str, bool]:
    ttl_seconds = cache_ttl_days * 24 * 60 * 60
    entry = cache.get("wikilists")
    if not no_cache and not refresh_cache and isinstance(entry, dict) and cache_entry_is_fresh(entry, ttl_seconds):
        html = entry.get("html")
        if isinstance(html, str) and html:
            return html, False

    html = fetch_wikilists_html(session)
    if not no_cache:
        cache["wikilists"] = {"fetched_at": int(time.time()), "html": html}
        return html, True
    return html, False


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {"wikis": {}}
    try:
        with path.open(encoding="utf-8") as infile:
            data = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return {"wikis": {}}
    if not isinstance(data, dict):
        return {"wikis": {}}
    data.setdefault("wikis", {})
    return data


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cache["updated_at"] = int(time.time())
    with path.open("w", encoding="utf-8") as outfile:
        json.dump(cache, outfile, ensure_ascii=False, indent=2, sort_keys=True)
        outfile.write("\n")


def cache_key(candidate: WikiCandidate) -> str:
    return f"{candidate.slug}|{candidate.language}"


def scrape_arg_for(slug: str, language: str) -> str:
    return slug if language == "en" else f"{slug}@{language}"


def cache_entry_is_fresh(entry: dict, ttl_seconds: int) -> bool:
    fetched_at = entry.get("fetched_at")
    return isinstance(fetched_at, int | float) and time.time() - fetched_at <= ttl_seconds


def candidate_from_cache(candidate: WikiCandidate, entry: dict, include_counts: bool) -> WikiCandidate | None:
    if not entry.get("ok"):
        return None
    if include_counts and ("pages" not in entry or "articles" not in entry):
        return None
    return WikiCandidate(
        name=entry.get("name") or candidate.name,
        slug=candidate.slug,
        url=candidate.url,
        language=candidate.language,
        scrape_arg=scrape_arg_for(candidate.slug, candidate.language),
        pages=entry.get("pages") if include_counts else None,
        articles=entry.get("articles") if include_counts else None,
    )


def cache_candidate(cache: dict, candidate: WikiCandidate | None, original: WikiCandidate) -> None:
    entry = {"fetched_at": int(time.time()), "ok": candidate is not None}
    if candidate:
        entry.update(
            {
                "name": candidate.name,
                "pages": candidate.pages,
                "articles": candidate.articles,
            }
        )
    cache["wikis"][cache_key(original)] = entry


def slug_from_url(url: str) -> tuple[str, str, str] | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host.endswith(".fandom.com"):
        return None

    path = unquote(parsed.path)
    if host == "community.fandom.com":
        match = re.search(r"/wiki/c:([^/#?]+)", path)
        if not match:
            return None
        slug = match.group(1).strip().lower()
        slug_parts = slug.split(".")
        language = (
            slug_parts[0]
            if len(slug_parts) > 1 and re.fullmatch(r"[a-z][a-z-]{1,7}", slug_parts[0])
            else "en"
        )
        return slug, language, f"https://{slug}.fandom.com/wiki/"

    slug = host.removesuffix(".fandom.com")
    host_parts = slug.split(".")
    host_language = "en"
    if len(host_parts) > 1 and re.fullmatch(r"[a-z][a-z-]{1,7}", host_parts[0]):
        host_language = host_parts[0]

    parts = [part for part in path.split("/") if part]
    path_language = parts[0] if parts and parts[0] != "wiki" and re.fullmatch(r"[a-z][a-z-]{1,7}", parts[0]) else None
    language = (
        path_language
        if path_language
        else host_language
    )
    canonical_path = f"/{path_language}/wiki/" if path_language else "/wiki/"
    return slug, language, f"https://{host}{canonical_path}"


def parse_wikis(html: str, include_languages: bool) -> list[WikiCandidate]:
    parser = WikiLinkParser()
    parser.feed(html)

    candidates: dict[tuple[str, str], WikiCandidate] = {}
    for name, url in parser.links:
        parsed = slug_from_url(url)
        if not parsed:
            continue
        slug, language, canonical_url = parsed
        if language != "en" and not include_languages:
            continue
        if slug in {"auth", "community"}:
            continue

        key = (slug, language)
        candidates.setdefault(
            key,
            WikiCandidate(
                name=name.lstrip("|").strip(),
                slug=slug,
                url=canonical_url,
                language=language,
                scrape_arg=scrape_arg_for(slug, language),
            ),
        )

    return sorted(candidates.values(), key=lambda item: (item.language, item.name.lower(), item.slug))


def fetch_german_wiki_titles(session: requests.Session) -> list[str]:
    titles = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": "Kategorie:Individuelle Wikis",
        "cmlimit": "max",
        "format": "json",
    }
    while True:
        response = session.get(DAS_WIKI_WIKI_DE_API, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        titles.extend(page["title"] for page in data.get("query", {}).get("categorymembers", []))
        continuation = data.get("continue")
        if not continuation:
            return titles
        params.update(continuation)


def parse_infobox_fields(content: str) -> dict[str, str]:
    fields = {}
    for match in re.finditer(r"^\s*\|\s*([^=|]+?)\s*=\s*(.*?)\s*$", content, re.MULTILINE):
        key = match.group(1).strip().lower()
        value = re.sub(r"\{\{.*?\}\}", "", match.group(2)).strip()
        if value:
            fields[key] = value
    return fields


def candidate_from_german_fields(title: str, fields: dict[str, str]) -> WikiCandidate | None:
    raw_url = fields.get("url", "").lower().strip()
    if not raw_url:
        return None
    language = fields.get("path-prefix") or fields.get("language") or "de"
    slug = raw_url.removeprefix(f"{language}.")
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return None
    name = fields.get("name") or title
    return WikiCandidate(
        name=name,
        slug=slug,
        url=f"https://{slug}.fandom.com/{language}/wiki/",
        language=language,
        scrape_arg=scrape_arg_for(slug, language),
    )


def fetch_german_wikis(session: requests.Session) -> list[WikiCandidate]:
    candidates = []
    titles = fetch_german_wiki_titles(session)
    for offset in range(0, len(titles), 50):
        response = session.get(
            DAS_WIKI_WIKI_DE_API,
            params={
                "action": "query",
                "prop": "revisions",
                "titles": "|".join(titles[offset : offset + 50]),
                "rvprop": "content",
                "rvslots": "main",
                "formatversion": "2",
                "format": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        for page in data.get("query", {}).get("pages", []):
            content = page.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("content", "")
            candidate = candidate_from_german_fields(page.get("title", ""), parse_infobox_fields(content))
            if candidate:
                candidates.append(candidate)
    return sorted(candidates, key=lambda item: (item.name.lower(), item.slug))


def get_german_wikis(
    session: requests.Session,
    cache: dict,
    cache_ttl_days: int,
    refresh_cache: bool,
    no_cache: bool,
) -> tuple[list[WikiCandidate], bool]:
    ttl_seconds = cache_ttl_days * 24 * 60 * 60
    entry = cache.get("das_wiki_wiki_de")
    if not no_cache and not refresh_cache and isinstance(entry, dict) and cache_entry_is_fresh(entry, ttl_seconds):
        cached = entry.get("wikis")
        if isinstance(cached, list):
            return [WikiCandidate(**item) for item in cached], False

    candidates = fetch_german_wikis(session)
    if not no_cache:
        cache["das_wiki_wiki_de"] = {
            "fetched_at": int(time.time()),
            "wikis": [asdict(candidate) for candidate in candidates],
        }
        return candidates, True
    return candidates, False


def enrich_candidate(session: requests.Session, candidate: WikiCandidate, include_counts: bool) -> WikiCandidate | None:
    siprop = "general|statistics" if include_counts else "general"
    api_url = candidate.url.replace("/wiki/", "/api.php")
    try:
        response = session.get(
            api_url,
            params={"action": "query", "meta": "siteinfo", "siprop": siprop, "format": "json"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        general = data.get("query", {}).get("general", {})
        statistics = data.get("query", {}).get("statistics", {})
        sitename = general.get("sitename")
    except Exception:
        return None

    if not sitename:
        return None
    return WikiCandidate(
        name=sitename,
        slug=candidate.slug,
        url=candidate.url,
        language=candidate.language,
        scrape_arg=scrape_arg_for(candidate.slug, candidate.language),
        pages=statistics.get("pages") if include_counts else None,
        articles=statistics.get("articles") if include_counts else None,
    )


def enrich_candidates(
    session: requests.Session,
    candidates: list[WikiCandidate],
    include_counts: bool,
    cache: dict,
    cache_ttl_days: int,
    refresh_cache: bool,
    no_cache: bool,
) -> tuple[list[WikiCandidate], bool]:
    ttl_seconds = cache_ttl_days * 24 * 60 * 60
    enriched = []
    cache_changed = False

    for candidate in candidates:
        key = cache_key(candidate)
        entry = cache.get("wikis", {}).get(key)
        if not no_cache and not refresh_cache and isinstance(entry, dict) and cache_entry_is_fresh(entry, ttl_seconds):
            cached = candidate_from_cache(candidate, entry, include_counts)
            if cached:
                enriched.append(cached)
                continue
            if entry.get("ok") is False:
                continue

        checked = enrich_candidate(session, candidate, include_counts)
        if checked:
            enriched.append(checked)
        if not no_cache:
            cache_candidate(cache, checked, candidate)
            cache_changed = True

    return enriched, cache_changed


def filter_candidates(
    candidates: list[WikiCandidate],
    query: str | None,
    language: str | None,
) -> list[WikiCandidate]:
    if language:
        candidates = [candidate for candidate in candidates if candidate.language == language]
    if query:
        needle = query.lower()
        candidates = [
            candidate
            for candidate in candidates
            if needle in candidate.name.lower() or needle in candidate.slug.lower()
        ]
    return candidates


def print_table(candidates: list[WikiCandidate]) -> None:
    if not candidates:
        return
    name_width = min(max(len("name"), *(len(candidate.name) for candidate in candidates)), 42)
    slug_width = min(max(len("scrape_arg"), *(len(candidate.scrape_arg) for candidate in candidates)), 24)
    show_counts = any(candidate.pages is not None or candidate.articles is not None for candidate in candidates)
    if show_counts:
        print(f"{'scrape_arg':{slug_width}}  {'lang':4}  {'pages':>9}  {'articles':>9}  {'name':{name_width}}  url")
        print(f"{'-' * slug_width}  {'-' * 4}  {'-' * 9}  {'-' * 9}  {'-' * name_width}  {'-' * 3}")
    else:
        print(f"{'scrape_arg':{slug_width}}  {'lang':4}  {'name':{name_width}}  url")
        print(f"{'-' * slug_width}  {'-' * 4}  {'-' * name_width}  {'-' * 3}")
    for candidate in candidates:
        name = candidate.name if len(candidate.name) <= name_width else candidate.name[: name_width - 1] + "…"
        if show_counts:
            pages = f"{candidate.pages}" if candidate.pages is not None else ""
            articles = f"{candidate.articles}" if candidate.articles is not None else ""
            print(
                f"{candidate.scrape_arg:{slug_width}}  {candidate.language:4}  "
                f"{pages:>9}  {articles:>9}  {name:{name_width}}  {candidate.url}"
            )
        else:
            print(f"{candidate.scrape_arg:{slug_width}}  {candidate.language:4}  {name:{name_width}}  {candidate.url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="List Fandom wikis that can be passed to run-me.sh.")
    parser.add_argument("query", nargs="?", help="Optional filter for wiki name or slug")
    parser.add_argument("--limit", type=int, default=100, help="Maximum rows to print")
    parser.add_argument("--language", "-l", help="Filter by WikiLists language code, e.g. en, de, pt-br")
    parser.add_argument("--all-languages", action="store_true", help="Include non-English wiki URLs from WikiLists")
    parser.add_argument("--verify", action="store_true", help="Verify each candidate with its api.php siteinfo")
    parser.add_argument("--counts", action="store_true", help="Fetch page and article counts from each candidate wiki")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help=f"Metadata cache path (default: {DEFAULT_CACHE})")
    parser.add_argument("--cache-ttl-days", type=int, default=DEFAULT_CACHE_TTL_DAYS)
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached wiki metadata and refresh requested rows")
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write cached wiki metadata")
    parser.add_argument("--format", choices=("table", "json", "tsv"), default="table")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.cache_ttl_days < 0:
        parser.error("--cache-ttl-days must not be negative")
    if args.language:
        args.language = args.language.lower()
        args.all_languages = True

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    cache = {"wikis": {}} if args.no_cache else load_cache(args.cache)

    try:
        html, cache_changed = get_wikilists_html(
            session=session,
            cache=cache,
            cache_ttl_days=args.cache_ttl_days,
            refresh_cache=args.refresh_cache,
            no_cache=args.no_cache,
        )
        candidates = parse_wikis(html, args.all_languages)
        if args.language == "de":
            german_candidates, german_cache_changed = get_german_wikis(
                session=session,
                cache=cache,
                cache_ttl_days=args.cache_ttl_days,
                refresh_cache=args.refresh_cache,
                no_cache=args.no_cache,
            )
            candidates_by_key = {(candidate.slug, candidate.language): candidate for candidate in candidates}
            for candidate in german_candidates:
                candidates_by_key[(candidate.slug, candidate.language)] = candidate
            candidates = sorted(candidates_by_key.values(), key=lambda item: (item.language, item.name.lower(), item.slug))
            cache_changed = cache_changed or german_cache_changed
    except Exception as exc:
        print(f"Failed to fetch WikiLists data: {exc}", file=sys.stderr)
        return 1

    candidates = filter_candidates(candidates, args.query, args.language)
    candidates = candidates[: args.limit]

    if args.verify or args.counts:
        candidates, metadata_cache_changed = enrich_candidates(
            session=session,
            candidates=candidates,
            include_counts=args.counts,
            cache=cache,
            cache_ttl_days=args.cache_ttl_days,
            refresh_cache=args.refresh_cache,
            no_cache=args.no_cache,
        )
        cache_changed = cache_changed or metadata_cache_changed

    if cache_changed and not args.no_cache:
        save_cache(args.cache, cache)

    if args.format == "json":
        print(json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=False, indent=2))
    elif args.format == "tsv":
        print("scrape_arg\tlanguage\tpages\tarticles\tname\turl")
        for candidate in candidates:
            pages = candidate.pages if candidate.pages is not None else ""
            articles = candidate.articles if candidate.articles is not None else ""
            print(f"{candidate.scrape_arg}\t{candidate.language}\t{pages}\t{articles}\t{candidate.name}\t{candidate.url}")
    else:
        print_table(candidates)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
