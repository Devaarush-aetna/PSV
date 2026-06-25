#!/usr/bin/env python3
"""
SouthDakota_BoardPsychologists_Psychologist_Scraper.py
Searches South Dakota Board of Examiners of Psychologists roster by name or license number.
"""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import argparse
import json
import re
import sys

from playwright.sync_api import sync_playwright
import fitz

BASE_URL = "https://www.sdboards.org/dss/psych/verify/"
FILE_PREFIX = "SouthDakota_Psych_roster_"
AGE_LIMIT_DAYS = 7
STATE = "South Dakota"
SOURCE = "South Dakota Board of Examiners of Psychologists"
PDF_FOLDER = "pdfs"


def make_timestamp(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M")


def find_latest_pdf(target_dir: Path) -> Path:
    files = sorted(target_dir.glob(f"{FILE_PREFIX}*.pdf"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return files[0] if files else None


def is_fresh(path: Path, days: int) -> bool:
    if not path or not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime) < timedelta(days=days)


def locate_pdf_url(page) -> str:
    try:
        link = page.query_selector("a[href*='pdf']")
        if link:
            href = link.get_attribute("href")
            if href:
                return urljoin(page.url, href)
    except Exception:
        pass
    return None


def download_via_playwright(page, pdf_url: str, save_path: Path):
    save_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with page.expect_download(timeout=30000) as dl_info:
            page.goto(pdf_url, wait_until="domcontentloaded", timeout=10000)
            download = dl_info.value
            download.save_as(str(save_path))
    except Exception as e:
        try:
            with page.expect_download(timeout=30000) as dl_info:
                page.evaluate(f"window.location.href = '{pdf_url}'")
                download = dl_info.value
                download.save_as(str(save_path))
        except Exception:
            raise RuntimeError(f"Failed to download PDF: {e}")


def parse_sd_psych_row(spans: list) -> dict:
    """Parse row spans: Last, First, Level+License or Level,License, EffDate, ExpDate, Discipline"""
    row = {
        "Last Name": "",
        "First Name": "",
        "Level": "",
        "License #": "",
        "Effective Date": "",
        "Expiration Date": "",
        "Discipline": ""
    }

    if len(spans) < 2:
        return row

    row["Last Name"] = spans[0] if spans[0] else ""
    row["First Name"] = spans[1] if len(spans) > 1 and spans[1] else ""

    if len(spans) == 6:
        # Format: Last, First, "Psy.D. 656", EffDate, ExpDate, Discipline
        level_license = spans[2].split() if len(spans) > 2 and spans[2] else []
        row["Level"] = " ".join(level_license[:-1]) if len(level_license) > 1 else level_license[0] if level_license else ""
        row["License #"] = level_license[-1] if len(level_license) > 1 else ""
        row["Effective Date"] = spans[3] if len(spans) > 3 and spans[3] else ""
        row["Expiration Date"] = spans[4] if len(spans) > 4 and spans[4] else ""
        row["Discipline"] = spans[5] if len(spans) > 5 and spans[5] else ""
    elif len(spans) >= 7:
        # Format: Last, First, "Ph.D.", "638", EffDate, ExpDate, Discipline
        row["Level"] = spans[2] if len(spans) > 2 and spans[2] else ""
        row["License #"] = spans[3] if len(spans) > 3 and spans[3] else ""
        row["Effective Date"] = spans[4] if len(spans) > 4 and spans[4] else ""
        row["Expiration Date"] = spans[5] if len(spans) > 5 and spans[5] else ""
        row["Discipline"] = spans[6] if len(spans) > 6 and spans[6] else ""

    return row


def extract_table_rows(pdf_path: Path) -> list:
    rows = []
    doc = fitz.open(str(pdf_path))
    try:
        for page in doc:
            d = page.get_text("dict")
            lines = []
            for block in d.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    spans = []
                    for span in line.get("spans", []):
                        txt = (span.get("text") or "").strip()
                        if not txt:
                            continue
                        x0 = span["bbox"][0]
                        spans.append((x0, txt))
                    if spans:
                        y0 = line.get("bbox", [0, 0, 0, 0])[1]
                        lines.append({"y": y0, "spans": spans})
            if not lines:
                continue

            lines.sort(key=lambda l: l["y"])
            grouped = []
            cur = {"y": lines[0]["y"], "spans": lines[0]["spans"].copy()}
            for ln in lines[1:]:
                if abs(ln["y"] - cur["y"]) < 3:
                    cur["spans"].extend(ln["spans"])
                else:
                    cur["spans"].sort(key=lambda s: s[0])
                    grouped.append(cur)
                    cur = {"y": ln["y"], "spans": ln["spans"].copy()}
            cur["spans"].sort(key=lambda s: s[0])
            grouped.append(cur)

            header_idx = None
            for i, g in enumerate(grouped):
                txt = " ".join(t for _, t in g["spans"])
                if re.search(r"\b(last\s*name|first\s*name|license)\b", txt, re.I):
                    header_idx = i
                    break
            if header_idx is None:
                continue

            data_start = header_idx + 1
            for g in grouped[data_start:]:
                spans = [txt for _, txt in g["spans"]]
                if len(spans) >= 2:
                    parsed_row = parse_sd_psych_row(spans)
                    rows.append(parsed_row)
    finally:
        doc.close()
    return rows


def map_row_to_license_detail(row: dict) -> dict:
    mapped = {
        "Provider Name": "",
        "License Number": "",
        "License Status": "Active",
        "Profession": "Psychologist",
        "License Original Issue Date": "",
        "License Expiration Date": "",
        "Address of Record": "",
        "Discipline on File": "No",
        "Public Complaint": "",
        "Secondary Locations: ...": "",
        "Discipline Admin Action: ...": ""
    }

    first = row.get("First Name", "").strip()
    last = row.get("Last Name", "").strip()
    license_num = row.get("License #", "").strip()
    eff_date = row.get("Effective Date", "").strip()
    exp_date = row.get("Expiration Date", "").strip()
    discipline = row.get("Discipline", "").strip()

    if first and last:
        mapped["Provider Name"] = f"{first} {last}"
    elif last:
        mapped["Provider Name"] = last

    if license_num:
        mapped["License Number"] = license_num

    if eff_date:
        mapped["License Original Issue Date"] = eff_date

    if exp_date:
        mapped["License Expiration Date"] = exp_date

    if discipline:
        mapped["Discipline on File"] = "No" if re.search(r"\b(no|none|n/a)\b", discipline, re.I) else "Yes"

    return mapped


def _normalize_alnum(text: str) -> str:
    return re.sub(r"\W+", "", (text or "")).lower()


def search_pdf(pdf_path: Path, last_name: str = "", first_name: str = "", license_number: str = "") -> list:
    table_rows = extract_table_rows(pdf_path)
    results = []
    q_last_norm = _normalize_alnum(last_name)
    q_first_norm = _normalize_alnum(first_name)
    q_license_norm = _normalize_alnum(license_number)

    for row in table_rows:
        mapped = map_row_to_license_detail(row)
        matched = False

        if q_license_norm:
            if _normalize_alnum(mapped["License Number"]) == q_license_norm:
                matched = True
        else:
            name_norm = _normalize_alnum(mapped["Provider Name"])
            if q_first_norm and q_last_norm:
                if q_first_norm in name_norm and q_last_norm in name_norm:
                    matched = True
            elif q_last_norm and q_last_norm in name_norm:
                matched = True

        if matched:
            results.append(mapped)

    return results


def build_output_json(search_params: dict, license_details: list) -> dict:
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "state": STATE,
        "source": SOURCE,
        "search_params": {
            "last_name": search_params.get("last_name", ""),
            "first_name": search_params.get("first_name", ""),
            "license_number": search_params.get("license_number", "")
        },
        "license_details": license_details,
        "scraped_at": scraped_at,
        "source_url": BASE_URL
    }


def save_json(output: dict, out_dir: Path):
    ts = make_timestamp(datetime.now())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"SouthDakota_Psych_results_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump([output], f, ensure_ascii=False, indent=2)
    print(f"Saved: {out_file}")


def ensure_pdf_available(root: Path) -> Path:
    pdf_dir = root / PDF_FOLDER
    pdf_dir.mkdir(parents=True, exist_ok=True)

    latest = find_latest_pdf(pdf_dir)
    if latest and is_fresh(latest, AGE_LIMIT_DAYS):
        return latest

    pdf_url = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            pdf_url = locate_pdf_url(page)
        except Exception:
            pass
        finally:
            if not pdf_url:
                browser.close()
                raise RuntimeError("Could not locate PDF URL")

            ts = make_timestamp(datetime.now())
            save_path = pdf_dir / f"{FILE_PREFIX}{ts}.pdf"
            download_via_playwright(page, pdf_url, save_path)
            browser.close()
            return save_path


def _parse_full_name(full_name: str) -> tuple:
    """Split 'First Last' or 'Last, First' format"""
    name = full_name.strip()
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        last = parts[0]
        first = parts[1] if len(parts) > 1 else ""
    else:
        parts = name.split()
        if len(parts) == 1:
            first, last = "", parts[0]
        else:
            first, last = parts[0], " ".join(parts[1:])
    return first, last


def main():
    parser = argparse.ArgumentParser(description="Search South Dakota psychologist license verification")
    parser.add_argument("--name", dest="full_name", default="", help="Full name (e.g. 'Philip Murphy' or 'Murphy, Philip')")
    parser.add_argument("--first-name", "--first", "-f", dest="first_name", default="", help="First name")
    parser.add_argument("--last-name", "--last", "-l", dest="last_name", default="", help="Last name")
    parser.add_argument("--license", "-n", dest="license_number", default="", help="License number")
    args = parser.parse_args()

    if args.full_name:
        first, last = _parse_full_name(args.full_name)
        if not args.first_name:
            args.first_name = first
        if not args.last_name:
            args.last_name = last

    if not any([args.first_name, args.last_name, args.license_number]):
        parser.print_help()
        sys.exit(1)

    root = Path.cwd()
    try:
        pdf_file = ensure_pdf_available(root)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    details = search_pdf(pdf_file, last_name=args.last_name, first_name=args.first_name, license_number=args.license_number)

    output = build_output_json(
        {"last_name": args.last_name, "first_name": args.first_name, "license_number": args.license_number},
        details
    )
    save_json(output, root)


if __name__ == "__main__":
    main()
