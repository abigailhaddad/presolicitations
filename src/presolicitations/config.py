"""Every outward-facing setting in one place. Environment variables override the defaults."""
import os

CSV_URL = (
    "https://s3.amazonaws.com/falextracts/Contract%20Opportunities/datagov/"
    "ContractOpportunitiesFullCSV.csv"
)
API = "https://sam.gov/api/prod/opps"
USER_AGENT = "federal presolicitations research prototype"
SLEEP_SECONDS = 0.4  # politeness delay before every SAM.gov request

# The notice type this dataset is about (CSV "Type" column).
NOTICE_TYPE = "Presolicitation"
# Notices posted before this date are not collected unless --since says so.
WINDOW_START = os.environ.get("PRESOL_WINDOW_START", "2026-10-19")

# Attachments larger than this are recorded but never downloaded or extracted.
MAX_FILE_BYTES = 30_000_000
FETCHABLE_EXTENSIONS = ("pdf", "docx")

# Tentative; Abigail decides the real names (see HANDOFF.md, "Decisions").
GITHUB_REPO = os.environ.get("PRESOL_GITHUB_REPO", "abigailhaddad/presolicitations")
HF_REPO = os.environ.get("PRESOL_HF_REPO", "abigailhaddad/presolicitations")

# Integrity thresholds. A run that trips any of them writes nothing and publishes nothing.
MAX_EMPTY_PDF_PCT = 35.0  # of PDFs fetched this run; the seed batch was 2.5% (21 of 837)
MAX_LIVE_ERROR_PCT = 20.0  # of live status checks this run that failed for a reason other than 404
MIN_CSV_PRESOL_ROWS = 100  # a CSV with fewer presolicitation rows than this is treated as broken

TABLES = ("notices", "attachments", "texts", "status_history")
