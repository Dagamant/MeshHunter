"""Release helpers for .github/workflows/release.yml.

  release_info.py version [TAG]   print the version; with TAG (e.g. v0.1.0 or
                                  refs/tags/v0.1.0) also check it matches
  release_info.py notes VERSION   print that version's CHANGELOG.md section

The version lives in three places that must agree: meshhunter/__init__.py
(Python package), cardputer-zero/CMakeLists.txt (the .deb) and
cardputer-zero/app-builder.json. tests/test_packaging.py checks the same.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def versions():
    init = (ROOT / "meshhunter" / "__init__.py").read_text(encoding="utf-8")
    cmake = (ROOT / "cardputer-zero" / "CMakeLists.txt").read_text(encoding="utf-8")
    builder = json.loads((ROOT / "cardputer-zero" / "app-builder.json").read_text(encoding="utf-8"))
    return {
        "meshhunter/__init__.py": re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1),
        "cardputer-zero/CMakeLists.txt": re.search(r"project\(\s*meshhunter\s+VERSION\s+([0-9.]+)", cmake).group(1),
        "cardputer-zero/app-builder.json": builder["version"],
    }


def changelog_section(version):
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else None


def main(argv):
    if len(argv) >= 2 and argv[1] == "version":
        found = versions()
        if len(set(found.values())) != 1:
            sys.exit("version mismatch: " + ", ".join(f"{k}={v}" for k, v in found.items()))
        version = next(iter(found.values()))
        if len(argv) > 2:
            tag = argv[2].removeprefix("refs/tags/")
            if tag != f"v{version}":
                sys.exit(f"tag {tag} doesn't match version {version} (expected v{version})")
        print(version)
    elif len(argv) == 3 and argv[1] == "notes":
        notes = changelog_section(argv[2])
        if not notes:
            sys.exit(f"CHANGELOG.md has no section for {argv[2]}")
        print(notes)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
