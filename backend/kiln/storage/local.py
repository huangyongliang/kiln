"""Local directory storage for artifact bytes.

The platform owns this directory. It is not a Nexus repository and it does not
read a Nexus URL. Keys are content digests, so a stored object can be checked
against the catalog checksum.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from kiln.errors import StorageError

_ALGORITHMS = {"sha256": 64, "sha512": 128}


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def prepare(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"无法创建制品存储目录 {self.root}: {exc}") from exc
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise RuntimeError(f"制品存储目录不可写: {self.root}")

    @staticmethod
    def key_for(algorithm: str, digest: str) -> str:
        length = _ALGORITHMS.get(algorithm)
        hex_ok = length is not None and len(digest) == length
        if not hex_ok or any(char not in "0123456789abcdef" for char in digest):
            raise StorageError("制品字节路径无效")
        return f"{algorithm}/{digest}"

    def write(self, algorithm: str, digest: str, data: bytes) -> str:
        actual = hashlib.new(algorithm, data).hexdigest()
        if actual != digest:
            raise StorageError("拒绝写入与校验和不一致的制品字节")
        key = self.key_for(algorithm, digest)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            if path.read_bytes() != data:
                raise StorageError("已保存的制品字节与校验和不一致")
            return key
        fd, tmp_name = tempfile.mkstemp(prefix=".partial-", dir=path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return key

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("制品字节不存在")
        return path.read_bytes()

    def _path(self, key: str) -> Path:
        algorithm, separator, digest = key.partition("/")
        if not separator:
            raise StorageError("制品字节路径无效")
        checked = self.key_for(algorithm, digest)
        if checked != key:
            raise StorageError("制品字节路径无效")
        root = self.root.resolve()
        path = (root / algorithm / digest).resolve()
        if path.parent != (root / algorithm).resolve():
            raise StorageError("制品字节路径无效")
        return path
