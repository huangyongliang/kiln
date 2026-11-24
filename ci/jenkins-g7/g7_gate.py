#!/usr/bin/env python3
"""Disposable Jenkins isolation gate. Runs as the unprivileged test user."""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SO_PEERCRED = 17
PSEUDO_FS = {
    "proc",
    "sysfs",
    "devpts",
    "mqueue",
    "cgroup",
    "cgroup2",
    "devtmpfs",
    "bpf",
    "securityfs",
    "pstore",
    "debugfs",
    "tracefs",
    "configfs",
    "fusectl",
    "ramfs",
    "nsfs",
    "autofs",
    "binfmt_misc",
}
TEST_ORDER = [
    "G7-HOST",
    "G7-RUNNER-API",
    "G7-SETUP-PRIV",
    "G7-PODMAN",
    "G7-DIGEST",
    "G7-CLEANUP-FAIL",
    "G7-ROOTLESS-START",
    "G7-ROOTFS",
    "G7-NET-CLOSED",
    "G7-EGRESS",
    "G7-PEER-UID",
    "G7-MOUNTS",
    "G7-REMOTING-NEG",
    "G7-ARGFILE",
    "G7-API",
    "G7-EXCLUSIVE",
    "G7-CAPABILITY",
    "G7-TEARDOWN",
]
FIXED = {
    "G7-RUNNER-API": (
        "NOT RUN",
        "Historical runners API 403 is final. This PR does not call it.",
    ),
    "G7-EGRESS": (
        "NOT CLAIMED",
        "Routed production destination allowlist was not run.",
    ),
}


class GateError(Exception):
    pass


class Guard:
    def __init__(self) -> None:
        self.values: list[str] = []

    def add(self, value: str | None) -> None:
        if value and len(value) >= 8:
            self.values.append(value)

    def contains(self, text: str) -> bool:
        return any(value and value in text for value in self.values)

    def redact(self, text: str) -> str:
        for value in self.values:
            if value:
                text = text.replace(value, "[redacted]")
        return text

    def assert_clean(self, label: str, text: str) -> None:
        if self.contains(text):
            raise GateError(f"{label} contained a secret value")


class Ledger:
    def __init__(self, guard: Guard) -> None:
        self.guard = guard
        self.rows: dict[str, dict] = {}
        self.locked = set(FIXED)

    def record(
        self,
        test_id: str,
        status: str,
        expected: str,
        observed: str,
        command_shape: str = "",
        teardown: str = "",
    ) -> None:
        if test_id in self.locked:
            return
        observed = self.guard.redact(observed)
        self.guard.assert_clean(test_id, observed)
        current = self.rows.get(test_id)
        if current and current["status"] in {"FAIL", "BLOCKED"}:
            return
        self.rows[test_id] = {
            "testID": test_id,
            "status": status,
            "expected": expected,
            "observed": observed,
            "command_shape": command_shape,
            "teardown": teardown,
        }

    def seed_fixed(self) -> None:
        for test_id, (status, observed) in FIXED.items():
            self.rows[test_id] = {
                "testID": test_id,
                "status": status,
                "expected": observed,
                "observed": observed,
                "command_shape": "not invoked",
                "teardown": "not applicable",
            }

    def fill_missing(self, reason: str) -> None:
        for test_id in TEST_ORDER:
            if test_id not in self.rows:
                status = "NOT RUN"
                self.rows[test_id] = {
                    "testID": test_id,
                    "status": status,
                    "expected": "measured in this job",
                    "observed": self.guard.redact(reason)[:500],
                    "command_shape": "not reached",
                    "teardown": "owned resources removed in finally",
                }

    def force(
        self,
        test_id: str,
        status: str,
        expected: str,
        observed: str,
        command_shape: str = "",
        teardown: str = "",
    ) -> None:
        if test_id in self.locked:
            return
        observed = self.guard.redact(observed)
        self.guard.assert_clean(test_id, observed)
        self.rows[test_id] = {
            "testID": test_id,
            "status": status,
            "expected": expected,
            "observed": observed,
            "command_shape": command_shape,
            "teardown": teardown,
        }

    def failed(self, aborted: str) -> bool:
        if aborted:
            return True
        explicit = {"G7-RUNNER-API": "NOT RUN", "G7-EGRESS": "NOT CLAIMED"}
        for test_id in TEST_ORDER:
            row = self.rows.get(test_id)
            if test_id in explicit:
                if not row or row["status"] != explicit[test_id]:
                    return True
                continue
            if not row or row["status"] != "PASS":
                return True
        return False

    def document(self, context: dict) -> dict:
        ordered = [self.rows[item] for item in TEST_ORDER if item in self.rows]
        body = {"schema": "kiln-g7-ledger-1", "context": context, "tests": ordered}
        encoded = json.dumps(body, indent=2, sort_keys=True)
        self.guard.assert_clean("ledger", encoded)
        return body


def log(guard: Guard, message: str) -> None:
    print(guard.redact(message), flush=True)


def run_cmd(cmd: list[str], timeout: int, guard: Guard) -> subprocess.CompletedProcess[bytes]:
    blob = "\0".join(cmd)
    guard.assert_clean("command", blob)
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def elf_build_id(path: Path) -> str:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise GateError("java is not a little-endian ELF64")
    section_off = int.from_bytes(data[40:48], "little")
    section_size = int.from_bytes(data[58:60], "little")
    section_count = int.from_bytes(data[60:62], "little")
    for index in range(section_count):
        offset = section_off + index * section_size
        if int.from_bytes(data[offset + 4 : offset + 8], "little") != 7:
            continue
        note_off = int.from_bytes(data[offset + 24 : offset + 32], "little")
        note_size = int.from_bytes(data[offset + 32 : offset + 40], "little")
        note = data[note_off : note_off + note_size]
        cursor = 0
        while cursor + 12 <= len(note):
            namesz = int.from_bytes(note[cursor : cursor + 4], "little")
            descsz = int.from_bytes(note[cursor + 4 : cursor + 8], "little")
            ntype = int.from_bytes(note[cursor + 8 : cursor + 12], "little")
            name = note[cursor + 12 : cursor + 12 + namesz].split(b"\x00", 1)[0]
            desc_off = cursor + 12 + ((namesz + 3) & ~3)
            desc = note[desc_off : desc_off + descsz]
            if name == b"GNU" and ntype == 3 and desc:
                return desc.hex()
            cursor = desc_off + ((descsz + 3) & ~3)
    raise GateError("java GNU BuildID note not found")


def tail(guard: Guard, blob: bytes, limit: int = 400) -> str:
    text = blob.decode("utf-8", "replace")
    if guard.contains(text):
        return "redacted: secret-bearing output omitted"
    return guard.redact(text)[-limit:]


def write_private(path: Path, data: bytes, mode: int = 0o600, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    fd = os.open(path, flags, mode)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    parent = os.lstat(path.parent)
    info = os.lstat(path)
    if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
        raise GateError(f"{path.parent.name} is not a private directory")
    if parent.st_uid != os.getuid() or (parent.st_mode & 0o777) != 0o700:
        raise GateError(f"{path.parent.name} owner/mode is {parent.st_uid}:{oct(parent.st_mode & 0o777)}")
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise GateError(f"{path.name} is not a regular file")
    if info.st_uid != os.getuid() or info.st_gid != os.getgid() or (info.st_mode & 0o777) != mode:
        raise GateError(
            f"{path.name} owner/mode is {info.st_uid}:{info.st_gid}:{oct(info.st_mode & 0o777)}"
        )


def destroy(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        path.unlink(missing_ok=True)
        return
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        size = os.fstat(fd).st_size
        os.write(fd, b"\0" * size)
        os.fsync(fd)
    finally:
        os.close(fd)
    path.unlink()


def parse_mounts(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        dest = parts[1].replace("\\040", " ")
        options = parts[3].split(",")
        rows.append(
            {
                "dest": dest,
                "fstype": parts[2],
                "rw": "rw" in options,
            }
        )
    return rows


def unexpected_writable(mounts: list[dict], allow: set[str]) -> list[str]:
    bad = []
    for item in mounts:
        if not item["rw"] or item["fstype"] in PSEUDO_FS:
            continue
        if item["dest"] not in allow:
            bad.append(f"{item['dest']} {item['fstype']}")
    return bad


def listen_ports(text: str) -> set[int]:
    ports = set()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3].upper() != "0A":
            continue
        ports.add(int(parts[1].split(":")[-1], 16))
    return ports


def ipv4_default(text: str) -> bool:
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "00000000":
            return True
    return False


PROXY_ENV = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "NO_PROXY",
    "socks_proxy",
    "SOCKS_PROXY",
    "socks5_proxy",
    "SOCKS5_PROXY",
)


def direct_curl_argv(args: list[str]) -> list[str]:
    # -q is curl's first argument, so a curlrc cannot set a proxy. An empty
    # --proxy and --noproxy * are repeated after the caller arguments so a
    # config file cannot turn the probe back into a proxied request. env -u
    # removes proxy variables for this process only.
    command = ["env"]
    for name in PROXY_ENV:
        command.extend(["-u", name])
    command.extend(["curl", "-q", "--proxy", "", "--noproxy", "*"])
    command.extend(args)
    command.extend(["--proxy", "", "--noproxy", "*"])
    return command


def classify_connect(returncode: int, http_code: str, error: str, direct: bool) -> dict:
    text = error.lower()
    proxy = "proxy" in text
    reason = "other"
    if direct and not proxy and returncode in {7, 28} and http_code in {"", "000"}:
        # Curl's non-verbose line is the generic "Couldn't connect to server".
        # The verbose "Immediate connect fail" line carries the OS errno text.
        if "immediate connect fail" in text and any(
            phrase in text
            for phrase in (
                "network is unreachable",
                "network unreachable",
                "no route to host",
                "host is unreachable",
            )
        ):
            reason = "unreachable"
        elif any(phrase in text for phrase in ("connection timed out", "operation timed out")):
            reason = "timeout"
    return {
        "isolated": reason in {"unreachable", "timeout"},
        "returncode": returncode,
        "http_code": http_code,
        "reason": reason,
        "direct": direct,
        "proxy": proxy,
    }


def ipv6_default(text: str) -> bool:
    # RTF_REJECT marks the kernel's unreachable ::/0 sink. A closed namespace has
    # that route on lo and no forwardable default route.
    reject = 0x0200
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            prefix = int(parts[1], 16)
            flags = int(parts[8], 16)
        except ValueError:
            continue
        if set(parts[0]) <= {"0"} and prefix == 0 and not flags & reject:
            return True
    return False


def peercred(conn: socket.socket) -> tuple[int, int, int]:
    raw = conn.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    return pid, uid, gid


def registry_bytes(url: str, headers: dict[str, str]) -> bytes:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # noqa: ANN001
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            location = exc.headers.get("Location")
            follow = urllib.request.Request(location, headers={"User-Agent": "kiln-g7"})
            with urllib.request.urlopen(follow, timeout=120) as response:
                return response.read()
        raise GateError(f"registry HTTP {exc.code}") from None


def token_for(lock: dict, repository: str) -> str:
    auth = lock["registry"]["auth_url"]
    url = f"{auth}?service=registry.docker.io&scope=repository:{repository}:pull"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "kiln-g7"}), timeout=60) as response:
        return json.load(response)["token"]


def verify_manifest(lock: dict, repository: str, digest: str, accept: str) -> dict:
    token = token_for(lock, repository)
    url = f"{lock['registry']['registry_url']}/v2/{repository}/manifests/{digest}"
    body = registry_bytes(
        url,
        {
            "User-Agent": "kiln-g7",
            "Authorization": f"Bearer {token}",
            "Accept": accept,
        },
    )
    actual = "sha256:" + hashlib.sha256(body).hexdigest()
    if actual != digest:
        raise GateError(f"{repository} manifest hash mismatch")
    return json.loads(body)


def verify_blob(lock: dict, repository: str, digest: str) -> bytes:
    token = token_for(lock, repository)
    url = f"{lock['registry']['registry_url']}/v2/{repository}/blobs/{digest}"
    body = registry_bytes(url, {"User-Agent": "kiln-g7", "Authorization": f"Bearer {token}"})
    actual = "sha256:" + hashlib.sha256(body).hexdigest()
    if actual != digest:
        raise GateError(f"{repository} blob hash mismatch")
    return body


def sha256_url(url: str) -> str:
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "kiln-g7"})
    with urllib.request.urlopen(request, timeout=180) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url: str, expected: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kiln-g7"})
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        raise GateError(f"checksum mismatch for {url.rsplit('/', 1)[-1]}")
    return body


def host_ipv4() -> tuple[str, str]:
    route = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, check=False)
    match = re.search(r"\bdev\s+(\S+)", route.stdout)
    if not match:
        raise GateError("no host default route device")
    device = match.group(1)
    addr = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", device], capture_output=True, text=True, check=False)
    found = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", addr.stdout)
    if not found:
        raise GateError("no host IPv4 on default device")
    return device, found.group(1)


def host_gateway() -> str:
    route = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, check=False)
    match = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", route.stdout)
    if not match:
        raise GateError("no host default gateway")
    return match.group(1)


class OutsideListener:
    def __init__(self, ip: str, family: int = socket.AF_INET, scope: int = 0) -> None:
        self.ip = ip
        self.family = family
        self.scope = scope
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            self.sock.bind((ip, 0, 0, scope))
        else:
            self.sock.bind((ip, 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.alive = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        while self.alive:
            try:
                self.sock.settimeout(0.5)
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.sendall(b"kiln-g7-outside-ok\n")
            finally:
                conn.close()

    def host_ok(self) -> bool:
        with socket.socket(self.family, socket.SOCK_STREAM) as conn:
            conn.settimeout(2)
            if self.family == socket.AF_INET6:
                conn.connect((self.ip, self.port, 0, self.scope))
            else:
                conn.connect((self.ip, self.port))
            return conn.recv(64) == b"kiln-g7-outside-ok\n"

    def close(self) -> None:
        self.alive = False
        self.sock.close()


OBS_BYTE_CAP = 65536
OBS_READ_TARGET = 5.0
OBS_WAIT_TARGET = 1.0
OBS_PHASES = frozenset(
    {
        "BEFORE_EXACT",
        "EXACT_FAILED",
        "AFTER_EXACT",
        "EXTRA_SUCCESS",
        "EXTRA_TIMEOUT",
        "EXTRA_FAILED",
    }
)
OBS_EVENTS = (
    ("Terminated", "TERMINATED"),
    ("Performing onReconnect operation.", "ON_RECONNECT"),
    ("Restarting agent via ", "RESTARTER_ATTEMPT"),
    ("Failed to exec", "EXEC_FAILED"),
)
_OBS_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?Z (.*)\Z", re.DOTALL)
_OBS_EVENT_RE = re.compile(
    r"^event=(TERMINATED|ON_RECONNECT|RESTARTER_ATTEMPT|EXEC_FAILED)@"
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z|UNKNOWN)$"
)


def obs_extra_terminal(timed_out: bool) -> str:
    if timed_out:
        return "EXTRA_TIMEOUT"
    return "EXTRA_FAILED"


def obs_resolve(original: BaseException | None, reap_unconfirmed: bool, emit_failed: bool) -> BaseException | None:
    if original is not None:
        return original
    if reap_unconfirmed:
        return GateError("observation collector reap unconfirmed")
    if emit_failed:
        return GateError("observation emission failed")
    return None


def obs_safe_emit(write, text: str) -> bool:
    try:
        write(text)
        return True
    except Exception:
        return False


def obs_split_stamp(line: str) -> tuple[str, str]:
    match = _OBS_STAMP_RE.match(line)
    if match is None:
        return "UNKNOWN", line
    try:
        datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return "UNKNOWN", match.group(8)
    fraction = match.group(7) or ""
    stamp = (
        f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        f"T{int(match.group(4)):02d}:{int(match.group(5)):02d}:{int(match.group(6)):02d}{fraction}Z"
    )
    return stamp, match.group(8)


def obs_events_from_lines(stdout_lines: list[str], stderr_lines: list[str]) -> tuple[list[str], bool, bool]:
    pairs: set[tuple[str, str]] = set()
    ambiguous = False
    for line in stdout_lines:
        for pair in _obs_pairs(line):
            pairs.add(pair)
    for line in stderr_lines:
        for pair in _obs_pairs(line):
            pairs.add(pair)
    by_id: dict[str, set[str]] = {}
    for event_id, stamp in pairs:
        by_id.setdefault(event_id, set()).add(stamp)
        if stamp == "UNKNOWN":
            ambiguous = True
    if any(len(stamps) > 1 for stamps in by_id.values()):
        ambiguous = True
    capped = len(pairs) > 8
    if capped:
        ambiguous = True
    exported = [f"event={event_id}@{stamp}" for event_id, stamp in sorted(pairs)[:8]]
    exported = [item for item in exported if _OBS_EVENT_RE.fullmatch(item)]
    return exported, ambiguous, capped


def _obs_pairs(line: str) -> list[tuple[str, str]]:
    stamp, payload = obs_split_stamp(line)
    return [(event_id, stamp) for needle, event_id in OBS_EVENTS if needle in payload]


def _obs_complete_lines(buf: bytes) -> tuple[list[str], bytes]:
    lines: list[str] = []
    while True:
        mark = buf.find(b"\n")
        if mark < 0:
            return lines, buf
        lines.append(buf[:mark].decode("utf-8", "replace"))
        buf = buf[mark + 1 :]


def obs_read_streams(proc) -> tuple[list[str], list[str], bool, bool, bool, bool]:
    streams = (proc.stdout, proc.stderr)
    pending = {stream: b"" for stream in streams}
    lines = {stream: [] for stream in streams}
    eof = {stream: False for stream in streams}
    remaining = OBS_BYTE_CAP
    discarded = False
    truncated = False
    read_failed = False
    deadline = time.monotonic() + OBS_READ_TARGET
    os.set_blocking(proc.stdout.fileno(), False)
    os.set_blocking(proc.stderr.fileno(), False)
    while remaining > 0 and not all(eof.values()):
        if time.monotonic() >= deadline:
            read_failed = True
            break
        watch = [stream for stream in streams if not eof[stream]]
        try:
            ready, _, _ = select.select(watch, [], [], max(0.0, deadline - time.monotonic()))
        except Exception:
            read_failed = True
            break
        late = time.monotonic() >= deadline
        if not ready:
            read_failed = True
            break
        if late:
            read_failed = True
        stop = False
        for stream in ready:
            if remaining <= 0 or eof[stream]:
                continue
            try:
                chunk = os.read(stream.fileno(), remaining)
            except BlockingIOError:
                continue
            except Exception:
                read_failed = True
                stop = True
                break
            if not isinstance(chunk, (bytes, bytearray)):
                read_failed = True
                stop = True
                break
            if len(chunk) > remaining:
                chunk = bytes(chunk[:remaining])
                truncated = True
            if chunk == b"":
                eof[stream] = True
                if pending[stream]:
                    discarded = True
                    pending[stream] = b""
                continue
            remaining -= len(chunk)
            pending[stream] = pending[stream] + bytes(chunk)
            complete, pending[stream] = _obs_complete_lines(pending[stream])
            lines[stream].extend(complete)
            if remaining <= 0:
                truncated = True
                stop = True
                break
        if time.monotonic() >= deadline:
            read_failed = True
            break
        if stop:
            break
    if remaining <= 0:
        truncated = True
    for stream in streams:
        if pending[stream]:
            discarded = True
            pending[stream] = b""
    if time.monotonic() >= deadline:
        read_failed = True
    both_eof = all(eof.values()) and not read_failed and not truncated
    return lines[streams[0]], lines[streams[1]], discarded, truncated, read_failed, both_eof


def obs_close_kill_wait(proc) -> tuple[bool, bool, int | None]:
    cleanup_error = False
    for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            cleanup_error = True
    try:
        running = proc.poll() is None
    except Exception:
        cleanup_error = True
        running = True
    if running:
        try:
            proc.kill()
        except Exception:
            cleanup_error = True
    try:
        proc.wait(timeout=OBS_WAIT_TARGET)
    except Exception:
        cleanup_error = True
    try:
        returncode = proc.poll()
    except Exception:
        cleanup_error = True
        returncode = None
    return cleanup_error, returncode is not None, returncode


def obs_judge(returncode: int | None, both_eof: bool, discarded: bool, truncated: bool, read_failed: bool, reaped: bool) -> str:
    if reaped and returncode == 0 and both_eof and not discarded and not truncated and not read_failed:
        return "ok"
    return "failed"


def obs_format_line(phase: str, fields: dict) -> str:
    phase_token = phase if phase in OBS_PHASES else "UNKNOWN"
    stamp, _rest = obs_split_stamp(str(fields.get("ts", "")) + " marker")
    events = [item for item in fields.get("events", []) if _OBS_EVENT_RE.fullmatch(item)]
    ambiguous = "true" if fields.get("ambiguous") else "false"
    capped = "true" if fields.get("events_capped") else "false"
    capture = "ok" if fields.get("capture") == "ok" else "failed"
    cleanup = "error" if fields.get("cleanup") == "error" else "ok"
    reap = "unconfirmed" if fields.get("reap") == "unconfirmed" else "confirmed"
    tokens = [
        f"phase={phase_token}",
        f"ts={stamp}",
        f"capture={capture}",
        "interval_coverage=UNKNOWN",
        "gaps_possible=true",
        "order=ambiguous",
        f"ambiguous={ambiguous}",
        f"events_capped={capped}",
        *events,
        "unmatched=UNKNOWN",
        "events_do_not_exclude_exec",
        f"cleanup={cleanup}",
        f"reap={reap}",
    ]
    return "G7-OBS " + " ".join(tokens)


def _obs_bool(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _obs_int(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return "none"
    return str(value)


def obs_cause_class(cause: object) -> str:
    if not isinstance(cause, dict):
        return "none"
    name = cause.get("_class")
    if not isinstance(name, str) or not name:
        return "none"
    if name.endswith("$DiskSpace") or name.endswith(".DiskSpace"):
        return "disk_space"
    if name.endswith("$ChannelTermination"):
        return "channel_termination"
    if name.endswith("$SimpleOfflineCause"):
        return "simple_offline"
    if name.endswith("$UserCause"):
        return "user_cause"
    return "other"


def obs_path_class(path: object) -> str:
    if not isinstance(path, str) or not path:
        return "NONE"
    if path == "/tmp" or path.startswith("/tmp/"):
        return "TMP"
    return "OTHER"


def obs_reason_class(reason: object) -> str:
    if not isinstance(reason, str) or not reason.strip():
        return "none"
    text = reason.casefold()
    if "disk space" in text:
        return "disk_space"
    if "connection was broken" in text or "channel is terminated" in text or (
        "channel" in text and any(token in text for token in ("terminat", "broken", "closed"))
    ):
        return "channel_terminated"
    if "monitor" in text:
        return "monitor_offline"
    return "other"


def obs_node_status_line(phase: str, data: object) -> str:
    phase_token = phase if phase in {"BEFORE_EXACT", "AFTER_EXACT", "EXTRA_TIMEOUT"} else "UNKNOWN"
    if not isinstance(data, dict):
        return f"G7-NODE phase={phase_token} read=failed"
    cause = data.get("offlineCause")
    cause_class = obs_cause_class(cause)
    cause_ts = "none"
    size = "none"
    total_size = "none"
    threshold = "none"
    path_class = "NONE"
    if isinstance(cause, dict):
        cause_ts = _obs_int(cause.get("timestamp"))
        if cause_class == "disk_space":
            size = _obs_int(cause.get("size"))
            total_size = _obs_int(cause.get("totalSize"))
            threshold = _obs_int(cause.get("threshold"))
            path_class = obs_path_class(cause.get("path"))
    executors = data.get("numExecutors")
    executor_token = str(executors) if isinstance(executors, int) and not isinstance(executors, bool) else "unknown"
    return (
        f"G7-NODE phase={phase_token} offline={_obs_bool(data.get('offline'))} "
        f"temporarily_offline={_obs_bool(data.get('temporarilyOffline'))} "
        f"idle={_obs_bool(data.get('idle'))} executors={executor_token} "
        f"cause_class={cause_class} cause_ts={cause_ts} size={size} total_size={total_size} "
        f"threshold={threshold} path_class={path_class} reason_class={obs_reason_class(data.get('offlineCauseReason'))}"
    )


def obs_capture(agent: str, live: list) -> dict:
    proc = subprocess.Popen(
        ["podman", "logs", "--timestamps", "--tail", "200", agent],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    live.append(proc)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    discarded = False
    truncated = False
    read_failed = True
    both_eof = False
    try:
        if proc.stdout is None or proc.stderr is None:
            read_failed = True
        else:
            stdout_lines, stderr_lines, discarded, truncated, read_failed, both_eof = obs_read_streams(proc)
    except Exception:
        read_failed = True
    cleanup_error, reaped, returncode = obs_close_kill_wait(proc)
    if reaped:
        try:
            live.remove(proc)
        except ValueError:
            pass
    events, ambiguous, capped = obs_events_from_lines(stdout_lines, stderr_lines)
    if discarded or truncated:
        ambiguous = True
    del stdout_lines, stderr_lines
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capture": obs_judge(returncode, both_eof, discarded, truncated, read_failed, reaped),
        "ambiguous": ambiguous,
        "events_capped": capped,
        "events": events,
        "cleanup": "error" if cleanup_error else "ok",
        "reap": "confirmed" if reaped else "unconfirmed",
    }


class Gate:
    def __init__(self) -> None:
        self.guard = Guard()
        self.ledger = Ledger(self.guard)
        self.private = Path(os.environ.get("KILN_G7_PRIVATE", "/home/kiln-g7/private"))
        self.deb_dir = Path(os.environ.get("KILN_G7_DEB_DIR", "/tmp/kiln-g7-debs"))
        self.peer_dir = Path(os.environ.get("KILN_G7_PEER_DIR", "/tmp/kiln-g7-peer"))
        self.lock_path = Path(os.environ["KILN_G7_LOCK"])
        self.lock = json.loads(self.lock_path.read_text())
        self.lock_sha = hashlib.sha256(self.lock_path.read_bytes()).hexdigest()
        self.deadline = time.monotonic() + 21 * 60
        self.aborted = ""
        self.controller = "kiln-g7-controller"
        self.agent = "kiln-g7-agent"
        self.neg = "kiln-g7-neg"
        self.cleanup_name = "kiln-g7-cleanup"
        self.home = self.private / "jenkins-home"
        self.controller_image = ""
        self.inbound_image = ""
        self.java_elf = self.lock["inbound"]["java_elf"]
        self.agent_jar = self.lock["inbound"]["agent_jar"]
        self.passwords: dict[str, str] = {}
        self.crumbs: dict[str, str] = {}
        self.containers = [self.controller, self.agent, self.neg, self.cleanup_name]
        self.phase = ""
        self._obs_live: list = []
        self._obs_reap_unconfirmed = False
        self._obs_emit_failed = False
        self._obs_cleanup_error = False
        self._extra_wait_timed_out = False

    def check_time(self, phase: str) -> None:
        if time.monotonic() > self.deadline:
            raise GateError(f"soft deadline before {phase}")

    def run(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        return run_cmd(cmd, timeout, self.guard)

    def note(self, message: str) -> None:
        log(self.guard, message)

    def fail(self, test_id: str, observed: str, expected: str) -> None:
        self.ledger.record(test_id, "FAIL", expected, observed, teardown="owned resources removed in finally")
        self.aborted = observed

    def podman_base(self, name: str, image: str, network: str, userns: str, tmp_size: int = 268435456) -> list[str]:
        return [
            "podman",
            "run",
            "-d",
            "--name",
            name,
            "--pull=never",
            "--read-only",
            "--read-only-tmpfs=false",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--restart=no",
            "--stop-timeout=8",
            "--network",
            network,
            "--userns",
            userns,
            "--user",
            "1000:1000",
            "--unsetenv=JAVA_TOOL_OPTIONS",
            "--unsetenv=JDK_JAVA_OPTIONS",
            "--unsetenv=_JAVA_OPTIONS",
            *[f"--unsetenv={name}" for name in PROXY_ENV],
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,mode=1777,size={tmp_size}",
            "--tmpfs",
            "/dev/shm:rw,nosuid,nodev,mode=1777,size=67108864",
        ]

    def prepare_runtime(self) -> None:
        config = Path.home() / ".config" / "containers"
        config.mkdir(parents=True, exist_ok=True)
        (config / "containers.conf").write_text(
            '[engine]\ncgroup_manager = "cgroupfs"\nevents_logger = "file"\nruntime = "crun"\n'
        )
        (config / "storage.conf").write_text('[storage]\ndriver = "overlay"\n')
        os.chmod(config, 0o700)

    def verify_packages(self) -> None:
        for package in self.lock["apt"]["packages"]:
            path = self.deb_dir / package["filename"]
            if not path.is_file():
                raise GateError(f"missing pinned deb {package['filename']}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != package["sha256"]:
                raise GateError(f"deb checksum mismatch for {package['name']}")
            query = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Version}", package["name"]],
                capture_output=True,
                text=True,
                check=False,
            )
            if query.stdout.strip() != package["version"]:
                raise GateError(f"{package['name']} version {query.stdout.strip()}")
        version = self.run(["podman", "--version"], 20)
        text = version.stdout.decode().strip()
        if text != "podman version 4.9.3":
            raise GateError(f"podman version {text}")

    def verify_rootless(self) -> dict:
        proc = self.run(["podman", "info", "--format", "json"], 40)
        if proc.returncode != 0:
            raise GateError("podman info failed: " + tail(self.guard, proc.stderr))
        info = json.loads(proc.stdout)
        host = info.get("host") or info.get("Host") or {}
        security = host.get("security") or host.get("Security") or {}
        rootless = security.get("rootless", security.get("Rootless"))
        remote = host.get("remoteSocket") or host.get("RemoteSocket") or {}
        socket_path = str(remote.get("path") or remote.get("Path") or "")
        if rootless is not True:
            raise GateError("podman info rootless is not true")
        if "docker.sock" in socket_path:
            raise GateError("podman is using the Docker socket")
        if os.environ.get("DOCKER_HOST"):
            raise GateError("DOCKER_HOST is set")
        return {"rootless": True, "runtime_socket": socket_path, "cgroup": host.get("cgroupManager") or host.get("CgroupManager")}

    def verify_identity(self, kind: str, spec: dict) -> None:
        index = verify_manifest(
            self.lock,
            spec["repository"],
            spec["index_digest"],
            "application/vnd.oci.image.index.v1+json",
        )
        amd64 = ""
        for item in index.get("manifests", []):
            platform = item.get("platform") or {}
            if platform.get("os") == "linux" and platform.get("architecture") == "amd64" and not platform.get("variant"):
                amd64 = item["digest"]
        if amd64 != spec["amd64_digest"]:
            raise GateError(f"{kind} amd64 digest mismatch")
        manifest = verify_manifest(
            self.lock,
            spec["repository"],
            spec["amd64_digest"],
            "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
        )
        linked = (manifest.get("config") or {}).get("digest")
        if linked != spec["config_digest"]:
            raise GateError(f"{kind} manifest config link mismatch")
        verify_blob(self.lock, spec["repository"], spec["config_digest"])

    def pull(self, repository: str, digest: str) -> str:
        ref = f"docker://{repository}@{digest}"
        proc = self.run(["podman", "pull", "--arch", "amd64", "--os", "linux", ref], 240)
        if proc.returncode != 0:
            raise GateError(f"pull failed for {repository}: " + tail(self.guard, proc.stderr))
        image = f"{repository}@{digest}"
        raw = self.run(["podman", "image", "inspect", image], 30)
        if raw.returncode != 0:
            raise GateError(f"inspect failed for {repository}")
        meta = json.loads(raw.stdout)[0]
        if meta.get("Architecture") != "amd64" or meta.get("Os") != "linux":
            raise GateError(f"{repository} platform is not linux/amd64")
        matches = [item for item in meta.get("RepoDigests") or [] if digest in str(item)]
        if meta.get("Architecture") != "amd64" or not matches:
            raise GateError(f"{repository} local digest does not match the pin")
        return matches[0]

    def measure_inbound(self) -> dict:
        image = self.inbound_image
        common = [
            "podman",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--read-only-tmpfs=false",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--userns=keep-id:uid=1000,gid=1000",
            "--user",
            "1000:1000",
        ]
        java = self.run(common + ["--entrypoint", self.java_elf, image, "-version"], 60)
        java_text = (java.stdout + java.stderr).decode("utf-8", "replace")
        if self.lock["inbound"]["java_version"] not in java_text:
            raise GateError("inbound JVM version does not match the lock")
        elf = self.run(common + ["--entrypoint", "/usr/bin/od", image, "-An", "-tx1", "-N", "4", self.java_elf], 30)
        elf_text = elf.stdout.decode().lower().replace(" ", "")
        if "7f454c46" not in elf_text:
            raise GateError("inbound java ELF magic mismatch")
        uid = self.run(common + ["--entrypoint", "/usr/bin/id", image, "-u"], 30)
        if uid.stdout.decode().strip() != "1000":
            raise GateError(f"inbound uid is {uid.stdout.decode().strip()}")
        jar_copy = self.private / "agent.jar"
        created = self.run(
            [
                "podman",
                "create",
                "--pull=never",
                "--entrypoint",
                "/bin/true",
                "--userns=keep-id:uid=1000,gid=1000",
                image,
            ],
            30,
        )
        if created.returncode != 0:
            raise GateError("unable to create inbound jar reader")
        cid = created.stdout.decode().strip()
        try:
            copied = self.run(["podman", "cp", f"{cid}:{self.agent_jar}", str(jar_copy)], 30)
            if copied.returncode != 0:
                raise GateError("inbound agent.jar path missing")
            import zipfile

            with zipfile.ZipFile(jar_copy) as archive:
                manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
        finally:
            self.run(["podman", "rm", cid], 20)
            destroy(jar_copy)
        version = ""
        build = ""
        for line in manifest.splitlines():
            if line.startswith("Implementation-Version:"):
                version = line.split(":", 1)[1].strip()
            if line.startswith("Implementation-Build:"):
                build = line.split(":", 1)[1].strip()
        if version != self.lock["inbound"]["remoting_version"] or build != self.lock["inbound"]["remoting_build"]:
            raise GateError(f"remoting identity {version} {build}")
        java_copy = self.private / "java.elf"
        created_java = self.run(
            [
                "podman",
                "create",
                "--pull=never",
                "--entrypoint",
                "/bin/true",
                "--userns=keep-id:uid=1000,gid=1000",
                image,
            ],
            30,
        )
        if created_java.returncode != 0:
            raise GateError("unable to read inbound java ELF")
        java_cid = created_java.stdout.decode().strip()
        try:
            copied_java = self.run(["podman", "cp", f"{java_cid}:{self.java_elf}", str(java_copy)], 30)
            if copied_java.returncode != 0:
                raise GateError("inbound java ELF path missing")
            build_id = elf_build_id(java_copy)
        finally:
            self.run(["podman", "rm", java_cid], 20)
            destroy(java_copy)
        return {
            "java": java_text.strip().splitlines()[0],
            "uid": 1000,
            "remoting": version,
            "build": build,
            "java_build_id": build_id,
        }

    def measure_controller_war(self) -> None:
        proc = self.run(
            [
                "podman",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--userns=keep-id:uid=1000,gid=1000",
                "--user",
                "1000:1000",
                "--entrypoint",
                "/bin/sh",
                self.controller_image,
                "-c",
                "sha256sum /usr/share/jenkins/jenkins.war",
            ],
            60,
        )
        if proc.returncode != 0 or self.lock["controller"]["war_sha256"] not in proc.stdout.decode():
            raise GateError("controller war checksum mismatch")

    def write_auth(self) -> None:
        import secrets

        auth = self.private / "auth"
        auth.mkdir(parents=True, exist_ok=True)
        os.chmod(auth, 0o700)
        for name in ("feas-admin", "feas-lifecycle", "feas-build"):
            password = secrets.token_hex(24)
            self.passwords[name] = password
            self.guard.add(password)
            write_private(auth / f"{name}.pw", (password + "\n").encode())
            write_private(
                auth / f"{name}.netrc",
                f"machine 127.0.0.1\nlogin {name}\npassword {password}\n".encode(),
            )

    def write_controller_home(self) -> None:
        plugins = self.home / "plugins"
        init = self.home / "init.groovy.d"
        private = self.home / ".g7-private"
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        for path in (plugins, init, private):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        for plugin in self.lock["plugins"]:
            body = download_verified(plugin["url"], plugin["sha256"])
            write_private(plugins / f"{plugin['name']}.hpi", body, mode=0o644, exclusive=True)
            write_private(plugins / f"{plugin['name']}.jpi.pinned", plugin["version"].encode(), mode=0o644, exclusive=True)
        groovy = r"""
import hudson.model.Item
import hudson.model.Computer
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.GlobalMatrixAuthorizationStrategy
import hudson.security.csrf.DefaultCrumbIssuer
import jenkins.model.Jenkins
import jenkins.model.JenkinsLocationConfiguration
import org.jenkinsci.plugins.matrixauth.PermissionEntry

def pw = { String name ->
  new File("/run/kiln-g7-auth/" + name + ".pw").getText("UTF-8").trim()
}
def instance = Jenkins.instance
instance.setNumExecutors(0)
instance.setSlaveAgentPort(-1)
instance.setQuietPeriod(0)
instance.setNoUsageStatistics(true)
def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount("feas-admin", pw("feas-admin"))
realm.createAccount("feas-lifecycle", pw("feas-lifecycle"))
realm.createAccount("feas-build", pw("feas-build"))
instance.setSecurityRealm(realm)
def strategy = new GlobalMatrixAuthorizationStrategy()
strategy.add(Jenkins.ADMINISTER, PermissionEntry.user("feas-admin"))
strategy.add(Jenkins.READ, PermissionEntry.user("feas-admin"))
strategy.add(Jenkins.READ, PermissionEntry.user("feas-lifecycle"))
strategy.add(Jenkins.READ, PermissionEntry.user("feas-build"))
strategy.add(Computer.CREATE, PermissionEntry.user("feas-lifecycle"))
strategy.add(Computer.CONFIGURE, PermissionEntry.user("feas-lifecycle"))
strategy.add(Computer.DELETE, PermissionEntry.user("feas-lifecycle"))
strategy.add(Computer.DISCONNECT, PermissionEntry.user("feas-lifecycle"))
strategy.add(Computer.BUILD, PermissionEntry.user("feas-build"))
strategy.add(Item.CREATE, PermissionEntry.user("feas-build"))
strategy.add(Item.READ, PermissionEntry.user("feas-build"))
strategy.add(Item.BUILD, PermissionEntry.user("feas-build"))
strategy.add(Item.CONFIGURE, PermissionEntry.user("feas-build"))
strategy.add(Item.CANCEL, PermissionEntry.user("feas-build"))
strategy.add(Item.DISCOVER, PermissionEntry.user("feas-build"))
instance.setAuthorizationStrategy(strategy)
instance.setCrumbIssuer(new DefaultCrumbIssuer(true))
JenkinsLocationConfiguration.get().setUrl("http://127.0.0.1:8080/")
instance.save()
println("g7-auth-ready")
"""
        write_private(init / "g7-auth.groovy", groovy.strip().encode() + b"\n", mode=0o600)
        etc = self.private / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        write_private(etc / "hosts", b"127.0.0.1 localhost\n::1 localhost\n", mode=0o644)
        write_private(etc / "resolv.conf", b"# kiln-g7 closed namespace: no external resolver\n", mode=0o644)
        write_private(etc / "hostname-controller", b"kiln-g7-controller\n", mode=0o644)
        write_private(etc / "hostname-agent", b"kiln-g7-agent\n", mode=0o644)

    def common_mounts(self, hostname: Path) -> list[str]:
        etc = self.private / "etc"
        return [
            f"type=bind,src={etc / 'hosts'},dst=/etc/hosts,ro",
            f"type=bind,src={etc / 'resolv.conf'},dst=/etc/resolv.conf,ro",
            f"type=bind,src={hostname},dst=/etc/hostname,ro",
        ]

    def start_controller(self) -> None:
        cmd = self.podman_base(
            self.controller,
            self.controller_image,
            "none",
            "keep-id:uid=1000,gid=1000",
        )
        cmd += [
            "-e",
            "JENKINS_SLAVE_AGENT_PORT=-1",
            "-e",
            "JAVA_OPTS=-Djenkins.install.runSetupWizard=false -Dhudson.model.UpdateCenter.never=true -Xms256m -Xmx768m",
            "-e",
            "JENKINS_OPTS=--httpListenAddress=127.0.0.1",
            "--mount",
            f"type=bind,src={self.home},dst=/var/jenkins_home",
            "--mount",
            f"type=bind,src={self.private / 'auth'},dst=/run/kiln-g7-auth,ro",
        ]
        for mount in self.common_mounts(self.private / "etc" / "hostname-controller"):
            cmd += ["--mount", mount]
        cmd.append(self.controller_image)
        proc = self.run(cmd, 60)
        if proc.returncode != 0:
            raise GateError("controller start failed: " + tail(self.guard, proc.stderr))

    def start_java(self, name: str, image_args: list[str], network: str, userns: str, mounts: list[str]) -> None:
        # 1536 MiB stays bounded and sits above the 1 GiB TemporarySpaceMonitor default.
        cmd = self.podman_base(name, self.inbound_image, network, userns, tmp_size=1610612736)
        cmd += ["-e", "HOME=/home/jenkins/agent"]
        for mount in mounts:
            cmd += ["--mount", mount]
        for mount in self.common_mounts(self.private / "etc" / "hostname-agent"):
            cmd += ["--mount", mount]
        cmd += ["--entrypoint", self.java_elf, self.inbound_image]
        cmd += image_args
        proc = self.run(cmd, 60)
        if proc.returncode != 0:
            raise GateError(f"{name} start failed: " + tail(self.guard, proc.stderr))

    def exec(self, name: str, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[bytes]:
        return self.run(["podman", "exec", name] + args, timeout)

    def http(
        self,
        path: str,
        user: str | None = None,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str | None = None,
        use_crumb: bool = False,
    ) -> tuple[int, bytes]:
        account = user or "anonymous"
        jar = f"/var/jenkins_home/.g7-private/cookies-{account}"
        lines = [
            "silent",
            "show-error",
            "globoff",
            "noproxy = \"*\"",
            "max-time = 20",
            'output = "/var/jenkins_home/.g7-private/resp-body"',
            'write-out = "%{http_code}"',
            f'cookie = "{jar}"',
            f'cookie-jar = "{jar}"',
            f'url = "http://127.0.0.1:8080{path}"',
        ]
        if user:
            lines.append(f'netrc-file = "/run/kiln-g7-auth/{user}.netrc"')
        if method != "GET":
            lines.append(f'request = "{method}"')
        if content_type:
            lines.append(f'header = "Content-Type: {content_type}"')
        if use_crumb:
            crumb = self.crumbs[user or ""]
            if any(char in crumb for char in '\n\r"'):
                raise GateError("crumb has unexpected characters")
            lines.append(f'header = "Jenkins-Crumb: {crumb}"')
        private = self.home / ".g7-private"
        if body is not None:
            write_private(private / "req-body", body)
            lines.append('data-binary = "@/var/jenkins_home/.g7-private/req-body"')
        write_private(private / "curl.cfg", ("\n".join(lines) + "\n").encode())
        try:
            proc = self.exec(
                self.controller,
                direct_curl_argv(["--config", "/var/jenkins_home/.g7-private/curl.cfg"]),
                30,
            )
            code_text = proc.stdout.decode().strip()
            code = int(code_text) if code_text.isdigit() else 0
            resp_path = private / "resp-body"
            payload = resp_path.read_bytes() if resp_path.exists() else b""
            if proc.returncode != 0 and code == 0:
                raise GateError("controller HTTP failed: " + tail(self.guard, proc.stderr))
            return code, payload
        finally:
            for name in ("curl.cfg", "req-body", "resp-body"):
                destroy(private / name)

    def load_crumb(self, user: str) -> None:
        code, body = self.http("/crumbIssuer/api/json", user=user)
        if code != 200:
            raise GateError(f"crumb request for {user} returned {code}")
        payload = json.loads(body)
        if payload.get("crumbRequestField") != "Jenkins-Crumb":
            raise GateError(f"crumb field is {payload.get('crumbRequestField')}")
        crumb = str(payload.get("crumb") or "")
        self.guard.add(crumb)
        self.crumbs[user] = crumb

    def wait_ready(self) -> None:
        deadline = time.time() + 180
        while time.time() < deadline:
            proc = self.exec(
                self.controller,
                direct_curl_argv(
                    [
                        "--silent",
                        "--show-error",
                        "--max-time",
                        "5",
                        "-o",
                        "/dev/null",
                        "-w",
                        "%{http_code}",
                        "http://127.0.0.1:8080/login",
                    ]
                ),
                15,
            )
            if proc.stdout.decode().strip() == "200":
                return
            time.sleep(2)
        logs = self.run(["podman", "logs", self.controller], 20)
        raise GateError("controller login did not become ready: " + tail(self.guard, logs.stdout + logs.stderr))

    def wait_security(self) -> None:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                code, _body = self.http("/api/json")
            except GateError:
                time.sleep(2)
                continue
            if code == 403:
                try:
                    for user in ("feas-admin", "feas-lifecycle", "feas-build"):
                        self.load_crumb(user)
                    return
                except GateError:
                    time.sleep(2)
                    continue
            time.sleep(2)
        raise GateError("controller security did not become ready")

    def read_proc_surface(self, name: str, pid: str) -> bytes:
        cmdline = self.exec(name, ["cat", f"/proc/{pid}/cmdline"], 10)
        environ = self.exec(name, ["cat", f"/proc/{pid}/environ"], 10)
        if cmdline.returncode != 0 or environ.returncode != 0:
            raise GateError(f"unreadable process surface pid={pid}")
        return cmdline.stdout + b"\n" + environ.stdout

    def child_surfaces(self, name: str, secret: str) -> dict:
        script = r"""
probe=$$
for dir in /proc/[0-9]*; do
  pid=${dir##*/}
  [ "$pid" = "$probe" ] && continue
  [ "$pid" = 1 ] && continue
  ppid=$(sed -n 's/^PPid:[[:space:]]*//p' "$dir/status") || continue
  [ -n "$ppid" ] || continue
  [ "$ppid" = "$probe" ] && continue
  parent=$(sed -n 's/^Name:[[:space:]]*//p' "/proc/${ppid}/status") || continue
  case "$parent" in
    java|sh|bash) ;;
    *) continue ;;
  esac
  pname=$(sed -n 's/^Name:[[:space:]]*//p' "$dir/status") || continue
  case "$pname" in
    sh|bash|sleep|tee) ;;
    *) continue ;;
  esac
  printf 'CHILD %s %s %s\n' "$pid" "$ppid" "$pname"
  printf 'CMDLINE '
  base64 -w 0 < "$dir/cmdline" || { printf '\nREADFAIL %s\n' "$pid"; exit 0; }
  printf '\nENVIRON '
  base64 -w 0 < "$dir/environ" || { printf '\nREADFAIL %s\n' "$pid"; exit 0; }
  printf '\n'
done
"""
        proc = self.exec(name, ["sh", "-c", script], 15)
        if proc.returncode != 0:
            raise GateError("build-child scan failed")
        text = proc.stdout.decode("utf-8", "replace")
        if "READFAIL " in text:
            raise GateError("build-child surface read failed")
        child_pids = []
        secret_present = False
        import base64

        for block in text.split("CHILD ")[1:]:
            header, _, rest = block.partition("\n")
            pid_text = header.split()[0]
            if not pid_text.isdigit():
                raise GateError("build-child scan returned an invalid pid")
            child_pids.append(int(pid_text))
            if "CMDLINE " not in rest or "\nENVIRON " not in rest:
                raise GateError("build-child surface was incomplete")
            cmdline_b64, _, after = rest.partition("\nENVIRON ")
            cmdline_b64 = cmdline_b64.removeprefix("CMDLINE ").strip()
            environ_b64 = after.split("\n", 1)[0].strip()
            try:
                surface = base64.b64decode(cmdline_b64, validate=True) + b"\n" + base64.b64decode(environ_b64, validate=True)
            except ValueError as exc:
                raise GateError("build-child surface decode failed") from exc
            if secret.encode() in surface:
                secret_present = True
        return {"child_pids": child_pids, "secret_present": secret_present}

    def logs_blob(self, name: str) -> bytes:
        proc = self.run(["podman", "logs", name], 20)
        if proc.returncode != 0:
            raise GateError(f"log read failed for {name}")
        return proc.stdout + proc.stderr

    def file_stat(self, name: str, path: str) -> str:
        proc = self.exec(name, ["stat", "-c", "%u %a %F", path], 15)
        if proc.returncode != 0:
            raise GateError(f"stat failed for {path}")
        return proc.stdout.decode().strip()

    def cleanup_fail(self) -> None:
        import secrets

        secret = secrets.token_hex(32)
        self.guard.add(secret)
        self.create_node("kiln-cleanup-fail", "/home/jenkins/agent-cleanup", "kiln-cleanup-slot")
        directory = self.private / "cleanup-args"
        work = self.private / "work-cleanup"
        work.mkdir(parents=True, exist_ok=True)
        os.chmod(work, 0o700)
        path = directory / "agent.argv"
        try:
            write_private(
                path,
                self.argfile_tokens(secret, "kiln-cleanup-fail", "http://127.0.0.1:8080/").replace(
                    b"/home/jenkins/agent\n",
                    b"/home/jenkins/agent-cleanup\n",
                ),
                exclusive=True,
            )
            mounts = [
                f"type=bind,src={directory},dst=/run/kiln-g7-args,ro",
                f"type=bind,src={work},dst=/home/jenkins/agent-cleanup",
            ]
            self.start_java(
                self.cleanup_name,
                ["@/run/kiln-g7-args/agent.argv"],
                "container:kiln-g7-controller",
                "container:kiln-g7-controller",
                mounts,
            )
            visible = False
            deadline = time.time() + 15
            while time.time() < deadline:
                exists = self.exec(self.cleanup_name, ["test", "-f", "/run/kiln-g7-args/agent.argv"], 10)
                cmdline = self.exec(self.cleanup_name, ["cat", "/proc/1/cmdline"], 10)
                parts = [item for item in cmdline.stdout.split(b"\0") if item]
                if exists.returncode == 0 and len(parts) == 2 and parts[1] == b"@/run/kiln-g7-args/agent.argv":
                    visible = True
                    break
                time.sleep(1)
            time.sleep(8)
            code, body = self.http("/computer/kiln-cleanup-fail/api/json?tree=offline", user="feas-admin")
            offline = code == 200 and json.loads(body).get("offline") is True
            secret_in_cmd = secret.encode() in cmdline.stdout
            destroy(path)
            inside = self.exec(self.cleanup_name, ["test", "!", "-e", "/run/kiln-g7-args/agent.argv"], 10)
            code_after, body_after = self.http("/computer/kiln-cleanup-fail/api/json?tree=offline", user="feas-admin")
            still_offline = code_after == 200 and json.loads(body_after).get("offline") is True
            gone = (not path.exists()) and inside.returncode == 0
            if not visible or not gone or not offline or not still_offline or secret_in_cmd:
                raise GateError(
                    f"cleanup visible={visible} gone={gone} offline={offline} still_offline={still_offline} secret_in_cmdline={secret_in_cmd}"
                )
        finally:
            destroy(path)
            removed = self.run(["podman", "rm", "-f", self.cleanup_name], 20)
            if removed.returncode != 0:
                raise GateError("cleanup container removal failed")
        self.delete_node("kiln-cleanup-fail")

    def rootfs(self, name: str, allow: set[str], home_probe: str) -> dict:
        proc = self.exec(name, ["cat", "/proc/mounts"], 15)
        mounts = parse_mounts(proc.stdout.decode())
        bad = unexpected_writable(mounts, allow)
        inspect = self.run(
            [
                "podman",
                "inspect",
                "--format",
                "{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.Privileged}} {{.HostConfig.NetworkMode}} {{.Config.User}}",
                name,
            ],
            20,
        )
        fields = inspect.stdout.decode().strip().split()
        ro_probe = self.exec(name, ["sh", "-c", "touch /kiln-g7-ro-probe"], 10)
        tmp_probe = self.exec(name, ["sh", "-c", "touch /tmp/kiln-g7-probe && rm -f /tmp/kiln-g7-probe"], 10)
        home = self.exec(name, ["sh", "-c", f"touch {home_probe} && rm -f {home_probe}"], 10)
        uid = self.exec(name, ["id", "-u"], 10).stdout.decode().strip()
        uid_map = self.exec(name, ["cat", "/proc/self/uid_map"], 10).stdout.decode().strip()
        network = fields[2] if len(fields) > 2 else ""
        ok = (
            not bad
            and len(fields) >= 2
            and fields[0] == "true"
            and fields[1] == "false"
            and (network == "none" or network.startswith("container:"))
            and ro_probe.returncode != 0
            and tmp_probe.returncode == 0
            and home.returncode == 0
            and uid == "1000"
        )
        return {
            "ok": ok,
            "bad": bad,
            "inspect": fields,
            "uid": uid,
            "uid_map": uid_map,
            "mounts": [{"dest": item["dest"], "fstype": item["fstype"], "rw": item["rw"]} for item in mounts],
        }

    def direct_curl(self, name: str, args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        return self.exec(name, direct_curl_argv(args), timeout)

    def connect_negative(self, name: str, target: str) -> dict:
        proc = self.direct_curl(
            name,
            [
                "--verbose",
                "--connect-timeout",
                "2",
                "--max-time",
                "3",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                target,
            ],
            10,
        )
        return classify_connect(
            proc.returncode,
            proc.stdout.decode().strip(),
            proc.stderr.decode("utf-8", "replace"),
            True,
        )

    def network_sample(
        self,
        name: str,
        listener: OutsideListener,
        listener6: OutsideListener,
        gateway: str,
    ) -> dict:
        host_before = listener.host_ok()
        inside = self.connect_negative(name, f"http://{listener.ip}:{listener.port}/")
        host_after = listener.host_ok()
        host6_before = listener6.host_ok()
        inside6 = self.connect_negative(name, f"http://[{listener6.ip}]:{listener6.port}/")
        host6_after = listener6.host_ok()
        local = self.direct_curl(
            name,
            ["--silent", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1:8080/login"],
            15,
        )
        ws = self.direct_curl(
            name,
            [
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "--include",
                "--http1.1",
                "-H",
                "Connection: Upgrade",
                "-H",
                "Upgrade: websocket",
                "-H",
                "Sec-WebSocket-Version: 13",
                "-H",
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
                "http://127.0.0.1:8080/wsagents/",
            ],
            15,
        )
        ws_text = ws.stdout.decode("utf-8", "replace")
        ws_status = ws_text.splitlines()[0].strip() if ws_text else ""
        ws_sentence = "This endpoint is only for use from agent.jar in WebSocket mode"
        tcp_proc = self.exec(name, ["cat", "/proc/net/tcp"], 10)
        tcp6_proc = self.exec(name, ["cat", "/proc/net/tcp6"], 10)
        route_proc = self.exec(name, ["cat", "/proc/net/route"], 10)
        route6_proc = self.exec(name, ["cat", "/proc/net/ipv6_route"], 10)
        resolv_proc = self.exec(name, ["cat", "/etc/resolv.conf"], 10)
        dev_proc = self.exec(name, ["ls", "/sys/class/net"], 10)
        route_text = route_proc.stdout.decode()
        route6_text = route6_proc.stdout.decode()
        route_read_ok = route_proc.returncode == 0 and route_text.startswith("Iface")
        route6_read_ok = route6_proc.returncode == 0
        resolv_read_ok = resolv_proc.returncode == 0
        devs = dev_proc.stdout.decode().split() if dev_proc.returncode == 0 else []
        gateway_hex = "".join(f"{int(part):02X}" for part in reversed(gateway.split(".")))
        gateway_present = route_read_ok and any(
            len(line.split()) > 2 and line.split()[2] == gateway_hex for line in route_text.splitlines()[1:]
        )
        foreign4 = [
            line.split()[0]
            for line in route_text.splitlines()[1:]
            if line.split() and line.split()[0] != "lo"
        ] if route_read_ok else ["unreadable"]
        foreign6 = []
        if route6_read_ok:
            for line in route6_text.splitlines():
                parts = line.split()
                if parts and parts[-1] != "lo":
                    foreign6.append(parts[-1])
        else:
            foreign6 = ["unreadable"]
        resolv = resolv_proc.stdout.decode() if resolv_read_ok else ""
        return {
            "host_positive": host_before and host_after,
            "inside_negative": inside["isolated"],
            "inside_negative_detail": {
                "returncode": inside["returncode"],
                "http_code": inside["http_code"],
                "reason": inside["reason"],
                "direct": inside["direct"],
                "proxy": inside["proxy"],
            },
            "ipv6_host_positive": host6_before and host6_after,
            "ipv6_inside_negative": inside6["isolated"],
            "ipv6_negative_detail": {
                "returncode": inside6["returncode"],
                "http_code": inside6["http_code"],
                "reason": inside6["reason"],
                "direct": inside6["direct"],
                "proxy": inside6["proxy"],
            },
            "positive_direct": True,
            "gateway_absent": route_read_ok and not gateway_present,
            "local_http": local.stdout.decode().strip() if local.returncode == 0 else "",
            "websocket_status": ws_status[:80],
            "websocket_endpoint": ws.returncode == 0 and ws_status.startswith("HTTP/1.1 400") and ws_sentence in ws_text,
            "ports": sorted(listen_ports(tcp_proc.stdout.decode()) | listen_ports(tcp6_proc.stdout.decode()))
            if tcp_proc.returncode == 0 and tcp6_proc.returncode == 0
            else [],
            "listeners_read_ok": tcp_proc.returncode == 0 and tcp6_proc.returncode == 0,
            "interfaces": devs,
            "interfaces_read_ok": dev_proc.returncode == 0,
            "ipv4_default": ipv4_default(route_text) if route_read_ok else True,
            "ipv6_default": ipv6_default(route6_text) if route6_read_ok else True,
            "route_read_ok": route_read_ok,
            "route6_read_ok": route6_read_ok,
            "resolv_read_ok": resolv_read_ok,
            "foreign_ipv4": foreign4,
            "foreign_ipv6": foreign6,
            "nameserver": (not resolv_read_ok) or ("nameserver" in resolv),
        }

    def network_gap(self, sample: dict) -> str:
        failed = []
        for name in (
            "host_positive",
            "inside_negative",
            "ipv6_host_positive",
            "ipv6_inside_negative",
            "gateway_absent",
            "route_read_ok",
            "route6_read_ok",
            "resolv_read_ok",
            "listeners_read_ok",
            "interfaces_read_ok",
            "websocket_endpoint",
            "positive_direct",
        ):
            if not sample[name]:
                failed.append(name)
        for label, detail in (("inside", sample["inside_negative_detail"]), ("ipv6", sample["ipv6_negative_detail"])):
            if not detail.get("direct"):
                failed.append(f"{label}_direct")
            if detail.get("proxy"):
                failed.append(f"{label}_proxy")
        if sample["local_http"] != "200":
            failed.append("local_http")
        if sample["websocket_status"].startswith(("HTTP/1.1 403", "HTTP/1.1 404")):
            failed.append("websocket_forbidden")
        if sample["interfaces"] != ["lo"]:
            failed.append("interfaces")
        if sample["foreign_ipv4"] or sample["foreign_ipv6"]:
            failed.append("foreign_route")
        if sample["ipv4_default"] or sample["ipv6_default"]:
            failed.append("default_route")
        if sample["nameserver"]:
            failed.append("nameserver")
        if sample["ports"] != [8080]:
            failed.append("ports")
        return json.dumps(
            {
                "failed": failed,
                "inside": sample["inside_negative_detail"],
                "inside6": sample["ipv6_negative_detail"],
                "local_http": sample["local_http"],
                "websocket_status": sample["websocket_status"],
                "interfaces": sample["interfaces"],
                "ports": sample["ports"],
                "foreign_ipv4": sample["foreign_ipv4"],
                "foreign_ipv6": sample["foreign_ipv6"],
            },
            sort_keys=True,
        )

    def safe_network(self, sample: dict) -> bool:
        return bool(
            sample["host_positive"]
            and sample["inside_negative"]
            and sample["inside_negative_detail"].get("direct") is True
            and sample["inside_negative_detail"].get("proxy") is False
            and sample["ipv6_host_positive"]
            and sample["ipv6_inside_negative"]
            and sample["ipv6_negative_detail"].get("direct") is True
            and sample["ipv6_negative_detail"].get("proxy") is False
            and sample["positive_direct"]
            and sample["gateway_absent"]
            and sample["route_read_ok"]
            and sample["route6_read_ok"]
            and sample["resolv_read_ok"]
            and sample["listeners_read_ok"]
            and sample["interfaces_read_ok"]
            and sample["local_http"] == "200"
            and sample["websocket_endpoint"]
            and not sample["websocket_status"].startswith(("HTTP/1.1 403", "HTTP/1.1 404"))
            and sample["interfaces"] == ["lo"]
            and not sample["foreign_ipv4"]
            and not sample["foreign_ipv6"]
            and not sample["ipv4_default"]
            and not sample["ipv6_default"]
            and not sample["nameserver"]
            and sample["ports"] == [8080]
        )

    def node_xml(self, name: str, remote_fs: str, label: str) -> bytes:
        return f"""<?xml version='1.1' encoding='UTF-8'?>
<slave>
  <name>{name}</name>
  <description>kiln g7 disposable fixture</description>
  <remoteFS>{remote_fs}</remoteFS>
  <numExecutors>1</numExecutors>
  <mode>EXCLUSIVE</mode>
  <retentionStrategy class="hudson.slaves.RetentionStrategy$Always"/>
  <launcher class="hudson.slaves.JNLPLauncher">
    <webSocket>true</webSocket>
    <workDirSettings>
      <disabled>false</disabled>
      <internalDir>remoting</internalDir>
      <failIfWorkDirIsMissing>false</failIfWorkDirIsMissing>
    </workDirSettings>
  </launcher>
  <label>{label}</label>
  <nodeProperties/>
</slave>
""".encode()

    def create_node(self, name: str, remote_fs: str, label: str) -> None:
        code, body = self.http(
            f"/computer/createItem?name={name}",
            user="feas-lifecycle",
            method="POST",
            body=self.node_xml(name, remote_fs, label),
            content_type="application/xml",
            use_crumb=True,
        )
        if code not in {200, 201, 302, 303}:
            raise GateError(f"create {name} returned {code}: " + tail(self.guard, body))

    def jnlp_secret(self, name: str) -> str:
        code, body = self.http(f"/computer/{name}/jenkins-agent.jnlp", user="feas-lifecycle")
        if code != 200:
            raise GateError(f"jnlp for {name} returned {code}")
        write_private(self.private / "jnlp" / f"{name}.jnlp", body)
        text = body.decode("utf-8", "replace")
        self.guard.assert_clean("jnlp-log-guard", "")
        args = re.findall(r"<argument>([^<]*)</argument>", text)
        if not args or not re.fullmatch(r"[0-9a-f]{32,}", args[0].strip()):
            raise GateError(f"jnlp secret pattern mismatch len={len(args[0]) if args else 0}")
        secret = args[0].strip()
        self.guard.add(secret)
        return secret

    def delete_node(self, name: str) -> None:
        self.http(f"/computer/{name}/doDelete", user="feas-lifecycle", method="POST", use_crumb=True)

    def freestyle(self, command: str, assigned: str | None, roam: bool, archive: bool) -> bytes:
        node = f"<assignedNode>{assigned}</assignedNode>" if assigned else ""
        publishers = ""
        if archive:
            publishers = """
  <publishers>
    <hudson.tasks.ArtifactArchiver>
      <artifacts>marker.txt</artifacts>
      <allowEmptyArchive>false</allowEmptyArchive>
    </hudson.tasks.ArtifactArchiver>
  </publishers>"""
        else:
            publishers = "<publishers/>"
        xml = f"""<?xml version='1.1' encoding='UTF-8'?>
<project>
  <actions/>
  <description>kiln g7 fixture</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <scm class="hudson.scm.NullSCM"/>
  <canRoam>{"true" if roam else "false"}</canRoam>
  <disabled>false</disabled>
  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>
  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>
  <triggers/>
  <concurrentBuild>false</concurrentBuild>
  {node}
  <builders>
    <hudson.tasks.Shell>
      <command>{command}</command>
    </hudson.tasks.Shell>
  </builders>
  {publishers}
  <buildWrappers/>
</project>
"""
        return xml.encode()

    def create_job(self, name: str, xml: bytes) -> None:
        code, body = self.http(
            f"/createItem?name={name}",
            user="feas-admin",
            method="POST",
            body=xml,
            content_type="application/xml",
            use_crumb=True,
        )
        if code not in {200, 201, 302, 303}:
            raise GateError(f"create job {name} returned {code}: " + tail(self.guard, body))

    def trigger(self, name: str) -> None:
        code, _body = self.http(f"/job/{name}/build", user="feas-build", method="POST", use_crumb=True)
        if code not in {200, 201, 302, 303}:
            raise GateError(f"trigger {name} returned {code}")

    def emit_observation(self, phase: str) -> None:
        if phase not in OBS_PHASES:
            return
        try:
            if self._obs_reap_unconfirmed:
                fields = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "capture": "failed",
                    "ambiguous": False,
                    "events_capped": False,
                    "events": [],
                    "cleanup": "error" if self._obs_cleanup_error else "ok",
                    "reap": "unconfirmed",
                }
            else:
                fields = obs_capture(self.agent, self._obs_live)
                if fields["reap"] == "unconfirmed":
                    self._obs_reap_unconfirmed = True
                if fields["cleanup"] == "error":
                    self._obs_cleanup_error = True
            text = obs_format_line(phase, fields)
        except Exception:
            self._obs_cleanup_error = True
            text = obs_format_line(
                phase,
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "capture": "failed",
                    "ambiguous": True,
                    "events_capped": False,
                    "events": [],
                    "cleanup": "error",
                    "reap": "unconfirmed" if self._obs_reap_unconfirmed else "confirmed",
                },
            )
        if not obs_safe_emit(lambda line: print(line, flush=True), text):
            self._obs_emit_failed = True
        if phase in {"BEFORE_EXACT", "AFTER_EXACT", "EXTRA_TIMEOUT"}:
            try:
                status = self._node_status_line(phase)
            except Exception:
                status = f"G7-NODE phase={phase} read=failed"
            obs_safe_emit(lambda line: print(line, flush=True), status)

    def _node_status_line(self, phase: str) -> str:
        code, body = self.http(
            "/computer/kiln-managed-1/api/json?tree=offline,temporarilyOffline,idle,numExecutors,"
            "offlineCause[_class,timestamp,size,totalSize,threshold,path],offlineCauseReason",
            user="feas-admin",
        )
        if code != 200:
            return f"G7-NODE phase={phase} read=failed"
        return obs_node_status_line(phase, json.loads(body))

    def _finish_obs(self, original: BaseException | None) -> None:
        exc = obs_resolve(original, self._obs_reap_unconfirmed, self._obs_emit_failed)
        if exc is not None:
            raise exc

    def _obs_teardown_fallback(self) -> None:
        try:
            for proc in list(self._obs_live):
                try:
                    cleanup_error, reaped, _returncode = obs_close_kill_wait(proc)
                except Exception:
                    cleanup_error, reaped = True, False
                if cleanup_error:
                    self._obs_cleanup_error = True
                if reaped:
                    try:
                        self._obs_live.remove(proc)
                    except ValueError:
                        pass
                else:
                    self._obs_reap_unconfirmed = True
        except Exception:
            self._obs_cleanup_error = True
            self._obs_reap_unconfirmed = True

    def wait_build(self, name: str, secret: str) -> tuple[dict, dict]:
        deadline = time.time() + 90
        saw_child_pid = False
        secret_seen = False
        read_failed = False
        child_pids: list[int] = []
        while time.time() < deadline:
            code, body = self.http(f"/job/{name}/lastBuild/api/json?tree=building,result,builtOn,number", user="feas-admin")
            if code == 200:
                data = json.loads(body)
                if data.get("building") is True:
                    try:
                        sample = self.child_surfaces(self.agent, secret)
                    except GateError:
                        read_failed = True
                    else:
                        if sample["child_pids"]:
                            saw_child_pid = True
                            child_pids = sample["child_pids"]
                        if sample["secret_present"]:
                            secret_seen = True
                if data.get("building") is False and data.get("result"):
                    return data, {
                        "saw_child_pid": saw_child_pid,
                        "child_pids": child_pids,
                        "secret_seen": secret_seen,
                        "read_failed": read_failed,
                        "clean": saw_child_pid and not secret_seen and not read_failed,
                    }
            time.sleep(1)
        message = f"{name} timed out {self.schedule_class(name)}"
        if name == "kiln-g7-extra":
            self._extra_wait_timed_out = True
        raise GateError(message)

    def schedule_class(self, name: str) -> str:
        code, body = self.http("/computer/kiln-managed-1/api/json?tree=offline,idle,numExecutors", user="feas-admin")
        offline = None
        idle = None
        executors = None
        if code == 200:
            data = json.loads(body)
            offline = data.get("offline")
            idle = data.get("idle")
            executors = data.get("numExecutors")
        queue_code, queue_body = self.http("/queue/api/json?tree=items[why,task[name]]", user="feas-admin")
        kind = "unread"
        if queue_code == 200:
            kind = "empty"
            for item in json.loads(queue_body).get("items") or []:
                task = item.get("task") or {}
                if task.get("name") != name:
                    continue
                why = str(item.get("why") or "").lower()
                if "offline" in why:
                    kind = "offline"
                elif "quiet" in why:
                    kind = "quiet"
                elif "executor" in why:
                    kind = "executor"
                elif "reserved" in why:
                    kind = "reserved"
                elif "label" in why:
                    kind = "label"
                elif why:
                    kind = "other"
                else:
                    kind = "blank"
                break
        agent = self.agent_state()
        cause = self.offline_class()
        return (
            f"queue={kind} offline={offline} idle={idle} executors={executors} "
            f"node_http={code} queue_http={queue_code} cause={cause} {agent}"
        )

    def agent_state(self) -> str:
        proc = self.run(
            ["podman", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}", self.agent],
            20,
        )
        if proc.returncode != 0:
            return "agent=inspect_failed"
        parts = proc.stdout.decode().split()
        if len(parts) < 3:
            return "agent=inspect_bad"
        status, exit_code, oom = parts[0], parts[1], parts[2]
        java = "unread"
        if status == "running":
            cmd = self.exec(self.agent, ["cat", "/proc/1/cmdline"], 10)
            java = "yes" if cmd.returncode == 0 and b"java" in cmd.stdout else "no"
        return f"agent={status} exit={exit_code} oom={oom} java={java}"

    def artifact(self, name: str) -> bytes:
        code, body = self.http(f"/job/{name}/lastSuccessfulBuild/artifact/marker.txt", user="feas-admin")
        if code != 200:
            raise GateError(f"artifact {name} returned {code}")
        return body

    def workspace_marker(self, job: str, marker: bytes) -> bool:
        script = (
            "for f in /home/jenkins/agent/workspace/*/marker.txt; do "
            'test -f "$f" || continue; case "$f" in *'
            + job
            + '*) printf "%s" "$f"; exit 0;; esac; done; exit 1'
        )
        found = self.exec(self.agent, ["sh", "-c", script], 15)
        if found.returncode != 0 or not found.stdout:
            return False
        content = self.exec(self.agent, ["cat", found.stdout.decode()], 10)
        return content.returncode == 0 and content.stdout.strip() == marker

    def api_checks(self, secret_saved: bool) -> None:
        code, _body = self.http("/api/json", user=None)
        if code != 403:
            raise GateError(f"anonymous api returned {code}")
        for user in ("feas-admin", "feas-lifecycle", "feas-build"):
            self.load_crumb(user)
        # A literal plus is a space in this content type, so the expression must be percent-encoded.
        script_form = b"script=println%281%2B1%29"
        no_crumb, no_body = self.http(
            "/scriptText",
            user="feas-admin",
            method="POST",
            body=script_form,
            content_type="application/x-www-form-urlencoded",
            use_crumb=False,
        )
        if no_crumb != 403 or b"No valid crumb was included in the request" not in no_body:
            raise GateError(f"missing crumb returned {no_crumb}")
        admin, admin_body = self.http(
            "/scriptText",
            user="feas-admin",
            method="POST",
            body=script_form,
            content_type="application/x-www-form-urlencoded",
            use_crumb=True,
        )
        if admin != 200 or admin_body.decode().strip() != "2":
            raise GateError(f"admin script status {admin} body_len {len(admin_body.strip())}")
        for user in ("feas-lifecycle", "feas-build"):
            status, payload = self.http(
                "/scriptText",
                user=user,
                method="POST",
                body=script_form,
                content_type="application/x-www-form-urlencoded",
                use_crumb=True,
            )
            if status != 403 or payload.decode().strip() == "2":
                raise GateError(f"{user} script returned {status}")
        probe_name = "kiln-g7-form-probe"
        form, form_body = self.http(
            f"/computer/doCreateItem?name={probe_name}",
            user="feas-lifecycle",
            method="POST",
            body=b"<slave></slave>",
            content_type="application/xml",
            use_crumb=True,
        )
        # The owned name passes checkName. It is a query parameter, so the parameter map is not
        # empty, and the XML body is not the json form field. Stapler then refuses the form.
        if b"This page expects a form submission" in form_body:
            rejection = "expects_form"
        elif b"Nothing is submitted" in form_body:
            rejection = "nothing_submitted"
        elif b"Query parameter 'name' is required" in form_body:
            rejection = "name_required"
        else:
            rejection = "other"
        if form != 400 or rejection != "expects_form":
            raise GateError(f"doCreateItem status {form} rejection {rejection} body_len {len(form_body)}")
        absent, _ = self.http(f"/computer/{probe_name}/api/json", user="feas-lifecycle")
        if absent != 404:
            raise GateError(f"form probe name status {absent}")
        code, body = self.http("/computer/kiln-managed-1/config.xml", user="feas-lifecycle")
        if code != 200:
            raise GateError(f"config.xml returned {code}")
        root = ET.fromstring(body)
        mode = root.findtext("mode")
        executors = root.findtext("numExecutors")
        if mode != "EXCLUSIVE" or executors != "1":
            raise GateError(f"parsed config mode={mode} executors={executors}")
        posted, _ = self.http(
            "/computer/kiln-managed-1/config.xml",
            user="feas-lifecycle",
            method="POST",
            body=body,
            content_type="application/xml",
            use_crumb=True,
        )
        denied, _ = self.http(
            "/computer/kiln-managed-1/config.xml",
            user="feas-build",
            method="POST",
            body=body,
            content_type="application/xml",
            use_crumb=True,
        )
        if posted not in {200, 302} or denied != 403:
            raise GateError(f"config post lifecycle={posted} build={denied}")
        info_code, info = self.http(
            "/computer/kiln-managed-1/api/json?tree=displayName,numExecutors,offline,temporarilyOffline,assignedLabels[name]",
            user="feas-lifecycle",
        )
        labels = sorted(item["name"] for item in json.loads(info).get("assignedLabels", []))
        if info_code != 200 or json.loads(info).get("numExecutors") != 1 or labels != ["kiln-managed-1", "kiln-slot-jdk-maven"]:
            raise GateError(f"node json labels={labels}")
        build_jnlp, _ = self.http("/computer/kiln-managed-1/jenkins-agent.jnlp", user="feas-build")
        if build_jnlp != 403 or not secret_saved:
            raise GateError(f"build jnlp returned {build_jnlp}")

    def remoting_negative(self) -> str:
        self.create_node("kiln-remoting-neg", "/home/jenkins/agent-neg", "kiln-neg-slot")
        secret = self.jnlp_secret("kiln-remoting-neg")
        directory = self.private / "neg-args"
        work = self.private / "work-neg"
        work.mkdir(parents=True, exist_ok=True)
        os.chmod(work, 0o700)
        secret_path = directory / "secret"
        write_private(secret_path, secret.encode(), exclusive=True)
        mounts = [
            f"type=bind,src={directory},dst=/run/kiln-g7-neg,ro",
            f"type=bind,src={work},dst=/home/jenkins/agent-neg",
        ]
        self.start_java(
            self.neg,
            [
                "-jar",
                self.agent_jar,
                "-url",
                "http://127.0.0.1:8080/",
                "-secret",
                "@/run/kiln-g7-neg/secret",
                "-name",
                "kiln-remoting-neg",
                "-webSocket",
                "-workDir",
                "/home/jenkins/agent-neg",
            ],
            "container:kiln-g7-controller",
            "container:kiln-g7-controller",
            mounts,
        )
        time.sleep(20)
        cmdline = self.exec(self.neg, ["cat", "/proc/1/cmdline"], 10).stdout
        environ = self.exec(self.neg, ["cat", "/proc/1/environ"], 10).stdout
        parts = [item for item in cmdline.split(b"\0") if item]
        secret_present = secret.encode() in cmdline or secret.encode() in environ
        expected = [
            b"-jar",
            self.agent_jar.encode(),
            b"-url",
            b"http://127.0.0.1:8080/",
            b"-secret",
            b"@/run/kiln-g7-neg/secret",
            b"-name",
            b"kiln-remoting-neg",
            b"-webSocket",
            b"-workDir",
            b"/home/jenkins/agent-neg",
        ]
        shape_ok = len(parts) == 12 and parts[0].endswith(b"/bin/java") and parts[1:] == expected
        code, body = self.http(
            "/computer/kiln-remoting-neg/api/json?tree=offline",
            user="feas-admin",
        )
        offline = json.loads(body).get("offline") is True if code == 200 else False
        self.run(["podman", "rm", "-f", self.neg], 20)
        destroy(secret_path)
        self.delete_node("kiln-remoting-neg")
        if secret_present or not shape_ok or not offline:
            raise GateError(
                f"remoting negative secret_in_argv={secret_present} argc={len(parts)} shape_ok={shape_ok} offline={offline}"
            )
        return "pinned 3386 kept -secret @path in argv, stayed offline, and did not expose the secret"

    def argfile_tokens(self, secret: str, name: str, url: str) -> bytes:
        tokens = [
            "-jar",
            self.agent_jar,
            "-url",
            url,
            "-secret",
            secret,
            "-name",
            name,
            "-webSocket",
            "-workDir",
            "/home/jenkins/agent",
        ]
        return ("\n".join(tokens) + "\n").encode()

    def start_positive(self, secret: str) -> Path:
        directory = self.private / "args"
        work = self.private / "work"
        work.mkdir(parents=True, exist_ok=True)
        os.chmod(work, 0o700)
        path = directory / "agent.argv"
        write_private(path, self.argfile_tokens(secret, "kiln-managed-1", "http://127.0.0.1:8080/"), exclusive=True)
        mounts = [
            f"type=bind,src={directory},dst=/run/kiln-g7-args,ro",
            f"type=bind,src={work},dst=/home/jenkins/agent",
        ]
        self.start_java(
            self.agent,
            ["@/run/kiln-g7-args/agent.argv"],
            "container:kiln-g7-controller",
            "container:kiln-g7-controller",
            mounts,
        )
        return path

    def offline_class(self) -> str:
        try:
            code, body = self.http(
                "/computer/kiln-managed-1/api/json?tree=offline,offlineCause[_class],offlineCauseReason",
                user="feas-admin",
            )
            if code != 200:
                return f"http{code}"
            data = json.loads(body)
        except Exception:
            return "unknown"
        if not isinstance(data, dict) or data.get("offline") is not True:
            return "online"
        return f"{obs_cause_class(data.get('offlineCause'))}/{obs_reason_class(data.get('offlineCauseReason'))}"

    def wait_online(self, name: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, body = self.http(f"/computer/{name}/api/json?tree=offline", user="feas-admin")
            if code == 200 and json.loads(body).get("offline") is False:
                return True
            time.sleep(2)
        return False

    def surfaces_clean(self, name: str, secret: str) -> tuple[bool, int, bool]:
        surface = self.read_proc_surface(name, "1")
        cmdline = self.exec(name, ["cat", "/proc/1/cmdline"], 10)
        logs = self.logs_blob(name)
        if cmdline.returncode != 0:
            raise GateError("agent process surface read failed")
        parts = [item for item in cmdline.stdout.split(b"\0") if item]
        present = secret.encode() in surface or secret.encode() in logs
        argc_ok = len(parts) == 2 and parts[1] == b"@/run/kiln-g7-args/agent.argv"
        return (not present), len(parts), argc_ok

    def bind_spec(self, source: Path, destination: str, rw: bool, mode: int) -> dict:
        return {
            "destination": destination,
            "type": "bind",
            "rw": rw,
            "source": os.path.realpath(source),
            "mode": mode,
        }

    def tmpfs_spec(self, destination: str) -> dict:
        return {
            "destination": destination,
            "type": "tmpfs",
            "rw": True,
            "source": "",
            "mode": 0o1777,
            "container_uid": 0,
            "container_gid": 0,
        }

    def controller_mount_specs(self) -> list[dict]:
        etc = self.private / "etc"
        return [
            self.bind_spec(self.home, "/var/jenkins_home", True, 0o700),
            self.bind_spec(self.private / "auth", "/run/kiln-g7-auth", False, 0o700),
            self.bind_spec(etc / "hosts", "/etc/hosts", False, 0o644),
            self.bind_spec(etc / "resolv.conf", "/etc/resolv.conf", False, 0o644),
            self.bind_spec(etc / "hostname-controller", "/etc/hostname", False, 0o644),
            self.tmpfs_spec("/tmp"),
            self.tmpfs_spec("/dev/shm"),
        ]

    def agent_mount_specs(self) -> list[dict]:
        etc = self.private / "etc"
        return [
            self.bind_spec(self.private / "work", "/home/jenkins/agent", True, 0o700),
            self.bind_spec(self.private / "args", "/run/kiln-g7-args", False, 0o700),
            self.bind_spec(etc / "hosts", "/etc/hosts", False, 0o644),
            self.bind_spec(etc / "resolv.conf", "/etc/resolv.conf", False, 0o644),
            self.bind_spec(etc / "hostname-agent", "/etc/hostname", False, 0o644),
            self.tmpfs_spec("/tmp"),
            self.tmpfs_spec("/dev/shm"),
        ]

    def mount_inventory(self, name: str, required: list[dict]) -> dict:
        raw = self.run(["podman", "inspect", name], 20)
        if raw.returncode != 0:
            raise GateError(f"inspect failed for {name}")
        meta = json.loads(raw.stdout)[0]
        rows = []
        for item in meta.get("Mounts") or []:
            source = str(item.get("Source") or "")
            host_stat = None
            if source:
                try:
                    info = os.lstat(source)
                    host_stat = {"uid": info.st_uid, "mode": info.st_mode & 0o777, "is_dir": stat.S_ISDIR(info.st_mode)}
                except OSError:
                    host_stat = None
            rows.append(
                {
                    "type": item.get("Type"),
                    "source": source,
                    "destination": str(item.get("Destination") or ""),
                    "mode": item.get("Mode"),
                    "rw": item.get("RW"),
                    "options": item.get("Options"),
                    "propagation": item.get("Propagation"),
                    "host_stat": host_stat,
                }
            )
        missing = []
        matched = set()
        for spec in required:
            found = None
            for index, row in enumerate(rows):
                if index in matched or row["destination"] != spec["destination"]:
                    continue
                source_ok = (
                    row["source"] == spec["source"]
                    if spec["type"] == "bind"
                    else row["source"] in {"", "tmpfs", spec["destination"]}
                )
                mode_text = str(row["mode"] or "")
                mode_ok = True if not mode_text else mode_text.startswith("rw" if spec["rw"] else "ro")
                options = row["options"] or []
                if isinstance(options, str):
                    options = [item for item in options.split(",") if item]
                options_ok = not any(str(option) in {"shared", "rshared", "master", "rslave"} for option in options)
                stat_ok = True
                if spec["type"] == "bind":
                    stat_ok = (
                        row["host_stat"] is not None
                        and row["host_stat"]["uid"] == os.getuid()
                        and row["host_stat"]["mode"] == spec["mode"]
                    )
                elif spec["type"] == "tmpfs":
                    measured = self.exec(name, ["stat", "-c", "%u %g %a", spec["destination"]], 10)
                    if measured.returncode != 0:
                        stat_ok = False
                    else:
                        observed = measured.stdout.decode().split()
                        row["container_stat"] = observed
                        stat_ok = observed == [
                            str(spec["container_uid"]),
                            str(spec["container_gid"]),
                            format(spec["mode"], "o"),
                        ]
                if (
                    row["type"] == spec["type"]
                    and row["rw"] is spec["rw"]
                    and source_ok
                    and mode_ok
                    and options_ok
                    and stat_ok
                ):
                    found = index
                    break
            if found is None:
                missing.append(spec["destination"])
            else:
                matched.add(found)
        unexpected = [rows[index]["destination"] for index in range(len(rows)) if index not in matched]
        env_proc = self.exec(name, ["env"], 10)
        env_bad = env_proc.returncode != 0 or any(
            item.encode() in env_proc.stdout.lower()
            for item in ("github_token", "actions_runtime_token", "aws_secret_access_key")
        )
        return {
            "ok": not missing and not unexpected and not env_bad,
            "missing": missing,
            "unexpected": unexpected,
            "mounts": rows,
            "env_bad": env_bad,
        }

    def queue_case(self) -> dict:
        self.create_job("kiln-g7-roam", self.freestyle("echo roam-not-expected", None, True, False))
        self.trigger("kiln-g7-roam")
        observed = ""
        build_items = None
        life_items = None
        deadline = time.time() + 20
        while time.time() < deadline:
            build_code, build_body = self.http("/queue/api/json?tree=items[id,why,task[name]]", user="feas-build")
            life_code, life_body = self.http("/queue/api/json?tree=items[id,why,task[name]]", user="feas-lifecycle")
            build_data = json.loads(build_body) if build_code == 200 else {}
            life_data = json.loads(life_body) if life_code == 200 else {}
            build_items = build_data.get("items", [])
            life_items = life_data.get("items", [])
            if build_items:
                observed = str(build_items[0].get("why") or "")
                if "reserved" in observed and "kiln-managed-1" in observed:
                    break
            time.sleep(1)
        item_id = build_items[0].get("id") if build_items else None
        if item_id is not None:
            self.http(f"/queue/cancelItem?id={item_id}", user="feas-build", method="POST", use_crumb=True)
        running, _ = self.http("/job/kiln-g7-roam/lastBuild/api/json?tree=building,result,builtOn", user="feas-admin")
        return {
            "why": observed,
            "build_count": len(build_items or []),
            "lifecycle_empty": life_items == [],
            "not_built": running == 404,
        }

    def pinned_job(self, name: str, command: str, assigned: str, marker: bytes, secret: str) -> dict:
        self.create_job(name, self.freestyle(command, assigned, False, True))
        self.trigger(name)
        result, children = self.wait_build(name, secret)
        artifact = self.artifact(name)
        console_code, console = self.http(f"/job/{name}/lastBuild/consoleText", user="feas-admin")
        self.guard.assert_clean("console", console.decode("utf-8", "replace"))
        return {
            "result": result.get("result"),
            "builtOn": result.get("builtOn"),
            "artifact": artifact.strip() == marker,
            "console": console_code == 200 and marker in console,
            "workspace": self.workspace_marker(name, marker),
            "child_secret_absent": children["clean"],
            "child_pids": children["child_pids"],
            "child_secret_seen": children["secret_seen"],
            "child_read_failed": children["read_failed"],
        }

    def remove_owned(self) -> dict:
        errors = []
        # The agent, negative probe, and cleanup containers share the controller network namespace.
        for name in (self.agent, self.neg, self.cleanup_name, self.controller):
            proc = self.run(["podman", "rm", "-f", name], 30)
            if proc.returncode != 0 and name == self.controller:
                proc = self.run(["podman", "rm", "-f", name], 30)
            if proc.returncode != 0:
                errors.append(f"rm {name} rc={proc.returncode}")
        proc = self.run(["podman", "ps", "-a", "--format", "{{.Names}}"], 20)
        if proc.returncode != 0:
            errors.append("podman ps failed")
            names = ["ps-failed"]
        else:
            names = [item for item in proc.stdout.decode().split() if item.startswith("kiln-g7-")]
        secret_left = []
        for relative in ("auth", "args", "neg-args", "cleanup-args", "jnlp", "jenkins-home", "work", "work-neg", "work-cleanup"):
            path = self.private / relative
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                elif path.exists() or path.is_symlink():
                    destroy(path)
            except OSError as exc:
                errors.append(f"{relative}: {exc}")
            if path.exists() or path.is_symlink():
                secret_left.append(relative)
        return {"remaining_containers": names, "secret_dirs_left": secret_left, "errors": errors}

    def execute(self) -> int:
        os.umask(0o077)
        self.private.mkdir(parents=True, exist_ok=True)
        os.chmod(self.private, 0o700)
        self.ledger.seed_fixed()
        context = {
            "lock_sha256": self.lock_sha,
            "initial_main": self.lock["baseline"]["initial_specified_main"],
            "integrated_main": self.lock["baseline"]["integrated_main"],
            "head_sha": os.environ.get("KILN_G7_HEAD_SHA", ""),
            "github_sha": os.environ.get("KILN_G7_GITHUB_SHA", ""),
            "run_id": os.environ.get("KILN_G7_RUN_ID", ""),
            "run_attempt": os.environ.get("KILN_G7_RUN_ATTEMPT", ""),
            "repository": os.environ.get("KILN_G7_REPOSITORY", ""),
            "ref": os.environ.get("KILN_G7_REF", ""),
        }
        try:
            self.phase = "G7-HOST"
            self.host_and_privilege()
            self.phase = "G7-PODMAN"
            self.prepare_runtime()
            self.verify_packages()
            info = self.verify_rootless()
            self.ledger.record(
                "G7-PODMAN",
                "PASS",
                "locked official Podman is rootless",
                json.dumps(info),
                "podman info --format json; dpkg-query and deb sha256",
            )
            self.phase = "G7-DIGEST"
            self.check_time("digest")
            self.verify_identity("controller", self.lock["controller"])
            self.verify_identity("inbound", self.lock["inbound"])
            published = sha256_url(self.lock["controller"]["war_url"])
            if published != self.lock["controller"]["war_sha256"]:
                raise GateError("published war checksum mismatch")
            self.controller_image = self.pull(self.lock["controller"]["repository"], self.lock["controller"]["amd64_digest"])
            self.inbound_image = self.pull(self.lock["inbound"]["repository"], self.lock["inbound"]["amd64_digest"])
            inbound = self.measure_inbound()
            self.measure_controller_war()
            self.ledger.record(
                "G7-DIGEST",
                "PASS",
                "controller and inbound digests verified before start",
                json.dumps(
                    {
                        "controller_amd64": self.lock["controller"]["amd64_digest"],
                        "inbound_amd64": self.lock["inbound"]["amd64_digest"],
                        "java": inbound["java"],
                        "remoting": inbound["remoting"],
                        "remoting_build": inbound["build"],
                        "java_build_id": inbound["java_build_id"],
                    }
                ),
                "registry manifest hash, podman pull by digest, war sha256",
            )
            self.phase = "G7-ROOTLESS-START"
            self.write_auth()
            self.write_controller_home()
            self.check_time("controller")
            self.start_controller()
            self.wait_ready()
            self.wait_security()
            device, ip = host_ipv4()
            gateway = host_gateway()
            ipv6 = os.environ.get("KILN_G7_IPV6", "")
            if ipv6 != "fd7c:67::1":
                raise GateError("controlled IPv6 target is not configured")
            listener = OutsideListener(ip)
            listener6 = OutsideListener(ipv6, socket.AF_INET6)
            try:
                self.phase = "G7-NET-CLOSED"
                before = self.network_sample(self.controller, listener, listener6, gateway)
                if not self.safe_network(before):
                    raise GateError(
                        "closed-network precondition failed before secret injection: " + self.network_gap(before)
                    )
                controller_fs = self.rootfs(
                    self.controller,
                    {"/var/jenkins_home", "/tmp", "/dev/shm"},
                    "/var/jenkins_home/.g7-private/probe",
                )
                self.phase = "G7-CLEANUP-FAIL"
                self.cleanup_fail()
                self.ledger.record(
                    "G7-CLEANUP-FAIL",
                    "PASS",
                    "bounded failed start stays offline and removes the private file",
                    "wrong-secret node stayed offline before and after container-visible unlink",
                    "direct java @argfile on kiln-cleanup-fail; destroy on the failure path",
                )
                self.phase = "G7-API"
                self.create_node("kiln-managed-1", "/home/jenkins/agent", "kiln-slot-jdk-maven")
                secret = self.jnlp_secret("kiln-managed-1")
                self.api_checks(True)
                self.ledger.record(
                    "G7-API",
                    "PASS",
                    "crumb, XML create, parsed EXCLUSIVE, script 403, jnlp split",
                    "basic-auth mode required a crumb; lifecycle created the node; build jnlp and both restricted script consoles were 403; admin script body was 2",
                    "curl --netrc-file and --config inside the controller network namespace",
                )
                self.phase = "G7-REMOTING-NEG"
                negative = self.remoting_negative()
                self.ledger.record(
                    "G7-REMOTING-NEG",
                    "PASS",
                    "measure the image remoting -secret @path behavior on a separate node",
                    negative,
                    "java -jar agent.jar -secret @/run/kiln-g7-neg/secret; bounded 20s; node then removed",
                    "negative container and node removed",
                )
                self.phase = "G7-ARGFILE"
                path = self.start_positive(secret)
                stat_text = self.file_stat(self.agent, "/run/kiln-g7-args/agent.argv")
                if not self.wait_online("kiln-managed-1", 90):
                    destroy(path)
                    raise GateError("positive agent did not come online")
                clean, argc, argc_ok = self.surfaces_clean(self.agent, secret)
                after = self.network_sample(self.agent, listener, listener6, gateway)
                agent_fs = self.rootfs(self.agent, {"/home/jenkins/agent", "/tmp", "/dev/shm"}, "/home/jenkins/agent/probe")
                existed = self.exec(self.agent, ["test", "-f", "/run/kiln-g7-args/agent.argv"], 10).returncode == 0
                destroy(path)
                inside_gone = self.exec(self.agent, ["test", "!", "-e", "/run/kiln-g7-args/agent.argv"], 10).returncode == 0
                still = self.wait_online("kiln-managed-1", 20)
                self.phase = "G7-EXCLUSIVE"
                queue = self.queue_case()
                self.emit_observation("BEFORE_EXACT")
                self._finish_obs(None)
                try:
                    exact = self.pinned_job(
                        "kiln-g7-exact",
                        "echo pinned-ok | tee marker.txt\nsleep 3",
                        "kiln-managed-1",
                        b"pinned-ok",
                        secret,
                    )
                except Exception as exc:
                    self.emit_observation("EXACT_FAILED")
                    self._finish_obs(exc)
                self.emit_observation("AFTER_EXACT")
                self._finish_obs(None)
                try:
                    extra = self.pinned_job(
                        "kiln-g7-extra",
                        "echo extra-label-ok | tee marker.txt\nsleep 3",
                        "kiln-slot-jdk-maven",
                        b"extra-label-ok",
                        secret,
                    )
                except Exception as exc:
                    self.emit_observation(obs_extra_terminal(self._extra_wait_timed_out))
                    self._finish_obs(exc)
                self.emit_observation("EXTRA_SUCCESS")
                self._finish_obs(None)
            finally:
                listener.close()
                listener6.close()
            net_ok = self.safe_network(before) and self.safe_network(after)
            self.ledger.record(
                "G7-NET-CLOSED",
                "PASS" if net_ok else "FAIL",
                "closed namespace, local WebSocket, outside listener rejected, no default route",
                json.dumps({"before": before, "after": after, "device": device}),
                "podman --network=none and --network=container; curl and /proc inside the namespace",
            )
            fs_ok = controller_fs["ok"] and agent_fs["ok"]
            self.ledger.record(
                "G7-ROOTFS",
                "PASS" if fs_ok else "FAIL",
                "read-only rootfs with separate controller and agent writable allowlists",
                json.dumps({"controller": controller_fs, "agent": agent_fs}),
                "podman --read-only --read-only-tmpfs=false; /proc/mounts and write probes",
            )
            self.ledger.record(
                "G7-ROOTLESS-START",
                "PASS" if controller_fs["uid"] == "1000" and agent_fs["uid"] == "1000" else "FAIL",
                "both containers are rootless and non-root",
                json.dumps({"controller_uid": controller_fs["uid"], "agent_uid": agent_fs["uid"]}),
                "podman --userns=keep-id:uid=1000,gid=1000 --user 1000:1000",
            )
            controller_mounts = self.mount_inventory(self.controller, self.controller_mount_specs())
            agent_mounts = self.mount_inventory(self.agent, self.agent_mount_specs())
            self.ledger.record(
                "G7-MOUNTS",
                "PASS" if controller_mounts["ok"] and agent_mounts["ok"] else "FAIL",
                "exact controller and agent mount inventory",
                json.dumps({"controller": controller_mounts, "agent": agent_mounts}),
                "podman inspect Mounts source, destination, type, options, owner, and mode",
            )
            arg_ok = (
                stat_text.startswith("1000 600")
                and clean
                and argc == 2
                and argc_ok
                and existed
                and inside_gone
                and not path.exists()
                and still
                and exact["result"] == "SUCCESS"
                and exact["builtOn"] == "kiln-managed-1"
                and exact["child_secret_absent"]
                and extra["child_secret_absent"]
            )
            self.ledger.record(
                "G7-ARGFILE",
                "PASS" if arg_ok else "FAIL",
                "direct java ELF @argfile, secret absent from surfaces, unlink keeps the agent online",
                json.dumps(
                    {
                        "stat": stat_text,
                        "argc": argc,
                        "secret_absent": clean,
                        "unlinked_inside": inside_gone,
                        "online_after_unlink": still,
                        "pinned_result": exact["result"],
                        "builtOn": exact["builtOn"],
                    }
                ),
                "java @/run/kiln-g7-args/agent.argv; directory bind, not a single-file bind",
            )
            exclusive_ok = (
                queue["not_built"]
                and queue["lifecycle_empty"]
                and queue["build_count"] >= 1
                and "reserved" in queue["why"]
                and "kiln-managed-1" in queue["why"]
                and exact["result"] == "SUCCESS"
                and exact["builtOn"] == "kiln-managed-1"
                and extra["result"] == "SUCCESS"
                and extra["builtOn"] == "kiln-managed-1"
            )
            self.ledger.record(
                "G7-EXCLUSIVE",
                "PASS" if exclusive_ok else "FAIL",
                "roam excluded, exact node runs, extra label also runs; Item.Read sees the queue",
                json.dumps(
                    {
                        "queue": queue,
                        "exact": exact["builtOn"],
                        "extra": extra["builtOn"],
                        "builtin_executors": "0 as fixture technique only",
                    }
                ),
                "queue API as feas-build and feas-lifecycle; assignedNode exact and extra label",
            )
            capability_ok = (
                exact["artifact"]
                and exact["console"]
                and exact["workspace"]
                and exact["result"] == "SUCCESS"
                and exact["child_secret_absent"]
            )
            self.phase = "G7-CAPABILITY"
            self.ledger.record(
                "G7-CAPABILITY",
                "PASS" if capability_ok else "FAIL",
                "real shell marker in the workspace after argfile deletion",
                json.dumps(
                    {
                        "result": exact["result"],
                        "artifact": exact["artifact"],
                        "console": exact["console"],
                        "workspace": exact["workspace"],
                    }
                ),
                "freestyle shell on the inbound node",
            )
        except (GateError, subprocess.TimeoutExpired, OSError, urllib.error.URLError, json.JSONDecodeError, ET.ParseError) as exc:
            message = tail(self.guard, str(exc).encode(), 500)
            self.note(f"gate stopped during {self.phase}: {message}")
            self.aborted = message
            if self.phase and self.phase not in FIXED:
                status = "BLOCKED" if "rootless" in message or "Docker socket" in message else "FAIL"
                self.ledger.force(self.phase, status, "completed measurement", message)
        finally:
            self._obs_teardown_fallback()
            try:
                removed = self.remove_owned()
                teardown_ok = not removed["remaining_containers"] and not removed["secret_dirs_left"] and not removed["errors"]
                self.ledger.force(
                    "G7-TEARDOWN",
                    "PASS" if teardown_ok else "FAIL",
                    "owned containers and private secret files are gone",
                    json.dumps(removed),
                    "podman rm -f and podman ps must both succeed",
                    "workflow always-step rechecks the same names and exits non-zero on failure",
                )
                if not teardown_ok:
                    self.aborted = self.aborted or "teardown failed"
            except Exception as exc:  # noqa: BLE001
                self.aborted = self.aborted or "teardown failed"
                self.ledger.force("G7-TEARDOWN", "FAIL", "teardown", tail(self.guard, str(exc).encode()))
            if not self.aborted and self._obs_reap_unconfirmed:
                self.aborted = "observation collector reap unconfirmed"
            elif not self.aborted and self._obs_emit_failed:
                self.aborted = "observation emission failed"
            self.phase = "G7-PEER-UID"
            self.peer_result()
            self.ledger.fill_missing(self.aborted or "not reached")
            try:
                body = self.ledger.document(context)
            except GateError:
                self.aborted = self.aborted or "ledger sanitization failed"
                self.ledger.rows["G7-LEDGER"] = {
                    "testID": "G7-LEDGER",
                    "status": "FAIL",
                    "expected": "sanitized ledger",
                    "observed": "sanitized ledger rejected; raw content not written",
                    "command_shape": "in-process redaction",
                    "teardown": "owned resources removed in finally",
                }
                body = {
                    "schema": "kiln-g7-ledger-1",
                    "context": {"head_sha": context.get("head_sha", ""), "lock_sha256": context.get("lock_sha256", "")},
                    "tests": [self.ledger.rows["G7-LEDGER"]],
                }
            ledger_path = self.private / "ledger.json"
            ledger_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
            os.chmod(ledger_path, 0o644)
            for row in body["tests"]:
                self.note(f"{row['testID']} {row['status']} {str(row.get('observed', ''))[:240]}")
        return 1 if self.ledger.failed(self.aborted) else 0

    def host_and_privilege(self) -> None:
        groups = set(subprocess.run(["id", "-nG"], capture_output=True, text=True, check=False).stdout.split())
        sudo = subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False)
        peer = subprocess.run(["id", "-nG", "kiln-g7-peer-bad"], capture_output=True, text=True, check=False)
        ok = (
            os.geteuid() != 0
            and "docker" not in groups
            and sudo.returncode != 0
            and "docker" not in set(peer.stdout.split())
            and os.uname().machine in {"x86_64", "amd64"}
            and os.environ.get("KILN_G7_RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("KILN_G7_RUNNER_OS") == "Linux"
        )
        self.ledger.record(
            "G7-HOST",
            "PASS" if ok else "FAIL",
            "GitHub-hosted Ubuntu x64",
            json.dumps(
                {
                    "runner": os.environ.get("KILN_G7_RUNNER_NAME"),
                    "environment": os.environ.get("KILN_G7_RUNNER_ENVIRONMENT"),
                    "os": os.environ.get("KILN_G7_RUNNER_OS"),
                    "arch": os.environ.get("KILN_G7_RUNNER_ARCH"),
                    "image": os.environ.get("KILN_G7_IMAGE_VERSION"),
                    "kernel": os.uname().release,
                }
            ),
            "runner context and uname",
        )
        self.ledger.record(
            "G7-SETUP-PRIV",
            "PASS" if ok else "FAIL",
            "setup sudo is separate from the non-sudo workload",
            json.dumps({"uid": os.geteuid(), "groups": sorted(groups), "sudo_n": sudo.returncode != 0}),
            "id and sudo -n true as the test user",
        )
        if not ok:
            raise GateError("host or workload identity prerequisite failed")

    def peer_result(self) -> None:
        try:
            server = json.loads((self.private / "peercred.json").read_text())
            authorized = json.loads((self.peer_dir / "authorized.json").read_text())
            unauthorized = json.loads((self.peer_dir / "unauthorized.json").read_text())
            dac = json.loads((self.peer_dir / "dac.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.ledger.record("G7-PEER-UID", "FAIL", "SO_PEERCRED accept and reject", f"missing peer evidence: {exc}")
            return
        ok = bool(
            server.get("authorized_decision") == "ACCEPT"
            and server.get("unauthorized_decision") == "REJECT-PEERCRED"
            and server.get("authorized_uid") != server.get("unauthorized_uid")
            and authorized.get("response") == "ACCEPT"
            and unauthorized.get("connect_ok") is True
            and unauthorized.get("response") == "REJECT-PEERCRED"
            and dac.get("errno") == "EACCES"
        )
        self.ledger.record(
            "G7-PEER-UID",
            "PASS" if ok else "FAIL",
            "authorized UID accepted; other UID rejected by SO_PEERCRED after connect succeeds; DAC is separate",
            json.dumps(
                {
                    "authorized_uid": server.get("authorized_uid"),
                    "unauthorized_uid": server.get("unauthorized_uid"),
                    "unauthorized_connect_ok": unauthorized.get("connect_ok"),
                    "dac_errno": dac.get("errno"),
                }
            ),
            "unix socket mode 0666 for policy and mode 0600 for DAC",
        )


def peer_serve() -> int:
    directory = Path(os.environ.get("KILN_G7_PEER_DIR", "/tmp/kiln-g7-peer"))
    result = Path(os.environ.get("KILN_G7_PRIVATE", "/home/kiln-g7/private")) / "peercred.json"
    directory.mkdir(parents=True, exist_ok=True)
    policy_path = directory / "policy.sock"
    dac_path = directory / "dac.sock"
    for path in (policy_path, dac_path, directory / "ready"):
        path.unlink(missing_ok=True)
    policy = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dac = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    policy.bind(str(policy_path))
    dac.bind(str(dac_path))
    os.chmod(policy_path, 0o666)
    os.chmod(dac_path, 0o600)
    policy.listen(2)
    dac.listen(1)
    (directory / "ready").write_text("ready\n")
    outcomes = []
    deadline = time.time() + 30
    policy.settimeout(1)
    while len(outcomes) < 2 and time.time() < deadline:
        try:
            conn, _addr = policy.accept()
        except socket.timeout:
            continue
        _pid, uid, _gid = peercred(conn)
        decision = "ACCEPT" if uid == os.getuid() else "REJECT-PEERCRED"
        conn.sendall((decision + "\n").encode())
        conn.close()
        outcomes.append({"uid": uid, "decision": decision})
    authorized = next((item for item in outcomes if item["decision"] == "ACCEPT"), {})
    rejected = next((item for item in outcomes if item["decision"] == "REJECT-PEERCRED"), {})
    result.write_text(
        json.dumps(
            {
                "authorized_uid": authorized.get("uid"),
                "unauthorized_uid": rejected.get("uid"),
                "authorized_decision": authorized.get("decision"),
                "unauthorized_decision": rejected.get("decision"),
            }
        )
        + "\n"
    )
    os.chmod(result, 0o644)
    return 0 if authorized and rejected else 1


def peer_client(role: str) -> int:
    directory = Path(os.environ.get("KILN_G7_PEER_DIR", "/tmp/kiln-g7-peer"))
    if role == "dac":
        path = directory / "dac.sock"
        errno_name = ""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(str(path))
            connect_ok = True
        except PermissionError as exc:
            connect_ok = False
            errno_name = "EACCES" if exc.errno == 13 else exc.__class__.__name__
        payload = {"role": role, "connect_ok": connect_ok, "errno": errno_name}
    else:
        path = directory / "policy.sock"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(str(path))
        sock.sendall(b"ping\n")
        response = sock.recv(64).decode().strip()
        sock.close()
        payload = {"role": role, "connect_ok": True, "response": response}
    (directory / f"{role}.json").write_text(json.dumps(payload) + "\n")
    if role == "authorized":
        return 0 if payload.get("response") == "ACCEPT" else 1
    if role == "unauthorized":
        return 0 if payload.get("response") == "REJECT-PEERCRED" else 1
    return 0 if payload.get("errno") == "EACCES" else 1


def self_check() -> int:
    guard = Guard()
    guard.add("supersecretvalue")
    if "supersecretvalue" in guard.redact("value supersecretvalue end"):
        return 1
    mounts = parse_mounts("overlay / overlay ro 0 0\ntmpfs /tmp tmpfs rw 0 0\nproc /proc proc rw 0 0\n")
    bad = unexpected_writable(mounts, {"/tmp"})
    if bad:
        print(bad)
        return 1
    directory = Path("/tmp/kiln-g7-selfcheck")
    shutil.rmtree(directory, ignore_errors=True)
    write_private(directory / "agent.argv", b"-jar\n/jar\n", exclusive=True)
    info = os.lstat(directory / "agent.argv")
    if (info.st_mode & 0o777) != 0o600:
        return 1
    try:
        write_private(directory / "agent.argv", b"x", exclusive=True)
        return 1
    except FileExistsError:
        pass
    shutil.rmtree(directory)
    print("self-check ok")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        return Gate().execute()
    if command == "peer-serve":
        return peer_serve()
    if command == "peer-client":
        return peer_client(sys.argv[2])
    if command == "self-check":
        return self_check()
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
