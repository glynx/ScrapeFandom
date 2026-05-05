import argparse
import json
import os
import re

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert WikiExtractor JSON output to one JSONL corpus.")
    parser.add_argument("input_dir", help="Directory with wiki json files")
    parser.add_argument("output", help="JSONL output path")
    args = parser.parse_args()

    print(convert_json_dir(args.input_dir, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
