"""Build a minimal PSV input Excel for the failing NC/DP rows 0041-0044."""
from pathlib import Path
import openpyxl

HEADERS = [
    "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME", "EPDB_PIN", "PROV_TYPE",
    "", "", "MAINTAINED_BY", "", "LIC_STATE",
    "LIC_TYPE", "LIC_ID", "LIC_EXPRTN_DT", "", "NPI_NO", "SVC_LOC_STATE",
]

ROWS = [
    ["Justin",   None,      "Waller",  "3281514", "DP", "", "", "AHP", "", "NC", "OPERATING", "852",  "", "", "1760122006", "NC"],
    ["Matthew",  "Anthony", "Borns",   "4811823", "DP", "", "", "AHP", "", "NC", "OPERATING", "626",  "", "", "1891046454", "NC"],
    ["Thomas",   "J.",      "Hagan",   "5509342", "DP", "", "", "AHP", "", "NC", "OPERATING", "137",  "", "", "1962406991", "NC"],
    ["Niara",    None,      "Wright",  "6796040", "DP", "", "", "AHP", "", "NC", "OPERATING", "812",  "", "", "1508318957", "NC"],
    ["Roxanne",  "L.",      "Burgess", "7964467", "DP", "", "", "AHP", "", "NC", "OPERATING", "461",  "", "", "1629048566", "NC"],
]

out_path = Path(__file__).parent / "Input_NC_DP_5.xlsx"
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "PSV Tab"
ws.append(HEADERS)
for r in ROWS:
    ws.append(r)
wb.save(out_path)
print(f"Saved: {out_path}")
