from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

SYNTHETIC_ACCOUNT_HANDLE = "plh_synthetic_owner_handle_0001"


def write_synthetic_pdf(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(str(path), pagesize=LETTER)
    text = document.beginText(54, 738)
    text.setLeading(16)
    for line in lines:
        text.textLine(line)
    document.drawText(text)
    document.save()
    return path


def m1_lines() -> list[str]:
    return [
        "M1 FINANCE LLC",
        "SYNTHETIC TEST STATEMENT - NOT A REAL ACCOUNT",
        "Statement Period: 01/01/2026 - 01/31/2026",
        "Account Number: SYNTH-TEST-0001",
        "Currency: USD",
        "Ending Account Value: $125,000.00",
        "Cash Balance: $2,000.00",
        "Margin Debit Balance: $7,000.00",
        "Activity",
        "01/08/2026 ACH Deposit $10,000.00",
        "01/19/2026 ACH Withdrawal ($1,500.00)",
        "01/22/2026 Dividend $125.00",
        "01/23/2026 Margin Interest ($15.00)",
    ]


def apex_lines() -> list[str]:
    return [
        "APEX CLEARING CORPORATION",
        "SYNTHETIC TEST STATEMENT - NOT A REAL ACCOUNT",
        "Statement Period: 02/01/2026 - 02/28/2026",
        "Account No. XXXX-0001",
        "Currency: USD",
        "Net Account Value: $132,500.00",
        "Cash & Cash Equivalents: $2,500.00",
        "Activity",
        "02/10/2026 Journal In Transfer $2,000.00",
        "02/18/2026 Buy Synthetic Security ($1,000.00)",
    ]


def apex_opening_closing_layout_lines() -> list[str]:
    """Sanitized facsimile of the column structure used by current Apex PDFs."""

    return [
        "APEX CLEARING CORPORATION",
        "SYNTHETIC TEST STATEMENT - NOT A REAL ACCOUNT",
        "July 1, 2026 - July 31, 2026",
        "ACCOUNT NUMBER  \u00ad\u00ad  TST\u00ad00001\u00ad01  QA DEMO",
        "OPENING BALANCE                     CLOSING BALANCE",
        "Cash account                         $4,000.00     $2,500.00",
        "TOTAL PRICED PORTFOLIO             $120,000.00   $132,500.00",
        "Activity",
        "07/10/2026 Journal In Transfer $2,000.00",
    ]
