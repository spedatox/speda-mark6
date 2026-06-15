"""
Generate the owner login credential for SPEDA Mark VI.

Run once on a trusted machine, then paste the output into packages/api/.env.
The password is hashed with scrypt (stdlib) — the plaintext is never stored.

  python scripts/hash_password.py                 # prompts for the password (hidden)
  python scripts/hash_password.py "my password"   # password as an argument

It also mints a random JWT_SECRET so you can fill the whole auth block at once.
"""

import getpass
import secrets
import sys
from pathlib import Path

# Allow running from anywhere — make the app package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.security import hash_password  # noqa: E402


def main() -> None:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Owner password: ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.", file=sys.stderr)
            sys.exit(1)

    if len(password) < 12:
        print("Warning: use at least 12 characters for an internet-facing login.", file=sys.stderr)

    print("\n# Paste these into packages/api/.env (keep them secret):\n")
    print(f"OWNER_PASSWORD_HASH={hash_password(password)}")
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print("# OWNER_USERNAME=<your username>")


if __name__ == "__main__":
    main()
