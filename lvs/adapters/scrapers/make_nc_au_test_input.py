"""Build a minimal PSV input Excel for the 6 failing NC/AU rows."""
from pathlib import Path
import openpyxl

HEADERS = [
    "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME", "EPDB_PIN", "PROV_TYPE",
    "", "", "MAINTAINED_BY", "", "LIC_STATE",
    "LIC_TYPE", "LIC_ID", "LIC_EXPRTN_DT", "", "", "SVC_LOC_STATE", "NPI_NO",
]

# row_0018 … row_0023 from the failing batch
ROWS = [
    ["Katlyn",        "",        "Crisp",    "", "AU", "", "", "", "", "NC", "AU", "14995",      "", "", "", "NC", "1962075929"],
    ["Karen",         "Hughes",  "Sikes",    "", "AU", "", "", "", "", "NC", "AU", "4741",       "", "", "", "NC", "1730356040"],
    ["Jessica",       "Dawn",    "Noblitt",  "", "AU", "", "", "", "", "NC", "AU", "14290",      "", "", "", "NC", "1891277200"],
    ["John",          "Owen",    "Ballance", "", "AU", "", "", "", "", "NC", "AU", "6716",       "", "", "", "NC", "1619155261"],
    ["Taylor",        "Leigh",   "Hines",    "", "AU", "", "", "", "", "NC", "AU", "30004022",   "", "", "", "NC", "1265931543"],
    ["Adam",          "",        "Nickles",  "", "AU", "", "", "", "", "NC", "AU", "30001948",   "", "", "", "NC", "1992054386"],
]

out_path = Path(__file__).parent / "Input_NC_AU_6.xlsx"
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "PSV Tab"
ws.append(HEADERS)
for r in ROWS:
    ws.append(r)
wb.save(out_path)
print(f"Saved: {out_path}")
