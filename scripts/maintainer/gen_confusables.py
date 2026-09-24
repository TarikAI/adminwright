#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regenerate the confusables block in scripts/admin_console_manifest.py.

Maintainers only; the validator never runs this. Download the four Unicode data files
into one folder, then:

    python scripts/maintainer/gen_confusables.py --data-dir <folder>          # rewrite the block
    python scripts/maintainer/gen_confusables.py --data-dir <folder> --check  # exit 1 if stale

  confusables.txt            https://www.unicode.org/Public/security/latest/confusables.txt
  DerivedCoreProperties.txt  https://www.unicode.org/Public/UCD/latest/ucd/DerivedCoreProperties.txt
  DerivedAge.txt             https://www.unicode.org/Public/UCD/latest/ucd/DerivedAge.txt
  UnicodeData.txt            https://www.unicode.org/Public/UCD/latest/ucd/UnicodeData.txt

Rule A (UTS #39): a non-ASCII code point X maps to the ASCII letter or digit c when
skeleton(X) == skeleton(c), where skeleton() is NFD, each character replaced by its
confusables.txt prototype, NFD again. X maps to "m" when its skeleton is "rn" (the
skeleton of "m"), and to "l", "i" or "1" when it lands in that class, picking the member
whose case matches X (capital iota -> "i", palochka -> "l", Arabic-Indic one -> "1").

Rule B (supplement): UTS #39 sends small-capital-like letters to non-ASCII prototypes
(kappa, Cyrillic ka and small capital K -> U+0138 KRA; small capital M and Cyrillic em ->
U+028D TURNED W; ...). A reader of MOCK in small capitals reads "mock", so every class
whose prototype is a LATIN LETTER SMALL CAPITAL <X>, KRA or TURNED W maps to that letter.

Entries NFKD already produces for code points assigned by Unicode 13.0 (Python 3.9's
database) are left out: the consumer runs NFKD first, and decompositions never change
once assigned. Newer code points keep an explicit entry because an older interpreter
cannot decompose them. Overlay letters (o-slash, l-stroke) are not folded: Danish
"Mockelbjerg" with an o-slash is not the word mock.
"""

import argparse
import re
import string
import sys
import unicodedata
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "admin_console_manifest.py"
BEGIN = "# BEGIN GENERATED: confusables."
END = "# END GENERATED: confusables\n"
PYTHON39_UNICODE = (13, 0)


def data_lines(folder, name):
    with open(str(folder / name), encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].strip()
            if line:
                yield [field.strip() for field in line.split(";")]


def header_value(folder, name, key):
    with open(str(folder / name), encoding="utf-8-sig") as handle:
        for raw in handle:
            if raw.startswith("# " + key + ":"):
                return raw.split(":", 1)[1].strip()
    return "?"


def load(folder):
    confusables = {}
    for fields in data_lines(folder, "confusables.txt"):
        source = [int(x, 16) for x in fields[0].split()]
        confusables[source[0]] = "".join(chr(int(x, 16)) for x in fields[1].split())
    names, categories = {}, {}
    with open(str(folder / "UnicodeData.txt"), encoding="utf-8") as handle:
        for raw in handle:
            fields = raw.split(";")
            names[int(fields[0], 16)] = fields[1]
            categories[int(fields[0], 16)] = fields[2]
    age = {}
    for fields in data_lines(folder, "DerivedAge.txt"):
        low, _, high = fields[0].partition("..")
        version = tuple(int(x) for x in fields[1].split("."))
        for point in range(int(low, 16), int(high or low, 16) + 1):
            age[point] = version
    ignorable = []
    for fields in data_lines(folder, "DerivedCoreProperties.txt"):
        if fields[1] == "Default_Ignorable_Code_Point":
            low, _, high = fields[0].partition("..")
            ignorable.append((int(low, 16), int(high or low, 16)))
    ignorable.sort()
    merged = []
    for low, high in ignorable:
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return confusables, names, categories, age, merged


def build(folder):
    confusables, names, categories, age, ignorable = load(folder)

    def category(ch):
        return categories.get(ord(ch)) or unicodedata.category(ch)

    def strip_marks(text):
        return "".join(ch for ch in text if category(ch) not in ("Mn", "Me"))

    def skeleton(text):
        mapped = "".join(confusables.get(ord(ch), ch) for ch in unicodedata.normalize("NFD", text))
        return unicodedata.normalize("NFD", mapped)

    classes = {}
    for ch in string.ascii_letters + string.digits:
        classes.setdefault(skeleton(ch), []).append(ch)

    def choose(candidates, source):
        upper = [c for c in candidates if c.isupper()]
        lower = [c for c in candidates if c.islower()]
        digits = [c for c in candidates if c.isdigit()]
        cat = category(source)
        if cat == "Nd":
            order = digits + upper + lower
        elif cat in ("Lu", "Lt"):
            order = upper + lower + digits
        else:
            order = lower + upper + digits
        return order[0].lower()

    supplement = {0x0138: "k", 0x028D: "m"}
    small_capital = re.compile(r"^LATIN LETTER SMALL CAPITAL ([A-Z])$")
    for point, name in names.items():
        match = small_capital.match(name)
        if match:
            supplement[point] = match.group(1).lower()

    combined = {}
    for point in sorted(set(confusables) | set(supplement)):
        if point < 0x80:
            continue
        key = skeleton(chr(point))
        if key in classes:
            combined[point] = choose(classes[key], chr(point))
        elif len(key) == 1 and ord(key) in supplement:
            combined[point] = supplement[ord(key)]
        elif point in supplement:
            combined[point] = supplement[point]

    kept = {}
    for point, target in sorted(combined.items()):
        ch = chr(point)
        nfkd = strip_marks(unicodedata.normalize("NFKD", ch))
        if nfkd == ch:
            kept[point] = target
            continue
        via_nfkd = "".join(combined.get(ord(c), c) for c in nfkd).lower()
        if age.get(point, (99, 0)) <= PYTHON39_UNICODE:
            continue  # NFKD runs first and already yields this (or disagrees and wins)
        kept[point] = via_nfkd if len(via_nfkd) == 1 and via_nfkd.isascii() and via_nfkd.isalnum() else target

    def wrap(items):
        lines, line = [], "    "
        for item in items:
            if len(line) + len(item) > 96:
                lines.append(line.rstrip())
                line = "    "
            line += item
        lines.append(line.rstrip())
        return "\n".join(lines)

    version = header_value(folder, "confusables.txt", "Version")
    dated = header_value(folder, "confusables.txt", "Date")
    derived = header_value(folder, "DerivedCoreProperties.txt", "Date")
    return (
        BEGIN + " scripts/maintainer/gen_confusables.py rewrites this block.\n"
        "# UTS #39 confusables.txt %s (%s) and DerivedCoreProperties.txt (%s).\n"
        "# Code point -> the lower-case ASCII letter or digit it is confusable with: UTS #39 skeleton\n"
        "# equality, plus small capitals, kappa, and Cyrillic ka and em, whose own prototypes are not\n"
        "# ASCII. Entries NFKD already produces on Unicode 13.0 (Python 3.9) are left out, so the\n"
        "# table is applied after NFKD.\n"
        "CONFUSABLE_TO_ASCII = {\n%s\n}\n"
        "# Default_Ignorable_Code_Point ranges, inclusive: they render as nothing.\n"
        "DEFAULT_IGNORABLE_RANGES = (\n%s\n)\n" + END
    ) % (
        version, dated, derived,
        wrap("0x%X:%r," % (point, target) for point, target in sorted(kept.items())),
        wrap("(0x%X,0x%X)," % pair for pair in ignorable),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", required=True, type=Path, help="folder holding the four Unicode files")
    parser.add_argument("--check", action="store_true", help="exit 1 if the block is not current")
    args = parser.parse_args()
    text = TARGET.read_text(encoding="utf-8")
    start = text.index(BEGIN)
    end = text.index(END, start) + len(END)
    block = build(args.data_dir)
    if args.check:
        current = text[start:end] == block
        print("confusables block is " + ("current" if current else "STALE"))
        return 0 if current else 1
    with open(str(TARGET), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text[:start] + block + text[end:])
    print("rewrote the confusables block in " + str(TARGET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
