"""
Mississippi Board of Physical Therapy Scraper

Features:
- Async Playwright (Edge browser)
- CLI input (license -> fallback last_name)
- Handles "No Results" page correctly
- Fieldset-based parsing
- Correct disciplinary action extraction (None / Yes / No)
- Structured JSON output
"""

import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
import argparse
import json
import re
import html

URL = "https://www.msbpt.ms.gov/secure/licenseverification.asp"


async def perform_search(page, license_no: str, last_name: str):
    """
    Performs search and waits for either results or "No Results".
    """

    if license_no:
        await page.fill('input[name="LICENSENO"]', license_no)
    elif last_name:
        await page.fill('input[name="LNAME"]', last_name)

    await page.click('input[type="submit"][value="SEARCH"]')

    # Wait for either results OR "No Results"
    await page.wait_for_function(
        """() => {
            return document.body.innerText.includes("No Results") ||
                   document.querySelectorAll("fieldset.frameset2").length > 0;
        }"""
    )


def parse_card_text(text: str) -> dict:
    """
    Parses license card text into structured fields.
    """

    def extract(pattern):
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    data = {
        "license_type": extract(r"Type:\s*([^\n\r]+?)\s*Status:"),
        "status": extract(r"Status:\s*([^\n\r]+)"),
        "license_number": extract(r"License #:\s*([^\n\r]+?)\s*Expiration Date:"),
        "expiration_date": extract(r"Expiration Date:\s*([^\n\r]+)"),
        "issue_date": extract(r"Issue Date:\s*([^\n\r]+)")
    }

    # Correct disciplinary action (skip "Proof Of Licensure")
    disc_match = re.search(
        r"Disciplinary Action.*?\n.*?\n\s*(None|Yes|No)",
        text,
        re.IGNORECASE | re.DOTALL
    )

    data["disciplinary_action"] = disc_match.group(1).strip() if disc_match else ""

    # Employer extraction
    employer_match = re.search(
        r"Employer\s*(.*?)County:",
        text,
        re.DOTALL | re.IGNORECASE
    )

    if employer_match:
        emp = employer_match.group(1)
        emp = re.sub(r"\s+", " ", emp).strip()
        data["employer"] = emp
    else:
        data["employer"] = ""

    return data


async def extract_records(page) -> list:
    """
    Extracts all license records.
    """

    records = []
    cards = await page.query_selector_all("fieldset.frameset2")

    for card in cards:
        try:
            name_elem = await card.query_selector(".dryneedlingname")
            if not name_elem:
                continue

            raw_name = await name_elem.inner_text()
            name = html.unescape(raw_name.split("\n")[0]).strip()

            text = await card.inner_text()
            parsed = parse_card_text(text)

            record = {
                "name": name,
                **parsed
            }

            records.append(record)

        except Exception:
            continue

    return records


async def scrape_one(browser, license_no: str, last_name: str) -> dict:
    """
    Scrapes a single input.
    """

    page = await browser.new_page()

    result = {
        "state": "Mississippi",
        "board": "Mississippi Board of Physical Therapy",
        "search_criteria": {
            "license": license_no,
            "last_name": last_name
        },
        "status": "success",
        "result_count": 0,
        "error_message": "",
        "license_details": [],
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "source_url": URL
    }

    try:
        await page.goto(URL)
        await page.wait_for_selector('input[name="LICENSENO"]')

        await perform_search(page, license_no, last_name)

        # ✅ HANDLE "NO RESULTS"
        page_text = await page.inner_text("body")

        if "No Results" in page_text:
            result["result_count"] = 0
            result["error_message"] = "No results found"
            await page.close()
            return result

        # ✅ Extract records
        records = await extract_records(page)

        result["license_details"] = records
        result["result_count"] = len(records)

        await page.close()

    except Exception as e:
        result["status"] = "failed"
        result["error_message"] = str(e)

    return result


async def scrape_bulk(inputs):
    """
    Runs concurrent scraping.
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=False)

        tasks = [scrape_one(browser, lic, lname) for lic, lname in inputs]
        results = await asyncio.gather(*tasks)

        await browser.close()

    return results


def save_output(data):
    """
    Saves JSON output file.
    """

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"Mississippi_PhysicalTherapy_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(filename)


def parse_args():
    """
    Parses CLI arguments.
    """

    parser = argparse.ArgumentParser()

    parser.add_argument("--license", nargs="*", default=[])
    parser.add_argument("--last_name", nargs="*", default=[])

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    inputs = []

    lic_list = args.license or [None]
    lname_list = args.last_name or [None]

    max_len = max(len(lic_list), len(lname_list))

    for i in range(max_len):
        lic = lic_list[i] if i < len(lic_list) else None
        lname = lname_list[i] if i < len(lname_list) else None
        inputs.append((lic, lname))

    results = asyncio.run(scrape_bulk(inputs))

    save_output(results)