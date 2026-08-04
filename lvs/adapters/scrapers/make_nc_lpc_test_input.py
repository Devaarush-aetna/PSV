"""Build a minimal PSV input Excel for the 5 failing NC/LPC rows and run the test."""
from pathlib import Path
import openpyxl

# Column layout matches C_* constants in psv_test.py:
#  0=first  1=middle  2=last  3=epdb_pin  4=prov_type
#  5-6=unused  7=maintained_by  8=unused  9=lic_state
#  10=lic_type  11=lic_id  12=expiry  13-14=unused  15=svc_loc_state  16=NPI_NO

HEADERS = [
    "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME", "EPDB_PIN", "PROV_TYPE",
    "", "", "MAINTAINED_BY", "", "LIC_STATE",
    "LIC_TYPE", "LIC_ID", "LIC_EXPRTN_DT", "", "", "SVC_LOC_STATE", "NPI_NO",
]

# row_0056 … row_0060 from the failing batch
ROWS = [
    # first       middle   last         pin  prov   x  x  maint  x   state  lic_type  lic_id    expiry  x  x  svc   npi
    ["Patricia", "Diane", "Hamlin",    "", "LPC",  "", "", "", "", "NC", "LPC", "14296",  "", "", "", "NC", "1669024170"],
    ["Kara",     "",      "Miller",    "", "LPC",  "", "", "", "", "NC", "LPC", "A17817", "", "", "", "NC", "1013641638"],
    ["Mark",     "Steven","Ackerman",  "", "LPC",  "", "", "", "", "NC", "LPC", "12619",  "", "", "", "NC", "1720558679"],
    ["Mariam",   "",      "Jabbie",    "", "LPC",  "", "", "", "", "NC", "LPC", "A21481", "", "", "", "NC", "1417751330"],
    ["Li-Ting",  "",      "Lin",       "", "LPC",  "", "", "", "", "NC", "LPC", "A18892", "", "", "", "NC", "1306522065"],
]

out_path = Path(__file__).parent / "Input_NC_LPC_5.xlsx"
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "PSV Tab"
ws.append(HEADERS)
for r in ROWS:
    ws.append(r)
wb.save(out_path)
print(f"Saved: {out_path}")
