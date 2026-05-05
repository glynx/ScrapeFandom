import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable

from tqdm import tqdm

try:
    from wordfreq import zipf_frequency
except ImportError:
    zipf_frequency = None


TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
CAPITALIZED_RE = re.compile(r"\b[^\W_\d][^\W_]*(?:['-][^\W_]+)?\b", re.UNICODE)
DEFAULT_EXCLUDE_FILES = [
    Path("dictionaries/common-english.txt"),
    Path("dictionaries/common-german.txt"),
]

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


def collect_title_keepwords(path: Path, min_title_count: int, stopwords: set[str]) -> set[str]:
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8") as infile:
        for line in infile:
            if not line.strip():
                continue
            title, _ = split_title(json.loads(line).get("text", ""))
            counts.update(token for token in set(tokens_from(title)) if token and token not in DOMAIN_STOPWORDS)
    return {token for token, count in counts.items() if count >= min_title_count}


def is_common_word(token: str, max_zipf: float, keepwords: set[str]) -> bool:
    if zipf_frequency is None or token in keepwords or not token.isalpha():
        return False
    return max(zipf_frequency(token, "en"), zipf_frequency(token, "de")) >= max_zipf


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
    if token.isdigit():
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
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    words: Counter[str] = Counter()
    terms: Counter[str] = Counter()
    names: Counter[str] = Counter()

    with path.open(encoding="utf-8") as infile:
        for line in tqdm(infile, desc=f"Reading {path.name}"):
            if not line.strip():
                continue
            data = json.loads(line)
            title, body = split_title(data.get("text", ""))
            title_tokens = tokens_from(title)
            body_tokens = tokens_from(body)

            for token in title_tokens:
                add_term(words, token, 8, min_len, max_len, stopwords, keepwords, common_word_max)
            for token in body_tokens:
                add_term(words, token, 1, min_len, max_len, stopwords, keepwords, common_word_max)

            add_phrases(terms, words, title_tokens, 12, min_len, max_len, stopwords, keepwords, common_word_max)
            add_phrases(terms, words, body_tokens, 1, min_len, max_len, stopwords, keepwords, common_word_max)

            for capitalized in collect_capitalized_terms(title, stopwords, keepwords):
                add_phrase_components(names, capitalized[:4], 12, min_len, max_len, stopwords, keepwords)
                add_phrase_components(words, capitalized[:4], 12, min_len, max_len, stopwords, keepwords)
                for phrase in phrase_variants(capitalized[:4]):
                    add_term(names, phrase, 12, min_len, max_len, stopwords, keepwords)
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
    return [term for term, _ in items[:limit]]


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one compact target-specific wordlist from scraped Fandom JSONL.")
    parser.add_argument("input", type=Path, help="Input JSONL file produced by json2jsonl.py")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="Output wordlist path. Defaults to wordlists/<input-stem>.txt, with @ replaced by _.",
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
        default=4.3,
        help="Suppress single words this common or more in English/German, using wordfreq if installed.",
    )
    parser.add_argument(
        "--title-keep-score",
        type=int,
        default=2,
        help="Keep words that appear in at least this many page titles, even if common.",
    )
    parser.add_argument("--max-words", type=int, default=50000)
    parser.add_argument("--max-terms", type=int, default=200000)
    parser.add_argument("--max-names", type=int, default=100000)
    parser.add_argument("--max-base", type=int, default=250000)
    args = parser.parse_args()

    if args.min_len < 1 or args.max_len < args.min_len:
        parser.error("length bounds are invalid")
    if zipf_frequency is None and args.common_word_max is not None:
        print("wordfreq is not installed; falling back to built-in stopword filtering only")

    stopwords = set(STOPWORDS)
    keepwords = set()
    if not args.no_default_excludes:
        for path in DEFAULT_EXCLUDE_FILES:
            if path.exists():
                stopwords.update(load_word_file(path))
    for path in args.exclude_file:
        stopwords.update(load_word_file(path))
    keepwords.update(collect_title_keepwords(args.input, args.title_keep_score, stopwords))
    for path in args.keep_file:
        keepwords.update(load_word_file(path))

    output_path = output_path_for(args.output) if args.output else default_output_path(args.input)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    words, terms, names = read_jsonl(
        args.input,
        args.min_len,
        args.max_len,
        stopwords,
        keepwords,
        args.common_word_max,
    )

    word_entries = ranked(words, args.min_word_score, args.max_words)
    term_entries = ranked(terms, args.min_term_score, args.max_terms)
    name_entries = ranked(names, args.min_name_score, args.max_names)

    seen = set()
    base_entries = []
    for entry in [*name_entries, *term_entries, *word_entries]:
        if entry not in seen:
            seen.add(entry)
            base_entries.append(entry)
        if len(base_entries) >= args.max_base:
            break

    count = write_list(output_path, base_entries)
    print(f"wordlist: {count} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
