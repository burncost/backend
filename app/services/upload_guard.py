"""Cheap, dependency-free checks on an uploaded file's *content*.

The BOQ endpoints only ever looked at the filename extension and the declared
MIME type — both are attacker-controlled strings. Two classes of abuse slip
through:

* a file that is not what it claims to be (an executable renamed ``bill.xlsx``);
  the parsers then get handed bytes they were never designed for;
* a zip bomb: an .xlsx/.docx only needs to be a few dozen KB to expand to
  petabytes when the archive's central directory declares absurd entry sizes,
  and ``openpyxl``/``python-docx`` will happily try to inflate it.

``inspect_upload`` answers both from the file's own bytes (and, for archives,
its central directory only — nothing is extracted), so a hostile upload is
rejected before any parser touches it. It never raises and never moves the
caller's stream position: a rejected upload is a message, a clean upload is
``None``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── signature sniffing ───────────────────────────────────────────────────────
# Office documents since 2007 (.xlsx/.xlsm/.docx) are zip containers, so they
# share one signature. `PK\x05\x06` (empty archive) and `PK\x07\x08` (spanning)
# are accepted because a valid writer may produce them.
_ZIP_SIGNATURES: Tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls / .doc

_SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    ".xlsx": _ZIP_SIGNATURES,
    ".xlsm": _ZIP_SIGNATURES,
    ".docx": _ZIP_SIGNATURES,
    ".xls": (_OLE2_SIGNATURE,),
    ".doc": (_OLE2_SIGNATURE,),
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
    ".tif": (b"II*\x00", b"MM\x00*"),
}

# Formats whose first bytes are read but which carry no signature of their own.
_TEXT_EXTENSIONS = {".csv", ".txt"}

# Only these containers are inspected for expansion; a bomb in an image or a
# PDF would need a decoder that is not in the parse path.
_ZIP_EXTENSIONS = {".xlsx", ".xlsm", ".docx"}

_HEAD_BYTES = 16  # enough for every signature above (WebP needs bytes 8..11)

# ── zip-bomb limits ──────────────────────────────────────────────────────────
# A real bill's uncompressed payload is a few tens of MB at most (the 100 MB
# upload cap is on the compressed file), so 1 GiB of expansion is slack for any
# legitimate workbook while still stopping a 42.zip-style archive.
_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
# Below this the ratio is meaningless (a 1 KB zip legitimately expands ~40x).
_MIN_RATIO_BYTES = 64 * 1024
# XLSX is XML-heavy and compresses ~4-15x; 200x is far past anything a
# spreadsheet writer produces and well under a bomb's millions.
_MAX_COMPRESSION_RATIO = 200
# A workbook with more than 20k parts is not a bill of quantities.
_MAX_ZIP_ENTRIES = 20000


def _extension(filename: Optional[str]) -> str:
    name = (filename or "").strip().lower()
    return ("." + name.rsplit(".", 1)[-1]) if "." in name else ""


def _matches_signature(extension: str, head: bytes) -> bool:
    """Does `head` look like the format `extension` claims to be?"""
    if extension == ".webp":
        # RIFF....WEBP — the fourcc sits after the RIFF size field.
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    expected = _SIGNATURES.get(extension)
    if expected is None:
        return False
    return any(head.startswith(signature) for signature in expected)


def _looks_like_text(head: bytes) -> bool:
    """A CSV is text: no NUL byte, and decodable as UTF-8."""
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # A cut in a multi-byte char at the end of the sample is not a reject:
        # only clearly broken bytes (control chars) should fail here.
        return "\ufffd" not in head.decode("utf-8", errors="replace")
    return True


def _known_binary_format(head: bytes) -> Optional[str]:
    """Name the format `head` belongs to, or None when nothing matches."""
    if head.startswith(_OLE2_SIGNATURE):
        return "an OLE2 (legacy Office) file"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "a WebP image"
    for extension, signatures in _SIGNATURES.items():
        if any(head.startswith(signature) for signature in signatures):
            return f"a {extension.lstrip('.').upper()} file"
    return None


def _zip_expansion_reason(
    fileobj: Any, max_uncompressed: int, max_entries: int
) -> Optional[str]:
    """Reject a zip container whose declared expansion is unreasonable.

    Only the central directory is read (no entry is decompressed), so a bomb
    costs the server a few KB of reads instead of all the memory it declares.
    """
    import zipfile

    try:
        with zipfile.ZipFile(fileobj) as archive:
            infos = archive.infolist()
    except Exception as exc:  # noqa: BLE001 - a corrupt archive is the parser's problem
        # The signature already matched, so let the existing reader report the
        # damage in the user's own terms ("could not open the workbook").
        logger.debug(f"Zip inspection skipped: {exc}")
        return None

    if len(infos) > max_entries:
        return (
            f"This document contains {len(infos):,} internal parts, "
            "which is not a bill of quantities."
        )

    uncompressed = sum(info.file_size for info in infos)
    compressed = sum(info.compress_size for info in infos)

    if uncompressed > max_uncompressed:
        return (
            "This document expands to "
            f"{uncompressed / (1024 ** 3):.1f}GB when opened, which is too large to process."
        )
    if (
        compressed >= _MIN_RATIO_BYTES
        and uncompressed > compressed * _MAX_COMPRESSION_RATIO
    ):
        return (
            "This document's contents expand far more than a normal spreadsheet "
            "or Word file (possible archive bomb)."
        )
    return None


def inspect_upload(
    fileobj: Any,
    filename: Optional[str],
    max_uncompressed: int = _MAX_UNCOMPRESSED_BYTES,
    max_entries: int = _MAX_ZIP_ENTRIES,
) -> Optional[str]:
    """Return a user-facing reason to reject the upload, or None to accept it.

    `fileobj` must be a seekable binary file object; its position is restored to
    the start on every path so the caller can hand it straight to a parser.
    An unknown extension is judged on its content alone — a PDF named ``plan``
    is still a PDF, and a file that matches nothing is refused.
    """
    extension = _extension(filename)
    try:
        try:
            fileobj.seek(0)
            head = fileobj.read(_HEAD_BYTES)
        except Exception as exc:  # noqa: BLE001 - an unreadable upload is a reject
            logger.warning(f"Could not read the start of '{filename}': {exc}")
            return "The uploaded file could not be read."

        if not head:
            return "The uploaded file is empty."

        if extension in _TEXT_EXTENSIONS:
            if not _looks_like_text(head):
                return (
                    f"The {extension.lstrip('.').upper()} file is not readable text — "
                    "it looks like a binary file with the wrong extension."
                )
            return None

        if extension in _SIGNATURES or extension == ".webp":
            if _matches_signature(extension, head):
                if extension in _ZIP_EXTENSIONS:
                    return _zip_expansion_reason(fileobj, max_uncompressed, max_entries)
                return None
            detected = _known_binary_format(head)
            described = f" — its contents are {detected}" if detected else ""
            return (
                f"This file is named '{filename}' but its contents are not "
                f"{extension.lstrip('.').upper()}{described}. Please upload the original file."
            )

        # No extension (or an unrecognised one): trust the content, not the name.
        if _looks_like_text(head):
            return None
        if _known_binary_format(head):
            return None
        return "Unsupported file content. Upload Excel, Word, PDF, CSV or an image."
    finally:
        # The archive check walks the central directory and leaves the stream at
        # the end of the file; the caller hands this same stream to a parser.
        try:
            fileobj.seek(0)
        except Exception:  # pragma: no cover - caller-owned stream
            pass

