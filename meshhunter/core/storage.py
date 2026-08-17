"""CSV load/save for known-node tables (repeaters/chat/room/sensor) and the
unfiltered-by-type all_contacts table."""

import csv

from .constants import ALL_CONTACTS_CSV_FIELDS, NODE_CSV_FIELDS


def load_known_nodes(csv_path):
    rows = {}
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("node_id"):
                    rows[row["node_id"]] = row
    return rows


def save_known_nodes(rows, csv_path):
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=NODE_CSV_FIELDS)
        writer.writeheader()
        for row in sorted(rows.values(), key=lambda r: r.get("name", "")):
            writer.writerow(row)


def load_all_contacts(csv_path):
    rows = {}
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("node_id"):
                    rows[row["node_id"]] = row
    return rows


def save_all_contacts(rows, csv_path):
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_CONTACTS_CSV_FIELDS)
        writer.writeheader()
        for row in sorted(rows.values(), key=lambda r: r.get("name", "")):
            writer.writerow(row)
