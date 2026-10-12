"""The standard schema every uploaded document is mapped onto.

T-12 = "trailing 12 months": a property's income and expenses for the last 12 months, month by month.
Rent roll = a list of every unit with its status, rent and lease dates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# ---------------------------------------------------------------- T-12 categories
# (key, section, plain-language meaning). Section decides how a line is used:
#   income       adds to effective gross income (deductions such as vacancy are entered as negatives)
#   expense      operating expense, subtracted to get NOI
#   below_line   not an operating expense (loan payments, capital items, owner draws): excluded from NOI
#   subtotal     a total row printed in the document; used only to check the arithmetic

T12_CATEGORIES: list[tuple[str, str, str]] = [
    ("rental_income", "income", "Rent at full occupancy (gross potential rent) or rent charged"),
    ("vacancy_loss", "income", "Rent lost to empty units and below-market leases (loss to lease); usually negative"),
    ("concessions", "income", "Discounts such as a free month; usually negative"),
    ("bad_debt", "income", "Rent charged but never collected; usually negative"),
    ("laundry_income", "income", "Laundry machines"),
    ("parking_income", "income", "Parking and garage fees"),
    ("fee_income", "income", "Pet, application, admin and other resident fees"),
    ("utility_reimbursement", "income", "Utilities billed back to residents"),
    ("late_fees", "income", "Late payment fees"),
    ("other_income", "income", "Other recurring income"),
    ("non_recurring_income", "income", "One-time money that won't repeat, such as insurance proceeds, settlements, refunds"),
    ("real_estate_taxes", "expense", "Property taxes"),
    ("insurance", "expense", "Property insurance"),
    ("utilities_water_sewer", "expense", "Water and sewer"),
    ("utilities_electric", "expense", "Electricity"),
    ("utilities_gas", "expense", "Natural gas"),
    ("utilities_trash", "expense", "Trash collection"),
    ("repairs_maintenance", "expense", "Routine repairs and upkeep"),
    ("contract_services", "expense", "Contracted services such as landscaping, pest control, elevator service"),
    ("payroll", "expense", "On-site staff wages and benefits"),
    ("management_fee", "expense", "Property management fee, usually a % of collected income"),
    ("administrative", "expense", "Office, legal, accounting and other admin costs"),
    ("marketing", "expense", "Advertising and leasing costs"),
    ("other_expense", "expense", "Other operating expenses"),
    ("debt_service", "below_line", "Loan payments (not an operating expense)"),
    ("capital_expenditures", "below_line", "Big replacement items such as a roof or HVAC (capex)"),
    ("owner_distributions", "below_line", "Money paid out to the owner"),
    ("depreciation", "below_line", "Accounting write-down (not a cash cost)"),
    ("total_income", "subtotal", "Reported total income"),
    ("total_expenses", "subtotal", "Reported total operating expenses"),
    ("net_operating_income", "subtotal", "Reported net operating income"),
    ("unmapped", "unknown", "Not recognized yet; needs a reviewer to choose a category"),
]
CATEGORY_SECTION = {k: s for k, s, _ in T12_CATEGORIES}
CATEGORY_MEANING = {k: m for k, _, m in T12_CATEGORIES}
UTILITY_CATEGORIES = ("utilities_water_sewer", "utilities_electric", "utilities_gas", "utilities_trash")

# Common labels seen on real statements. Matching is done on a normalized label (lowercase,
# punctuation removed, "&" -> "and"). Order doesn't matter; longest match wins.
T12_SYNONYMS: dict[str, list[str]] = {
    "rental_income": ["gross potential rent", "gpr", "rental income", "rent income", "rents", "gross rents",
                      "scheduled rent", "apartment rent", "residential rent", "base rent", "rent revenue", "rent"],
    "vacancy_loss": ["vacancy", "vacancy loss", "physical vacancy", "loss to lease", "vacancy and loss to lease",
                     "economic vacancy", "vacancy and credit loss"],
    "concessions": ["concessions", "rent concessions", "move in specials", "free rent"],
    "bad_debt": ["bad debt", "write offs", "uncollectible", "collection loss", "bad debt expense"],
    "laundry_income": ["laundry", "laundry income", "coin laundry"],
    "parking_income": ["parking", "parking income", "garage income", "parking fees"],
    "fee_income": ["pet fees", "pet rent", "application fees", "admin fees", "fees", "resident fees", "pet fees and other fees"],
    "utility_reimbursement": ["utility reimbursement", "rubs", "utility billback", "utility reimbursements", "water reimbursement"],
    "late_fees": ["late fees", "late charges", "nsf fees"],
    "other_income": ["other income", "misc income", "miscellaneous income", "storage income", "vending"],
    "non_recurring_income": ["insurance proceeds", "insurance settlement", "insurance claim", "insurance claim settlement",
                             "settlement", "legal settlement", "refund", "tax refund", "property tax refund",
                             "insurance reimbursement", "one time income", "non recurring income"],
    "real_estate_taxes": ["real estate taxes", "re taxes", "property taxes", "property tax", "taxes", "real estate tax"],
    "insurance": ["insurance", "property insurance", "hazard insurance", "liability insurance"],
    "utilities_water_sewer": ["water", "sewer", "water and sewer", "water sewer", "water and sewer expense"],
    "utilities_electric": ["electric", "electricity", "electric common area", "power"],
    "utilities_gas": ["gas", "natural gas", "gas utility"],
    "utilities_trash": ["trash", "trash removal", "refuse", "garbage", "waste removal"],
    "repairs_maintenance": ["repairs and maintenance", "r and m", "repairs", "maintenance", "repair and maintenance",
                            "general repairs", "maintenance and repairs", "turnover", "make ready"],
    "contract_services": ["contract services", "landscaping", "pest control", "pest control service", "elevator service",
                          "gardening", "janitorial", "security service", "pool service"],
    "payroll": ["payroll", "salaries", "wages", "on site payroll", "payroll and benefits", "staff"],
    "management_fee": ["management fee", "management fees", "mgmt fee", "mgmt fees", "property management",
                       "management"],
    "administrative": ["administrative", "admin", "office expense", "legal and accounting", "accounting", "legal",
                       "general and administrative", "g and a", "bank fees"],
    "marketing": ["marketing", "advertising", "leasing costs", "advertising and marketing"],
    "other_expense": ["other expense", "other expenses", "miscellaneous expense", "misc expense"],
    "debt_service": ["debt service", "mortgage", "mortgage payment", "loan payment", "interest expense", "principal"],
    "capital_expenditures": ["capital expenditures", "capex", "capital improvements", "capital repairs"],
    "owner_distributions": ["owner distributions", "distributions", "owner draw", "owner draws"],
    "depreciation": ["depreciation", "amortization", "depreciation and amortization"],
    "total_income": ["total income", "effective gross income", "egi", "total revenue", "total operating income",
                     "gross income"],
    "total_expenses": ["total expenses", "total operating expenses", "total opex", "operating expenses total"],
    "net_operating_income": ["net operating income", "noi"],
}

# Words in a repairs line that suggest a capital item rather than a routine repair (flag A7).
CAPEX_WORDS = ("roof", "hvac", "repip", "re pip", "boiler", "replace", "replacement", "elevator modern", "parking lot",
               "repav", "resurfac", "water heater", "window", "furnace", "chiller", "compressor")


def normalize_label(label: str) -> str:
    s = label.lower().replace("&", " and ").replace("/", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_SYNONYM_INDEX = {normalize_label(syn): cat for cat, syns in T12_SYNONYMS.items() for syn in syns}


def match_category(label: str) -> tuple[str | None, str]:
    """Map an original label to a standard category with a confidence of high/medium/low.

    high: the whole label is a known synonym. medium: a known synonym appears inside the label
    (longest one wins). None: not recognized; the caller may ask the AI or the reviewer.
    """
    n = normalize_label(label)
    if not n:
        return None, "low"
    if n in _SYNONYM_INDEX:
        return _SYNONYM_INDEX[n], "high"
    best = None
    for syn, cat in _SYNONYM_INDEX.items():
        if len(syn) >= 3 and re.search(rf"\b{re.escape(syn)}\b", n):
            if best is None or len(syn) > len(best[0]):
                best = (syn, cat)
    if best:
        return best[1], "medium"
    return None, "low"


# ---------------------------------------------------------------- rent roll

UNIT_STATUSES = ("occupied", "vacant", "model", "down", "employee")
# Physical occupancy counts units with someone living in them. Model and down units are empty.
PHYSICALLY_OCCUPIED = ("occupied", "employee")

STATUS_SYNONYMS = {
    "occupied": ["occupied", "occ", "o", "leased", "current", "rented", "notice", "on notice", "occupied ntv"],
    "vacant": ["vacant", "vac", "v", "vacant unrented", "vacant rented", "empty", "available"],
    "model": ["model", "show unit", "model unit"],
    "down": ["down", "down unit", "offline", "renovation", "under renovation", "non revenue"],
    "employee": ["employee", "emp", "staff", "manager", "managers unit", "employee unit", "admin unit"],
}
_STATUS_INDEX = {normalize_label(s): k for k, v in STATUS_SYNONYMS.items() for s in v}

# Rent roll columns: field -> header synonyms. Tenant-identifying columns are recognized only so
# they can be dropped; their values are never stored, displayed, exported or sent to AI.
RENT_ROLL_COLUMNS: dict[str, list[str]] = {
    "unit_id": ["unit", "unit number", "unit no", "unit id", "apt", "apartment", "unit num"],
    "unit_type": ["type", "unit type", "floorplan", "floor plan", "plan"],
    "beds": ["beds", "bedrooms", "br", "bd"],
    "baths": ["baths", "bathrooms", "ba"],
    "beds_baths": ["bd ba", "bed bath", "beds baths", "br ba", "bd ba type"],
    "sqft": ["sq ft", "sqft", "square feet", "sf", "size", "area"],
    "status": ["status", "occupancy", "unit status", "occ status"],
    "current_rent": ["rent", "current rent", "lease rent", "contract rent", "actual rent", "in place rent",
                     "monthly rent", "charged rent"],
    "market_rent": ["market", "market rent", "asking rent", "market rate", "scheduled rent"],
    "lease_start": ["lease start", "lease from", "move in", "start", "lease begin", "from"],
    "lease_end": ["lease end", "lease to", "expiration", "lease exp", "lease expiration", "end", "to", "expires"],
    "concessions": ["concession", "concessions", "monthly concession", "discount"],
    "deposit": ["deposit", "security deposit", "dep"],
    "_tenant": ["tenant", "tenant name", "resident", "resident name", "name", "lessee", "occupant"],
    "_contact": ["phone", "email", "e mail", "contact", "tel", "telephone", "mobile", "cell"],
}
_COLUMN_INDEX = {normalize_label(s): k for k, v in RENT_ROLL_COLUMNS.items() for s in v}
PRIVATE_FIELDS = ("_tenant", "_contact")


_COLUMN_INDEX_NOSPACE = {k.replace(" ", ""): v for k, v in _COLUMN_INDEX.items()}


def match_column(header: str) -> str | None:
    """Match a rent roll header. Also tries without spaces, because OCR often drops them ("LeaseFrom")."""
    n = normalize_label(header)
    return _COLUMN_INDEX.get(n) or _COLUMN_INDEX_NOSPACE.get(n.replace(" ", ""))


def match_status(raw: str) -> str | None:
    """Exact match first; otherwise the first word (OCR can fuse a status with the next cell's text)."""
    n = normalize_label(raw)
    if n in _STATUS_INDEX:
        return _STATUS_INDEX[n]
    first = n.split(" ")[0] if n else ""
    return _STATUS_INDEX.get(first) if len(first) >= 3 else None


# ---------------------------------------------------------------- normalized records

@dataclass
class SourceRef:
    """Where a value came from: file, sheet or page, and cell or line."""
    file: str
    location: str          # "Sheet 'T12' row 14" or "page 2, line 7"
    method: str            # excel | csv | pdf_text | pdf_ocr | manual
    original_label: str = ""

    def text(self) -> str:
        return f"{self.file}, {self.location}"


@dataclass
class RentRollUnit:
    unit_id: str
    unit_type: str | None
    beds: Decimal | None
    baths: Decimal | None
    sqft: Decimal | None
    status: str | None             # one of UNIT_STATUSES, or None if unrecognized
    current_rent: Decimal | None   # monthly
    market_rent: Decimal | None    # monthly
    lease_start: date | None
    lease_end: date | None
    month_to_month: bool
    concessions: Decimal | None    # monthly
    deposit: Decimal | None
    source: SourceRef
    confidence: str = "high"
    issues: list[str] = field(default_factory=list)


@dataclass
class T12Line:
    original_label: str
    category: str
    months: dict[str, Decimal | None]   # "2025-10" -> amount, in document order
    reported_total: Decimal | None
    source: SourceRef
    confidence: str = "high"
    mapped_by: str = "synonym"          # synonym | fuzzy | ai | reviewer | section_default | unmapped
    issues: list[str] = field(default_factory=list)
    section_hint: str = ""              # the document's own section heading above this line

    @property
    def section(self) -> str:
        return CATEGORY_SECTION.get(self.category, "unknown")

    def total(self) -> Decimal:
        return sum((v for v in self.months.values() if v is not None), Decimal(0))
