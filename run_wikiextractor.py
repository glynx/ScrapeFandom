import re
import sys


real_compile = re.compile


def compile_compatible(pattern, flags=0):
    try:
        return real_compile(pattern, flags)
    except re.PatternError as exc:
        if isinstance(pattern, str) and "(?i)" in pattern and "global flags" in str(exc):
            pattern = pattern.replace("(((?i)", "(((?i:")
            pattern = pattern.replace("((?i)", "((?i:")
            pattern = pattern.replace(r')[^][<>"\x00-\x20\x7F\s]', r'))[^][<>"\x00-\x20\x7F\s]', 1)
            pattern = pattern.replace("jpeg)$", "jpeg))$")
            return real_compile(pattern, flags)
        raise


re.compile = compile_compatible

from wikiextractor.WikiExtractor import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
