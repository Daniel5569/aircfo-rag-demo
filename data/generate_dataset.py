"""
Generates a synthetic but realistic startup financial dataset for the airCFO demo:
- transactions.csv   (QuickBooks/Xero-style general ledger export)
- monthly_pnl.csv    (monthly P&L)
- invoices.csv       (AP invoices, including a duplicate-payment and two overdue items)
- contracts/*.pdf    (2 vendor service agreements, used as citable source docs)

The dataset is built around one deliberate, findable story: SaaS spend jumps in
March 2026 because the team added 3 new tools + doubled seats on an existing one.
That story is what the "why did our SaaS spend jump in March?" demo question answers.
"""
import csv
import random
from datetime import date
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

random.seed(42)

BASE_DIR = Path(__file__).parent
CONTRACTS_DIR = BASE_DIR / "contracts"
CONTRACTS_DIR.mkdir(exist_ok=True)

COMPANY = "Nimbus Robotics, Inc."
MONTHS = [
    date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
    date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1),
]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June"]

# --- SaaS vendors: baseline set present all 6 months, plus 3 new ones starting March,
# plus a seat-count doubling on Notion starting March. This is the "anomaly" the
# query_financials / monthly_flux_analysis tools should be able to explain with citations.
SAAS_BASELINE = [
    ("Vercel", 250),
    ("Neon (Postgres)", 180),
    ("Notion", 320),   # will double in March
    ("Slack", 210),
    ("Figma", 150),
    ("Datadog", 480),
]
SAAS_NEW_IN_MARCH = [
    ("Retool", 600),
    ("Segment", 450),
    ("PagerDuty", 380),
]

OTHER_CATEGORIES = {
    "Payroll": (42000, 46000),
    "Contractors": (4000, 9000),
    "Rent": (5200, 5200),
    "Marketing": (2000, 7500),
    "Legal & Professional Services": (0, 4200),
    "Cloud Infra (AWS)": (6000, 11000),
    "Travel": (300, 3200),
    "Office Supplies": (150, 900),
}

VENDOR_FOR_CATEGORY = {
    "Payroll": "Gusto Payroll",
    "Contractors": "Toptal / Upwork Contractors",
    "Rent": "WeWork - Market St",
    "Marketing": "Google Ads",
    "Legal & Professional Services": "Cooley LLP",
    "Cloud Infra (AWS)": "Amazon Web Services",
    "Travel": "Various (Delta, Marriott, Uber)",
    "Office Supplies": "Amazon Business",
}


def gen_transactions():
    rows = []
    txn_id = 1000
    for m_idx, month_start in enumerate(MONTHS):
        month_name = MONTH_NAMES[m_idx]
        is_march_or_later = m_idx >= 2  # March = index 2

        # SaaS baseline
        for vendor, base_amount in SAAS_BASELINE:
            amount = base_amount
            note = ""
            if vendor == "Notion" and is_march_or_later:
                amount = base_amount * 2
                note = "Seat count doubled from 8 to 16 (eng team growth)"
            rows.append({
                "transaction_id": f"TXN-{txn_id}",
                "date": date(month_start.year, month_start.month, 5).isoformat(),
                "vendor": vendor,
                "category": "SaaS Subscriptions",
                "amount": -amount,
                "description": f"{vendor} monthly subscription - {month_name} 2026" + (f" ({note})" if note else ""),
            })
            txn_id += 1

        # New SaaS tools starting March
        if is_march_or_later:
            for vendor, amount in SAAS_NEW_IN_MARCH:
                rows.append({
                    "transaction_id": f"TXN-{txn_id}",
                    "date": date(month_start.year, month_start.month, 8).isoformat(),
                    "vendor": vendor,
                    "category": "SaaS Subscriptions",
                    "amount": -amount,
                    "description": f"{vendor} monthly subscription - {month_name} 2026 (new vendor, onboarded March 2026)",
                })
                txn_id += 1

        # Other operating categories
        for category, (lo, hi) in OTHER_CATEGORIES.items():
            amount = round(random.uniform(lo, hi), 2) if lo != hi else lo
            rows.append({
                "transaction_id": f"TXN-{txn_id}",
                "date": date(month_start.year, month_start.month, random.randint(1, 27)).isoformat(),
                "vendor": VENDOR_FOR_CATEGORY[category],
                "category": category,
                "amount": -amount,
                "description": f"{category} - {month_name} 2026",
            })
            txn_id += 1

        # Revenue (positive amounts, growing MoM)
        revenue = 58000 + m_idx * 6500 + random.randint(-1500, 1500)
        rows.append({
            "transaction_id": f"TXN-{txn_id}",
            "date": date(month_start.year, month_start.month, 1).isoformat(),
            "vendor": "Customer Subscription Revenue",
            "category": "Revenue",
            "amount": revenue,
            "description": f"SaaS subscription revenue - {month_name} 2026",
        })
        txn_id += 1

    return rows


def write_transactions(rows):
    path = BASE_DIR / "transactions.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["transaction_id", "date", "vendor", "category", "amount", "description"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")
    return path


def gen_pnl(transactions):
    by_month = {name: {"revenue": 0.0, "opex": 0.0, "saas": 0.0} for name in MONTH_NAMES}
    for row in transactions:
        m_idx = int(row["date"].split("-")[1]) - 1
        month_name = MONTH_NAMES[m_idx]
        amt = float(row["amount"])
        if row["category"] == "Revenue":
            by_month[month_name]["revenue"] += amt
        else:
            by_month[month_name]["opex"] += -amt
            if row["category"] == "SaaS Subscriptions":
                by_month[month_name]["saas"] += -amt

    path = BASE_DIR / "monthly_pnl.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["month", "revenue", "total_opex", "saas_subscriptions_opex", "net_income"])
        writer.writeheader()
        for name in MONTH_NAMES:
            d = by_month[name]
            writer.writerow({
                "month": f"{name} 2026",
                "revenue": round(d["revenue"], 2),
                "total_opex": round(d["opex"], 2),
                "saas_subscriptions_opex": round(d["saas"], 2),
                "net_income": round(d["revenue"] - d["opex"], 2),
            })
    print(f"wrote {path}")
    return path


def gen_invoices():
    rows = [
        {"invoice_id": "INV-2001", "vendor": "Cooley LLP", "amount": 4200.00, "issue_date": "2026-02-10", "due_date": "2026-03-10", "paid_date": "2026-03-09", "status": "paid"},
        {"invoice_id": "INV-2002", "vendor": "Amazon Web Services", "amount": 8950.00, "issue_date": "2026-03-01", "due_date": "2026-03-31", "paid_date": "2026-03-28", "status": "paid"},
        {"invoice_id": "INV-2003", "vendor": "Toptal / Upwork Contractors", "amount": 6200.00, "issue_date": "2026-04-01", "due_date": "2026-05-01", "paid_date": "", "status": "overdue"},
        {"invoice_id": "INV-2004", "vendor": "Retool", "amount": 600.00, "issue_date": "2026-03-08", "due_date": "2026-04-07", "paid_date": "2026-04-05", "status": "paid"},
        # Duplicate payment anomaly: same vendor/amount/near-identical date, two invoice numbers
        {"invoice_id": "INV-2005", "vendor": "Datadog", "amount": 480.00, "issue_date": "2026-05-05", "due_date": "2026-06-04", "paid_date": "2026-05-20", "status": "paid"},
        {"invoice_id": "INV-2006", "vendor": "Datadog", "amount": 480.00, "issue_date": "2026-05-05", "due_date": "2026-06-04", "paid_date": "2026-05-20", "status": "paid"},
        {"invoice_id": "INV-2007", "vendor": "WeWork - Market St", "amount": 5200.00, "issue_date": "2026-05-01", "due_date": "2026-05-31", "paid_date": "", "status": "overdue"},
        {"invoice_id": "INV-2008", "vendor": "Google Ads", "amount": 5100.00, "issue_date": "2026-06-01", "due_date": "2026-07-01", "paid_date": "2026-06-25", "status": "paid"},
    ]
    path = BASE_DIR / "invoices.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["invoice_id", "vendor", "amount", "issue_date", "due_date", "paid_date", "status"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")
    return path


def gen_contract_pdf(filename, vendor, monthly_fee, term_months, effective_date, notes):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Master Service Agreement", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(4)
    pdf.multi_cell(0, 7,
        f"This Master Service Agreement (\"Agreement\") is entered into as of {effective_date} "
        f"between {COMPANY} (\"Customer\") and {vendor} (\"Vendor\").\n\n"
        f"1. Services. Vendor shall provide its standard software-as-a-service platform to Customer.\n\n"
        f"2. Fees. Customer shall pay Vendor a monthly subscription fee of ${monthly_fee:,.2f}, "
        f"invoiced monthly in arrears, due net 30.\n\n"
        f"3. Term. This Agreement shall commence on {effective_date} and continue for an initial "
        f"term of {term_months} months, renewing automatically for successive 12-month periods "
        f"unless either party provides 30 days' written notice of non-renewal.\n\n"
        f"4. Notes. {notes}\n\n"
        f"5. Termination. Either party may terminate this Agreement for material breach upon "
        f"30 days' written notice and failure to cure.\n"
    )
    path = CONTRACTS_DIR / filename
    pdf.output(str(path))
    print(f"wrote {path}")
    return path


def main():
    transactions = gen_transactions()
    write_transactions(transactions)
    gen_pnl(transactions)
    gen_invoices()
    gen_contract_pdf(
        "notion_msa.pdf", "Notion Labs, Inc.", 320.00, 12, "January 15, 2026",
        "Effective March 2026, Customer expanded from 8 to 16 seats under the existing plan, "
        "doubling the monthly fee to $640.00 per the plan's per-seat pricing addendum.",
    )
    gen_contract_pdf(
        "retool_msa.pdf", "Retool, Inc.", 600.00, 12, "March 8, 2026",
        "New vendor engagement onboarded in March 2026 to support internal tooling for the "
        "finance and ops teams.",
    )


if __name__ == "__main__":
    main()
