"""The demo's sales warehouse: the repo's own schema, with data that has a story.

**Schema.** Read from `backend/fixtures/sales_seed.sql` and
`backend/fixtures/sales_comments.sql` rather than copied, so the demo's tables
and columns are the fixture's tables and columns and move when they move. Three
things the eval harness plants on purpose are left out, because they exist to
make a model fail and would only read as noise to someone browsing the schema:

* the deprecated singular `product` table (a near-duplicate of `products`);
* the `flg_2` and `cust_ref` legacy columns;
* the eight wide audit columns the fixture bolts onto every table to push the
  snapshot past a retrieval budget.

Two comments in the fixture are deliberately false (see its header). They are
replaced with true ones here: the demo documents its schema honestly.

**Data.** Generated, deterministic, and anchored to `DEMO_TODAY`. The fixture's
own rows are uniform noise (`Customer 4417`, flat `random()` revenue); these
have a shape a reader can explain:

* revenue grows about 2% a month over two years;
* November and December are the holiday peak, January and February the dip;
* **June of the current year is an outlier**: Meridian Health Systems placed a
  single fleet-refresh order (monitors and docks for every clinic), which is
  also why one monitor tops the year's product ranking.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
SEED_SQL = REPO / "backend" / "fixtures" / "sales_seed.sql"
COMMENTS_SQL = REPO / "backend" / "fixtures" / "sales_comments.sql"

DROPPED_TABLES = {"product"}
DROPPED_COLUMNS = {"flg_2", "cust_ref"}

#: The two planted comments, told truthfully.
CORRECTED_COMMENTS = {
    ("COLUMN", "customers.segment"): (
        "Commercial segment the account is managed under: SMB, Mid-Market or "
        "Enterprise. Enterprise accounts are named and have an account manager."
    ),
    ("COLUMN", "orders.subtotal"): (
        "Sum of the order's line totals, before the order-level discount, tax "
        "and shipping. Not what the customer paid — that is total_amount."
    ),
}

SEED = 20260925


# ── schema ──────────────────────────────────────────────────────────────────
def schema_statements() -> list[str]:
    """Every CREATE TABLE in the fixture, minus the plants."""
    text = SEED_SQL.read_text()
    text = text.split("--  DATA", 1)[0]
    blocks = re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", text, flags=re.S)
    out: list[str] = []
    for name, body in blocks:
        if name in DROPPED_TABLES:
            continue
        lines = [
            line for line in body.split("\n")
            if not re.match(r"\s*(%s)\s" % "|".join(DROPPED_COLUMNS), line)
        ]
        out.append(f"CREATE TABLE {name} ({chr(10).join(lines)}\n)")
    return out


def comment_statements() -> list[str]:
    """Every COMMENT ON in the fixture, minus the plants, with the lies fixed."""
    text = COMMENTS_SQL.read_text()
    pattern = re.compile(
        r"COMMENT ON (DATABASE|SCHEMA|TABLE|COLUMN) ([\w.]+) IS\s*'((?:[^']|'')*)';",
        flags=re.S,
    )
    out: list[str] = []
    for kind, target, body in pattern.findall(text):
        table = target.split(".")[0]
        column = target.split(".")[1] if "." in target else ""
        if kind in ("TABLE", "COLUMN") and table in DROPPED_TABLES:
            continue
        if kind == "COLUMN" and column in DROPPED_COLUMNS:
            continue
        corrected = CORRECTED_COMMENTS.get((kind, target))
        if corrected is not None:
            body = corrected.replace("'", "''")
        out.append(f"COMMENT ON {kind} {target} IS '{body}'")
    return out


# ── data ────────────────────────────────────────────────────────────────────
@dataclass
class Table:
    columns: list[str]
    rows: list[tuple[Any, ...]] = field(default_factory=list)

    def add(self, *values: Any) -> None:
        self.rows.append(values)


COUNTRIES = [
    # iso, name, continent, currency
    ("US", "United States", "North America", "USD"),
    ("CA", "Canada", "North America", "CAD"),
    ("MX", "Mexico", "North America", "MXN"),
    ("GB", "United Kingdom", "Europe", "GBP"),
    ("DE", "Germany", "Europe", "EUR"),
    ("FR", "France", "Europe", "EUR"),
    ("NL", "Netherlands", "Europe", "EUR"),
    ("ES", "Spain", "Europe", "EUR"),
    ("SE", "Sweden", "Europe", "SEK"),
    ("NO", "Norway", "Europe", "NOK"),
    ("DK", "Denmark", "Europe", "DKK"),
    ("JP", "Japan", "Asia", "JPY"),
    ("SG", "Singapore", "Asia", "SGD"),
    ("AU", "Australia", "Oceania", "AUD"),
    ("IN", "India", "Asia", "INR"),
    ("KR", "South Korea", "Asia", "KRW"),
    ("TW", "Taiwan", "Asia", "TWD"),
    ("BR", "Brazil", "South America", "BRL"),
    ("CL", "Chile", "South America", "CLP"),
    ("AE", "United Arab Emirates", "Asia", "AED"),
    ("SA", "Saudi Arabia", "Asia", "SAR"),
    ("ZA", "South Africa", "Africa", "ZAR"),
    ("KE", "Kenya", "Africa", "KES"),
]

# name, code, anchor country, share of customers, tax %, cities, legal suffix
REGIONS = [
    ("North America", "NA", "US", 0.37, 7.25,
     ["New York", "Chicago", "Austin", "Seattle", "Toronto", "Denver", "Atlanta", "Boston"], ["Inc.", "LLC", "Co."]),
    ("Europe", "EU", "DE", 0.26, 20.0,
     ["Berlin", "London", "Paris", "Amsterdam", "Madrid", "Munich", "Manchester", "Lyon"], ["GmbH", "Ltd", "BV", "SAS", "S.L."]),
    ("Asia Pacific", "APAC", "SG", 0.17, 9.0,
     ["Singapore", "Sydney", "Tokyo", "Melbourne", "Bengaluru", "Seoul", "Taipei"], ["Pte Ltd", "Pty Ltd", "K.K.", "Pvt Ltd"]),
    ("Latin America", "LATAM", "BR", 0.07, 16.0,
     ["São Paulo", "Mexico City", "Santiago", "Monterrey", "Rio de Janeiro"], ["S.A.", "Ltda.", "S.A. de C.V."]),
    ("Middle East", "ME", "AE", 0.06, 5.0,
     ["Dubai", "Abu Dhabi", "Riyadh", "Doha"], ["FZ-LLC", "LLC", "W.L.L."]),
    ("Nordics", "NORD", "SE", 0.04, 25.0,
     ["Stockholm", "Oslo", "Copenhagen", "Gothenburg"], ["AB", "AS", "ApS"]),
    ("Africa", "AF", "ZA", 0.025, 15.0,
     ["Cape Town", "Johannesburg", "Nairobi", "Lagos"], ["(Pty) Ltd", "Ltd"]),
    ("Unassigned", None, None, 0.005, 0.0, ["—"], ["Ltd"]),
]

CATEGORIES = [
    ("Accessories", "Hubs, docks, stands, chargers and cables"),
    ("Peripherals", "Keyboards, mice, webcams and drawing tablets"),
    ("Displays", "Desktop monitors and portable screens"),
    ("Audio", "Headsets, earbuds, speakers and microphones"),
    ("Networking", "Routers, switches and adapters"),
    ("Storage", "SSDs, hard drives, flash drives and NAS"),
]

SUBCATEGORIES = [
    ("Accessories", "Hubs & docks", "USB-C hubs and Thunderbolt docks"),
    ("Accessories", "Stands & mounts", "Laptop stands and monitor arms"),
    ("Accessories", "Power & cables", "Chargers, power banks and cables"),
    ("Accessories", "Bags & sleeves", "Laptop sleeves and backpacks"),
    ("Peripherals", "Keyboards", "Mechanical and low-profile keyboards"),
    ("Peripherals", "Mice", "Wireless and ergonomic mice"),
    ("Peripherals", "Webcams", "HD and 4K webcams"),
    ("Peripherals", "Tablets", "Pen displays and drawing tablets"),
    ("Displays", "Monitors", "Desktop monitors, 24 to 34 inches"),
    ("Displays", "Portable displays", "USB-C portable screens"),
    ("Audio", "Headsets", "Wired and wireless headsets"),
    ("Audio", "Earbuds", "True-wireless earbuds"),
    ("Audio", "Speakers", "Bluetooth and conference speakers"),
    ("Audio", "Microphones", "USB and XLR microphones"),
    ("Networking", "Routers", "Wi-Fi routers and mesh systems"),
    ("Networking", "Switches", "Unmanaged and smart switches"),
    ("Networking", "Adapters", "Ethernet and powerline adapters"),
    ("Storage", "SSDs", "Internal and portable SSDs"),
    ("Storage", "Hard drives", "External hard drives"),
    ("Storage", "Flash & NAS", "Flash drives and network storage"),
]

BRANDS = [
    # name, country iso, website slug
    ("Arcwave", "TW", "arcwave"),
    ("Keystone", "US", "keystone-input"),
    ("Halo Audio", "DK", "haloaudio"),
    ("Meshline", "US", "meshline"),
    ("Vault", "KR", "vaultstorage"),
    ("Lumen", "US", "lumen-supply"),
    ("Clarity Labs", "JP", "claritylabs"),
    ("Northpeak", "DE", "northpeak"),
]

# name, subcategory, brand, price, cost, weight, popularity, launched (days before today)
PRODUCTS = [
    ('Arcwave 27" 4K Monitor', "Monitors", "Arcwave", 429.00, 268.00, 6.4, 9.0, 900),
    ('Arcwave 32" Curved QHD Monitor', "Monitors", "Arcwave", 389.00, 241.00, 7.9, 5.0, 700),
    ('Arcwave 24" FHD Monitor', "Monitors", "Arcwave", 169.00, 104.00, 3.9, 8.0, 1400),
    ('Arcwave 34" UltraWide Monitor', "Monitors", "Arcwave", 649.00, 402.00, 9.1, 3.2, 420),
    ('Arcwave 16" Portable USB-C Monitor', "Portable displays", "Arcwave", 219.00, 131.00, 0.8, 4.0, 380),
    ("Keystone K2 Mechanical Keyboard", "Keyboards", "Keystone", 129.00, 58.00, 1.1, 8.5, 1100),
    ("Keystone Slim Low-Profile Keyboard", "Keyboards", "Keystone", 79.00, 34.00, 0.6, 7.0, 800),
    ("Keystone Glide Wireless Mouse", "Mice", "Keystone", 39.00, 14.00, 0.1, 12.0, 1300),
    ("Keystone Vertical Ergonomic Mouse", "Mice", "Keystone", 59.00, 22.00, 0.2, 6.0, 600),
    ("Clarity 1080p Webcam", "Webcams", "Clarity Labs", 69.00, 31.00, 0.2, 7.5, 1200),
    ("Clarity 4K Pro Webcam", "Webcams", "Clarity Labs", 179.00, 88.00, 0.3, 4.2, 500),
    ("Clarity Pen Display 13", "Tablets", "Clarity Labs", 349.00, 196.00, 1.3, 1.6, 650),
    ("Halo NC700 Noise-Cancelling Headset", "Headsets", "Halo Audio", 249.00, 118.00, 0.3, 6.5, 850),
    ("Halo Office Wired Headset", "Headsets", "Halo Audio", 59.00, 24.00, 0.2, 7.0, 1300),
    ("Halo Air Wireless Earbuds", "Earbuds", "Halo Audio", 129.00, 52.00, 0.1, 6.0, 450),
    ("Halo Boom Bluetooth Speaker", "Speakers", "Halo Audio", 89.00, 38.00, 0.7, 3.5, 1000),
    ("Halo Meet Conference Speakerphone", "Speakers", "Halo Audio", 199.00, 97.00, 0.5, 2.8, 520),
    ("Halo Studio USB Microphone", "Microphones", "Halo Audio", 139.00, 61.00, 0.9, 3.0, 760),
    ("Meshline Wi-Fi 6 Mesh System (3-pack)", "Routers", "Meshline", 279.00, 158.00, 1.9, 3.8, 950),
    ("Meshline Wi-Fi 6E Router", "Routers", "Meshline", 229.00, 129.00, 1.2, 2.9, 330),
    ("Meshline 8-Port Gigabit Switch", "Switches", "Meshline", 49.00, 21.00, 0.5, 5.0, 1500),
    ("Meshline 24-Port Smart Switch", "Switches", "Meshline", 319.00, 188.00, 3.1, 1.4, 900),
    ("Meshline USB-C Ethernet Adapter", "Adapters", "Meshline", 29.00, 10.00, 0.1, 8.0, 1250),
    ("Meshline Powerline Adapter Kit", "Adapters", "Meshline", 89.00, 41.00, 0.6, 1.8, 1100),
    ("Vault 2TB Portable SSD", "SSDs", "Vault", 189.00, 112.00, 0.1, 6.5, 700),
    ("Vault 1TB NVMe SSD", "SSDs", "Vault", 99.00, 58.00, 0.05, 6.0, 800),
    ("Vault 4TB External Hard Drive", "Hard drives", "Vault", 119.00, 71.00, 0.3, 4.0, 1100),
    ("Vault 256GB USB-C Flash Drive", "Flash & NAS", "Vault", 35.00, 13.00, 0.02, 7.0, 900),
    ("Vault NAS 4-Bay Enclosure", "Flash & NAS", "Vault", 499.00, 312.00, 4.2, 1.2, 620),
    ("Northpeak Thunderbolt 4 Dock", "Hubs & docks", "Northpeak", 229.00, 121.00, 0.6, 6.0, 640),
    ("Lumen USB-C 7-in-1 Hub", "Hubs & docks", "Lumen", 59.00, 21.00, 0.1, 10.0, 1200),
    ("Lumen Aluminium Laptop Stand", "Stands & mounts", "Lumen", 49.00, 17.00, 0.9, 7.5, 1000),
    ("Lumen Dual Monitor Arm", "Stands & mounts", "Lumen", 139.00, 63.00, 5.8, 3.6, 780),
    ("Lumen 100W GaN Charger", "Power & cables", "Lumen", 69.00, 27.00, 0.2, 8.0, 540),
    ("Lumen Braided USB-C Cable (2 m)", "Power & cables", "Lumen", 19.00, 5.00, 0.1, 11.0, 1400),
    ("Lumen 20,000 mAh Power Bank", "Power & cables", "Lumen", 59.00, 25.00, 0.4, 4.5, 870),
    ('Lumen Laptop Sleeve 14"', "Bags & sleeves", "Lumen", 39.00, 12.00, 0.3, 5.0, 1300),
    ("Lumen Commuter Tech Backpack", "Bags & sleeves", "Lumen", 99.00, 38.00, 1.1, 3.2, 480),
]

#: Colour/size variants for the products that come in them.
VARIANTS = {
    "Keystone K2 Mechanical Keyboard": [("black", "full-size", 0), ("white", "full-size", 0), ("black", "tenkeyless", -10)],
    "Keystone Glide Wireless Mouse": [("graphite", "one-size", 0), ("white", "one-size", 0), ("blue", "one-size", 0)],
    "Halo NC700 Noise-Cancelling Headset": [("black", "one-size", 0), ("silver", "one-size", 0)],
    "Halo Air Wireless Earbuds": [("black", "one-size", 0), ("white", "one-size", 0)],
    'Lumen Laptop Sleeve 14"': [("charcoal", '13"', -3), ("charcoal", '14"', 0), ("sand", '16"', 5)],
    "Lumen Commuter Tech Backpack": [("black", "20 L", 0), ("olive", "20 L", 0), ("black", "26 L", 15)],
    "Lumen Braided USB-C Cable (2 m)": [("black", "1 m", -4), ("black", "2 m", 0), ("white", "2 m", 0)],
}

SUPPLIERS = [
    # name, region code, city, lead time, rating
    ("Brightway Electronics Co.", "APAC", "Shenzhen", 21, 4.6),
    ("Formosa Display Technologies", "APAC", "Taipei", 28, 4.8),
    ("Penang Assembly Sdn Bhd", "APAC", "Penang", 24, 4.3),
    ("Hanil Digital Components", "APAC", "Seoul", 18, 4.5),
    ("Dongguan Acoustic Works", "APAC", "Dongguan", 26, 4.1),
    ("Kaohsiung Storage Systems", "APAC", "Kaohsiung", 22, 4.4),
    ("Nordic Audio Supply AB", "NORD", "Malmö", 9, 4.7),
    ("Rhein Connect GmbH", "EU", "Cologne", 7, 4.2),
    ("Rotterdam Distribution BV", "EU", "Rotterdam", 5, 4.0),
    ("Guadalajara Tech Manufacturing", "LATAM", "Guadalajara", 14, 3.9),
    ("Great Lakes Components Inc.", "NA", "Milwaukee", 6, 4.4),
    ("Pacific Crest Peripherals LLC", "NA", "Portland", 8, 4.1),
]

#: Which suppliers can source each brand: (preferred, alternate).
BRAND_SUPPLIERS = {
    "Arcwave": ("Formosa Display Technologies", "Brightway Electronics Co."),
    "Keystone": ("Pacific Crest Peripherals LLC", "Brightway Electronics Co."),
    "Halo Audio": ("Nordic Audio Supply AB", "Dongguan Acoustic Works"),
    "Meshline": ("Great Lakes Components Inc.", "Penang Assembly Sdn Bhd"),
    "Vault": ("Kaohsiung Storage Systems", "Hanil Digital Components"),
    "Lumen": ("Brightway Electronics Co.", "Guadalajara Tech Manufacturing"),
    "Clarity Labs": ("Hanil Digital Components", "Penang Assembly Sdn Bhd"),
    "Northpeak": ("Rhein Connect GmbH", "Rotterdam Distribution BV"),
}

TEAMS = [
    ("Enterprise Accounts", "NA"),
    ("Mid-Market East", "NA"),
    ("Mid-Market West", "NA"),
    ("EMEA Sales", "EU"),
    ("APAC Sales", "APAC"),
    ("Partner Channel", None),
    ("Inside Sales", None),
    ("Renewals & Expansion", None),
]

EMPLOYEES = [
    # name, title, region, team
    ("Rachel Okafor", "VP of Sales", "NA", None),
    ("Daniel Brooks", "Director, Enterprise", "NA", "Enterprise Accounts"),
    ("Sofia Marchetti", "Director, EMEA", "EU", "EMEA Sales"),
    ("Kenji Watanabe", "Director, APAC", "APAC", "APAC Sales"),
    ("Grace Lindqvist", "Director, Channel", "NORD", "Partner Channel"),
    ("Omar Haddad", "Regional Manager", "ME", "EMEA Sales"),
    ("Emily Carter", "Account Manager", "NA", "Enterprise Accounts"),
    ("Luis Fernández", "Account Manager", "LATAM", "Partner Channel"),
    ("Aisha Bello", "Account Manager", "AF", "EMEA Sales"),
    ("Tom Nguyen", "Account Manager", "NA", "Enterprise Accounts"),
    ("Hannah Weber", "Account Manager", "EU", "EMEA Sales"),
    ("Arjun Mehta", "Account Manager", "APAC", "APAC Sales"),
    ("Chloe Martin", "Sales Rep", "EU", "EMEA Sales"),
    ("Jacob Miller", "Sales Rep", "NA", "Mid-Market East"),
    ("Mia Johansson", "Sales Rep", "NORD", "Partner Channel"),
    ("Noah Kim", "Sales Rep", "NA", "Mid-Market West"),
    ("Isabella Rossi", "Sales Rep", "EU", "EMEA Sales"),
    ("Ethan Walker", "Sales Rep", "NA", "Mid-Market East"),
    ("Yuki Tanaka", "Sales Rep", "APAC", "APAC Sales"),
    ("Lucas Silva", "Sales Rep", "LATAM", "Partner Channel"),
    ("Zara Ahmed", "Sales Rep", "ME", "Inside Sales"),
    ("Ben Thompson", "Sales Rep", "NA", "Mid-Market West"),
    ("Priya Sharma", "Sales Rep", "APAC", "APAC Sales"),
    ("Oliver Wright", "Sales Rep", "EU", "Inside Sales"),
    ("Ava Robinson", "Sales Rep", "NA", "Inside Sales"),
    ("Mateo García", "Sales Rep", "LATAM", "Inside Sales"),
    ("Freya Nilsen", "Sales Rep", "NORD", "Inside Sales"),
    ("Samuel Osei", "Sales Rep", "AF", "Inside Sales"),
    ("Layla Hassan", "Sales Rep", "ME", "Inside Sales"),
    ("Max Fischer", "Sales Rep", "EU", "Renewals & Expansion"),
    ("Chen Jing", "Sales Rep", "APAC", "Renewals & Expansion"),
    ("Olivia Davis", "Sales Rep", "NA", "Renewals & Expansion"),
    ("Ryan Patel", "Sales Rep", "NA", "Mid-Market East"),
    ("Elena Popescu", "Sales Rep", "EU", "Renewals & Expansion"),
    ("Kwame Mensah", "Sales Rep", "AF", "Partner Channel"),
    ("Julia Novak", "Sales Rep", "EU", "Mid-Market West"),
]
MANAGERS = {"Sales Rep": "Account Manager", "Account Manager": "Director"}

COMPANY_FIRST = [
    "Cedar", "Harbor", "Summit", "Bluewater", "Northfield", "Ironwood", "Maple", "Granite",
    "Beacon", "Crescent", "Riverside", "Oakridge", "Silverline", "Brightpath", "Evergreen",
    "Pioneer", "Horizon", "Lakeshore", "Sterling", "Westbrook", "Aspen", "Fairview",
    "Redwood", "Highland", "Coastal", "Prairie", "Liberty", "Copper", "Falcon", "Juniper",
    "Willow", "Atlas", "Orchard", "Parkside", "Cobalt", "Kestrel", "Linden", "Marlow",
    "Quarry", "Tidewater", "Vantage", "Wren", "Alder", "Birch", "Canyon", "Driftwood",
    "Elmstead", "Foxglove", "Glenmore", "Hawthorn", "Ivory", "Jasper", "Keel", "Larkspur",
    "Mosaic", "Nimbus", "Onyx", "Pembroke", "Quill", "Rowan",
]
COMPANY_SECOND = [
    "Dental Group", "Architects", "Logistics", "Legal", "Analytics", "Design Studio",
    "Health Partners", "Engineering", "Realty", "Accounting", "Media", "Consulting", "Labs",
    "Academy", "Clinic", "Manufacturing", "Hospitality", "Credit Union", "Veterinary",
    "Insurance", "Studios", "Robotics", "Foods", "Energy", "Capital", "Pharmacy",
    "Construction", "Software", "Freight", "Research",
]

FIRST_NAMES = [
    "James", "Mary", "Wei", "Fatima", "Carlos", "Anna", "Mohammed", "Sara", "David", "Yuki",
    "Elena", "Kofi", "Lina", "Pedro", "Nora", "Ivan", "Amara", "Leo", "Maya", "Hiro",
]
LAST_NAMES = [
    "Smith", "Garcia", "Chen", "Khan", "Müller", "Rossi", "Silva", "Kowalski", "Nakamura",
    "Okoye", "Dubois", "Jensen", "Haddad", "Novak", "Park", "Costa", "Ahmed", "Larsen",
]

PROMOTIONS = [
    # name, description, type, pct, budget, starts (month offset, day), length days
    ("Back to School 2024", "Students and educators: 10% off peripherals", "percent", 10, 40000, (-24, 5), 30),
    ("Black Friday 2024", "Storewide Black Friday pricing", "percent", 15, 120000, (-22, 25), 7),
    ("Cyber Monday 2024", "Online-only Cyber Monday offer", "percent", 12, 60000, (-21, 1), 2),
    ("New Year Clearance 2025", "Clearance on last season's models", "percent", 20, 30000, (-20, 2), 21),
    ("Spring Refresh 2025", "Monitors and docks for the new office", "percent", 8, 50000, (-18, 10), 30),
    ("Summer Workspace 2025", "Buy a monitor, get a stand", "bogo", 0, 35000, (-15, 1), 45),
    ("Back to School 2025", "Students and educators: 10% off peripherals", "percent", 10, 45000, (-12, 5), 30),
    ("Black Friday 2025", "Storewide Black Friday pricing", "percent", 15, 150000, (-10, 24), 7),
    ("Cyber Monday 2025", "Online-only Cyber Monday offer", "percent", 12, 70000, (-9, 1), 2),
    ("New Year Clearance 2026", "Clearance on last season's models", "percent", 20, 30000, (-8, 2), 21),
    ("Spring Refresh 2026", "Monitors and docks for the new office", "percent", 8, 60000, (-6, 9), 30),
    ("Summer Workspace 2026", "Buy a monitor, get a stand", "bogo", 0, 40000, (-3, 1), 45),
    ("Back to School 2026", "Students and educators: 10% off peripherals", "percent", 10, 50000, (0, 1), 30),
    ("Enterprise Volume Program", "Tiered pricing for orders over $25,000", "fixed", 0, 250000, (-24, 1), 760),
    ("Loyalty Double Points", "Double loyalty points for Gold and Platinum", "percent", 5, 20000, (-16, 1), 60),
]

REVIEW_TITLES = {
    5: ["Exactly what we needed", "Great value", "Rock solid", "Would buy again", "Excellent build quality"],
    4: ["Very good", "Solid choice", "Does the job well", "Happy with it", "Good, minor quirks"],
    3: ["It's fine", "Average", "OK for the price", "Mixed feelings"],
    2: ["Disappointing", "Not as described", "Had issues"],
    1: ["Stopped working", "Returned it", "Poor quality"],
}
REVIEW_BODIES = {
    5: "Rolled these out to the whole team and nobody has complained since.",
    4: "Works well day to day. Setup took a few minutes longer than expected.",
    3: "Does what it says, but there are better options at this price.",
    2: "Worked at first, then started acting up after a few weeks.",
    1: "Arrived faulty and support was slow to respond.",
}
TICKET_SUBJECTS = [
    "Where is my order?", "Request a copy of the invoice", "Dock not detecting second monitor",
    "Change delivery address", "Wrong item received", "Bulk pricing request", "Warranty claim",
    "Headset microphone too quiet", "Update billing contact", "Return label not received",
    "Monitor has a dead pixel", "Question about Wi-Fi 6E compatibility",
]


def _month_shift(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def _money(x: float) -> float:
    return float(f"{x:.2f}")


def generate(today: date) -> dict[str, Table]:
    """Every table's rows, in an order foreign keys can be loaded in."""
    rng = random.Random(SEED)
    t: dict[str, Table] = {}
    now = datetime.combine(today, time(6, 0), tzinfo=timezone.utc)
    start = today - timedelta(days=730)

    def ts(d: date, hour: float = 9.0) -> datetime:
        return datetime.combine(d, time(0, 0), tzinfo=timezone.utc) + timedelta(hours=hour)

    created = ts(start - timedelta(days=400))

    # ── reference ──
    countries = t["countries"] = Table(["id", "iso_code", "name", "continent", "currency_code", "created_at"])
    country_id = {}
    for i, (iso, name, continent, cur) in enumerate(COUNTRIES, 1):
        countries.add(i, iso, name, continent, cur, created)
        country_id[iso] = i

    regions = t["regions"] = Table(["id", "country_id", "name", "code", "created_at"])
    region_id: dict[str | None, int] = {}
    for i, (name, code, iso, *_rest) in enumerate(REGIONS, 1):
        regions.add(i, country_id.get(iso) if iso else None, name, code, created)
        region_id[code] = i
    region_meta = {region_id[r[1]]: r for r in REGIONS}

    categories = t["categories"] = Table(["id", "name", "description", "created_at", "updated_at"])
    category_id = {}
    for i, (name, desc) in enumerate(CATEGORIES, 1):
        categories.add(i, name, desc, created, created)
        category_id[name] = i

    subcategories = t["subcategories"] = Table(["id", "category_id", "name", "description", "created_at"])
    sub_id, sub_category = {}, {}
    for i, (cat, name, desc) in enumerate(SUBCATEGORIES, 1):
        subcategories.add(i, category_id[cat], name, desc, created)
        sub_id[name] = i
        sub_category[name] = cat

    brands = t["brands"] = Table(["id", "name", "country_id", "website", "is_active", "source_system", "created_at", "updated_at"])
    brand_id = {}
    for i, (name, iso, slug) in enumerate(BRANDS, 1):
        brands.add(i, name, country_id[iso], f"https://{slug}.example", True, "pim", created, created)
        brand_id[name] = i

    products = t["products"] = Table([
        "id", "name", "category", "category_id", "subcategory_id", "brand_id", "sku", "price",
        "cost", "weight_kg", "active", "discontinued", "launched_at", "source_system",
        "external_ref", "created_at", "updated_at",
    ])
    product_rows = []
    for i, (name, sub, brand, price, cost, weight, pop, age) in enumerate(PRODUCTS, 1):
        cat = sub_category[sub]
        launched = today - timedelta(days=age)
        sku = f"{brand[:3].upper()}-{cat[:3].upper()}-{i:04d}"
        discontinued = name == "Meshline Powerline Adapter Kit"
        products.add(
            i, name, cat, category_id[cat], sub_id[sub], brand_id[brand], sku, price, cost, weight,
            not discontinued, discontinued, launched, "pim", f"PIM-{100000 + i * 37}",
            ts(launched - timedelta(days=30)), ts(today - timedelta(days=rng.randint(5, 200))),
        )
        product_rows.append({
            "id": i, "name": name, "price": price, "cost": cost, "pop": pop, "brand": brand,
            "category": cat, "launched": launched, "discontinued": discontinued,
        })
    product_by_name = {p["name"]: p for p in product_rows}

    variants = t["product_variants"] = Table(["id", "product_id", "variant_sku", "color", "size", "extra_price", "barcode", "active", "created_at"])
    variant_ids: dict[int, list[int]] = {}
    vid = 0
    for pname, options in VARIANTS.items():
        p = product_by_name[pname]
        for color, size, extra in options:
            vid += 1
            variants.add(vid, p["id"], f"V-{p['id']:04d}-{vid:03d}", color, size, float(extra), f"50{p['id']:04d}{vid:06d}", True, ts(p["launched"]))
            variant_ids.setdefault(p["id"], []).append(vid)

    suppliers = t["suppliers"] = Table([
        "id", "name", "region_id", "contact_email", "phone", "lead_time_days", "rating", "active",
        "address_line1", "city", "postal_code", "created_at", "updated_at",
    ])
    supplier_id = {}
    for i, (name, code, city, lead, rating) in enumerate(SUPPLIERS, 1):
        slug = re.sub(r"[^a-z]+", "", name.lower())[:18]
        suppliers.add(
            i, name, region_id[code], f"orders@{slug}.example", f"+{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
            lead, rating, True, f"{rng.randint(2, 480)} Industrial Park Road", city, f"{rng.randint(10000, 99999)}", created, created,
        )
        supplier_id[name] = i

    warehouses = t["warehouses"] = Table(["id", "name", "region_id", "capacity", "address_line1", "city", "is_active", "created_at"])
    warehouse_for_region = {}
    wh_specs = [("Reno DC", "NA", 24000, "Reno"), ("Columbus DC", "NA", 18000, "Columbus"),
                ("Venlo DC", "EU", 20000, "Venlo"), ("Singapore DC", "APAC", 12000, "Singapore"),
                ("Sydney DC", "APAC", 6000, "Sydney"), ("São Paulo DC", "LATAM", 5000, "São Paulo"),
                ("Dubai DC", "ME", 4000, "Dubai"), ("Gothenburg DC", "NORD", 3500, "Gothenburg")]
    for i, (name, code, cap, city) in enumerate(wh_specs, 1):
        warehouses.add(i, name, region_id[code], cap, f"{rng.randint(10, 900)} Logistics Way", city, True, created)
        warehouse_for_region.setdefault(region_id[code], i)
    warehouse_for_region[region_id["AF"]] = 3
    warehouse_for_region[region_id[None]] = 1

    teams = t["teams"] = Table(["id", "name", "region_id", "created_at"])
    team_id = {}
    for i, (name, code) in enumerate(TEAMS, 1):
        teams.add(i, name, region_id[code] if code else None, created)
        team_id[name] = i

    employees = t["employees"] = Table([
        "id", "name", "title", "region_id", "team_id", "manager_id", "email", "hired_at",
        "terminated_at", "salary", "commission_pct", "active", "created_at",
    ])
    emp_rows = []
    for i, (name, title, code, team) in enumerate(EMPLOYEES, 1):
        emp_rows.append({"id": i, "name": name, "title": title, "region": region_id[code], "team": team})
    for e in emp_rows:
        if e["title"] == "VP of Sales":
            manager = None
        elif e["title"].startswith("Director") or e["title"] == "Regional Manager":
            manager = 1
        else:
            directors = [d for d in emp_rows if d["title"].startswith("Director") and d["team"] == e["team"]]
            managers = [m for m in emp_rows if m["title"] == "Account Manager" and m["region"] == e["region"]]
            pick = (managers if e["title"] == "Sales Rep" and managers else directors) or [emp_rows[1]]
            manager = pick[0]["id"] if pick[0]["id"] != e["id"] else 1
        e["manager"] = manager
        hired = today - timedelta(days=rng.randint(200, 3200))
        terminated = today - timedelta(days=rng.randint(30, 150)) if e["name"] in ("Oliver Wright", "Julia Novak") else None
        base = {"VP of Sales": 210000, "Regional Manager": 150000, "Account Manager": 118000, "Sales Rep": 78000}.get(e["title"], 165000)
        email = e["name"].lower().replace(" ", ".").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
        employees.add(
            e["id"], e["name"], e["title"], e["region"], team_id.get(e["team"]) if e["team"] else None, manager,
            f"{email}@lumen-supply.example", hired, terminated, float(base + rng.randint(-8, 12) * 1000),
            0.0 if e["title"].startswith(("VP", "Director")) else float(rng.choice([2.5, 3.0, 4.0, 5.0])),
            terminated is None, ts(hired),
        )
        e["active"] = terminated is None
        e["terminated"] = terminated

    employee_teams = t["employee_teams"] = Table(["employee_id", "team_id", "role_in_team", "assigned_on"])
    for e in emp_rows:
        if e["team"]:
            lead = e["title"].startswith(("Director", "Regional"))
            employee_teams.add(e["id"], team_id[e["team"]], "lead" if lead else "member", today - timedelta(days=rng.randint(60, 900)))
    for name in ("Emily Carter", "Tom Nguyen", "Chloe Martin", "Max Fischer"):
        e = next(x for x in emp_rows if x["name"] == name)
        employee_teams.add(e["id"], team_id["Renewals & Expansion"] if e["team"] != "Renewals & Expansion" else team_id["Inside Sales"], "member", today - timedelta(days=rng.randint(30, 400)))

    tiers = t["loyalty_tiers"] = Table(["id", "name", "min_points", "discount_pct", "created_at"])
    for i, (name, pts, pct) in enumerate([("Bronze", 0, 0.0), ("Silver", 1000, 2.5), ("Gold", 5000, 5.0), ("Platinum", 20000, 10.0)], 1):
        tiers.add(i, name, pts, pct, created)

    currencies = t["currencies"] = Table(["id", "code", "name", "symbol", "created_at"])
    for i, (code, name, sym) in enumerate([
        ("USD", "US Dollar", "$"), ("EUR", "Euro", "€"), ("GBP", "Pound Sterling", "£"),
        ("JPY", "Japanese Yen", "¥"), ("AUD", "Australian Dollar", "A$"), ("CAD", "Canadian Dollar", "C$"),
        ("SEK", "Swedish Krona", "kr"), ("BRL", "Brazilian Real", "R$"),
    ], 1):
        currencies.add(i, code, name, sym, created)

    price_lists = t["price_lists"] = Table(["id", "name", "currency_id", "valid_from", "valid_to", "is_active", "created_at"])
    price_lists.add(1, "List price (USD)", 1, start - timedelta(days=200), None, True, created)
    price_lists.add(2, "Partner", 1, start - timedelta(days=200), None, True, created)
    price_lists.add(3, "Enterprise volume", 1, start, None, True, created)
    price_lists.add(4, "EU retail (EUR)", 2, start, None, True, created)
    price_lists.add(5, "List price 2023", 1, start - timedelta(days=700), start - timedelta(days=200), False, created)
    pli = t["price_list_items"] = Table(["price_list_id", "product_id", "unit_price"])
    for p in product_rows:
        pli.add(1, p["id"], p["price"])
        pli.add(2, p["id"], _money(p["price"] * 0.88))
        if p["price"] >= 150:
            pli.add(3, p["id"], _money(p["price"] * 0.85))
        pli.add(4, p["id"], _money(p["price"] * 0.93))
        if p["launched"] < start:
            pli.add(5, p["id"], _money(p["price"] * 1.06))

    tax_rates = t["tax_rates"] = Table(["id", "region_id", "name", "rate_pct", "valid_from", "created_at"])
    tax_for_region = {}
    for i, (name, code, _iso, _share, rate, *_rest) in enumerate(REGIONS, 1):
        rid = region_id[code]
        label = {"NA": "US/CA sales tax (blended)", "EU": "EU VAT standard", "APAC": "GST (blended)",
                 "LATAM": "IVA (blended)", "ME": "VAT standard", "NORD": "Nordic VAT standard",
                 "AF": "VAT standard", None: "Exempt"}[code]
        tax_rates.add(i, rid, label, rate, start - timedelta(days=365), created)
        tax_for_region[rid] = (i, rate)
    tax_rates.add(len(REGIONS) + 1, region_id["EU"], "EU VAT reduced", 7.0, start - timedelta(days=365), created)

    promotions = t["promotions"] = Table([
        "id", "name", "description", "promo_type", "discount_pct", "budget", "starts_on", "ends_on",
        "is_active", "created_at", "updated_at",
    ])
    promo_rows = []
    for i, (name, desc, kind, pct, budget, (moff, day), length) in enumerate(PROMOTIONS, 1):
        s = _month_shift(today.replace(day=1), moff).replace(day=day)
        e = s + timedelta(days=length)
        promotions.add(i, name, desc, kind, float(pct), float(budget), s, e, e >= today, ts(s - timedelta(days=14)), ts(s - timedelta(days=7)))
        promo_rows.append({"id": i, "name": name, "pct": pct, "kind": kind, "start": s, "end": e})

    coupons = t["coupons"] = Table(["id", "promotion_id", "code", "max_uses", "times_used", "expires_on", "created_at"])
    coupon_rows = []
    cid = 0
    for p in promo_rows:
        if p["name"].startswith(("Enterprise", "Loyalty")):
            continue
        for suffix in ("WEB", "PARTNER"):
            cid += 1
            words = p["name"].split()
            year = words[-1][-2:] if words[-1].isdigit() else ""
            code = "".join(w[0] for w in words if not w.isdigit()).upper() + year + suffix
            coupons.add(cid, p["id"], code, 5000, 0, p["end"], ts(p["start"] - timedelta(days=7)))
            coupon_rows.append({"id": cid, "promo": p, "uses": 0})

    tags = t["tags"] = Table(["id", "name", "kind", "created_at"])
    tag_names = ['bestseller', 'clearance', 'new', 'eco', 'premium', 'bulk', 'fragile', 'refurb',
                 'bundle', 'limited', 'gaming', 'office', 'travel', 'wireless', 'usb-c', '4k', 'rgb',
                 'compact', 'heavy-duty', 'warranty-3y', 'warranty-1y', 'imported', 'local', 'seasonal', 'staff-pick']
    for i, name in enumerate(tag_names, 1):
        tags.add(i, name, ["attribute", "merch", "merch", "lifecycle"][i % 4], created)
    tag_id = {n: i for i, n in enumerate(tag_names, 1)}

    carriers = t["carriers"] = Table(["id", "name", "tracking_url", "is_active", "created_at"])
    for i, (name, url) in enumerate([
        ("UPS", "https://ups.example/track?n="), ("FedEx", "https://fedex.example/track?n="),
        ("DHL", "https://dhl.example/track?n="), ("USPS", "https://usps.example/track?n="),
        ("Aramex", "https://aramex.example/track?n="), ("Local Courier", None),
    ], 1):
        carriers.add(i, name, url, True, created)
    carrier_for_region = {region_id["NA"]: [1, 2, 4], region_id["EU"]: [3, 1], region_id["APAC"]: [3, 2],
                          region_id["LATAM"]: [3, 2], region_id["ME"]: [5, 3], region_id["NORD"]: [3, 6],
                          region_id["AF"]: [3, 5], region_id[None]: [1]}

    methods = t["payment_methods"] = Table(["id", "name", "kind", "is_active", "created_at"])
    for i, (name, kind) in enumerate([("Visa", "card"), ("Mastercard", "card"), ("Amex", "card"),
                                      ("PayPal", "wallet"), ("Wire transfer", "bank"), ("Store credit", "credit")], 1):
        methods.add(i, name, kind, True, created)

    # ── bridges on the catalogue ──
    ps = t["product_suppliers"] = Table(["product_id", "supplier_id", "cost", "is_preferred", "lead_time_days"])
    for p in product_rows:
        pref, alt = BRAND_SUPPLIERS[p["brand"]]
        ps.add(p["id"], supplier_id[pref], p["cost"], True, SUPPLIERS[supplier_id[pref] - 1][3])
        if p["price"] >= 60:
            ps.add(p["id"], supplier_id[alt], _money(p["cost"] * rng.uniform(1.03, 1.12)), False, SUPPLIERS[supplier_id[alt] - 1][3])

    ptags = t["product_tags"] = Table(["product_id", "tag_id"])
    for p in product_rows:
        chosen = set()
        if p["pop"] >= 8:
            chosen.add("bestseller")
        if p["price"] >= 300:
            chosen.add("premium")
        if "Wireless" in p["name"] or "Wi-Fi" in p["name"] or "Bluetooth" in p["name"] or "Earbuds" in p["name"]:
            chosen.add("wireless")
        if "USB-C" in p["name"] or "Thunderbolt" in p["name"]:
            chosen.add("usb-c")
        if "4K" in p["name"]:
            chosen.add("4k")
        if p["launched"] > today - timedelta(days=400):
            chosen.add("new")
        if p["discontinued"]:
            chosen.add("clearance")
        chosen.add(rng.choice(["office", "warranty-1y", "warranty-3y", "compact", "travel", "staff-pick"]))
        for name in sorted(chosen):
            ptags.add(p["id"], tag_id[name])

    # ── customers ──
    customers = t["customers"] = Table([
        "id", "name", "email", "phone", "region_id", "loyalty_tier_id", "referred_by_id", "segment",
        "credit_limit", "signed_up_at", "last_order_at", "is_deleted", "deleted_at", "created_at", "updated_at",
    ])
    cust_rows: list[dict[str, Any]] = []
    names_seen: set[str] = set()
    region_codes = [r[1] for r in REGIONS]
    region_weights = [r[3] for r in REGIONS]
    n_customers = 1450
    first_signup = today - timedelta(days=1500)

    def company_name(code: str | None) -> str:
        meta = next(r for r in REGIONS if r[1] == code)
        while True:
            base = f"{rng.choice(COMPANY_FIRST)} {rng.choice(COMPANY_SECOND)}"
            name = f"{base} {rng.choice(meta[6])}"
            if base not in names_seen:
                names_seen.add(base)
                return name

    specials = [("Meridian Health Systems Inc.", "NA", "Enterprise")]
    for i in range(1, n_customers + 1):
        if i == 7:
            name, code, segment = specials[0]
            names_seen.add("Meridian Health Systems")
        else:
            code = rng.choices(region_codes, region_weights)[0]
            name = company_name(code)
            segment = rng.choices(["SMB", "Mid-Market", "Enterprise"], [0.62, 0.28, 0.10])[0]
        # Sign-ups accelerate: more of them recently than four years ago.
        u = rng.random() ** 0.8
        signed = first_signup + timedelta(days=int(u * (today - first_signup).days))
        if i == 7:
            signed = today - timedelta(days=1200)
        deleted = rng.random() < 0.06 and signed < today - timedelta(days=200) and i != 7
        deleted_at = ts(today - timedelta(days=rng.randint(10, 180))) if deleted else None
        slug = re.sub(r"[^a-z]+", "", name.lower())[:24]
        tier = rng.choices([None, 1, 2, 3, 4], [0.3, 0.35, 0.2, 0.1, 0.05])[0]
        credit = {"SMB": 5000, "Mid-Market": 25000, "Enterprise": 250000}[segment] * rng.choice([1, 1, 2, 4])
        cust_rows.append({
            "id": i, "name": name, "region": region_id[code], "segment": segment, "signed": signed,
            "deleted_at": deleted_at, "tier": tier, "credit": float(credit), "email": f"purchasing@{slug}.example",
            "activity": rng.lognormvariate(0, 0.7) * {"SMB": 1.0, "Mid-Market": 1.8, "Enterprise": 3.2}[segment],
        })
    for c in cust_rows:
        referrer = None
        if c["id"] > 60 and rng.random() < 0.08:
            earlier = [x for x in cust_rows[: c["id"] - 1] if x["signed"] < c["signed"]]
            if earlier:
                referrer = rng.choice(earlier)["id"]
        c["referrer"] = referrer

    # ── orders ──
    orders = t["orders"] = Table([
        "id", "customer_id", "employee_id", "coupon_id", "currency_id", "order_date", "status", "channel",
        "subtotal", "discount_total", "tax_total", "shipping_fee", "total_amount", "notes", "placed_at",
        "created_at", "updated_at",
    ])
    items = t["order_items"] = Table(["id", "order_id", "product_id", "variant_id", "quantity", "unit_price", "discount", "tax_rate_id", "line_total", "created_at"])
    order_promos = t["order_promotions"] = Table(["order_id", "promotion_id", "discount_amount"])

    seasonal = {1: 0.80, 2: 0.87, 3: 1.00, 4: 1.00, 5: 1.01, 6: 1.00, 7: 0.97, 8: 0.92, 9: 1.00, 10: 1.02, 11: 1.22, 12: 1.30}
    weekday = [1.12, 1.15, 1.14, 1.10, 1.02, 0.62, 0.50]
    reps_by_region: dict[int, list[int]] = {}
    for e in emp_rows:
        if e["title"] in ("Sales Rep", "Account Manager") and e["active"]:
            reps_by_region.setdefault(e["region"], []).append(e["id"])
    all_reps = [e["id"] for e in emp_rows if e["title"] in ("Sales Rep", "Account Manager")]

    order_rows: list[dict[str, Any]] = []
    oid = iid = 0
    outlier_month = _month_shift(today.replace(day=1), -3)  # June when today is in September
    outlier_day = outlier_month.replace(day=16)

    def pick_products(segment: str, d: date) -> list[tuple[dict[str, Any], int]]:
        live = [p for p in product_rows if p["launched"] <= d and not (p["discontinued"] and d > today - timedelta(days=120))]
        n_lines = rng.choices([1, 2, 3, 4], [0.42, 0.31, 0.18, 0.09])[0]
        chosen = []
        for p in rng.choices(live, [p["pop"] for p in live], k=n_lines):
            if any(p is q for q, _ in chosen):
                continue
            qty = rng.choices([1, 2, 3, 4, 6], [0.58, 0.22, 0.1, 0.06, 0.04])[0]
            if segment == "Mid-Market":
                qty *= rng.choice([1, 2])
            elif segment == "Enterprise":
                qty *= rng.choice([2, 3, 4])
            chosen.append((p, qty))
        return chosen

    def active_promo(d: date) -> dict[str, Any] | None:
        live = [p for p in promo_rows if p["start"] <= d <= p["end"] and p["kind"] == "percent" and not p["name"].startswith("Loyalty")]
        return live[0] if live else None

    def add_order(d: date, customer: dict[str, Any], lines: list[tuple[dict[str, Any], int]], *,
                  channel: str | None = None, note: str | None = None, hour: float | None = None,
                  line_discount: float | None = None, allow_promo: bool = True) -> None:
        nonlocal oid, iid
        oid += 1
        channel = channel or rng.choices(["web", "phone", "partner"], [0.6, 0.25, 0.15])[0]
        rep = None
        if channel != "web":
            rep = rng.choice(reps_by_region.get(customer["region"]) or all_reps)
        placed = ts(d, hour if hour is not None else rng.uniform(7.5, 19.5))
        tax_id, tax_pct = tax_for_region[customer["region"]]
        subtotal = 0.0
        for p, qty in lines:
            iid += 1
            disc = line_discount if line_discount is not None else rng.choices([0, 5, 10], [0.82, 0.12, 0.06])[0]
            vlist = variant_ids.get(p["id"])
            variant = rng.choice(vlist) if vlist and rng.random() < 0.85 else None
            line_total = _money(qty * p["price"] * (1 - disc / 100))
            subtotal += line_total
            items.add(iid, oid, p["id"], variant, qty, p["price"], float(disc), tax_id if tax_pct else None, line_total, placed)
        subtotal = _money(subtotal)
        discount = 0.0
        coupon = None
        promo = active_promo(d) if allow_promo else None
        if promo and rng.random() < 0.45:
            discount = _money(subtotal * promo["pct"] / 100)
            if channel == "web" and rng.random() < 0.6:
                options = [c for c in coupon_rows if c["promo"] is promo]
                if options:
                    coupon = options[0]["id"]
                    options[0]["uses"] += 1
            order_promos.add(oid, promo["id"], discount)
        tax = _money((subtotal - discount) * tax_pct / 100)
        shipping = 0.0 if (subtotal >= 150 or channel != "web") else 9.99
        total = _money(subtotal - discount + tax + shipping)
        age = (today - d).days
        if note:
            status = "completed" if age > 12 else "shipped"
        elif age <= 2:
            status = rng.choices(["pending", "shipped", "cancelled"], [0.62, 0.33, 0.05])[0]
        elif age <= 12:
            status = rng.choices(["shipped", "completed", "cancelled"], [0.55, 0.40, 0.05])[0]
        else:
            status = rng.choices(["completed", "cancelled", "returned"], [0.915, 0.05, 0.035])[0]
        updated = placed + timedelta(days=min(age, rng.randint(1, 8)))
        orders.add(oid, customer["id"], rep, coupon, 1, d, status, channel, subtotal, discount, tax, shipping, total,
                   note, placed, placed, updated)
        order_rows.append({"id": oid, "customer": customer, "date": d, "status": status, "channel": channel,
                           "placed": placed, "total": total, "rep": rep, "first_item": iid - len(lines) + 1,
                           "n_items": len(lines)})

    months = (today.year - start.year) * 12 + today.month - start.month
    d = start
    while d <= today:
        month_index = (d.year - start.year) * 12 + d.month - start.month
        trend = 1.021 ** month_index
        rate = 15.5 * trend * seasonal[d.month] * weekday[d.weekday()]
        if d == today:
            rate *= 0.3  # the current day is partial until the nightly load
        n = int(rate) + (1 if rng.random() < rate - int(rate) else 0)
        live = [c for c in cust_rows if c["signed"] <= d and (c["deleted_at"] is None or c["deleted_at"].date() > d)]
        weights = [c["activity"] for c in live]
        for customer in rng.choices(live, weights, k=n):
            add_order(d, customer, pick_products(customer["segment"], d))
        if d == outlier_day:
            meridian = cust_rows[6]
            add_order(
                d, meridian,
                [(product_by_name['Arcwave 27" 4K Monitor'], 300), (product_by_name["Northpeak Thunderbolt 4 Dock"], 300),
                 (product_by_name["Lumen Dual Monitor Arm"], 150), (product_by_name["Keystone Slim Low-Profile Keyboard"], 300)],
                channel="phone", note="Fleet refresh — 26 clinics, delivery in two waves", hour=15.25,
                line_discount=5, allow_promo=False,
            )
        d += timedelta(days=1)
    del months

    items_by_order: dict[int, list[tuple[Any, ...]]] = {}
    for row in items.rows:
        items_by_order.setdefault(row[1], []).append(row)

    last_order: dict[int, datetime] = {}
    for o in order_rows:
        if o["status"] != "cancelled":
            last_order[o["customer"]["id"]] = max(last_order.get(o["customer"]["id"], o["placed"]), o["placed"])

    for c in cust_rows:
        customers.add(
            c["id"], c["name"], c["email"], f"+{rng.randint(1, 99)} {rng.randint(200, 999)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
            c["region"], c["tier"], c["referrer"], c["segment"], c["credit"], ts(c["signed"], rng.uniform(8, 18)),
            last_order.get(c["id"]), c["deleted_at"] is not None, c["deleted_at"], ts(c["signed"]),
            c["deleted_at"] or ts(min(today, c["signed"] + timedelta(days=rng.randint(0, 400)))),
        )

    addresses = t["customer_addresses"] = Table(["id", "customer_id", "kind", "line1", "line2", "city", "region_id", "postal_code", "country_id", "is_primary", "created_at"])
    aid = 0
    for c in cust_rows:
        meta = region_meta[c["region"]]
        city = rng.choice(meta[5])
        anchor = country_id.get(meta[2]) if meta[2] else None
        for kind in (["shipping", "billing"] if c["segment"] != "SMB" or rng.random() < 0.3 else ["shipping"]):
            aid += 1
            addresses.add(aid, c["id"], kind, f"{rng.randint(1, 2400)} {rng.choice(['Market', 'Harbor', 'Elm', 'King', 'Station', 'Park', 'Mill'])} {rng.choice(['Street', 'Avenue', 'Road', 'Way'])}",
                          rng.choice([None, None, f"Suite {rng.randint(100, 900)}", f"Floor {rng.randint(2, 30)}"]),
                          city, c["region"], f"{rng.randint(10000, 99999)}", anchor, kind == "shipping", ts(c["signed"]))

    # ── fulfilment and money ──
    payments = t["payments"] = Table(["id", "order_id", "payment_method_id", "amount", "currency_id", "status", "paid_at", "txn_ref", "created_at"])
    shipments = t["shipments"] = Table(["id", "order_id", "warehouse_id", "carrier_id", "tracking_number", "status", "shipped_at", "delivered_at", "weight_kg", "cost", "created_at"])
    shipment_items = t["shipment_items"] = Table(["shipment_id", "order_item_id", "quantity"])
    returns = t["returns"] = Table(["id", "order_item_id", "reason", "quantity", "refund_amount", "status", "returned_at", "created_at"])
    refunds = t["refunds"] = Table(["id", "return_id", "payment_id", "amount", "method", "processed_at", "created_at"])
    history = t["order_status_history"] = Table(["id", "order_id", "old_status", "new_status", "changed_by", "changed_at", "note"])
    pid = sid = rid = fid = hid = 0
    end_of_today = ts(today, 23.99)

    def clamp(x: datetime) -> datetime:
        return min(x, now)

    for o in order_rows:
        placed = o["placed"]
        hid += 1
        history.add(hid, o["id"], None, "pending", o["rep"], placed, "created")
        if o["status"] == "cancelled":
            hid += 1
            history.add(hid, o["id"], "pending", "cancelled", o["rep"], clamp(placed + timedelta(hours=rng.randint(1, 30))), rng.choice(["customer request", "payment declined", "duplicate order"]))
            continue
        if o["status"] == "pending":
            continue
        pid += 1
        method = 5 if o["customer"]["segment"] == "Enterprise" else rng.choices([1, 2, 3, 4, 6], [0.38, 0.28, 0.12, 0.18, 0.04])[0]
        paid = clamp(placed + timedelta(hours=rng.choice([0.1, 0.2, 2, 20, 44])))
        payments.add(pid, o["id"], method, o["total"], 1, "captured", paid, f"TXN{o['id']:09d}", paid)
        payment_id = pid
        order_items = items_by_order[o["id"]]
        waves = 2 if o["customer"]["id"] == 7 and o["total"] > 100000 else 1
        shipped_at = clamp(placed + timedelta(hours=rng.randint(14, 40)))
        delivered = None
        for wave in range(waves):
            sid += 1
            s_at = clamp(shipped_at + timedelta(days=wave * 9))
            done = o["status"] in ("completed", "returned")
            delivered = clamp(s_at + timedelta(days=rng.randint(1, 6))) if done else None
            carrier = rng.choice(carrier_for_region[o["customer"]["region"]])
            weight = sum(PRODUCTS[r[2] - 1][5] * r[4] for r in order_items) / waves
            shipments.add(sid, o["id"], warehouse_for_region[o["customer"]["region"]], carrier, f"1Z{rng.randint(10**9, 10**10 - 1)}",
                          "delivered" if done else "in_transit", s_at, delivered, _money(weight), _money(8 + weight * 1.4), s_at)
            for r in order_items:
                qty = r[4] if waves == 1 else (r[4] // 2 if wave == 0 else r[4] - r[4] // 2)
                shipment_items.add(sid, r[0], qty)
        hid += 1
        history.add(hid, o["id"], "pending", "shipped", o["rep"], shipped_at, "picked and packed")
        if o["status"] in ("completed", "returned") and delivered:
            hid += 1
            history.add(hid, o["id"], "shipped", "completed", None, delivered, "carrier confirmed delivery")
        if o["status"] == "returned" and delivered:
            r = rng.choice(order_items)
            rid += 1
            returned_at = clamp(delivered + timedelta(days=rng.randint(2, 20)))
            qty = min(r[4], rng.choice([1, 1, 2]))
            reason = rng.choice(["defective", "wrong item", "damaged", "no longer needed"])
            returns.add(rid, r[0], reason, qty, _money(qty * r[5]), "approved", returned_at, returned_at)
            fid += 1
            refunds.add(fid, rid, payment_id, _money(qty * r[5]), "card", clamp(returned_at + timedelta(days=2)), returned_at)
            hid += 1
            history.add(hid, o["id"], "completed", "returned", None, returned_at, reason)
        elif o["status"] == "completed" and delivered and rng.random() < 0.012:
            r = rng.choice(order_items)
            rid += 1
            returned_at = clamp(delivered + timedelta(days=rng.randint(3, 25)))
            returns.add(rid, r[0], rng.choice(["defective", "damaged"]), 1, _money(r[5]), "rejected", returned_at, returned_at)
    del end_of_today

    for c in coupon_rows:
        pass
    coupons.rows = [(r[0], r[1], r[2], r[3], next(c["uses"] for c in coupon_rows if c["id"] == r[0]), r[5], r[6]) for r in coupons.rows]

    # ── voice of the customer ──
    reviews = t["reviews"] = Table(["id", "product_id", "customer_id", "order_id", "rating", "title", "body", "is_verified", "is_hidden", "created_at"])
    delivered_orders = [o for o in order_rows if o["status"] == "completed"]
    for i in range(1, 2601):
        o = rng.choice(delivered_orders)
        r = rng.choice(items_by_order[o["id"]])
        p = product_rows[r[2] - 1]
        base = 4.3 if p["pop"] >= 5 else 3.9
        rating = max(1, min(5, round(rng.gauss(base, 0.9))))
        anonymous = rng.random() < 0.12
        created_at = clamp(o["placed"] + timedelta(days=rng.randint(5, 40)))
        reviews.add(i, p["id"], None if anonymous else o["customer"]["id"], None if anonymous else o["id"], rating,
                    rng.choice(REVIEW_TITLES[rating]), REVIEW_BODIES[rating], not anonymous, rng.random() < 0.025, created_at)

    tickets = t["support_tickets"] = Table(["id", "customer_id", "order_id", "employee_id", "subject", "status", "priority", "opened_at", "closed_at", "created_at"])
    support_staff = [e["id"] for e in emp_rows if e["team"] == "Inside Sales" and e["active"]]
    for i in range(1, 1401):
        o = rng.choice(order_rows)
        opened = clamp(o["placed"] + timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 9)))
        age = (now - opened).days
        if age < 3:
            status = rng.choice(["open", "open", "pending"])
        elif age < 14:
            status = rng.choice(["open", "pending", "resolved", "resolved"])
        else:
            status = rng.choice(["resolved", "closed", "closed"])
        closed = clamp(opened + timedelta(hours=rng.randint(3, 96))) if status in ("resolved", "closed") else None
        tickets.add(i, o["customer"]["id"], o["id"] if rng.random() < 0.72 else None,
                    rng.choice(support_staff) if rng.random() < 0.85 else None, rng.choice(TICKET_SUBJECTS), status,
                    rng.choices(["low", "normal", "high", "urgent"], [0.25, 0.5, 0.2, 0.05])[0], opened, closed, opened)

    wishlists = t["wishlists"] = Table(["id", "customer_id", "product_id", "added_at", "note"])
    for i in range(1, 1801):
        c = rng.choice(cust_rows)
        p = rng.choices(product_rows, [p["pop"] for p in product_rows])[0]
        added = clamp(ts(c["signed"]) + timedelta(days=rng.randint(0, max(1, (today - c["signed"]).days))))
        wishlists.add(i, c["id"], p["id"], added, rng.choice([None, None, None, "for the Q4 refresh", "compare with current model", "gift idea"]))

    price_history = t["product_price_history"] = Table(["id", "product_id", "old_price", "new_price", "changed_at", "reason"])
    for i, p in enumerate([p for p in product_rows if p["launched"] < start][:16], 1):
        price_history.add(i, p["id"], _money(p["price"] * rng.choice([1.05, 1.08, 1.1, 0.95])), p["price"], ts(start + timedelta(days=rng.randint(30, 600))),
                          rng.choice(["annual review", "supplier cost change", "competitive match"]))

    inventory = t["inventory"] = Table(["id", "product_id", "warehouse_id", "quantity", "reorder_level", "updated_at"])
    inv = 0
    for p in product_rows:
        for w in range(1, len(wh_specs) + 1):
            inv += 1
            level = max(10, int(p["pop"] * (6 if w <= 3 else 2)))
            qty = 0 if p["discontinued"] else max(0, int(rng.gauss(level * 3, level * 1.4)))
            inventory.add(inv, p["id"], w, qty, level, now - timedelta(hours=rng.randint(1, 20)))

    # ── the rollup: trailing 90 days only, like the fixture's ──
    rollup = t["sales_daily_rollup"] = Table(["id", "day", "region_id", "orders_count", "gross_revenue", "units_sold", "refunds_total", "created_at"])
    agg: dict[tuple[date, int], list[float]] = {}
    for o in order_rows:
        if o["date"] < today - timedelta(days=90) or o["date"] >= today:
            continue
        key = (o["date"], o["customer"]["region"])
        bucket = agg.setdefault(key, [0, 0.0, 0])
        bucket[0] += 1
        for r in items_by_order[o["id"]]:
            bucket[1] += r[8]
            bucket[2] += r[4]
    for i, ((day, region), (count, revenue, units)) in enumerate(sorted(agg.items()), 1):
        rollup.add(i, day, region, count, _money(revenue), units, 0.0, ts(day + timedelta(days=1), 3))

    order = [
        "countries", "regions", "categories", "subcategories", "brands", "products", "product_variants",
        "suppliers", "warehouses", "teams", "employees", "employee_teams", "loyalty_tiers", "currencies",
        "price_lists", "price_list_items", "tax_rates", "promotions", "coupons", "tags", "carriers",
        "payment_methods", "product_suppliers", "product_tags", "customers", "customer_addresses", "orders",
        "order_items", "order_promotions", "payments", "shipments", "shipment_items", "returns", "refunds",
        "reviews", "support_tickets", "wishlists", "order_status_history", "product_price_history",
        "inventory", "sales_daily_rollup",
    ]
    assert set(order) == set(t), set(order) ^ set(t)
    return {name: t[name] for name in order}


if __name__ == "__main__":  # a quick look at the shape, without a database
    import sys
    from collections import defaultdict

    today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    tables = generate(today)
    for name, table in tables.items():
        print(f"{name:24} {len(table.rows):>7}")
    monthly: dict[str, float] = defaultdict(float)
    cols = tables["orders"].columns
    for row in tables["orders"].rows:
        rec = dict(zip(cols, row))
        if rec["status"] in ("completed", "shipped"):
            monthly[rec["order_date"].strftime("%Y-%m")] += rec["total_amount"]
    for month, total in sorted(monthly.items()):
        print(month, f"{total:>14,.2f}", "#" * int(total / 20000))
    print(len(schema_statements()), "tables;", len(comment_statements()), "comments")
    _ = math
