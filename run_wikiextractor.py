import re
import sys
import warnings


real_compile = re.compile
warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"wikiextractor\..*")


def compile_compatible(pattern, flags=0):
    try:
        return real_compile(pattern, flags)
    except re.PatternError as exc:
        if isinstance(pattern, str) and "(?i)" in pattern and "global flags" in str(exc):
            return real_compile(pattern.replace("(?i)", ""), flags | re.IGNORECASE)
        raise


re.compile = compile_compatible

from wikiextractor.WikiExtractor import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
