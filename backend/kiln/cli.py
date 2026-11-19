"""Operator commands.

Exit codes:

- 0: created the admin
- 2: arguments, password, or settings are invalid
- 3: an admin already exists
- 4: the username belongs to a non-admin
- 1: migration or role seed failed after retries
- 5: the database cannot be reached

Passwords come from the terminal or the first line of stdin. They are never
taken from argv or the environment, and they are never written to output
or logs. This module does not write SQL. A concurrent loser of migrate or
seed retries, then exits 3 when the other run created the admin. It does
not print a traceback.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from getpass import getpass
from pathlib import Path

from pydantic import ValidationError

from kiln.config import Settings, settings_failure_lines
from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.seed import seed_rbac
from kiln.db.store import AdminAlreadyPresent, PostgresStore
from kiln.domain.accounts import AccountCreate
from kiln.errors import BadRequest, Conflict
from kiln.storage.local import LocalBlobStore

DB_UNREACHABLE = "无法连接数据库。请确认 PostgreSQL 已启动，并且 DATABASE_URL 正确"
DB_MIGRATE_FAILED = "数据库迁移或角色目录初始化失败，请重试"
_PREPARE_ATTEMPTS = 8
# Alembic's script cache is not safe for two threads in one process. Two
# processes still race in PostgreSQL and retry below.
_SCHEMA_LOCK = threading.Lock()


class _Rejected(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


class _Unreachable(Exception):
    pass


class _MigrateFailed(Exception):
    pass


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
        print("未知子命令", file=sys.stderr)
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
    except ValidationError as exc:
        print(_account_rule(exc), file=sys.stderr)
        return 2
    try:
        settings = Settings()
    except ValidationError as exc:
        for line in settings_failure_lines(exc):
            print(line, file=sys.stderr)
        return 2
    try:
        store = _prepare_store(settings)
    except _Unreachable:
        print(DB_UNREACHABLE, file=sys.stderr)
        return 5
    except _MigrateFailed:
        print(DB_MIGRATE_FAILED, file=sys.stderr)
        return 1
    try:
        try:
            account = store.bootstrap_admin(payload)
        except AdminAlreadyPresent:
            print("已存在管理员，未做改动", file=sys.stderr)
            return 3
        except Conflict as exc:
            if exc.detail == "用户名已存在":
                print("用户名已存在", file=sys.stderr)
                return 4
            raise
        except BadRequest:
            print("口令长度须为 8–128 个字符", file=sys.stderr)
            return 2
    finally:
        store.engine.dispose()
    print(f"已创建管理员 {account.username}")
    return 0


def _prepare_store(settings: Settings) -> PostgresStore:
    database_url = settings.database_url
    artifact_root = settings.kiln_artifact_root
    for attempt in range(_PREPARE_ATTEMPTS):
        engine = None
        try:
            with _SCHEMA_LOCK:
                upgrade_database(database_url)
                engine = create_db_engine(database_url)
                seed_rbac(engine)
            return PostgresStore(engine, LocalBlobStore(Path(artifact_root)))
        except Exception as exc:
            if engine is not None:
                engine.dispose()
            if _is_unreachable(exc):
                raise _Unreachable() from None
            if attempt == _PREPARE_ATTEMPTS - 1:
                raise _MigrateFailed() from None
            time.sleep(0.05 * (attempt + 1))
    raise _MigrateFailed()


def _is_unreachable(exc: BaseException) -> bool:
    for item in _chain(exc):
        text = str(item).lower()
        if "deadlock" in text or "lock timeout" in text or "could not obtain lock" in text:
            continue
        if (
            "could not connect" in text
            or "connection refused" in text
            or "password authentication failed" in text
            or ("database" in text and "does not exist" in text)
            or "name or service not known" in text
            or "could not translate host" in text
            or "network is unreachable" in text
            or "no route to host" in text
            or ("timeout expired" in text and "lock" not in text)
        ):
            return True
    return False


def _chain(exc: BaseException):
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.append(current.__cause__)
        stack.append(current.__context__)
        origin = getattr(current, "orig", None)
        if isinstance(origin, BaseException):
            stack.append(origin)


def _account_rule(exc: ValidationError) -> str:
    for err in exc.errors():
        loc = err.get("loc") or ()
        if "password" in loc:
            return "口令长度须为 8–128 个字符"
        if "username" in loc:
            return "用户名不符合规则"
        if "display_name" in loc:
            return _plain_message(err, "显示名不符合规则")
        if "email" in loc:
            return "邮箱格式不正确"
    return "参数或口令不合规"


def _plain_message(err: dict[str, object], fallback: str) -> str:
    message = str(err.get("msg") or fallback)
    prefix = "Value error, "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return message or fallback


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
        raise _Rejected("未提供口令")
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
