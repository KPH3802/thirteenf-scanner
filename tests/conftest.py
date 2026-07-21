"""
Test bootstrap for the 13F scanner.

`thirteenf_scanner` imports `yfinance` and `config` at module import time.
Neither is available (or desirable) in an offline unit-test run, and A13
forbids materialising a real `config.py` with live secrets. We therefore
install lightweight stand-ins in sys.modules BEFORE importing the scanner,
so the pure functions under test (parser + filing selection) import cleanly
without network, credentials, or third-party deps.
"""
import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
FIXTURES = os.path.join(_HERE, "fixtures")

# --- stub yfinance (only used by get_vix(), never in these tests) -----------
if "yfinance" not in sys.modules:
    sys.modules["yfinance"] = types.ModuleType("yfinance")

# --- stub config with placeholder (non-secret) values -----------------------
if "config" not in sys.modules:
    cfg = types.ModuleType("config")
    cfg.EMAIL_SENDER = "test@example.com"
    cfg.EMAIL_PASSWORD = "PLACEHOLDER"       # not a real secret (A13)
    cfg.EMAIL_RECEIVER = "test@example.com"
    cfg.SMTP_SERVER = "smtp.example.com"
    cfg.SMTP_PORT = 587
    cfg.SEC_USER_AGENT = "Test Runner test@example.com"
    cfg.SEC_REQUEST_DELAY = 0.0
    cfg.OPENFIGI_API_KEY = ""
    cfg.OPENFIGI_REQUEST_DELAY = 0.0
    cfg.SCANNER_DB = "test_thirteenf.db"
    cfg.MIN_NEW_INITIATIONS = 3
    cfg.MIN_POSITION_VALUE = 1_000_000
    cfg.HOLD_DAYS = 91
    cfg.FILING_WINDOW_DAYS = 45
    cfg.HEDGE_FUND_FILERS = []
    sys.modules["config"] = cfg

if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


@pytest.fixture
def scanner():
    import thirteenf_scanner
    return thirteenf_scanner


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()
