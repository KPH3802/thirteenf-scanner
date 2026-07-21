"""
Root-cause regression tests for the Q1 "0 holdings" defect.

Symptom (as reported): the scanner collected ~0 holdings from Q1 (Mar 31)
filings while other quarters worked, silencing THIRTEENF_BULL.

Root cause: `get_filing_for_quarter` selects the most-recently-FILED filing
whose report_date matches the quarter, ignoring the form type. Q1 report
periods disproportionately attract a later `13F-HR/A` amendment (e.g.
confidential-treatment releases / annual restatements filed months after the
window). Those amendments are PARTIAL restatements that carry only the handful
of changed positions (or, for a cover-only correction, ZERO holdings). Because
the amendment is filed after the original 13F-HR, the buggy selector returns
the amendment and the complete original holdings list is discarded.

Real-data proof (Berkshire Hathaway, CIK 0001067983, report 2025-03-31):
  original  13F-HR  (filed 2025-05-15): 110 holdings
  amendment 13F-HR/A (filed 2025-08-14):   4 holdings  <- what the bug returns

The parser itself is fine (see test_parser_regression.py); the defect is
filing selection. Fix: prefer the original 13F-HR over any 13F-HR/A amendment
for the same report period.
"""
from datetime import date


def _q1_filings():
    """Two filings for the same Q1 report date: a later amendment and the
    original 13F-HR. Sorted newest-filed first, exactly as get_13f_filings
    returns them."""
    return [
        {"accession": "AMEND0000000001", "filed_date": "2025-08-14",
         "report_date": "2025-03-31", "form": "13F-HR/A"},
        {"accession": "ORIG00000000001", "filed_date": "2025-05-15",
         "report_date": "2025-03-31", "form": "13F-HR"},
    ]


def test_prefers_original_13fhr_over_later_amendment(scanner):
    """The complete original 13F-HR must be chosen, NOT the partial /A amendment."""
    picked = scanner.get_filing_for_quarter(_q1_filings(), date(2025, 3, 31))
    assert picked is not None
    assert picked["form"] == "13F-HR", (
        "selected a %s amendment instead of the original 13F-HR; the amendment "
        "is a partial restatement and collapses Q1 holdings to near-zero"
        % picked["form"]
    )
    assert picked["accession"] == "ORIG00000000001"


def test_amendment_used_only_when_no_original_exists(scanner):
    """If the original 13F-HR is genuinely absent, fall back to the amendment."""
    only_amendment = [
        {"accession": "AMEND0000000001", "filed_date": "2025-08-14",
         "report_date": "2025-03-31", "form": "13F-HR/A"},
    ]
    picked = scanner.get_filing_for_quarter(only_amendment, date(2025, 3, 31))
    assert picked is not None
    assert picked["accession"] == "AMEND0000000001"


def test_non_q1_single_filing_still_selected(scanner):
    """Non-Q1 quarters (no amendment) must keep working — no regression."""
    filings = [
        {"accession": "Q4ORIG000000001", "filed_date": "2026-02-17",
         "report_date": "2025-12-31", "form": "13F-HR"},
    ]
    picked = scanner.get_filing_for_quarter(filings, date(2025, 12, 31))
    assert picked is not None
    assert picked["accession"] == "Q4ORIG000000001"


def test_fuzzy_report_date_match_preserved(scanner):
    """The +/-5 day tolerant match must still resolve to the original 13F-HR."""
    filings = [
        {"accession": "AMEND0000000001", "filed_date": "2025-08-14",
         "report_date": "2025-03-30", "form": "13F-HR/A"},
        {"accession": "ORIG00000000001", "filed_date": "2025-05-15",
         "report_date": "2025-03-30", "form": "13F-HR"},
    ]
    picked = scanner.get_filing_for_quarter(filings, date(2025, 3, 31))
    assert picked is not None
    assert picked["form"] == "13F-HR"


def test_get_13f_filings_carries_form_type(scanner):
    """get_filing_for_quarter can only prefer the original if get_13f_filings
    records the form type. Guard that the field is populated."""
    fake = {
        "filings": {"recent": {
            "form": ["13F-HR/A", "13F-HR", "10-K"],
            "accessionNumber": ["0000000000-25-000002",
                                "0000000000-25-000001",
                                "0000000000-25-000003"],
            "filingDate": ["2025-08-14", "2025-05-15", "2025-03-01"],
            "reportDate": ["2025-03-31", "2025-03-31", "2024-12-31"],
        }}
    }
    orig_edgar_get = scanner.edgar_get
    scanner.edgar_get = lambda url: fake
    try:
        filings = scanner.get_13f_filings("0000000000")
    finally:
        scanner.edgar_get = orig_edgar_get

    assert len(filings) == 2, "should keep only 13F-HR / 13F-HR/A forms"
    assert all("form" in f for f in filings), "form type must be recorded"
    picked = scanner.get_filing_for_quarter(filings, date(2025, 3, 31))
    assert picked["form"] == "13F-HR"
