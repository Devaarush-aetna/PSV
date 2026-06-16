"""
Mississippi Optometry Board Async Bulk Scraper

Features:
- Async Playwright with Edge browser
- CLI input (license, last_name, first_name)
- Bulk concurrent scraping
- Handles DataTables pagination correctly
- Scrolls to Next button before clicking
- Uses DOM change detection (correct async usage)
- Saves structured JSON output

Usage:
    python script.py --last_name Cole Smith
    python script.py --license 1066 1128
"""

import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
import argparse
import json
import re

URL = "https://www.ms.gov/msbo/license_renewal/home/licenseverification"


def parse_row_text(text: str) -> dict:
    """
    Parses row text into structured fields.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    data = {
        "name": lines[0] if lines else "",
        "address": "",
        "license_number": "",
        "tpa_number": "",
        "expire_date": "",
        "original_issue_date": "",
        "dpa": "",
        "status": "",
        "disciplinary_action": ""
    }

    def extract(pattern):
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    data["license_number"] = extract(r"License #:\s*(.+)")
    data["tpa_number"] = extract(r"TPA #:\s*(.+)")
    data["expire_date"] = extract(r"Expire Date:\s*(.+?)\s")
    data["original_issue_date"] = extract(r"Original Issue Date:\s*(.+)")
    data["dpa"] = extract(r"DPA:\s*(.+?)\s")
    data["status"] = extract(r"Status:\s*(.+?)\s")
    data["disciplinary_action"] = extract(r"Disciplinary Action:\s*(.+)")

    address_lines = []
    for line in lines[1:]:
        if "License #" in line:
            break
        address_lines.append(line)

    data["address"] = ", ".join(address_lines)

    return data


async def perform_search(page, value: str):
    """
    Performs search operation.
    """
    search_box = page.locator('input[type="search"]')
    await search_box.fill("")
    await search_box.fill(value)
    await search_box.press("Enter")

    await page.wait_for_selector("tbody tr")


async def extract_all_pages(page) -> list:
    """
    Extracts all paginated records using correct DataTables logic.
    """

    all_records = []
    visited_pages = set()

    while True:
        await page.wait_for_selector("tbody tr")

        rows = await page.query_selector_all("tbody tr")
        if not rows:
            break

        first_text = (await rows[0].inner_text()).strip()

        if first_text in visited_pages:
            break

        visited_pages.add(first_text)

        for row in rows:
            cell = await row.query_selector("td.lic")
            if not cell:
                continue

            text = await cell.inner_text()
            all_records.append(parse_row_text(text))

        print(f"Extracted: {len(all_records)} records")

        next_li = page.locator("#LicenseTable_next")
        next_btn = page.locator("#LicenseTable_next a")

        class_attr = await next_li.get_attribute("class")

        if class_attr and "disabled" in class_attr:
            break

        # Scroll into view before click (critical fix)
        await next_btn.scroll_into_view_if_needed()

        old_content = await page.locator("tbody").inner_text()

        try:
            await next_btn.click(timeout=3000)
        except:
            await next_btn.click(force=True)

        # Correct async wait_for_function usage
        await page.wait_for_function(
            """(oldText) => {
                const tbody = document.querySelector("tbody");
                return tbody && tbody.innerText !== oldText;
            }""",
            arg=old_content
        )

    return all_records


async def scrape_one(browser, search_value: str) -> dict:
    """
    Scrapes data for a single search input.
    """

    page = await browser.new_page()

    result = {
        "state": "Mississippi",
        "board": "Mississippi State Board of Optometry",
        "search_criteria": {"search_value": search_value},
        "status": "success",
        "result_count": 0,
        "error_message": "",
        "license_details": [],
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "source_url": URL
    }

    try:
        await page.goto(URL)
        await page.wait_for_selector('input[type="search"]')

        await perform_search(page, search_value)

        rows = await page.query_selector_all("tbody tr")

        if not rows:
            result["status"] = "failed"
            result["error_message"] = "No results found"
            await page.close()
            return result

        records = await extract_all_pages(page)

        result["license_details"] = records
        result["result_count"] = len(records)

        await page.close()

    except Exception as e:
        result["status"] = "failed"
        result["error_message"] = str(e)

    return result


async def scrape_bulk(inputs):
    """
    Runs concurrent scraping tasks.
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=False)

        tasks = [scrape_one(browser, val) for val in inputs]
        results = await asyncio.gather(*tasks)

        await browser.close()

    return results


def save_output(data):
    """
    Saves output JSON file.
    """

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"Mississippi_Optometry_{timestamp}.json"

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
    parser.add_argument("--first_name", nargs="*", default=[])

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    inputs = args.license + args.last_name + args.first_name

    if not inputs:
        print("Provide at least one input")
        exit()

    results = asyncio.run(scrape_bulk(inputs))

    save_output(results)
