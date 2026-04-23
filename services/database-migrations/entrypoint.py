"""Run Alembic migrations, then provision per-service DB login users."""
import subprocess
import sys

import init_roles


def main() -> int:
    rc = subprocess.call(["/opt/venv/bin/alembic", "upgrade", "head"])
    if rc != 0:
        return rc
    return init_roles.main()


if __name__ == "__main__":
    sys.exit(main())
