import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from jsonl2wordlist import default_output_path


def run_command(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, check=True)


def remove_artifact(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
        print(f"removed artifact directory: {path}")
    elif path.exists():
        path.unlink()
        print(f"removed artifact file: {path}")


def ensure_wikiextractor() -> None:
    try:
        import wikiextractor.WikiExtractor  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Missing dependency: wikiextractor. Run `uv sync` first.") from exc


def process_target(target: str, args: argparse.Namespace) -> None:
    xml_path = Path(f"{target}.xml")
    extract_dir = Path(target)
    jsonl_path = Path(f"{target}.jsonl")
    wordlist_path = default_output_path(jsonl_path)

    if not args.skip_scrape:
        scrape_command = [sys.executable, "ScrapeFandom.py", target]
        if args.batch_size is not None:
            scrape_command.extend(["--batch-size", str(args.batch_size)])
        if args.no_export_cache:
            scrape_command.append("--no-cache")
        if args.refresh_export_cache:
            scrape_command.append("--refresh-cache")
        run_command(scrape_command)

    if not args.skip_extract:
        if args.clean and extract_dir.exists():
            shutil.rmtree(extract_dir)
        run_command(
            [
                sys.executable,
                "run_wikiextractor.py",
                str(xml_path),
                "--no-templates",
                "-l",
                "--json",
                "-o",
                str(extract_dir),
            ]
        )

    if not args.skip_jsonl:
        run_command([sys.executable, "json2jsonl.py", str(extract_dir), str(jsonl_path)])

    if not args.no_wordlist:
        wordlist_command = [sys.executable, "jsonl2wordlist.py", str(jsonl_path), str(wordlist_path)]
        if args.max_base is not None:
            wordlist_command.extend(["--max-base", str(args.max_base)])
        run_command(wordlist_command)

    if not args.keep_artifacts:
        if not args.skip_extract:
            remove_artifact(extract_dir)
        if not args.skip_scrape:
            remove_artifact(xml_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Fandom wikis and generate JSONL plus wordlists.")
    parser.add_argument("targets", nargs="+", help="Fandom target(s), e.g. pokemon@de harrypotter")
    parser.add_argument("--batch-size", type=int, help="Pages per export API batch")
    parser.add_argument("--max-base", type=int, help="Maximum entries in generated wordlist")
    parser.add_argument("--skip-scrape", action="store_true", help="Reuse an existing <target>.xml")
    parser.add_argument("--skip-extract", action="store_true", help="Reuse an existing WikiExtractor directory")
    parser.add_argument("--skip-jsonl", action="store_true", help="Reuse an existing <target>.jsonl")
    parser.add_argument("--no-wordlist", action="store_true", help="Only produce XML/JSONL, not a wordlist")
    parser.add_argument("--no-export-cache", action="store_true", help="Disable resumable export batch cache")
    parser.add_argument("--refresh-export-cache", action="store_true", help="Refetch export batches instead of reusing cache")
    parser.add_argument("--clean", action="store_true", help="Delete the extracted JSON directory before extracting")
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep intermediate <target>.xml and extraction directory")
    args = parser.parse_args()

    try:
        ensure_wikiextractor()
        for target in args.targets:
            process_target(target, args)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
