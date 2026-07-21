"""
Parser regression guard.

These tests pin down that `parse_infotable_xml` is NOT the source of the Q1
"0 holdings" defect: given a real (trimmed) Q1 infotable it returns the full
set of holdings, and a real non-Q1 (Q4) infotable parses equally well. The
undercount is proven to come from selecting a partial amendment, not from
parsing. Fixtures are trimmed excerpts of genuine SEC EDGAR 13F-HR infotable
XML (Berkshire Hathaway, CIK 0001067983).
"""
from conftest import read_fixture


def test_q1_original_parses_all_holdings(scanner):
    holdings = scanner.parse_infotable_xml(read_fixture("q1_original_infotable.xml"))
    assert len(holdings) == 6, "real Q1 original infotable must parse fully"
    assert all(h["cusip"] for h in holdings)


def test_q4_non_q1_parses(scanner):
    holdings = scanner.parse_infotable_xml(read_fixture("q4_infotable.xml"))
    assert len(holdings) == 2, "non-Q1 (Q4) infotable must parse — no regression"


def test_partial_amendment_yields_fewer_than_original(scanner):
    """Documents the mechanism: the real Q1 13F-HR/A amendment carries only a
    handful of positions, far fewer than the complete original. Selecting it
    (the bug) is what starved signal detection."""
    original = scanner.parse_infotable_xml(read_fixture("q1_original_infotable.xml"))
    amendment = scanner.parse_infotable_xml(read_fixture("q1_amendment_infotable.xml"))
    assert len(amendment) < len(original)
