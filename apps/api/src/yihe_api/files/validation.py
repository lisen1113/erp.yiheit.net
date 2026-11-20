"""Bounded content checks performed on the specific object version being confirmed."""

import hashlib
import io
import os
import re
import socket
import struct
import warnings
from enum import StrEnum
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from PIL import Image, UnidentifiedImageError


class Kind(StrEnum):
    BRAND = "BRAND"
    ATTACHMENT = "ATTACHMENT"
    IMPORT = "IMPORT"


class Verdict(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    UNAVAILABLE = "UNAVAILABLE"


IMAGE_TYPES = {
    "png": ("image/png", "PNG"),
    "jpeg": ("image/jpeg", "JPEG"),
    "jpg": ("image/jpeg", "JPEG"),
    "webp": ("image/webp", "WEBP"),
}
DOC_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}
DEFAULT_LIMITS = {Kind.BRAND: 10, Kind.ATTACHMENT: 50, Kind.IMPORT: 20}


def limit(kind: Kind) -> int:
    maximum = DEFAULT_LIMITS[kind]
    return min(maximum, max(1, int(os.environ.get(f"ERP_FILE_{kind}_MAX_MIB", maximum)))) * 1024**2


def filename_extension(filename: str, kind: Kind, mime: str) -> str:
    if (
        len(filename) > 180
        or filename.count(".") != 1
        or filename.startswith(".")
        or not re.fullmatch(r"[^/\\:\x00-\x1f\x7f]+", filename)
        or filename != filename.strip()
    ):
        raise ValueError("Unsafe filename")
    extension = filename.rsplit(".", 1)[-1].lower()
    valid = {
        Kind.BRAND: {ext: value[0] for ext, value in IMAGE_TYPES.items()},
        Kind.ATTACHMENT: DOC_TYPES | {ext: value[0] for ext, value in IMAGE_TYPES.items()},
        Kind.IMPORT: {ext: DOC_TYPES[ext] for ext in ("xlsx", "csv")},
    }
    if valid[kind].get(extension) != mime:
        raise ValueError("File extension or declared media type is not permitted")
    return extension


def inspect_office(content: bytes, extension: str) -> None:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > 100 * 1024**2:
                raise ValueError("Office decompression limit exceeded")
            for item in members:
                path = PurePosixPath(item.filename)
                name = item.filename.lower()
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or item.flag_bits & 1
                    or item.file_size > 100 * max(item.compress_size, 1)
                    or "vbaproject" in name
                    or name.endswith((".bin", ".exe", ".dll"))
                    or "/externallinks/" in name
                    or "/embeddings/" in name
                    or "/activex/" in name
                ):
                    raise ValueError("Unsafe Office package")
                if name.endswith(".rels") and item.file_size <= 1024**2:
                    data = archive.read(item).lower()
                    if re.search(rb"targetmode\s*=\s*[\"']external", data):
                        raise ValueError("External Office relationship")
                elif name.endswith(".rels"):
                    raise ValueError("Office relationships exceed limit")
            manifest = archive.getinfo("[Content_Types].xml")
            if manifest.file_size > 1024**2:
                raise ValueError("Office manifest exceeds limit")
            types = archive.read(manifest).lower()
            expected = "word/document.xml" if extension == "docx" else "xl/workbook.xml"
            if expected not in archive.namelist() or b"macroenabled" in types:
                raise ValueError("Unexpected or macro-enabled Office document")
    except (BadZipFile, KeyError, RuntimeError) as exc:
        raise ValueError("Invalid Office package") from exc


def verify_format(content: bytes, extension: str) -> None:
    if content.startswith((b"MZ", b"\x7fELF", b"#!")):
        raise ValueError("Executable input")
    if extension in IMAGE_TYPES:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content), formats=[IMAGE_TYPES[extension][1]]) as image:
                    if (
                        image.format != IMAGE_TYPES[extension][1]
                        or image.width * image.height > 16_000_000
                        or getattr(image, "n_frames", 1) != 1
                    ):
                        raise ValueError("Unexpected image format, dimensions, or animation")
                    image.verify()
                with Image.open(
                    io.BytesIO(content), formats=[IMAGE_TYPES[extension][1]]
                ) as decoded:
                    decoded.load()
        except (
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
        ) as exc:
            raise ValueError("Invalid image") from exc
    elif extension == "pdf":
        if not re.match(rb"%PDF-[12]\.\d", content) or not content.rstrip().endswith(b"%%EOF"):
            raise ValueError("Invalid PDF")
    elif extension in {"docx", "xlsx"}:
        inspect_office(content, extension)
    elif extension == "csv":
        text = content.decode("utf-8-sig")
        if not text.strip() or any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            raise ValueError("Invalid CSV")
    else:
        raise ValueError("Asset format not implemented")


def scan(content: bytes) -> Verdict:
    """Submit to ClamAV INSTREAM, failing closed on unavailable or inconclusive results."""
    eicar_hash = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"
    if hashlib.sha256(content).hexdigest() == eicar_hash:
        return Verdict.INFECTED
    host = os.environ.get("ERP_CLAMD_HOST")
    if not host:
        return Verdict.UNAVAILABLE
    try:
        with socket.create_connection(
            (host, int(os.environ.get("ERP_CLAMD_PORT", "3310"))), timeout=5
        ) as connection:
            connection.sendall(b"zINSTREAM\0")
            for offset in range(0, len(content), 65536):
                block = content[offset : offset + 65536]
                connection.sendall(struct.pack(">I", len(block)) + block)
            connection.sendall(b"\0" * 4)
            reply = b""
            while b"\0" not in reply and len(reply) < 1024:
                block = connection.recv(1024)
                if not block:
                    break
                reply += block
            if reply.partition(b"\0")[0] == b"stream: OK":
                return Verdict.CLEAN
            if reply.partition(b"\0")[0].endswith(b" FOUND"):
                return Verdict.INFECTED
    except (OSError, ValueError):
        pass
    return Verdict.UNAVAILABLE
