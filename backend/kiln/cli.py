"""Operator commands.

Passwords come from the terminal or the first line of stdin. They are never
taken from argv or the environment, and they are never written to output
or logs. This module does not write SQL.
"""

from __future__ import annotations

import argparse
import sys
from getpass import getpass
from pathlib import Path

from pydantic import ValidationError

from kiln.config import API_TOKEN_ERROR, Settings
from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.seed import seed_rbac
from kiln.db.store import AdminAlreadyPresent, PostgresStore
from kiln.domain.accounts import AccountCreate
from kiln.errors import BadRequest, Conflict
from kiln.storage.local import LocalBlobStore


class _Rejected(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


def main(argv: list[str] | None = None) -> int:
    """Run ``kiln-admin``. Returns a process exit code."""

    parser = _parser()
    try:
        args, unknown = parser.parse_known_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        return code if isinstance(code, int) else 2
    if unknown or args.command != "bootstrap-admin":
        print("参数或口令不合规", file=sys.stderr)
        return 2
    try:
        password = _read_password()
        payload = AccountCreate(
            username=args.username,
            display_name=args.display_name,
            email=args.email,
            password=password,
            role_ids=["admin"],
        )
    except _Rejected as exc:
        print(exc.message, file=sys.stderr)
        return 2
    except ValidationError:
        print("参数或口令不合规", file=sys.stderr)
        return 2
    try:
        settings = Settings()
    except ValidationError:
        print(API_TOKEN_ERROR, file=sys.stderr)
        return 2
    upgrade_database(settings.database_url)
    engine = create_db_engine(settings.database_url)
    try:
        seed_rbac(engine)
        store = PostgresStore(engine, LocalBlobStore(Path(settings.kiln_artifact_root)))
        try:
            account = store.bootstrap_admin(payload)
        except AdminAlreadyPresent:
            print("已存在管理员，未做改动")
            return 3
        except Conflict as exc:
            if exc.detail == "用户名已存在":
                print("用户名已存在")
                return 4
            raise
        except BadRequest:
            print("参数或口令不合规", file=sys.stderr)
            return 2
    finally:
        engine.dispose()
    print(f"已创建管理员 {account.username}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiln-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap-admin")
    boot.add_argument("--username", required=True)
    boot.add_argument("--display-name", required=True)
    boot.add_argument("--email", default=None)
    return parser


def _read_password() -> str:
    if _stdin_is_tty():
        first = getpass("口令：")
        second = getpass("再次输入口令：")
        if first != second:
            raise _Rejected("两次输入的口令不一致")
        return first
    line = sys.stdin.readline()
    if line == "":
        raise _Rejected("参数或口令不合规")
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _stdin_is_tty() -> bool:
    isatty = getattr(sys.stdin, "isatty", None)
    if not callable(isatty):
        return False
    return bool(isatty())
