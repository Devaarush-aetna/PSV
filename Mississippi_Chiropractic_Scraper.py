"""
Mississippi Board of Chiropractic Examiners Scraper

This script:
1. Opens the license verification website using Microsoft Edge.
2. Accepts last name input from CLI.
3. Extracts all results from the table.
4. Saves the output into a structured JSON file.

Usage:
    python script.py --last_name Smith

Output:
    Mississippi_Chiropractic_<timestamp>.json
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import json
import argparse


URL = "https://www.msbce.ms.gov/secure/licenseverification.asp"


def scrape_chiropractors(last_name: str) -> dict:
    """
    Scrapes chiropractic license data based on last name.

    Args:
        last_name (str): Last name to search

    Returns:
        dict: Structured JSON containing license details
    """

    result = {
        "state": "Mississippi",
        "board": "Mississippi Board of Chiropractic Examiners",
        "search_criteria": {"last_name": last_name},
        "status": "success",
        "result_count": 0,
        "error_message": "",
        "license_details": [],
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "source_url": URL
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=False)
            page = browser.new_page()

            page.goto(URL, timeout=60000)

            page.fill('input[name="lName"]', last_name)
            page.click('input[type="image"][alt="Submit"]')

            page.wait_for_selector('table[bgcolor="#FFFFFF"]', timeout=60000)

            rows = page.query_selector_all('table[bgcolor="#FFFFFF"] tbody tr')[1:]

            for row in rows:
                cols = row.query_selector_all('td')

                if len(cols) < 9:
                    continue

                record = {
                    "name": cols[0].inner_text().strip(),
                    "work_address": cols[1].inner_text().replace('\n', ', ').strip(),
                    "phone_number": cols[2].inner_text().strip(),
                    "license_number": cols[3].inner_text().strip(),
                    "license_issued": cols[4].inner_text().strip(),
                    "discipline": cols[5].inner_text().strip(),
                    "license_expires": cols[6].inner_text().strip(),
                    "status": cols[7].inner_text().strip(),
                    "graduate_of": cols[8].inner_text().strip()
                }

                result["license_details"].append(record)

            result["result_count"] = len(result["license_details"])
            browser.close()

    except Exception as e:
        result["status"] = "failed"
        result["error_message"] = str(e)

    return result


def save_to_file(data: dict):
    """
    Saves the scraped data into a JSON file with timestamp.

    Args:
        data (dict): Scraped JSON data
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"Mississippi_Chiropractic_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    print(f"File saved: {filename}")


def parse_arguments():
    """
    Parses CLI arguments.

    Returns:
        Namespace: Parsed CLI arguments
    """
    parser = argparse.ArgumentParser(
        description="Scrape Mississippi Chiropractic License Data"
    )
    parser.add_argument(
        "--last_name",
        required=True,
        help="Last name to search in the license database"
    )
    return parser.parse_args()


if __name__ == "__main__":
    """
    Entry point for script execution.
    """
    args = parse_arguments()

    scraped_data = scrape_chiropractors(args.last_name)

    save_to_file(scraped_data)
