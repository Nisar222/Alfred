"""Owner-run 3CX directory import with an owner-only credential export.

Passwords are written only to the requested protected directory. They are
never printed, logged, returned by the API, or stored in plaintext in the DB.
"""
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import string

from sqlalchemy import or_, select

from .auth import hash_password
from .config import get_settings
from .database import SessionLocal
from .models import User
from .threecx import ThreeCXClient, ThreeCXError


def _temporary_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "-_%!"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


def main() -> None:
    output_dir = Path(os.environ.get("ALFRED_CREDENTIAL_EXPORT_DIR", "/credentials"))
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    output_path = output_dir / f"alfred-agent-credentials-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"

    client = ThreeCXClient(get_settings())
    db = SessionLocal()
    created = updated = skipped_owner = skipped_invalid = 0
    rows: list[dict[str, str]] = []
    try:
        directory = client.list_xapi_users()
        for person in directory:
            extension = (person.extension or "").strip()
            if not extension.isdigit():
                skipped_invalid += 1
                continue
            user = db.scalar(select(User).where(or_(
                User.threecx_user_id == person.user_id,
                User.threecx_extension == extension,
                User.email == (person.email or "").strip().lower(),
            )))
            if user and user.role == "owner":
                if not user.threecx_user_id:
                    user.threecx_user_id = person.user_id
                if not user.threecx_extension:
                    user.threecx_extension = extension
                user.threecx_last_synced_at = datetime.now(timezone.utc)
                skipped_owner += 1
                continue

            password = _temporary_password()
            if user is None:
                email = (person.email or f"{extension}@3cx.local").strip().lower()
                if db.scalar(select(User.id).where(User.email == email)):
                    email = f"{extension}@3cx.local"
                user = User(email=email, display_name=person.name, role="agent")
                db.add(user)
                created += 1
            else:
                updated += 1
            user.display_name = person.name
            user.role = "agent"
            user.is_active = True
            user.threecx_user_id = person.user_id
            user.threecx_extension = extension
            user.threecx_last_synced_at = datetime.now(timezone.utc)
            user.password_hash = hash_password(password)
            rows.append({"username": extension, "name": person.name, "temporary_password": password})

        db.commit()
        with output_path.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["username", "name", "temporary_password"])
            writer.writeheader()
            writer.writerows(rows)
        os.chmod(output_path, 0o600)
    except (OSError, ThreeCXError):
        db.rollback()
        if output_path.exists():
            output_path.unlink()
        raise
    finally:
        db.close()
        client.close()

    print(f"Created {created}; updated {updated}; preserved owners {skipped_owner}; skipped invalid {skipped_invalid}.")
    print(f"Credentials saved to {output_path} with owner-only permissions.")


if __name__ == "__main__":
    main()
