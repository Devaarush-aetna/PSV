#!/usr/bin/env bash
# =============================================================================
# run_address_standardization.sh
# =============================================================================
# Runs address_standardization.py with the required environment variables.
#
# Usage:
#   chmod +x run_address_standardization.sh
#   ./run_address_standardization.sh
#
# To override defaults, edit the CONFIGURATION section below or pass
# arguments to the Python script at the bottom of this file.
# =============================================================================

set -euo pipefail   # exit on error, undefined var, or failed pipe

# =============================================================================
# CONFIGURATION – fill in the values before running
# =============================================================================

# SmartyStreets credentials
# Obtain from: https://www.smarty.com/docs/cloud/authentication
export SMARTY_AUTH_ID="3b08d4f0-73a6-d2ca-cc42-cbbc6e2dfceb"
export SMARTY_AUTH_TOKEN="cNHcyUKPSP05covwO4TK"

# Corporate proxy (hostname:port or full URL)
export PROXY="proxy:9119"

# Input / output files
INPUT_FILE="epdb_hosp_priv.xlsx"       # path to input file (.xlsx, .xls, .csv, .tsv, .txt)
OUTPUT_FILE="std_addr.csv"             # path for standardized-address output CSV
DELIMITER=""                           # column separator for text files (ignored for Excel)

# Processing limits
BATCH_SIZE=50                          # addresses per SmartyStreets API call (max 100)
MAX_RECORDS=500                        # max records to process (cost / smoke-test guard)

# Python interpreter – defaults to whatever is active in the current shell
PYTHON="${PYTHON:-python3}"

# =============================================================================
# VALIDATION – check that placeholders have been replaced
# =============================================================================

for VAR in SMARTY_AUTH_ID SMARTY_AUTH_TOKEN PROXY; do
    VAL="${!VAR}"
    if [[ "$VAL" == YOUR_* ]]; then
        echo "ERROR: \$$VAR is still set to a placeholder value." >&2
        echo "       Edit the CONFIGURATION section in $(basename "$0") and re-run." >&2
        exit 1
    fi
    if [[ -z "$VAL" ]]; then
        echo "ERROR: \$$VAR must not be empty." >&2
        exit 1
    fi
done

if [[ ! -f "$INPUT_FILE" ]]; then
    echo "ERROR: Input file '$INPUT_FILE' not found." >&2
    exit 1
fi

# =============================================================================
# PROXY PASSWORD – prompted securely; never stored on disk
# =============================================================================

if [[ -z "${PROXY_PASS:-}" ]]; then
    # Prompt the user; input is not echoed to the terminal
    read -r -s -p "Proxy password: " PROXY_PASS
    echo   # newline after silent input
fi
export PROXY_PASS

# =============================================================================
# RUN
# =============================================================================

echo "Starting address standardization..."
echo "  Input  : $INPUT_FILE"
echo "  Output : $OUTPUT_FILE"
echo "  Batches: $BATCH_SIZE records/call, up to $MAX_RECORDS total records"
echo ""

"$PYTHON" address_standardization.py \
    --input        "$INPUT_FILE"  \
    --output       "$OUTPUT_FILE" \
    --delimiter    "$DELIMITER"   \
    --batch-size   "$BATCH_SIZE"  \
    --max-records  "$MAX_RECORDS"

EXIT_CODE=$?

# Unset the proxy password from the environment as soon as the process exits
unset PROXY_PASS

if [[ $EXIT_CODE -eq 0 ]]; then
    echo ""
    echo "Done. Results saved to: $OUTPUT_FILE"
else
    echo ""
    echo "ERROR: address_standardization.py exited with code $EXIT_CODE." >&2
    exit $EXIT_CODE
fi
