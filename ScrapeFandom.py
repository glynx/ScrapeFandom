import argparse
import hashlib
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import quoteattr

import requests
from tqdm import tqdm


USER_AGENT = "ScrapeFandom/1.0"
MEDIAWIKI_NS = "http://www.mediawiki.org/xml/export-0.11/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XML_NS = "http://www.w3.org/XML/1998/namespace"
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "scrape-fandom" / "export-batches"
RETRY_STATUSES = {429, 500, 502, 503, 504}


ET.register_namespace("", MEDIAWIKI_NS)
ET.register_namespace("xsi", XSI_NS)


def batched(items: list[str], size: int) -> Iterable[list[str]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def parse_fandom_target(target: str) -> tuple[str, str]:
    if "@" not in target:
        return target, "en"
    fandom_site, language = target.rsplit("@", 1)
    if not fandom_site or not language:
        raise ValueError("Fandom target must look like 'slug' or 'slug@language'")
    return fandom_site, language


def api_url(fandom_site: str, language: str = "en") -> str:
    if language == "en":
        return f"https://{fandom_site}.fandom.com/api.php"
    return f"https://{fandom_site}.fandom.com/{language}/api.php"


def fetch_all_titles(session: requests.Session, fandom_site: str, language: str) -> list[str]:
    titles: list[str] = []
    params = {
        "action": "query",
        "list": "allpages",
        "aplimit": "max",
        "apnamespace": "0",
        "format": "json",
        "formatversion": "2",
    }

    while True:
        response = session.get(api_url(fandom_site, language), params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(data["error"].get("info", data["error"]))

        titles.extend(page["title"] for page in data.get("query", {}).get("allpages", []))

        continuation = data.get("continue")
        if not continuation:
            return titles
        params.update(continuation)

def export_batch(
    session: requests.Session,
    fandom_site: str,
    language: str,
    titles: list[str],
    retries: int,
    retry_delay: float,
) -> ET.Element:
    if len(titles) > 45:
        raise ValueError(f"Too many titles in one batch: {len(titles)}")

    if any("|" in title for title in titles):
        raise ValueError("A title contains '|'; use pageids or an alternate separator.")

    url = api_url(fandom_site, language)

    headers = {
        "User-Agent": "YourProjectName/1.0 contact@example.com",
        "Accept": "application/xml,text/xml,*/*",
    }

    params = {
        "action": "query",
        "export": "1",
        "exportnowrap": "1",
        "format": "xml",
        "titles": "|".join(titles),
    }

    for attempt in range(retries + 1):
        response = session.get(url, params=params, headers=headers, timeout=120)
        if response.status_code not in RETRY_STATUSES:
            response.raise_for_status()
            break
        if attempt >= retries:
            response.raise_for_status()
        sleep_for = retry_delay * (2**attempt)
        print(
            f"Export batch got HTTP {response.status_code}; retrying in {sleep_for:.1f}s",
            file=sys.stderr,
        )
        time.sleep(sleep_for)

    content_type = response.headers.get("content-type", "")
    text_start = response.text[:500].replace("\n", " ")

    if "xml" not in content_type.lower() and not response.text.lstrip().startswith("<"):
        raise RuntimeError(
            f"Fandom did not return XML. "
            f"status={response.status_code}, content-type={content_type}, body={text_start}"
        )

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise RuntimeError(
            f"Fandom returned invalid XML. "
            f"status={response.status_code}, content-type={content_type}, body={text_start}"
        ) from exc

    error = root.find(".//error")
    if error is not None:
        raise RuntimeError(f"MediaWiki API error: {error.attrib}")

    return root


def cache_namespace(output_name: str, fandom_site: str, language: str, batch_size: int) -> str:
    key = f"{output_name}\0{fandom_site}\0{language}\0{batch_size}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def cache_path_for(cache_dir: Path, namespace: str, titles: list[str]) -> Path:
    digest = hashlib.sha256("\0".join(titles).encode("utf-8")).hexdigest()
    return cache_dir / namespace / f"{digest}.xml"


def read_cached_batch(path: Path) -> ET.Element | None:
    if not path.exists():
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        return None


def write_cached_batch(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    ET.ElementTree(root).write(tmp_path, encoding="utf-8", xml_declaration=True)
    tmp_path.replace(path)


def attr_name(name: str) -> str:
    if name.startswith("{"):
        namespace, local_name = name[1:].split("}", 1)
        if namespace == XSI_NS:
            return f"xsi:{local_name}"
        if namespace == XML_NS:
            return f"xml:{local_name}"
    return name


def write_root_start(outfile, root: ET.Element) -> None:
    attrs = [
        f'xmlns="{MEDIAWIKI_NS}"',
        f'xmlns:xsi="{XSI_NS}"',
    ]
    attrs.extend(f"{attr_name(name)}={quoteattr(value)}" for name, value in root.attrib.items())
    outfile.write(f"<mediawiki {' '.join(attrs)}>\n")


def write_dump(
    session: requests.Session,
    output_name: str,
    fandom_site: str,
    language: str,
    titles: list[str],
    batch_size: int,
    cache_dir: Path | None,
    refresh_cache: bool,
    retries: int,
    retry_delay: float,
) -> None:
    batches = list(batched(titles, batch_size))
    wrote_header = False
    output_path = Path(f"{output_name}.xml")
    tmp_output_path = output_path.with_suffix(output_path.suffix + ".tmp")
    namespace = cache_namespace(output_name, fandom_site, language, batch_size)

    with tmp_output_path.open("w", encoding="utf-8") as outfile:
        outfile.write('<?xml version="1.0" encoding="utf-8"?>\n')

        for title_batch in tqdm(batches, desc=f"Exporting {output_name}"):
            cache_path = cache_path_for(cache_dir, namespace, title_batch) if cache_dir else None
            root = None if refresh_cache or cache_path is None else read_cached_batch(cache_path)
            if root is None:
                root = export_batch(session, fandom_site, language, title_batch, retries, retry_delay)
                if cache_path is not None:
                    write_cached_batch(cache_path, root)

            if not wrote_header:
                write_root_start(outfile, root)
                siteinfo = root.find(f"{{{MEDIAWIKI_NS}}}siteinfo")
                if siteinfo is not None:
                    outfile.write(ET.tostring(siteinfo, encoding="unicode"))
                    outfile.write("\n")
                wrote_header = True

            for page in root.findall(f"{{{MEDIAWIKI_NS}}}page"):
                outfile.write(ET.tostring(page, encoding="unicode"))
                outfile.write("\n")

            time.sleep(0.1)

        if not wrote_header:
            raise RuntimeError(f"No pages found for {output_name}")

        outfile.write("</mediawiki>\n")
    tmp_output_path.replace(output_path)


def scrape_target(args: argparse.Namespace, session: requests.Session, target: str) -> None:
    fandom_site, language = parse_fandom_target(target)
    titles = fetch_all_titles(session, fandom_site, language)
    if not titles:
        raise RuntimeError(f"No pages found for {target}")
    write_dump(
        session,
        target,
        fandom_site,
        language,
        titles,
        args.batch_size,
        None if args.no_cache else args.cache_dir,
        args.refresh_cache,
        args.retries,
        args.retry_delay,
    )
    print(f"Wrote {target}.xml with {len(titles)} pages")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_fandom", nargs="+", help="Fandom site name(s), e.g. harrypotter or harry-potter@de")
    parser.add_argument("--batch-size", type=int, default=30, help="Number of pages per export API request")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Directory for resumable export batch cache (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable export batch cache")
    parser.add_argument("--refresh-cache", action="store_true", help="Fetch all batches again and replace cached batches")
    parser.add_argument("--retries", type=int, default=5, help="Retries for transient export HTTP errors")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Initial retry delay in seconds")
    args = parser.parse_args()

    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.retries < 0:
        parser.error("--retries must not be negative")
    if args.retry_delay < 0:
        parser.error("--retry-delay must not be negative")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    failed = False
    for target in args.input_fandom:
        try:
            scrape_target(args, session, target)
        except Exception as exc:
            print(f"Scrape failed for {target}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
