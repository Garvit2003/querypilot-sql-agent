"""Builds the sample SQLite database with deterministic fake data.

A fixed random seed means every build produces the identical dataset, so eval results
and demo answers are reproducible.
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from . import config

RNG_SEED = 42

FIRST_NAMES = [
    "Aarav", "Olivia", "Liam", "Emma", "Noah", "Ava", "Ethan", "Sophia", "Mason", "Isabella",
    "Lucas", "Mia", "Arjun", "Priya", "James", "Charlotte", "Benjamin", "Amelia", "Elijah", "Harper",
    "Daniel", "Evelyn", "Henry", "Abigail", "Sebastian", "Emily", "Jack", "Ella", "Owen", "Scarlett",
    "Rohan", "Ananya", "Leo", "Grace", "David", "Chloe", "Samuel", "Zoe", "Carlos", "Maria",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
    "Martinez", "Patel", "Sharma", "Lee", "Walker", "Hall", "Allen", "Young", "King", "Wright",
    "Lopez", "Hill", "Scott", "Green", "Adams", "Baker", "Nelson", "Carter", "Mitchell", "Kapoor",
    "Nguyen", "Chen", "Kim", "Clark", "Lewis", "Robinson",
]
STATES_CITIES = {
    "California": ["Los Angeles", "San Francisco", "San Diego", "San Jose"],
    "Texas": ["Austin", "Houston", "Dallas"],
    "New York": ["New York City", "Buffalo", "Albany"],
    "Florida": ["Miami", "Orlando", "Tampa"],
    "Illinois": ["Chicago", "Springfield"],
    "Washington": ["Seattle", "Spokane"],
    "Massachusetts": ["Boston", "Cambridge"],
    "Georgia": ["Atlanta", "Savannah"],
    "Colorado": ["Denver", "Boulder"],
    "Arizona": ["Phoenix", "Tucson"],
}
STATE_WEIGHTS = [22, 14, 14, 10, 8, 8, 7, 6, 6, 5]
SEGMENTS = ["Consumer", "Corporate", "Small Business"]
SEGMENT_WEIGHTS = [55, 25, 20]

DEPARTMENTS = [
    # name, location, budget
    ("Sales", "New York", 1_800_000),
    ("Engineering", "San Francisco", 3_200_000),
    ("Marketing", "Austin", 950_000),
    ("Finance", "New York", 700_000),
    ("Human Resources", "Chicago", 450_000),
    ("Customer Support", "Phoenix", 600_000),
    ("Operations", "Seattle", 1_100_000),
]
JOB_TITLES = {
    "Sales": [("Sales Representative", 55_000, 85_000), ("Account Executive", 70_000, 110_000)],
    "Engineering": [("Software Engineer", 95_000, 160_000), ("Senior Software Engineer", 140_000, 200_000)],
    "Marketing": [("Marketing Specialist", 55_000, 85_000), ("Content Strategist", 60_000, 90_000)],
    "Finance": [("Financial Analyst", 65_000, 100_000), ("Accountant", 60_000, 90_000)],
    "Human Resources": [("HR Generalist", 50_000, 75_000), ("Recruiter", 55_000, 80_000)],
    "Customer Support": [("Support Specialist", 40_000, 60_000), ("Support Lead", 55_000, 75_000)],
    "Operations": [("Operations Analyst", 60_000, 90_000), ("Logistics Coordinator", 50_000, 70_000)],
}
PRODUCTS = {
    # category: [(name, price)]
    "Electronics": [("Wireless Mouse", 25), ("Mechanical Keyboard", 95), ("27-inch Monitor", 289),
                    ("USB-C Hub", 45), ("Noise-Cancelling Headphones", 249), ("Webcam HD", 79),
                    ("Portable SSD 1TB", 129), ("Laptop Stand", 39)],
    "Furniture": [("Ergonomic Office Chair", 349), ("Standing Desk", 549), ("Bookshelf", 159),
                  ("Desk Lamp", 49), ("Filing Cabinet", 189), ("Monitor Arm", 119)],
    "Office Supplies": [("Notebook Pack", 12), ("Gel Pens (12)", 9), ("Stapler", 15), ("Whiteboard", 89),
                        ("Printer Paper (5 reams)", 35), ("Desk Organizer", 22), ("Sticky Notes (24)", 18)],
    "Software": [("Project Suite Annual", 299), ("Antivirus Annual", 59), ("Design Toolkit Annual", 499),
                 ("Cloud Backup 1TB Annual", 99), ("Office Suite Annual", 149)],
    "Accessories": [("Laptop Sleeve", 29), ("Cable Organizer Kit", 19), ("Phone Stand", 15),
                    ("Mouse Pad XL", 24), ("Blue Light Glasses", 35), ("Travel Backpack", 89)],
}
ORDER_STATUSES = ["Delivered", "Shipped", "Pending", "Cancelled", "Returned"]
STATUS_WEIGHTS = [62, 14, 8, 10, 6]

DATA_START = date(2023, 1, 1)
DATA_END = date(2026, 10, 9)


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build_database(db_path: Path = config.DB_PATH) -> None:
    """Create a fresh database file from schema.sql and fill it with sample rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = random.Random(RNG_SEED)
    conn = sqlite3.connect(db_path)
    conn.executescript(config.SCHEMA_SQL_PATH.read_text())

    # Departments
    for i, (name, loc, budget) in enumerate(DEPARTMENTS, start=1):
        conn.execute("INSERT INTO Departments VALUES (?, ?, ?, ?)", (i, name, loc, budget))
    dept_ids = {name: i for i, (name, _, _) in enumerate(DEPARTMENTS, start=1)}

    # Employees: one head per department first (no manager), then staff reporting to them
    employees = []
    used_emails = set()

    def make_email(first, last, domain):
        base = f"{first}.{last}".lower()
        email, n = f"{base}@{domain}", 1
        while email in used_emails:
            n += 1
            email = f"{base}{n}@{domain}"
        used_emails.add(email)
        return email

    emp_id = 0
    heads = {}
    for name in dept_ids:
        emp_id += 1
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        title = f"Head of {name}"
        employees.append((emp_id, first, last, make_email(first, last, "querypilot-demo.com"), title,
                          dept_ids[name], None, _random_date(rng, date(2018, 1, 1), date(2020, 12, 31)).isoformat(),
                          float(rng.randrange(150_000, 230_000, 1000))))
        heads[name] = emp_id

    staff_per_dept = {"Sales": 14, "Engineering": 16, "Marketing": 7, "Finance": 5,
                      "Human Resources": 4, "Customer Support": 9, "Operations": 6}
    sales_reps = []
    for name, count in staff_per_dept.items():
        for _ in range(count):
            emp_id += 1
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            title, lo, hi = rng.choice(JOB_TITLES[name])
            hire = _random_date(rng, date(2019, 1, 1), date(2026, 9, 30))
            employees.append((emp_id, first, last, make_email(first, last, "querypilot-demo.com"), title,
                              dept_ids[name], heads[name], hire.isoformat(), float(rng.randrange(lo, hi, 500))))
            if name == "Sales":
                sales_reps.append(emp_id)
    sales_reps.append(heads["Sales"])
    conn.executemany("INSERT INTO Employees VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", employees)

    # Customers
    states = list(STATES_CITIES)
    customers = []
    for cid in range(1, 301):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        state = rng.choices(states, weights=STATE_WEIGHTS)[0]
        city = rng.choice(STATES_CITIES[state])
        segment = rng.choices(SEGMENTS, weights=SEGMENT_WEIGHTS)[0]
        signup = _random_date(rng, date(2022, 1, 1), date(2026, 9, 30))
        customers.append((cid, first, last, make_email(first, last, "example.com"), city, state, "USA",
                          segment, signup.isoformat()))
    conn.executemany("INSERT INTO Customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", customers)

    # Products
    products = []
    pid = 0
    for category, items in PRODUCTS.items():
        for pname, price in items:
            pid += 1
            products.append((pid, pname, category, float(price), rng.randint(0, 500),
                             0 if rng.random() < 0.08 else 1))
    conn.executemany("INSERT INTO Products VALUES (?, ?, ?, ?, ?, ?)", products)
    price_of = {p[0]: p[3] for p in products}

    # Orders and order items. An order can't predate the customer's signup.
    order_item_id = 0
    signup_of = {c[0]: date.fromisoformat(c[8]) for c in customers}
    for oid in range(1, 1501):
        cid = rng.randint(1, len(customers))
        start = max(DATA_START, signup_of[cid])
        if start > DATA_END:
            start = DATA_END
        odate = _random_date(rng, start, DATA_END)
        status = rng.choices(ORDER_STATUSES, weights=STATUS_WEIGHTS)[0]
        if odate > DATA_END - timedelta(days=10):
            status = rng.choice(["Pending", "Shipped"])
        rep = rng.choice(sales_reps) if rng.random() < 0.85 else None  # some orders are self-serve
        total = 0.0
        items = []
        for prod in rng.sample(range(1, len(products) + 1), k=rng.randint(1, 4)):
            order_item_id += 1
            qty = rng.choices([1, 2, 3, 4, 5, 10], weights=[45, 25, 12, 8, 6, 4])[0]
            discount = rng.choices([0, 0.05, 0.1, 0.15, 0.2], weights=[60, 15, 12, 8, 5])[0]
            unit = price_of[prod]
            total += qty * unit * (1 - discount)
            items.append((order_item_id, oid, prod, qty, unit, discount))
        conn.execute("INSERT INTO Orders VALUES (?, ?, ?, ?, ?, ?)",
                     (oid, cid, rep, odate.isoformat(), status, round(total, 2)))
        conn.executemany("INSERT INTO OrderItems VALUES (?, ?, ?, ?, ?, ?)", items)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    build_database()
    print(f"Built {config.DB_PATH}")
