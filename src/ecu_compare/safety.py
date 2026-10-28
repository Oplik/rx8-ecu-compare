"""Path guard: writes only under the EcuProgram root. Outside paths are copy sources."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SafetyError(PermissionError):
    """Raised when a write would leave this program's folder."""


def project_root() -> Path:
    return ROOT


def assert_inside_root(path: Path) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SafetyError(
            f"Refusing path outside {ROOT}: {resolved}"
        ) from exc
    return resolved


def ensure_dirs() -> None:
    for rel in ("data/defs", "data/roms", "data/reports", "data/exports"):
        path = ROOT / rel
        assert_inside_root(path)
        path.mkdir(parents=True, exist_ok=True)


def _unique_dest(dest_dir: Path, name: str, payload: bytes) -> Path:
    dest = dest_dir / name
    if not dest.exists():
        return dest
    existing = dest.read_bytes()
    if existing == payload:
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem}_copy{n}{suffix}"
        if not candidate.exists():
            return candidate
        if candidate.read_bytes() == payload:
            return candidate
        n += 1


def import_file(src: str | Path, dest_dir: Path) -> Path:
    """Read *src* (anywhere) once and write a copy under dest_dir (inside root)."""
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"File not found: {src_path}")
    dest_dir = assert_inside_root(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    payload = src_path.read_bytes()
    dest = _unique_dest(dest_dir, src_path.name, payload)
    dest = assert_inside_root(dest)
    if not dest.exists():
        dest.write_bytes(payload)
    return dest


def import_rom(src: str | Path) -> Path:
    ensure_dirs()
    return import_file(src, ROOT / "data" / "roms")


def write_bytes(dest: str | Path, payload: bytes) -> Path:
    dest_path = assert_inside_root(Path(dest))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(payload)
    return dest_path


def write_text(dest: str | Path, text: str, encoding: str = "utf-8") -> Path:
    dest_path = assert_inside_root(Path(dest))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(text, encoding=encoding, newline="\n")
    return dest_path


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)
    return cleaned.strip("._") or "file"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
