"""Operator CLI for one-time administrator provisioning."""

import argparse
import sys
from getpass import getpass

from sqlalchemy.exc import SQLAlchemyError

from yihe_api.auth.router import session_factory
from yihe_api.rbac.bootstrap import bootstrap_admin
from yihe_api.request_logs import configure_api_logging
from yihe_api.tracing import LOGGER


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["bootstrap-admin"])
    parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("bootstrap-admin requires an interactive terminal")
    try:
        company_code = input("Company code: ").strip()
        company_name = input("Company name: ").strip()
        employee_no = input("Employee number: ").strip()
        employee_name = input("Employee name: ").strip()
        username = input("Administrator username: ").strip()
        password = getpass("Administrator password: ")
        if password != getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
        with session_factory().begin() as db:
            actor_id = bootstrap_admin(
                db,
                company_code=company_code,
                company_name=company_name,
                employee_no=employee_no,
                employee_name=employee_name,
                username=username,
                password=password,
            )
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            "Bootstrap failed and was rolled back. Check database connectivity and uniqueness.",
            file=sys.stderr,
        )
        return 1
    configure_api_logging()
    LOGGER.info(
        "rbac.bootstrap",
        extra={
            "actor_id": str(actor_id),
            "target_id": str(actor_id),
            "rbac_action": "bootstrap-admin",
        },
    )
    print("Administrator created. Complete MFA enrollment before using administrator permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
