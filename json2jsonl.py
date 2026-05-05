import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tqdm import tqdm


URL_RE = r'&lt;a href="(.*?)"&gt;(.*?)&lt;/a&gt;'


def convert_json_dir(input_dir: str, output: str) -> int:
    directories = os.listdir(input_dir)
    counter = 0
    with open(output, "w", encoding="utf-8") as fout:
        for directory in tqdm(directories):
            directory_path = os.path.join(input_dir, directory)
            for filename in tqdm(os.listdir(directory_path), desc="Processing " + directory):
                if not filename.startswith("wiki"):
                    continue

                path = os.path.join(directory_path, filename)
                with open(path, encoding="utf-8") as fin:
                    for line in fin:
                        data = json.loads(line)

                        if data["text"] == "":
                            continue

                        title = "#" + data["title"] + "\n"
                        text = re.sub(URL_RE, r"\2", data["text"])
                        text = re.sub(r"\(\s+", "(", text)
                        output_json = {
                            "meta": data["url"],
                            "text": title + text.replace("()", "").replace("\u00a0", " ").replace(" , ", ", "),
                        }
                        counter += 1
                        fout.write(json.dumps(output_json, ensure_ascii=False) + "\n")
    return counter


def extract_xml_to_json_dir(xml_path: Path, output_dir: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "run_wikiextractor",
            str(xml_path),
            "--no-templates",
            "-l",
            "--json",
            "-o",
            str(output_dir),
        ],
        check=True,
    )


def convert_input(input_path: Path, output_path: Path, keep_extracted: bool) -> int:
    if input_path.suffix != ".xml":
        return convert_json_dir(str(input_path), str(output_path))

    if keep_extracted:
        extract_dir = output_path.with_suffix("")
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        cleanup_dir = False
    else:
        extract_dir = Path(tempfile.mkdtemp(prefix=f"{input_path.stem}-", dir="/tmp"))
        cleanup_dir = True

    extract_xml_to_json_dir(input_path, extract_dir)
    try:
        return convert_json_dir(str(extract_dir), str(output_path))
    finally:
        if cleanup_dir:
            shutil.rmtree(extract_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert WikiExtractor JSON output or a MediaWiki XML dump to one JSONL corpus.")
    parser.add_argument("input", type=Path, help="WikiExtractor output directory or MediaWiki XML dump")
    parser.add_argument("output", type=Path, help="JSONL output path")
    parser.add_argument("--keep-extracted", action="store_true", help="Keep extracted JSON directory when input is XML")
    args = parser.parse_args()

    try:
        print(convert_input(args.input, args.output, args.keep_extracted))
        return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
