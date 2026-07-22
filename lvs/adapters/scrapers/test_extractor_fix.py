"""
Test: _extract_four_column_table first-writer-wins fix
Simulates old (last-writer-wins) vs new (first-writer-wins) logic
on real HTML evidence files.

Part 1 uses evidence from run 20260721_2038_001 (files named by license number).
Part 2 uses evidence from run 20260722_1428_IN_001 (the ambiguous batch re-run).
"""
import os
import sys
import re
from html.parser import HTMLParser

# Screenshot rows come from the original run that captured the bug
EVIDENCE_DIR_SCREENSHOT = (
    r"c:\Users\n676150\Downloads\PSV_TEST_Sprint_1_Final 1"
    r"\PSV_TEST_Sprint_1_Final\PSV_TEST_Sprint_1\PSV_TEST\PSV_DEV"
    r"\Evidence\202607\20260721_2038_001"
)

# The ambiguous batch re-run has more detail pages for overall stats
EVIDENCE_DIR_BATCH = (
    r"c:\Users\n676150\Downloads\PSV_TEST_Sprint_1_Final 1"
    r"\PSV_TEST_Sprint_1_Final\PSV_TEST_Sprint_1\PSV_TEST\PSV_DEV"
    r"\Evidence\202607\20260722_1428_IN_001"
)

# (person_name, license_number) from screenshot
SCREENSHOT_ROWS = [
    ("Alexa Howder",        "34009963A"),
    ("Gloria Hood",         "35000914A"),
    ("Andrea McClellan",    "28294899A"),
    ("Latanya Neely",       "28294583A"),
    ("Morgan Custer",       "28291527A"),
    ("Elizabeth Schraeder", "71017491A"),
    ("Kristen Iseler",      "39003266A"),
    ("Amy Wall",            "71003341A"),
    ("Ranada Dalton",       "39003303A"),
    ("Bethany Lubenow",     "10001172A"),
]


# ---------------------------------------------------------------------------
# Minimal HTML parser replicating _extract_four_column_table behaviour
# ---------------------------------------------------------------------------

class IndianaDetailParser(HTMLParser):
    """
    Tracks all <tr> rows (including nested) and the text of each <td>.
    Mirrors Playwright where row.locator('td') finds ALL descendant <td>s.
    """

    def __init__(self):
        super().__init__()
        self.all_rows: list[list[str]] = []
        self._row_stack: list[list[str]] = []
        self._cell_stack: list[list[str]] = []
        self._cell_hidden: list[bool] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row_stack.append([])
        elif tag == "td":
            attrs_d = {k.lower(): (v or "") for k, v in attrs}
            style = attrs_d.get("style", "").replace(" ", "").lower()
            hidden = "display:none" in style
            self._cell_hidden.append(hidden)
            self._cell_stack.append([])

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "td":
            if self._cell_stack:
                parts = self._cell_stack.pop()
                hidden = self._cell_hidden.pop() if self._cell_hidden else False
                text = (" ".join(parts).strip()) if not hidden else ""
                for row in self._row_stack:
                    row.append(text)
        elif tag == "tr":
            if self._row_stack:
                row = self._row_stack.pop()
                if row:
                    self.all_rows.append(row)

    def handle_data(self, data: str):
        if self._cell_stack and self._cell_hidden and not self._cell_hidden[-1]:
            text = data.strip()
            if text:
                self._cell_stack[-1].append(text)


def simulate_old(rows: list[list[str]]) -> dict:
    """Last-writer-wins (original buggy behaviour)."""
    result: dict = {}
    for cells in rows:
        n = len(cells)
        if n >= 4:
            for i in range(0, n - 1, 2):
                k = cells[i].rstrip(":").strip()
                v = cells[i + 1].strip() if (i + 1) < n else ""
                if k and "\n" not in k and "\t" not in k:
                    result[k] = v
    return result


def simulate_new(rows: list[list[str]]) -> dict:
    """First-writer-wins (fixed behaviour)."""
    result: dict = {}
    for cells in rows:
        n = len(cells)
        if n >= 4:
            for i in range(0, n - 1, 2):
                k = cells[i].rstrip(":").strip()
                v = cells[i + 1].strip() if (i + 1) < n else ""
                if k and "\n" not in k and "\t" not in k and k not in result:
                    result[k] = v
    return result


def process_html(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    p = IndianaDetailParser()
    p.feed(html)

    old_raw = simulate_old(p.all_rows)
    new_raw = simulate_new(p.all_rows)

    # Collect ALL "Lic #" values for diagnostics
    lic_occurrences = []
    for cells in p.all_rows:
        n = len(cells)
        if n >= 4:
            for i in range(0, n - 1, 2):
                k = cells[i].rstrip(":").strip()
                v = cells[i + 1].strip() if (i + 1) < n else ""
                if k == "Lic #" and v:
                    lic_occurrences.append(v)

    old_lic = old_raw.get("Lic #", "")
    new_lic = new_raw.get("Lic #", "")
    return old_lic, new_lic, lic_occurrences


def numeric_only(s):
    return re.sub(r"\D", "", str(s or ""))


def lic_match(master, candidate):
    if not master or not candidate:
        return False
    m = numeric_only(master)
    c = numeric_only(candidate)
    if not m or not c:
        return False
    return m == c or m.lstrip("0") == c.lstrip("0")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Redirect stdout to UTF-8 to avoid cp1252 encoding errors
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)

    # ---- PART 1: Screenshot rows ----------------------------------------
    print("=" * 76)
    print("PART 1 -- SCREENSHOT ROWS (evidence from run 20260721_2038_001)")
    print("=" * 76)
    print(f"{'Name':<22} {'Expected':>12}  {'OLD (buggy)':>12}  {'NEW (fixed)':>12}  {'Bug?':>5}  {'Fixed?':>7}")
    print("-" * 76)

    for person, expected_lic in SCREENSHOT_ROWS:
        fname = f"IN_PLA_{expected_lic}_detail_page.html"
        fpath = os.path.join(EVIDENCE_DIR_SCREENSHOT, fname)

        if not os.path.exists(fpath):
            print(f"  {person:<20}  {expected_lic:>12}  FILE NOT FOUND")
            continue

        old_lic, new_lic, all_lics = process_html(fpath)
        bug = old_lic != expected_lic
        fixed = new_lic == expected_lic

        print(
            f"  {person:<20}  {expected_lic:>12}  {old_lic:>12}  {new_lic:>12}"
            f"  {'YES' if bug else 'no':>5}  {'YES' if fixed else 'N/A':>7}"
        )
        if len(all_lics) > 1:
            print(f"    -> {len(all_lics)} 'Lic #' entries found in page: {all_lics}")

    # ---- PART 2: Overall statistics (batch run) -------------------------
    print()
    print("=" * 76)
    print("PART 2 -- BATCH RUN STATISTICS (evidence from run 20260722_1428_IN_001)")
    print("=" * 76)

    if not os.path.isdir(EVIDENCE_DIR_BATCH):
        print(f"  ERROR: Batch evidence directory not found:\n    {EVIDENCE_DIR_BATCH}")
    else:
        primary_files = sorted(
            f for f in os.listdir(EVIDENCE_DIR_BATCH)
            if "_detail_page.html" in f
            and not any(f.endswith(f"_a{n}.html") for n in range(2, 50))
        )
        print(f"  Primary detail pages analysed: {len(primary_files)}")

        total = 0
        bug_count = 0
        fixed_count = 0
        multi_lic_count = 0
        bug_examples = []

        for fname in primary_files:
            fpath = os.path.join(EVIDENCE_DIR_BATCH, fname)
            old_lic, new_lic, all_lics = process_html(fpath)
            total += 1

            if len(all_lics) > 1:
                multi_lic_count += 1

            if old_lic != new_lic:
                bug_count += 1
                if new_lic:
                    fixed_count += 1
                if len(bug_examples) < 15:
                    label = fname.replace("IN_PLA_", "").replace("_detail_page.html", "")
                    bug_examples.append((label, old_lic, new_lic, all_lics))

        print(f"  Pages with multiple 'Lic #' entries : {multi_lic_count}")
        print(f"  Pages where OLD != NEW logic         : {bug_count}")
        print(f"  Pages where NEW gives a license      : {fixed_count}")

        if bug_examples:
            print()
            print("  Sample affected pages (up to 15):")
            print(f"    {'Identifier':<18} {'OLD (wrong)':>14}  {'NEW (correct)':>14}  All occurrences")
            print("    " + "-" * 65)
            for name, old_l, new_l, lics in bug_examples:
                print(f"    {name:<18} {old_l:>14}  {new_l:>14}  {lics}")

    # ---- PART 3: Scoring impact -----------------------------------------
    print()
    print("=" * 76)
    print("PART 3 -- SCORING IMPACT (confidence before vs after fix)")
    print("=" * 76)
    print("  Weights: lic*0.35 + first*0.30 + last*0.20 + pt*0.10 + state*0.05")
    print("  Assuming first=1.0, last=1.0, pt=1.0, state=1.0 (exact match scenario)")
    print("  Threshold for license_present profile: 0.90")
    print()
    print(f"  {'Name':<22} {'Conf OLD':>9}  {'Verdict OLD':<28}  {'Conf NEW':>9}  {'Verdict NEW'}")
    print("  " + "-" * 72)

    for person, expected_lic in SCREENSHOT_ROWS:
        fname = f"IN_PLA_{expected_lic}_detail_page.html"
        fpath = os.path.join(EVIDENCE_DIR_SCREENSHOT, fname)
        if not os.path.exists(fpath):
            continue

        old_lic, new_lic, _ = process_html(fpath)

        old_score = 1.0 if lic_match(expected_lic, old_lic) else 0.0
        new_score = 1.0 if lic_match(expected_lic, new_lic) else 0.0

        old_conf = old_score * 0.35 + 0.30 + 0.20 + 0.10 + 0.05
        new_conf = new_score * 0.35 + 0.30 + 0.20 + 0.10 + 0.05

        old_verdict = "PASS (>= 0.90)" if old_conf >= 0.90 else f"AMBIGUOUS (conf={old_conf:.2f})"
        new_verdict = "PASS (>= 0.90)" if new_conf >= 0.90 else f"AMBIGUOUS (conf={new_conf:.2f})"

        changed = "**FIXED**" if (old_conf < 0.90 and new_conf >= 0.90) else ""
        print(f"  {person:<22} {old_conf:>9.2f}  {old_verdict:<28}  {new_conf:>9.2f}  {new_verdict}  {changed}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
