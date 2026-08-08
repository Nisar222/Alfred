import csv
from dataclasses import dataclass
from io import StringIO

PHONE_HEADERS = frozenset({"phone", "number", "mobile", "tel", "telephone"})


@dataclass(frozen=True)
class ParsedContact:
    phone: str
    name: str | None = None
    details: str | None = None


def parse_contact_upload(content: str, filename: str = "") -> list[ParsedContact]:
    """Parse uploaded contact lists.

    Phone-only formats (name and details are omitted):
    - Plain text (.txt): one phone number per line
    - Single-column CSV with optional ``phone`` header row

    Legacy CSV with a ``phone`` column may also include optional ``name`` and
    ``details`` columns.
    """
    text = content.strip()
    if not text:
        return []

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if extension == "txt" or ("," not in text and "\t" not in text):
        contacts: list[ParsedContact] = []
        for line in text.splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            if value.lower() in PHONE_HEADERS:
                continue
            contacts.append(ParsedContact(phone=value))
        return contacts

    dict_reader = csv.DictReader(StringIO(text))
    fieldnames = dict_reader.fieldnames or []
    phone_field = next(
        (name for name in fieldnames if name and name.strip().lower() in PHONE_HEADERS),
        None,
    )
    if phone_field:
        contacts = []
        for row in csv.DictReader(StringIO(text)):
            phone = (row.get(phone_field) or "").strip()
            if not phone:
                continue
            contacts.append(
                ParsedContact(
                    phone=phone,
                    name=(row.get("name") or None),
                    details=(row.get("details") or None),
                )
            )
        return contacts

    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(StringIO(text))
        if any(cell.strip() for cell in row)
    ]
    if rows and all(len(row) == 1 for row in rows):
        start = 1 if rows[0][0].lower() in PHONE_HEADERS else 0
        return [ParsedContact(phone=row[0]) for row in rows[start:] if row[0]]

    raise ValueError(
        "Upload phone numbers only: one number per line in a .txt file, "
        "or a single-column list with an optional phone header row."
    )
