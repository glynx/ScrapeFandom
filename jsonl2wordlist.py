import argparse
import hashlib
import json
import re
import tempfile
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from tqdm import tqdm

try:
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
except ImportError:
    Progress = None

try:
    import orjson
except ImportError:
    orjson = None

try:
    from wordfreq import zipf_frequency
except ImportError:
    zipf_frequency = None


TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
CAPITALIZED_RE = re.compile(r"\b[^\W_\d][^\W_]*(?:['-][^\W_]+)?\b", re.UNICODE)
ASCII_DIGITS_RE = re.compile(r"^[0-9]+$")
DEFAULT_EXCLUDE_FILES = [
    Path("dictionaries/common-english.txt"),
    Path("dictionaries/common-german.txt"),
]
DEFAULT_WORDLIST_CACHE_DIR = Path(tempfile.gettempdir()) / "scrape-fandom" / "wordlist-cache"
LARGE_INPUT_BYTES = 512 * 1024 * 1024
HUGE_INPUT_BYTES = 1024 * 1024 * 1024

EN_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "almost", "also", "although",
    "always", "am", "an", "and", "another", "any", "are", "around", "as", "at", "back",
    "be", "became", "because", "become", "been", "before", "being", "below", "between",
    "both", "but", "by", "called", "came", "can", "could", "did", "do", "does", "doing",
    "down", "during", "each", "either", "else", "even", "ever", "every", "few", "for",
    "from", "further", "gave", "get", "gets", "go", "goes", "going", "got", "had", "has",
    "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his",
    "how", "however", "i", "if", "in", "into", "is", "it", "its", "itself", "just",
    "know", "known", "later", "like", "made", "make", "many", "may", "me", "might",
    "more", "most", "much", "must", "my", "near", "no", "nor", "not", "now", "of",
    "off", "often", "on", "once", "one", "only", "or", "other", "our", "out", "over",
    "own", "part", "same", "see", "seen", "she", "should", "since", "so", "some",
    "still", "such", "take", "than", "that", "the", "their", "theirs", "them", "then",
    "there", "these", "they", "thing", "this", "those", "through", "to", "too", "took",
    "under", "until", "up", "upon", "us", "use", "used", "using", "very", "was", "way",
    "we", "well", "were", "what", "when", "where", "which", "while", "who", "whom",
    "whose", "why", "will", "with", "within", "without", "would", "you", "your", "yours",
}

DE_STOPWORDS = {
    "aber", "alle", "allem", "allen", "aller", "alles", "als", "also", "am", "an",
    "ander", "andere", "anderem", "anderen", "anderer", "anderes", "auch", "auf", "aus",
    "bei", "beim", "bin", "bis", "bist", "da", "dadurch", "dafuer", "damit", "danach",
    "dann", "darauf", "das", "dass", "dein", "deine", "deinem", "deinen", "deiner",
    "deines", "dem", "den", "denn", "der", "deren", "des", "dessen", "dich", "die",
    "dies", "diese", "diesem", "diesen", "dieser", "dieses", "dir", "doch", "dort",
    "du", "durch", "ein", "eine", "einem", "einen", "einer", "eines", "einige", "er",
    "es", "euch", "euer", "eure", "fuer", "gab", "gegen", "gehabt", "gehen", "geht",
    "gemacht", "gerade", "gewesen", "hat", "hatte", "hatten", "hier", "hin", "hinter",
    "ich", "ihm", "ihn", "ihnen", "ihr", "ihre", "ihrem", "ihren", "ihrer", "ihres",
    "im", "immer", "in", "ins", "ist", "ja", "jede", "jedem", "jeden", "jeder", "jedes",
    "kann", "kein", "keine", "keinem", "keinen", "keiner", "keines", "koennen", "macht",
    "man", "mehr", "mein", "meine", "meinem", "meinen", "meiner", "meines", "mich",
    "mir", "mit", "nach", "nicht", "nichts", "noch", "nur", "ob", "oder", "ohne", "sehr",
    "sein", "seine", "seinem", "seinen", "seiner", "seines", "seit", "sich", "sie", "sind",
    "so", "solche", "soll", "sollte", "sondern", "ueber", "um", "und", "uns", "unser",
    "unsere", "unter", "vom", "von", "vor", "war", "waren", "warst", "was", "weg", "weil",
    "weiter", "welche", "welchem", "welchen", "welcher", "welches", "wenn", "wer", "werde",
    "werden", "wie", "wieder", "wir", "wird", "wo", "wurde", "wurden", "zu", "zum", "zur",
    "zwar", "zwischen",
}

DOMAIN_STOPWORDS = {
    "article", "articles", "category", "chapter", "citation", "edit", "episode", "etymology",
    "external", "fandom", "file", "gallery", "history", "image", "information", "list", "main",
    "name", "named", "notes", "page", "pages", "plot", "reference", "references", "related",
    "section", "series", "source", "sources", "stub", "template", "trivia", "unknown", "wiki",
    "wikia", "wikipedia",
}

STOPWORDS = EN_STOPWORDS | DE_STOPWORDS | DOMAIN_STOPWORDS
UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def normalize_token(token: str) -> str:
    token = token.strip("'-.").lower()
    token = token.replace("’", "'").replace("`", "'")
    return re.sub(r"[-']+", "", token)


def ascii_fold(value: str) -> str:
    value = value.translate(UMLAUT_MAP)
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def load_word_file(path: Path) -> set[str]:
    words = set()
    with path.open(encoding="utf-8") as infile:
        for line in infile:
            word = line.partition("#")[0].partition(";")[0].strip()
            if word:
                words.add(normalize_token(word))
    return words


def iter_jsonl_lines(path: Path, description: str) -> Iterable[bytes]:
    total = path.stat().st_size
    if Progress is not None:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(binary_units=True),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(description, total=total)
            with path.open("rb") as infile:
                for line in infile:
                    progress.update(task, advance=len(line))
                    yield line
        return

    with path.open("rb") as infile:
        with tqdm(total=total, desc=description, unit="B", unit_scale=True, unit_divisor=1024) as progress:
            for line in infile:
                progress.update(len(line))
                yield line


def json_line_text(line: bytes | str) -> str:
    if orjson is not None:
        return orjson.loads(line).get("text", "")
    return json.loads(line).get("text", "")


def title_cache_key(path: Path, min_title_count: int, stopwords: set[str]) -> str:
    stat = path.stat()
    stopword_digest = hashlib.sha256("\n".join(sorted(stopwords)).encode("utf-8")).hexdigest()[:16]
    key = f"{path.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}\0{min_title_count}\0{stopword_digest}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def title_keepword_cache_path(cache_dir: Path, path: Path, min_title_count: int, stopwords: set[str]) -> Path:
    return cache_dir / f"{path.stem}-{title_cache_key(path, min_title_count, stopwords)}.json"


def read_cached_keepwords(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as infile:
            data = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return None
    keepwords = data.get("keepwords") if isinstance(data, dict) else None
    if not isinstance(keepwords, list) or not all(isinstance(word, str) for word in keepwords):
        return None
    return set(keepwords)


def write_cached_keepwords(path: Path, keepwords: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as outfile:
        json.dump({"keepwords": sorted(keepwords)}, outfile, ensure_ascii=False)
    tmp_path.replace(path)


def collect_title_keepwords(path: Path, min_title_count: int, stopwords: set[str]) -> set[str]:
    counts: Counter[str] = Counter()
    for line in iter_jsonl_lines(path, f"Scanning titles {path.name}"):
        if not line.strip():
            continue
        title, _ = split_title(json_line_text(line))
        counts.update(token for token in set(tokens_from(title)) if token and token not in STOPWORDS)
    return {token for token, count in counts.items() if count >= min_title_count}


def load_title_keepwords(path: Path, min_title_count: int, stopwords: set[str], cache_dir: Path | None, refresh: bool) -> set[str]:
    cache_path = title_keepword_cache_path(cache_dir, path, min_title_count, stopwords) if cache_dir else None
    if cache_path is not None and not refresh:
        keepwords = read_cached_keepwords(cache_path)
        if keepwords is not None:
            print(f"Loaded {len(keepwords)} cached title keepwords for {path.name}")
            return keepwords

    keepwords = collect_title_keepwords(path, min_title_count, stopwords)
    if cache_path is not None:
        write_cached_keepwords(cache_path, keepwords)
    return keepwords


@lru_cache(maxsize=200_000)
def cached_common_word(token: str, max_zipf: float) -> bool:
    if zipf_frequency is None or not token.isalpha():
        return False
    return max(zipf_frequency(token, "en"), zipf_frequency(token, "de")) >= max_zipf


def is_common_word(token: str, max_zipf: float, keepwords: set[str]) -> bool:
    if token in keepwords:
        return False
    return cached_common_word(token, max_zipf)


def is_candidate(
    token: str,
    min_len: int,
    max_len: int,
    stopwords: set[str],
    keepwords: set[str],
    max_zipf: float | None = None,
) -> bool:
    if not (min_len <= len(token) <= max_len):
        return False
    if token not in keepwords and token in stopwords:
        return False
    if max_zipf is not None and is_common_word(token, max_zipf, keepwords):
        return False
    if ASCII_DIGITS_RE.fullmatch(token):
        return 1900 <= int(token) <= 2099
    if sum(char.isalpha() for char in token) < min_len:
        return False
    return True


def split_title(text: str) -> tuple[str, str]:
    if text.startswith("#"):
        first_line, _, rest = text.partition("\n")
        return first_line[1:].strip(), rest
    return "", text


def tokens_from(text: str) -> list[str]:
    return [normalize_token(match.group(0)) for match in TOKEN_RE.finditer(text)]


def phrase_variants(words: Iterable[str]) -> list[str]:
    parts = [word for word in words if word]
    if len(parts) < 2:
        return []

    lower = [part.lower() for part in parts]
    title = "".join(part[:1].upper() + part[1:] for part in lower)
    return [
        "".join(lower),
        title,
        "-".join(lower),
        "_".join(lower),
    ]


def compact_variants(term: str) -> list[str]:
    if term.isascii():
        return [term]
    variants = [term]
    folded = ascii_fold(term)
    if folded and folded != term and folded not in variants:
        variants.append(folded)
    return variants


def add_term(
    counter: Counter[str],
    term: str,
    weight: int,
    min_len: int,
    max_len: int,
    stopwords: set[str],
    keepwords: set[str],
    max_zipf: float | None = None,
) -> None:
    if is_candidate(term.lower(), min_len, max_len, stopwords, keepwords, max_zipf):
        for variant in compact_variants(term):
            counter[variant] += weight


def add_phrase_components(
    counter: Counter[str],
    words: Iterable[str],
    weight: int,
    min_len: int,
    max_len: int,
    stopwords: set[str],
    keepwords: set[str],
) -> None:
    for word in words:
        if word in keepwords or word not in stopwords:
            add_term(counter, word, weight, min_len, max_len, stopwords, keepwords)


def collect_capitalized_terms(text: str, stopwords: set[str], keepwords: set[str]) -> Iterable[list[str]]:
    current: list[str] = []
    for match in CAPITALIZED_RE.finditer(text):
        raw = match.group(0)
        normalized = normalize_token(raw)
        if raw[:1].isupper() and (normalized in keepwords or normalized not in stopwords) and not normalized.isdigit():
            current.append(normalized)
            continue
        if len(current) >= 2:
            yield current
        current = []
    if len(current) >= 2:
        yield current


def add_phrases(
    counter: Counter[str],
    component_counter: Counter[str],
    words: list[str],
    weight: int,
    min_len: int,
    max_len: int,
    stopwords: set[str],
    keepwords: set[str],
    max_zipf: float | None,
) -> None:
    filtered = [word for word in words if (word in keepwords or word not in stopwords) and not word.isdigit()]
    for size in (2, 3):
        for index in range(0, len(filtered) - size + 1):
            phrase_words = filtered[index : index + size]
            if max_zipf is not None and all(is_common_word(word, max_zipf, keepwords) for word in phrase_words):
                continue
            add_phrase_components(counter, phrase_words, weight, min_len, max_len, stopwords, keepwords)
            add_phrase_components(component_counter, phrase_words, weight, min_len, max_len, stopwords, keepwords)
            for phrase in phrase_variants(filtered[index : index + size]):
                add_term(counter, phrase, weight, min_len, max_len, stopwords, keepwords)


def read_jsonl(
    path: Path,
    min_len: int,
    max_len: int,
    stopwords: set[str],
    keepwords: set[str],
    common_word_max: float | None,
    body_phrases: bool,
    body_names: bool,
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    words: Counter[str] = Counter()
    terms: Counter[str] = Counter()
    names: Counter[str] = Counter()

    for line in iter_jsonl_lines(path, f"Reading {path.name}"):
        if not line.strip():
            continue
        title, body = split_title(json_line_text(line))
        title_tokens = tokens_from(title)
        body_tokens = tokens_from(body)

        for token in title_tokens:
            add_term(words, token, 8, min_len, max_len, stopwords, keepwords, common_word_max)
        for token in body_tokens:
            add_term(words, token, 1, min_len, max_len, stopwords, keepwords, common_word_max)

        add_phrases(terms, words, title_tokens, 12, min_len, max_len, stopwords, keepwords, common_word_max)
        if body_phrases:
            add_phrases(terms, words, body_tokens, 1, min_len, max_len, stopwords, keepwords, common_word_max)

        for capitalized in collect_capitalized_terms(title, stopwords, keepwords):
            add_phrase_components(names, capitalized[:4], 12, min_len, max_len, stopwords, keepwords)
            add_phrase_components(words, capitalized[:4], 12, min_len, max_len, stopwords, keepwords)
            for phrase in phrase_variants(capitalized[:4]):
                add_term(names, phrase, 12, min_len, max_len, stopwords, keepwords)
        if body_names:
            for capitalized in collect_capitalized_terms(body, stopwords, keepwords):
                if len(capitalized) <= 5:
                    add_phrase_components(names, capitalized[:4], 2, min_len, max_len, stopwords, keepwords)
                    add_phrase_components(words, capitalized[:4], 2, min_len, max_len, stopwords, keepwords)
                    for phrase in phrase_variants(capitalized[:4]):
                        add_term(names, phrase, 2, min_len, max_len, stopwords, keepwords)

    return words, terms, names


def ranked(counter: Counter[str], min_score: int, limit: int) -> list[str]:
    items = [(term, score) for term, score in counter.items() if score >= min_score]
    items.sort(key=lambda item: (-item[1], len(item[0]), item[0].lower()))
    if limit > 0:
        items = items[:limit]
    return [term for term, _ in items]


def scored_count(counter: Counter[str], min_score: int) -> int:
    return sum(1 for score in counter.values() if score >= min_score)


def write_list(path: Path, entries: Iterable[str]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as outfile:
        for entry in entries:
            outfile.write(entry)
            outfile.write("\n")
            count += 1
    return count


def output_path_for(prefix: Path) -> Path:
    if prefix.suffix:
        return prefix
    return prefix.with_suffix(".txt")


def default_output_path(input_path: Path) -> Path:
    name = input_path.stem.replace("@", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    if not name:
        name = "wordlist"
    return Path("wordlists") / f"{name}.txt"


def build_wordlist(args: argparse.Namespace, input_path: Path, output_path: Path) -> int:
    input_size = input_path.stat().st_size
    body_phrases, body_names, common_word_filter, mode_notes = extraction_plan(args, input_size)
    stopwords = set(STOPWORDS)
    keepwords = set()
    if not args.no_default_excludes:
        for path in DEFAULT_EXCLUDE_FILES:
            if path.exists():
                stopwords.update(load_word_file(path))
    for path in args.exclude_file:
        stopwords.update(load_word_file(path))
    keepwords.update(
        load_title_keepwords(
            input_path,
            args.title_keep_score,
            stopwords,
            None if args.no_wordlist_cache else args.wordlist_cache_dir,
            args.refresh_wordlist_cache,
        )
    )
    for path in args.keep_file:
        keepwords.update(load_word_file(path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    words, terms, names = read_jsonl(
        input_path,
        args.min_len,
        args.max_len,
        stopwords,
        keepwords,
        args.common_word_max if common_word_filter else None,
        body_phrases,
        body_names,
    )

    word_entries = ranked(words, args.min_word_score, args.max_words)
    term_entries = ranked(terms, args.min_term_score, args.max_terms)
    name_entries = ranked(names, args.min_name_score, args.max_names)
    title_counter = Counter(
        {
            word: words.get(word, 0) + terms.get(word, 0) + names.get(word, 0)
            for word in keepwords
            if word in words or word in terms or word in names
        }
    )
    title_entries = ranked(title_counter, 1, 0)

    seen = set()
    base_entries = []
    for entry in [*title_entries, *name_entries, *term_entries, *word_entries]:
        if entry not in seen:
            seen.add(entry)
            base_entries.append(entry)
        if args.max_base > 0 and len(base_entries) >= args.max_base:
            break

    count = write_list(output_path, base_entries)
    common_cache = cached_common_word.cache_info()
    print(
        "stats: "
        f"mode={','.join(mode_notes)} "
        f"titles={len(title_entries)}/{len(keepwords)} "
        f"names={len(name_entries)}/{scored_count(names, args.min_name_score)} "
        f"terms={len(term_entries)}/{scored_count(terms, args.min_term_score)} "
        f"words={len(word_entries)}/{scored_count(words, args.min_word_score)} "
        f"max-base={args.max_base or 'unlimited'} "
        f"common-cache={common_cache.hits}/{common_cache.misses}"
    )
    print(f"wordlist: {count} -> {output_path}")
    return count


def extraction_plan(args: argparse.Namespace, input_size: int) -> tuple[bool, bool, bool, list[str]]:
    body_phrases = args.body_phrases
    body_names = args.body_names
    common_word_filter = args.common_word_filter
    notes = ["full"]

    if args.auto_tune and input_size >= LARGE_INPUT_BYTES:
        notes = ["auto-large"]
        if body_phrases is None:
            body_phrases = False
        if input_size >= HUGE_INPUT_BYTES:
            notes = ["auto-huge"]
            if body_names is None:
                body_names = False
            if common_word_filter is None:
                common_word_filter = False

    if body_phrases is None:
        body_phrases = True
    if body_names is None:
        body_names = True
    if common_word_filter is None:
        common_word_filter = True

    if not body_phrases:
        notes.append("no-body-phrases")
    if not body_names:
        notes.append("no-body-names")
    if not common_word_filter:
        notes.append("no-common-word-filter")
    return body_phrases, body_names, common_word_filter, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one compact target-specific wordlist from scraped Fandom JSONL.")
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Input JSONL file(s). With one input, an optional explicit output path may follow.",
    )
    parser.add_argument("--min-len", type=int, default=4)
    parser.add_argument("--max-len", type=int, default=32)
    parser.add_argument("--min-word-score", type=int, default=3)
    parser.add_argument("--min-term-score", type=int, default=4)
    parser.add_argument("--min-name-score", type=int, default=4)
    parser.add_argument(
        "--exclude-file",
        action="append",
        type=Path,
        default=[],
        help="Additional dictionary/stopword file to exclude, one word per line. Can be repeated.",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Do not load dictionaries/common-english.txt and dictionaries/common-german.txt automatically.",
    )
    parser.add_argument(
        "--keep-file",
        action="append",
        type=Path,
        default=[],
        help="Domain-specific words to keep even if they are common or present in an exclude file. Can be repeated.",
    )
    parser.add_argument(
        "--common-word-max",
        type=float,
        default=5.2,
        help="Suppress single words this common or more in English/German, using wordfreq if installed.",
    )
    common_group = parser.add_mutually_exclusive_group()
    common_group.add_argument("--common-word-filter", dest="common_word_filter", action="store_true", help="Force-enable wordfreq filtering")
    common_group.add_argument("--no-common-word-filter", dest="common_word_filter", action="store_false", help="Disable wordfreq lookups for faster runs")
    parser.set_defaults(common_word_filter=None)
    phrases_group = parser.add_mutually_exclusive_group()
    phrases_group.add_argument("--body-phrases", dest="body_phrases", action="store_true", help="Force-enable 2/3-word phrase variants from article bodies")
    phrases_group.add_argument("--no-body-phrases", dest="body_phrases", action="store_false", help="Do not generate 2/3-word phrase variants from article bodies")
    parser.set_defaults(body_phrases=None)
    names_group = parser.add_mutually_exclusive_group()
    names_group.add_argument("--body-names", dest="body_names", action="store_true", help="Force-enable capitalized name variants from article bodies")
    names_group.add_argument("--no-body-names", dest="body_names", action="store_false", help="Do not generate capitalized name variants from article bodies")
    parser.set_defaults(body_names=None)
    parser.add_argument("--no-auto-tune", dest="auto_tune", action="store_false", help="Disable automatic large-input resource tuning")
    parser.set_defaults(auto_tune=True)
    parser.add_argument(
        "--title-keep-score",
        type=int,
        default=1,
        help="Keep words that appear in at least this many page titles, even if common.",
    )
    parser.add_argument("--max-words", type=int, default=0, help="Cap ranked single-word entries. 0 disables this cap.")
    parser.add_argument("--max-terms", type=int, default=0, help="Cap ranked multi-word/phrase entries. 0 disables this cap.")
    parser.add_argument("--max-names", type=int, default=0, help="Cap ranked capitalized/name entries. 0 disables this cap.")
    parser.add_argument("--max-base", type=int, default=0, help="Cap final unique output entries. 0 disables this cap.")
    parser.add_argument(
        "--wordlist-cache-dir",
        type=Path,
        default=DEFAULT_WORDLIST_CACHE_DIR,
        help=f"Directory for cached title keepwords (default: {DEFAULT_WORDLIST_CACHE_DIR})",
    )
    parser.add_argument("--no-wordlist-cache", action="store_true", help="Disable wordlist helper caches")
    parser.add_argument("--refresh-wordlist-cache", action="store_true", help="Rebuild cached title keepwords")
    args = parser.parse_args()

    if args.min_len < 1 or args.max_len < args.min_len:
        parser.error("length bounds are invalid")
    if min(args.max_words, args.max_terms, args.max_names, args.max_base) < 0:
        parser.error("max limits must be 0 or greater")
    if zipf_frequency is None and args.common_word_max is not None and args.common_word_filter is not False:
        print("wordfreq is not installed; falling back to built-in stopword filtering only")

    inputs = args.paths
    explicit_output = None
    if len(args.paths) == 2 and args.paths[0].suffix == ".jsonl" and args.paths[1].suffix != ".jsonl":
        inputs = [args.paths[0]]
        explicit_output = args.paths[1]
    elif len(args.paths) > 1 and any(path.suffix != ".jsonl" for path in args.paths):
        parser.error("multiple-input mode only accepts .jsonl paths; explicit output is only supported with one input")

    for input_path in inputs:
        output_path = output_path_for(explicit_output) if explicit_output else default_output_path(input_path)
        build_wordlist(args, input_path, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
