"""Synthetic invoices and one quarterly report.

Company names are fictional. Nothing in this catalog is a real customer,
a real person, or a real address.
"""

from __future__ import annotations

INVOICES: list[dict] = [
    {
        "filename": "harbor-pine-hp-2026-0142.pdf",
        "fields": {
            "vendor_name": "Harbor & Pine Supply Co.",
            "invoice_number": "HP-2026-0142",
            "invoice_date": "2026-02-12",
            "due_date": "2026-03-14",
            "bill_to": "Lantern Street Clinic",
            "currency": "USD",
            "subtotal": "245.40",
            "tax": "12.27",
            "total": "257.67",
        },
        "line_items": [
            {
                "description": "Kraft mailers, case of 100",
                "quantity": "4",
                "unit_price": "28.50",
                "amount": "114.00",
            },
            {
                "description": "Archive boxes",
                "quantity": "12",
                "unit_price": "6.25",
                "amount": "75.00",
            },
            {
                "description": "Packing tape, 6-pack",
                "quantity": "6",
                "unit_price": "9.40",
                "amount": "56.40",
            },
        ],
    },
    {
        "filename": "northbank-nb-2026-088.pdf",
        "fields": {
            "vendor_name": "Northbank Analytics LLC",
            "invoice_number": "NB-2026-088",
            "invoice_date": "2026-01-20",
            "due_date": "2026-02-19",
            "bill_to": "Osprey Municipal Library",
            "currency": "USD",
            "subtotal": "2250.00",
            "tax": "0.00",
            "total": "2250.00",
        },
        "line_items": [
            {
                "description": "Monthly dashboard retainer",
                "quantity": "1",
                "unit_price": "1800.00",
                "amount": "1800.00",
            },
            {
                "description": "Source refresh, extra hour",
                "quantity": "3",
                "unit_price": "150.00",
                "amount": "450.00",
            },
        ],
    },
    {
        "filename": "blue-heron-bhp-3319.pdf",
        "fields": {
            "vendor_name": "Blue Heron Printworks",
            "invoice_number": "BHP-3319",
            "invoice_date": "2026-03-02",
            "due_date": "2026-04-01",
            "bill_to": "Cedar Wharf Logistics",
            "currency": "USD",
            "subtotal": "486.00",
            "tax": "29.16",
            "total": "515.16",
        },
        "line_items": [
            {
                "description": "Pallet labels, roll",
                "quantity": "20",
                "unit_price": "14.00",
                "amount": "280.00",
            },
            {
                "description": "Bill of lading pads",
                "quantity": "8",
                "unit_price": "11.75",
                "amount": "94.00",
            },
            {
                "description": "Carbonless forms",
                "quantity": "5",
                "unit_price": "22.40",
                "amount": "112.00",
            },
        ],
    },
]

REPORTS: list[dict] = [
    {
        "filename": "cedar-wharf-q1-2026.pdf",
        "fields": {
            "title": "Q1 activity report",
            "organization": "Cedar Wharf Logistics",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "prepared_by": "Northbank Analytics LLC",
        },
        "rows": [
            {"metric": "Shipments", "value": "1284", "notes": "Closed bills of lading"},
            {"metric": "On-time share", "value": "0.94", "notes": "Delivered by promise date"},
            {"metric": "Average weight lb", "value": "186.40", "notes": "Mean across closed loads"},
            {"metric": "Exception count", "value": "17", "notes": "Status exception"},
        ],
    }
]
