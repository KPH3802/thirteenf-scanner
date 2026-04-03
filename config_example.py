# 13F Institutional Initiations Scanner -- Configuration Example
# Copy this file to config.py and fill in your credentials.

EMAIL_SENDER   = 'your_email@gmail.com'
EMAIL_PASSWORD = 'YOUR_GMAIL_APP_PASSWORD'
EMAIL_RECEIVER = 'your_email@gmail.com'
SMTP_SERVER    = 'smtp.gmail.com'
SMTP_PORT      = 587

SEC_USER_AGENT    = 'Your Name your_email@gmail.com'
SEC_REQUEST_DELAY = 0.12

OPENFIGI_API_KEY       = ''
OPENFIGI_REQUEST_DELAY = 2.5

SCANNER_DB = 'thirteenf_scanner.db'

MIN_NEW_INITIATIONS = 3
MIN_POSITION_VALUE  = 1_000_000

HOLD_DAYS          = 91
FILING_WINDOW_DAYS = 45

HEDGE_FUND_FILERS = [
    ('0001067983', 'Berkshire Hathaway'),
    ('0001336528', 'Pershing Square Capital Mgmt'),
    ('0001103804', 'Viking Global Investors'),
    ('0001135730', 'Coatue Management'),
    ('0001061165', 'Lone Pine Capital'),
    ('0001040273', 'Third Point LLC'),
    ('0001656456', 'Appaloosa LP'),
    ('0000934639', 'Maverick Capital'),
    ('0001079114', 'Greenlight Capital'),
    ('0001061768', 'Baupost Group'),
    ('0001138995', 'Glenview Capital Management'),
    ('0000909661', 'Farallon Capital Management'),
    ('0000921669', 'Carl C. Icahn / Icahn Associates'),
    ('0001388838', 'Tiger Global Management'),
    ('0001045810', 'Soros Fund Management'),
    ('0001543160', 'Point72 Asset Management'),
    ('0001159159', 'Paulson & Co'),
]
