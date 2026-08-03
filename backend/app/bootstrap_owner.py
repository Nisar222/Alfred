"""Interactive, local-only first-owner provisioning command.

Run inside the API container after the reviewed migration.  It never prints a
password and refuses to replace an existing active owner.
"""
import getpass

from sqlalchemy import select

from .auth import hash_password
from .database import SessionLocal
from .models import User


def main() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(User.id).where(User.role == "owner", User.is_active.is_(True))):
            raise SystemExit("An active Alfred owner already exists; refusing to create another.")
        email = input("Owner email: ").strip().lower()
        display_name = input("Owner display name: ").strip()
        password = getpass.getpass("New password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if not email or not display_name or len(password) < 12 or password != confirmation:
            raise SystemExit("Use a name, email, and matching password of at least 12 characters.")
        if db.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("That email already exists; refusing to overwrite it.")
        db.add(User(email=email, display_name=display_name, role="owner", is_active=True,
                    password_hash=hash_password(password)))
        db.commit()
        print("Owner account created. Sign in through Alfred; the password was not stored in terminal history.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
