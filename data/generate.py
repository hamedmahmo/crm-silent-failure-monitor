"""Generate a seeded synthetic dataset for Northwind Analytics, a fictional B2B SaaS company.

Tables: owners, companies, contacts, deals, usage_events, support_tickets.
With --defects, the 23 data-quality defects listed in data/DEFECTS.md are planted and a
defects_manifest.json file records which records each defect touched.

Everything is fictional: names come from small word pools and every email address and company
domain uses the reserved .example top-level domain. Only the standard library is used.

Usage:
    uv run python data/generate.py [--seed 42] [--scale 1.0] [--out data/output] [--defects]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- constants

AS_OF = date(2026, 6, 30)
AS_OF_END = datetime(2026, 6, 30, 23, 59, 59)
DATA_START = datetime(2024, 1, 1)
LAST_COMPANY_CREATED = datetime(2026, 3, 31)
SOURCE_REQUIRED_FROM = date(2026, 1, 1)
STALE_STAGE_DAYS = 60
EVENT_WINDOW_DAYS = 60
EVENT_WINDOW_START = AS_OF - timedelta(days=EVENT_WINDOW_DAYS - 1)
MIN_DEAL_AMOUNT = 2_000
MAX_DEAL_AMOUNT = 150_000
CHURN_RATE = 0.12
BASE_COMPANIES = 200
MIN_SCALE = 0.5
MAX_SCALE = 4.0
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

OPEN_STAGES = [
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
]
CLOSED_STAGES = ["closedwon", "closedlost"]
LIFECYCLE_STAGES = [
    "subscriber",
    "lead",
    "marketingqualifiedlead",
    "salesqualifiedlead",
    "opportunity",
    "customer",
]
SOURCES = [
    "ORGANIC_SEARCH",
    "PAID_SEARCH",
    "EMAIL_MARKETING",
    "SOCIAL_MEDIA",
    "REFERRALS",
    "DIRECT_TRAFFIC",
    "OFFLINE",
]
INDUSTRIES = [
    "Software",
    "Logistics",
    "Retail",
    "Manufacturing",
    "Financial Services",
    "Education",
    "Media",
    "Energy",
    "Construction",
    "Hospitality",
    "Agriculture",
    "Telecommunications",
]
COUNTRIES = ["US", "GB", "DE", "FR", "NL", "CA", "AU", "SE", "IE", "ES"]
EMPLOYEE_BANDS = ["1-10", "11-50", "51-200", "201-1000", "1000+"]
PLAN_TIERS = ["starter", "growth", "enterprise"]
JOB_TITLES = [
    "Head of Operations",
    "Data Analyst",
    "VP Sales",
    "RevOps Manager",
    "Finance Director",
    "Customer Success Lead",
    "IT Manager",
    "Marketing Manager",
    "CTO",
    "Operations Analyst",
]
EVENT_TYPES = ["login", "report_run", "dashboard_view", "export", "api_call"]
FEATURES = ["dashboards", "reports", "alerts", "integrations", "exports"]
TICKET_PRIORITIES = ["low", "medium", "high", "urgent"]
TICKET_CATEGORIES = ["billing", "bug", "how_to", "integration", "access"]
TICKET_OPEN_STATUSES = ["new", "waiting_on_contact", "waiting_on_us"]

FIRST_NAMES = [
    "Avery", "Blake", "Casey", "Dana", "Elliot", "Finley", "Gray", "Harper", "Indy", "Jordan",
    "Kai", "Logan", "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Riley", "Sage", "Taylor",
    "Rowan", "Ellis", "Wren", "Remy", "Shay", "Arden",
]  # fmt: skip
LAST_NAMES = [
    "Ashvale", "Birchmoor", "Coldbrook", "Dunmere", "Elmsworth", "Fernhollow", "Greystone",
    "Hartwell", "Juniper", "Kestrel", "Larkspur", "Millbrook", "Northcott", "Oakhurst",
    "Pinecrest", "Redfern", "Stonebridge", "Thornbury", "Underhill", "Westbrook", "Yarrow",
]  # fmt: skip
COMPANY_ADJECTIVES = [
    "Amber", "Arctic", "Blue", "Bright", "Cedar", "Clear", "Copper", "Crimson", "Delta", "Ember",
    "Granite", "Harbor", "Iron", "Juniper", "Keystone", "Lunar", "Maple", "Nimbus", "North",
    "Opal", "Pine", "Quartz", "Rapid", "Silver", "Summit", "Tidal", "Upland", "Vector", "Willow",
    "Zephyr",
]  # fmt: skip
COMPANY_NOUNS = [
    "Anchor", "Beacon", "Bridge", "Canyon", "Circuit", "Field", "Forge", "Gate", "Grove", "Hive",
    "Isle", "Junction", "Kite", "Lane", "Ledger", "Mill", "Orbit", "Peak", "Point", "Quay",
    "Ridge", "River", "Signal", "Spark", "Stone", "Trail", "Valley", "Wave", "Works", "Yard",
]  # fmt: skip
COMPANY_SUFFIXES = ["Labs", "Logistics", "Systems", "Supply", "Studio", "Partners", "Group"]

OWNERS = [
    ("OWN-01", "Avery Hartwell", True),
    ("OWN-02", "Blake Northcott", True),
    ("OWN-03", "Casey Redfern", True),
    ("OWN-04", "Dana Oakhurst", True),
    ("OWN-05", "Elliot Greystone", True),
    ("OWN-06", "Finley Westbrook", True),
    ("OWN-07", "Harper Millbrook", True),
    ("OWN-08", "Jordan Pinecrest", True),
    ("OWN-09", "Morgan Thornbury", True),
    ("OWN-10", "Quinn Larkspur", True),
    ("OWN-11", "Riley Dunmere", False),
    ("OWN-12", "Sage Coldbrook", False),
]
ACTIVE_OWNER_IDS = [owner_id for owner_id, _, active in OWNERS if active]
INACTIVE_OWNER_IDS = [owner_id for owner_id, _, active in OWNERS if not active]
UNKNOWN_OWNER_ID = "OWN-99"

COLUMNS = {
    "owners": ["owner_id", "name", "active"],
    "companies": [
        "company_id", "name", "domain", "industry", "country", "employee_band", "plan_tier",
        "owner_id", "created_at", "churned_at",
    ],
    "contacts": [
        "contact_id", "company_id", "first_name", "last_name", "email", "job_title",
        "lifecycle_stage", "owner_id", "source", "created_at",
    ],
    "deals": [
        "deal_id", "company_id", "primary_contact_id", "owner_id", "stage", "amount", "currency",
        "created_at", "stage_entered_at", "close_date", "closed_at",
    ],
    "usage_events": [
        "event_id", "company_id", "contact_id", "event_type", "feature", "quantity", "event_ts",
    ],
    "support_tickets": [
        "ticket_id", "company_id", "contact_id", "priority", "status", "category", "opened_at",
        "first_response_at", "closed_at",
    ],
}  # fmt: skip
ID_FORMATS = {
    "companies": ("company_id", "COMP-{:04d}"),
    "contacts": ("contact_id", "CONT-{:05d}"),
    "deals": ("deal_id", "DEAL-{:05d}"),
    "usage_events": ("event_id", "EVT-{:06d}"),
    "support_tickets": ("ticket_id", "TCK-{:05d}"),
}

Row = dict[str, str]
Tables = dict[str, list[Row]]


# ---------------------------------------------------------------- small helpers


def fmt_ts(value: datetime) -> str:
    return value.strftime(TS_FORMAT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT)


def parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def end_of_day(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, 23, 59, 59)


def random_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(seconds, 0)))


def random_time_on(rng: random.Random, day: date) -> datetime:
    return datetime(day.year, day.month, day.day) + timedelta(seconds=rng.randint(0, 86_399))


def next_id(rows: list[Row], table: str) -> Callable[[], str]:
    key, pattern = ID_FORMATS[table]
    counter = max((int(row[key].split("-")[1]) for row in rows), default=0)

    def make() -> str:
        nonlocal counter
        counter += 1
        return pattern.format(counter)

    return make


# ---------------------------------------------------------------- clean data


def company_end(company: Row) -> datetime:
    """Last moment the company is active: its churn day, or AS_OF."""
    if company["churned_at"]:
        return end_of_day(parse_day(company["churned_at"]))
    return AS_OF_END


def contacts_created_by(contacts: list[Row], moment: datetime) -> list[Row]:
    return [c for c in contacts if parse_ts(c["created_at"]) <= moment]


def make_companies(rng: random.Random, n: int) -> list[Row]:
    pairs = [(a, b) for a in COMPANY_ADJECTIVES for b in COMPANY_NOUNS]
    companies = []
    for i, (adjective, noun) in enumerate(rng.sample(pairs, n), start=1):
        companies.append(
            {
                "company_id": f"COMP-{i:04d}",
                "name": f"{adjective} {noun} {rng.choice(COMPANY_SUFFIXES)}",
                "domain": f"{adjective}{noun}.example".lower(),
                "industry": rng.choice(INDUSTRIES),
                "country": rng.choice(COUNTRIES),
                "employee_band": rng.choice(EMPLOYEE_BANDS),
                "plan_tier": rng.choice(PLAN_TIERS),
                "owner_id": rng.choice(ACTIVE_OWNER_IDS),
                "created_at": fmt_ts(random_between(rng, DATA_START, LAST_COMPANY_CREATED)),
                "churned_at": "",
            }
        )
    latest_created = AS_OF_END - timedelta(days=100)
    can_churn = [c for c in companies if parse_ts(c["created_at"]) <= latest_created]
    for company in rng.sample(can_churn, round(n * CHURN_RATE)):
        earliest = parse_ts(company["created_at"]) + timedelta(days=90)
        latest = AS_OF_END - timedelta(days=10)
        company["churned_at"] = random_between(rng, earliest, latest).date().isoformat()
    return companies


def make_contacts(rng: random.Random, companies: list[Row]) -> list[Row]:
    contacts: list[Row] = []
    for company in companies:
        created = parse_ts(company["created_at"])
        used_emails: set[str] = set()
        for i in range(rng.randint(3, 7)):
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            local, n = f"{first}.{last}".lower(), 1
            while f"{local}@{company['domain']}" in used_emails:
                n += 1
                local = f"{first}.{last}{n}".lower()
            email = f"{local}@{company['domain']}"
            used_emails.add(email)
            # The first contact is created with the company, so every company has a contact
            # that predates its deals, tickets and usage events.
            contact_created = (
                created if i == 0 else random_between(rng, created, company_end(company))
            )
            if contact_created.date() >= SOURCE_REQUIRED_FROM or rng.random() >= 0.3:
                source = rng.choice(SOURCES)
            else:
                source = ""  # allowed: attribution was not required before the cutoff
            contacts.append(
                {
                    "contact_id": f"CONT-{len(contacts) + 1:05d}",
                    "company_id": company["company_id"],
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "job_title": rng.choice(JOB_TITLES),
                    "lifecycle_stage": rng.choice(LIFECYCLE_STAGES),
                    "owner_id": (
                        company["owner_id"] if rng.random() < 0.7 else rng.choice(ACTIVE_OWNER_IDS)
                    ),
                    "source": source,
                    "created_at": fmt_ts(contact_created),
                }
            )
    return contacts


def random_amount(rng: random.Random) -> str:
    return str(rng.randrange(MIN_DEAL_AMOUNT, MAX_DEAL_AMOUNT + 1, 500))


def make_deals(
    rng: random.Random, companies: list[Row], by_company: dict[str, list[Row]]
) -> list[Row]:
    deals: list[Row] = []
    stale_cutoff = datetime.combine(
        AS_OF - timedelta(days=STALE_STAGE_DAYS - 1), datetime.min.time()
    )
    for company in companies:
        start, end = parse_ts(company["created_at"]), company_end(company)
        for _ in range(rng.randint(1, 5)):
            created = random_between(rng, start, end - timedelta(days=1))
            contact = rng.choice(contacts_created_by(by_company[company["company_id"]], created))
            closed = bool(company["churned_at"]) or rng.random() < 0.6
            if created > AS_OF_END - timedelta(days=7):
                closed = False
            deal = {
                "deal_id": f"DEAL-{len(deals) + 1:05d}",
                "company_id": company["company_id"],
                "primary_contact_id": contact["contact_id"],
                "owner_id": company["owner_id"],
                "currency": "USD",
                "created_at": fmt_ts(created),
                "closed_at": "",
            }
            if closed:
                low = created + timedelta(days=1)
                high = min(end, created + timedelta(days=180))
                closed_at = random_between(rng, low if low <= high else created, high)
                stage = "closedwon" if rng.random() < 0.55 else "closedlost"
                has_amount = stage == "closedwon" or rng.random() < 0.8
                deal |= {
                    "stage": stage,
                    "stage_entered_at": fmt_ts(closed_at),
                    "close_date": closed_at.date().isoformat(),
                    "closed_at": fmt_ts(closed_at),
                }
            else:
                stage = rng.choice(OPEN_STAGES)
                # Early-stage open deals may have no amount yet; that is allowed.
                has_amount = (
                    stage in ("contractsent", "decisionmakerboughtin") or rng.random() < 0.6
                )
                entered = random_between(rng, max(created, stale_cutoff), AS_OF_END)
                close_date = AS_OF + timedelta(days=rng.randint(1, 120))
                deal |= {
                    "stage": stage,
                    "stage_entered_at": fmt_ts(entered),
                    "close_date": close_date.isoformat(),
                }
            deal["amount"] = random_amount(rng) if has_amount else ""
            deals.append({column: deal[column] for column in COLUMNS["deals"]})
    return deals


def make_usage_events(
    rng: random.Random, companies: list[Row], by_company: dict[str, list[Row]]
) -> list[Row]:
    """At least one event per company per day inside the event window, so gaps mean data loss."""
    events: list[Row] = []
    for company in companies:
        first_day = max(
            EVENT_WINDOW_START, parse_ts(company["created_at"]).date() + timedelta(days=1)
        )
        last_day = company_end(company).date()
        contacts = by_company[company["company_id"]]
        for offset in range((last_day - first_day).days + 1):
            current = first_day + timedelta(days=offset)
            for _ in range(rng.randint(1, 3)):
                moment = random_time_on(rng, current)
                events.append(
                    {
                        "event_id": f"EVT-{len(events) + 1:06d}",
                        "company_id": company["company_id"],
                        "contact_id": rng.choice(contacts_created_by(contacts, moment))[
                            "contact_id"
                        ],
                        "event_type": rng.choice(EVENT_TYPES),
                        "feature": rng.choice(FEATURES),
                        "quantity": str(rng.randint(1, 20)),
                        "event_ts": fmt_ts(moment),
                    }
                )
    return events


def make_tickets(
    rng: random.Random, companies: list[Row], by_company: dict[str, list[Row]]
) -> list[Row]:
    tickets: list[Row] = []
    for company in companies:
        start, end = parse_ts(company["created_at"]), company_end(company)
        for _ in range(rng.randint(0, 15)):
            opened = random_between(rng, start, end)
            contact = rng.choice(contacts_created_by(by_company[company["company_id"]], opened))
            first_response = opened + timedelta(minutes=rng.randint(10, 48 * 60))
            closed_at = ""
            if first_response > AS_OF_END:
                first_response_at, status = "", "new"
            else:
                first_response_at = fmt_ts(first_response)
                recent = opened > AS_OF_END - timedelta(days=14)
                if recent and rng.random() < 0.6 or not recent and rng.random() < 0.15:
                    status = rng.choice(TICKET_OPEN_STATUSES)
                else:
                    status = "closed"
                    closed = first_response + timedelta(minutes=rng.randint(60, 14 * 24 * 60))
                    closed_at = fmt_ts(min(closed, AS_OF_END))
            tickets.append(
                {
                    "ticket_id": f"TCK-{len(tickets) + 1:05d}",
                    "company_id": company["company_id"],
                    "contact_id": contact["contact_id"],
                    "priority": rng.choice(TICKET_PRIORITIES),
                    "status": status,
                    "category": rng.choice(TICKET_CATEGORIES),
                    "opened_at": fmt_ts(opened),
                    "first_response_at": first_response_at,
                    "closed_at": closed_at,
                }
            )
    return tickets


def generate_clean(seed: int, scale: float) -> Tables:
    rng = random.Random(seed)
    companies = make_companies(rng, round(BASE_COMPANIES * scale))
    contacts = make_contacts(rng, companies)
    by_company: dict[str, list[Row]] = {c["company_id"]: [] for c in companies}
    for contact in contacts:
        by_company[contact["company_id"]].append(contact)
    owners = [
        {"owner_id": owner_id, "name": name, "active": "true" if active else "false"}
        for owner_id, name, active in OWNERS
    ]
    return {
        "owners": owners,
        "companies": companies,
        "contacts": contacts,
        "deals": make_deals(rng, companies, by_company),
        "usage_events": make_usage_events(rng, companies, by_company),
        "support_tickets": make_tickets(rng, companies, by_company),
    }


# ---------------------------------------------------------------- defect planting


@dataclass
class Plant:
    """State shared by the defect functions while planting."""

    tables: Tables
    rng: random.Random
    touched: set[tuple[str, str]]

    def pick(
        self, table: str, k: int, where: Callable[[Row], bool] = lambda row: True
    ) -> list[Row]:
        """Pick k untouched rows matching `where`, and mark them touched."""
        key = ID_FORMATS[table][0]
        pool = [r for r in self.tables[table] if (table, r[key]) not in self.touched and where(r)]
        if len(pool) < k:
            raise ValueError(f"only {len(pool)} eligible {table} rows, need {k}")
        chosen = self.rng.sample(pool, k)
        self.touched.update((table, row[key]) for row in chosen)
        return chosen

    def companies(self) -> dict[str, Row]:
        return {c["company_id"]: c for c in self.tables["companies"]}

    def contacts_of(self, company_id: str) -> list[Row]:
        return [c for c in self.tables["contacts"] if c["company_id"] == company_id]

    def events_untouched(self, company_id: str) -> bool:
        return ("usage_events.company", company_id) not in self.touched

    def new_event(self, make_id: Callable[[], str], company_id: str, moment: datetime) -> Row:
        event = {
            "event_id": make_id(),
            "company_id": company_id,
            "contact_id": self.rng.choice(self.contacts_of(company_id))["contact_id"],
            "event_type": self.rng.choice(EVENT_TYPES),
            "feature": self.rng.choice(FEATURES),
            "quantity": str(self.rng.randint(1, 20)),
            "event_ts": fmt_ts(moment),
        }
        self.tables["usage_events"].append(event)
        self.touched.add(("usage_events.company", company_id))
        return event


PlantResult = dict[str, object]


def ids(rows: list[Row], table: str) -> list[str]:
    key = ID_FORMATS[table][0]
    return [row[key] for row in rows]


def is_open(deal: Row) -> bool:
    return deal["stage"] in OPEN_STAGES


def plant_d01(p: Plant, count: int) -> PlantResult:
    variants = [str.upper, str.title, lambda s: s[:1].upper() + s[1:]]
    make_id = next_id(p.tables["contacts"], "contacts")
    duplicates = []
    for i, original in enumerate(p.pick("contacts", count)):
        duplicate = dict(original)
        created = min(
            parse_ts(original["created_at"]) + timedelta(days=p.rng.randint(1, 30)), AS_OF_END
        )
        duplicate["contact_id"] = make_id()
        duplicate["email"] = variants[i % len(variants)](original["email"])
        duplicate["created_at"] = fmt_ts(created)
        if created.date() >= SOURCE_REQUIRED_FROM and not duplicate["source"]:
            duplicate["source"] = "OFFLINE"  # keep D22 separate
        duplicates.append(duplicate)
    p.tables["contacts"].extend(duplicates)
    p.touched.update(("contacts", d["contact_id"]) for d in duplicates)
    return {"record_ids": ids(duplicates, "contacts")}


def plant_d02(p: Plant, count: int) -> PlantResult:
    # Contacts with tickets are skipped so the change doesn't also trip D15.
    with_tickets = {t["contact_id"] for t in p.tables["support_tickets"]}
    chosen = p.pick("contacts", count, lambda c: c["contact_id"] not in with_tickets)
    for i, contact in enumerate(chosen):
        contact["company_id"] = f"COMP-{9001 + i}"
    return {"record_ids": ids(chosen, "contacts")}


def plant_d03(p: Plant, count: int) -> PlantResult:
    variants = [
        lambda e: e.replace("@", ""),
        lambda e: e.replace("@", "@@"),
        lambda e: e + " ",
    ]
    chosen = p.pick("contacts", count)
    for i, contact in enumerate(chosen):
        contact["email"] = variants[i % len(variants)](contact["email"])
    return {"record_ids": ids(chosen, "contacts")}


def plant_d04(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("companies", count)
    for i, company in enumerate(chosen):
        if i % 3 in (0, 2):
            company["industry"] = ""
        if i % 3 in (1, 2):
            company["country"] = ""
    return {"record_ids": ids(chosen, "companies")}


EVENTS_PER_CHURNED_COMPANY = 4


def plant_d05(p: Plant, count: int) -> PlantResult:
    latest_churn = AS_OF - timedelta(days=5)

    def eligible(c: Row) -> bool:
        return (
            bool(c["churned_at"])
            and parse_day(c["churned_at"]) <= latest_churn
            and p.events_untouched(c["company_id"])
            and bool(p.contacts_of(c["company_id"]))
        )

    chosen = p.pick("companies", count, eligible)
    make_id = next_id(p.tables["usage_events"], "usage_events")
    event_ids = []
    for company in chosen:
        churned = parse_day(company["churned_at"])
        for _ in range(EVENTS_PER_CHURNED_COMPANY):
            moment = random_time_on(
                p.rng, churned + timedelta(days=p.rng.randint(1, (AS_OF - churned).days))
            )
            event_ids.append(p.new_event(make_id, company["company_id"], moment)["event_id"])
    return {"record_ids": ids(chosen, "companies"), "event_ids": event_ids}


def plant_d06(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("deals", count, lambda d: d["stage"] == "closedwon")
    for i, deal in enumerate(chosen):
        deal["amount"] = "" if i % 2 == 0 else "0"
    return {"record_ids": ids(chosen, "deals")}


def plant_d07(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("deals", count, lambda d: bool(d["amount"]))
    for deal in chosen:
        deal["amount"] = str(int(deal["amount"]) * 100)
    return {"record_ids": ids(chosen, "deals")}


def plant_d08(p: Plant, count: int) -> PlantResult:
    # Closed deals only: an open deal with a past close date would also trip D20.
    chosen = p.pick("deals", count, lambda d: d["stage"] in CLOSED_STAGES)
    for deal in chosen:
        created = parse_ts(deal["created_at"]).date()
        deal["close_date"] = (created - timedelta(days=p.rng.randint(5, 60))).isoformat()
    return {"record_ids": ids(chosen, "deals")}


def plant_d09(p: Plant, count: int) -> PlantResult:
    variants = ["Closed Won", "won", "closedwon "]
    chosen = p.pick("deals", count, lambda d: d["stage"] == "closedwon")
    for i, deal in enumerate(chosen):
        deal["stage"] = variants[i % len(variants)]
    return {"record_ids": ids(chosen, "deals")}


def plant_d10(p: Plant, count: int) -> PlantResult:
    variants = [*INACTIVE_OWNER_IDS, UNKNOWN_OWNER_ID]
    chosen = p.pick("deals", count)
    for i, deal in enumerate(chosen):
        deal["owner_id"] = variants[i % len(variants)]
    return {"record_ids": ids(chosen, "deals")}


def plant_d11(p: Plant, count: int) -> PlantResult:
    companies = p.companies()
    chosen = p.pick(
        "usage_events",
        count,
        lambda e: (
            not companies[e["company_id"]]["churned_at"] and p.events_untouched(e["company_id"])
        ),
    )
    p.tables["usage_events"].extend(dict(event) for event in chosen)
    p.touched.update(("usage_events.company", e["company_id"]) for e in chosen)
    return {"record_ids": ids(chosen, "usage_events")}


def plant_d12(p: Plant, count: int) -> PlantResult:
    chosen = p.pick(
        "companies",
        count,
        lambda c: (
            not c["churned_at"]
            and p.events_untouched(c["company_id"])
            and bool(p.contacts_of(c["company_id"]))
        ),
    )
    make_id = next_id(p.tables["usage_events"], "usage_events")
    event_ids = []
    for company in chosen:
        moment = random_time_on(p.rng, AS_OF + timedelta(days=p.rng.randint(1, 30)))
        event_ids.append(p.new_event(make_id, company["company_id"], moment)["event_id"])
    return {"record_ids": event_ids}


GAP_DAYS = 3


def plant_d13(p: Plant, count: int) -> PlantResult:
    chosen = p.pick(
        "companies",
        count,
        lambda c: (
            not c["churned_at"]
            and parse_ts(c["created_at"]).date() < EVENT_WINDOW_START
            and p.events_untouched(c["company_id"])
        ),
    )
    start = EVENT_WINDOW_START + timedelta(days=p.rng.randint(3, EVENT_WINDOW_DAYS - GAP_DAYS - 3))
    end = start + timedelta(days=GAP_DAYS - 1)
    dropped = {c["company_id"] for c in chosen}
    p.tables["usage_events"] = [
        e
        for e in p.tables["usage_events"]
        if not (e["company_id"] in dropped and start <= parse_day(e["event_ts"]) <= end)
    ]
    p.touched.update(("usage_events.company", company_id) for company_id in dropped)
    return {
        "record_ids": ids(chosen, "companies"),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


def plant_d14(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("support_tickets", count, lambda t: t["status"] == "closed")
    for ticket in chosen:
        opened = parse_ts(ticket["opened_at"])
        ticket["closed_at"] = fmt_ts(opened - timedelta(minutes=p.rng.randint(60, 10 * 24 * 60)))
    return {"record_ids": ids(chosen, "support_tickets")}


def plant_d15(p: Plant, count: int) -> PlantResult:
    companies = p.companies()
    chosen = p.pick("support_tickets", count)
    for ticket in chosen:
        [other] = p.pick(
            "contacts",
            1,
            lambda c, t=ticket: c["company_id"] != t["company_id"] and c["company_id"] in companies,
        )
        ticket["contact_id"] = other["contact_id"]
    return {"record_ids": ids(chosen, "support_tickets")}


def plant_d16(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("contacts", count)
    for contact in chosen:
        contact["lifecycle_stage"] = ""
    return {"record_ids": ids(chosen, "contacts")}


def plant_d17(p: Plant, count: int) -> PlantResult:
    contacts = p.pick("contacts", count // 2)
    deals = p.pick("deals", count - count // 2)
    for row in [*contacts, *deals]:
        row["owner_id"] = ""
    return {"record_ids": ids(contacts, "contacts") + ids(deals, "deals")}


def plant_d18(p: Plant, count: int) -> PlantResult:
    make_id = next_id(p.tables["companies"], "companies")
    pairs, duplicates = [], []
    for original in p.pick("companies", count, lambda c: not c["churned_at"]):
        created = random_between(p.rng, AS_OF_END - timedelta(days=20), AS_OF_END)
        duplicate = original | {"company_id": make_id(), "created_at": fmt_ts(created)}
        duplicates.append(duplicate)
        pairs.append([original["company_id"], duplicate["company_id"]])
    p.tables["companies"].extend(duplicates)
    p.touched.update(("companies", d["company_id"]) for d in duplicates)
    return {"record_ids": ids(duplicates, "companies"), "pairs": pairs}


def plant_d19(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("deals", count)
    for deal in chosen:
        deal["company_id"] = ""
    return {"record_ids": ids(chosen, "deals")}


def plant_d20(p: Plant, count: int) -> PlantResult:
    latest_created = AS_OF - timedelta(days=2)
    chosen = p.pick(
        "deals", count, lambda d: is_open(d) and parse_ts(d["created_at"]).date() <= latest_created
    )
    for deal in chosen:
        created = parse_ts(deal["created_at"]).date()
        days_back = p.rng.randint(1, (AS_OF - created).days)
        deal["close_date"] = (AS_OF - timedelta(days=days_back)).isoformat()
    return {"record_ids": ids(chosen, "deals")}


def plant_d21(p: Plant, count: int) -> PlantResult:
    latest_entry = end_of_day(AS_OF - timedelta(days=STALE_STAGE_DAYS + 1))
    earliest_entry = AS_OF_END - timedelta(days=150)
    chosen = p.pick(
        "deals", count, lambda d: is_open(d) and parse_ts(d["created_at"]) <= latest_entry
    )
    for deal in chosen:
        start = max(parse_ts(deal["created_at"]), earliest_entry)
        deal["stage_entered_at"] = fmt_ts(random_between(p.rng, start, latest_entry))
    return {"record_ids": ids(chosen, "deals")}


def plant_d22(p: Plant, count: int) -> PlantResult:
    chosen = p.pick(
        "contacts",
        count,
        lambda c: parse_ts(c["created_at"]).date() >= SOURCE_REQUIRED_FROM and bool(c["source"]),
    )
    for contact in chosen:
        contact["source"] = ""
    return {"record_ids": ids(chosen, "contacts")}


def plant_d23(p: Plant, count: int) -> PlantResult:
    chosen = p.pick("deals", count, lambda d: d["stage"] == "contractsent")
    for deal in chosen:
        deal["amount"] = ""
    return {"record_ids": ids(chosen, "deals")}


@dataclass(frozen=True)
class Defect:
    id: str
    title: str
    record_types: tuple[str, ...]
    count: int
    unit: str
    hubspot_plantable: bool
    hubspot_note: str
    plant: Callable[[Plant, int], PlantResult]


NOT_IN_HUBSPOT = "usage events are not HubSpot records"
CONFIRM = "to confirm in a HubSpot sandbox"

DEFECTS: list[Defect] = [
    Defect("D01", "Duplicate contact: same email differing only by letter case", ("contacts",),
           8, "contacts", False, "HubSpot rejects duplicate emails", plant_d01),
    Defect("D02", "Orphan contact: company_id not in companies", ("contacts",),
           5, "contacts", False, "HubSpot rejects unknown company IDs", plant_d02),
    Defect("D03", "Malformed email", ("contacts",),
           6, "contacts", False, "HubSpot rejects malformed emails", plant_d03),
    Defect("D04", "Required company fields blank (industry, country)", ("companies",),
           6, "companies", True, "", plant_d04),
    Defect("D05", "Churned company still emits usage events after churned_at",
           ("companies", "usage_events"), 3, "companies", False, NOT_IN_HUBSPOT, plant_d05),
    Defect("D06", "closedwon deal with null or zero amount", ("deals",),
           5, "deals", True, "", plant_d06),
    Defect("D07", "Deal amount 100x too large (cents in a dollars field)", ("deals",),
           4, "deals", True, "", plant_d07),
    Defect("D08", "Closed deal with close_date before created_at", ("deals",),
           5, "deals", True,
           "close date set before the real create time; HubSpot accepts it (probe P1, 2026-11-12)",
           plant_d08),
    Defect("D09", "Deal stage outside the picklist", ("deals",),
           6, "deals", False, "HubSpot rejects invalid stages", plant_d09),
    Defect("D10", "Deal owner is inactive or not in owners", ("deals",),
           5, "deals", False, f"HubSpot validates owner IDs; {CONFIRM}", plant_d10),
    Defect("D11", "Duplicate usage event (same event_id replayed)", ("usage_events",),
           10, "events", False, NOT_IN_HUBSPOT, plant_d11),
    Defect("D12", "Usage event timestamp after AS_OF", ("usage_events",),
           8, "events", False, NOT_IN_HUBSPOT, plant_d12),
    Defect("D13", "Ingestion gap: all events dropped for 3 consecutive days", ("usage_events",),
           2, "companies", False, NOT_IN_HUBSPOT, plant_d13),
    Defect("D14", "Ticket closed_at before opened_at", ("support_tickets",),
           5, "tickets", True, f"closed date set before the real create time; {CONFIRM}",
           plant_d14),
    Defect("D15", "Ticket contact belongs to a different company than the ticket",
           ("support_tickets", "contacts"), 6, "tickets", True, "", plant_d15),
    Defect("D16", "Contact missing lifecycle_stage", ("contacts",),
           10, "contacts", True, "", plant_d16),
    Defect("D17", "Contact or deal with no owner", ("contacts", "deals"),
           8, "records (4 contacts, 4 deals)", True, "", plant_d17),
    Defect("D18", "Two companies share the same domain", ("companies",),
           3, "pairs", True, "", plant_d18),
    Defect("D19", "Deal with no associated company", ("deals",),
           5, "deals", True, "", plant_d19),
    Defect("D20", "Open deal with close_date before AS_OF", ("deals",),
           8, "deals", True, "", plant_d20),
    Defect("D21", "Open deal in the same stage for more than 60 days", ("deals",),
           6, "deals", True, "needs a custom stage_entered_at property", plant_d21),
    Defect("D22", "Contact created on or after the source cutoff with blank source",
           ("contacts",), 10, "contacts", True, "", plant_d22),
    Defect("D23", "Open deal at contractsent with no amount", ("deals",),
           5, "deals", True, "", plant_d23),
]  # fmt: skip


def plant_defects(tables: Tables, seed: int) -> dict:
    """Plant every defect in DEFECTS. Each defect has its own RNG and never reuses a record."""
    touched: set[tuple[str, str]] = set()
    entries = []
    for defect in DEFECTS:
        plant = Plant(tables, random.Random(f"{seed}:{defect.id}"), touched)
        result = defect.plant(plant, defect.count)
        entries.append(
            {
                "id": defect.id,
                "title": defect.title,
                "record_types": list(defect.record_types),
                "count": defect.count,
                "unit": defect.unit,
                "hubspot_plantable": defect.hubspot_plantable,
                "hubspot_note": defect.hubspot_note,
                **result,
            }
        )
    return {"seed": seed, "as_of": AS_OF.isoformat(), "defects": entries}


# ---------------------------------------------------------------- output


def build(seed: int = 42, scale: float = 1.0, defects: bool = False) -> tuple[Tables, dict | None]:
    tables = generate_clean(seed, scale)
    manifest = plant_defects(tables, seed) if defects else None
    return tables, manifest


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_output(out: Path, tables: Tables, manifest: dict | None, seed: int, scale: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS[name], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    metadata = {
        "seed": seed,
        "scale": scale,
        "as_of": AS_OF.isoformat(),
        "source_required_from": SOURCE_REQUIRED_FROM.isoformat(),
        "stale_stage_days": STALE_STAGE_DAYS,
        "event_window_start": EVENT_WINDOW_START.isoformat(),
        "max_deal_amount": MAX_DEAL_AMOUNT,
        "defects": manifest is not None,
        "row_counts": {name: len(rows) for name, rows in tables.items()},
    }
    write_json(out / "metadata.json", metadata)
    manifest_path = out / "defects_manifest.json"
    if manifest is not None:
        write_json(manifest_path, manifest)
    elif manifest_path.exists():
        manifest_path.unlink()  # don't leave a stale manifest next to clean data


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help=f"size multiplier, {MIN_SCALE} to {MAX_SCALE} (1.0 = 200 companies)",
    )
    parser.add_argument("--out", type=Path, default=Path("data/output"), help="output folder")
    parser.add_argument("--defects", action="store_true", help="plant the defects in DEFECTS.md")
    args = parser.parse_args(argv)
    if not MIN_SCALE <= args.scale <= MAX_SCALE:
        parser.error(f"--scale must be between {MIN_SCALE} and {MAX_SCALE}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tables, manifest = build(args.seed, args.scale, args.defects)
    write_output(args.out, tables, manifest, args.seed, args.scale)
    print(f"Wrote Northwind Analytics data to {args.out} (seed {args.seed}, scale {args.scale})")
    for name, rows in tables.items():
        print(f"  {name:<16} {len(rows):>7} rows")
    if manifest:
        print(f"Planted {len(manifest['defects'])} defects (see defects_manifest.json):")
        for entry in manifest["defects"]:
            plantable = "yes" if entry["hubspot_plantable"] else "no"
            print(
                f"  {entry['id']}  {entry['count']:>2} {entry['unit']:<31} "
                f"hubspot={plantable:<3}  {entry['title']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
