"""Detect and strip C2PA / AI-related metadata from PNG and JPEG (stdlib)."""

from __future__ import annotations

import re
import struct
import subprocess
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shutil import which

from common import tool_arg

PNG_SIG = b"\x89PNG\r\n\x1a\n"
JPEG_SOI = b"\xff\xd8"
MAX_PNG_TEXT_BYTES = 8 * 1024 * 1024

# Chunk types / content patterns associated with C2PA / AI provenance
C2PA_MARKERS = (
    b"c2pa",
    b"C2PA",
    b"jumb",
    b"JUMB",
    b"c2ma",
    b"contentcredentials",
    b"contentauth",
    b"cai:",
    b"http://ns.adobe.com/xmp/InstanceID/",  # not solely C2PA
)

AI_META_HINTS = (
    b"c2pa",
    b"C2PA",
    b"contentcredentials",
    b"ContentCredentials",
    b"digitalSourceType",
    b"trainedAlgorithmicMedia",
    b"compositeWithTrainedAlgorithmicMedia",
    b"algorithmicMedia",
    b"AIGC",
    b"aigc",
    b"AI generated",
    b"Claude",
    b"Anthropic",
    b"OpenAI",
    b"SynthID",
    b"dcterms:provenance",
)


@dataclass
class ImageInspectReport:
    path: str
    format: str  # png | jpeg | unknown
    has_c2pa: bool
    has_ai_metadata: bool
    findings: list[str] = field(default_factory=list)
    tools: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "format": self.format,
            "has_c2pa": self.has_c2pa,
            "has_ai_metadata": self.has_ai_metadata,
            "findings": self.findings,
            "tools": self.tools,
            "error": self.error,
        }


def detect_image_format(data: bytes) -> str:
    if data.startswith(PNG_SIG):
        return "png"
    if data.startswith(JPEG_SOI):
        return "jpeg"
    return "unknown"


def _jpeg_app_is_render(marker: int, payload: bytes) -> bool:
    """APP2 ICC and APP14 Adobe are color data, not provenance."""
    return (marker == 0xE2 and payload.startswith(b"ICC_PROFILE")) or (
        marker == 0xEE and payload.startswith(b"Adobe")
    )


def _provenance_needles(render_segment: bool) -> tuple[bytes, ...]:
    needles = AI_META_HINTS + C2PA_MARKERS
    if not render_segment:
        return needles
    # jumb / cai: / c2pa are short enough to occur inside an ICC profile.
    return tuple(n for n in needles if len(n) >= 8)


def _contains_any(blob: bytes, needles: tuple[bytes, ...]) -> list[str]:
    found = []
    lower = blob.lower()
    for n in needles:
        if n.lower() in lower:
            try:
                found.append(n.decode("ascii", errors="replace"))
            except Exception:
                found.append(repr(n))
    return found


def _png_text_payload(ctype: bytes, payload: bytes) -> bytes:
    """Decode PNG text with a strict output cap, retaining keyword/header fields."""
    if ctype == b"tEXt":
        return payload
    keyword, separator, rest = payload.partition(b"\x00")
    if not separator:
        raise ValueError("missing text keyword separator")
    if ctype == b"zTXt":
        if not rest or rest[0] != 0:
            raise ValueError("invalid zTXt compression method")
        header, text, compressed = keyword + separator, rest[1:], True
    else:  # iTXt: compression flag, method, language, translated keyword, text
        if len(rest) < 2 or rest[0] not in (0, 1) or rest[1] != 0:
            raise ValueError("invalid iTXt compression fields")
        fields = rest[2:].split(b"\x00", 2)
        if len(fields) != 3:
            raise ValueError("missing iTXt language/keyword separator")
        header = keyword + separator + fields[0] + b"\x00" + fields[1] + b"\x00"
        text, compressed = fields[2], bool(rest[0])
    limit = MAX_PNG_TEXT_BYTES - len(header)
    if limit < 0:
        raise ValueError("text metadata header exceeds limit")
    if compressed:
        decoder = zlib.decompressobj()
        text = decoder.decompress(text, limit + 1)
        if len(text) > limit or decoder.unconsumed_tail:
            raise ValueError("decompressed text exceeds limit")
        if not decoder.eof or decoder.unused_data:
            raise ValueError("invalid or truncated compressed text")
    elif len(text) > limit:
        raise ValueError("text metadata exceeds limit")
    return header + text


def inspect_png(data: bytes) -> tuple[bool, bool, list[str]]:
    findings: list[str] = []
    has_c2pa = False
    has_ai = False
    if not data.startswith(PNG_SIG):
        return False, False, ["not a PNG"]
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk_start = pos + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            findings.append(f"truncated chunk {ctype!r}")
            break
        payload = data[chunk_start:chunk_end]
        name = ctype.decode("latin-1", errors="replace")
        # Private/ancillary chunks sometimes used for JUMBF/C2PA
        if ctype in (b"caBX", b"juMB", b"jumb") or ctype.startswith(b"c2"):
            has_c2pa = True
            findings.append(f"PNG chunk {name} (possible C2PA container)")
        elif ctype != b"IDAT":
            # IDAT is compressed pixels. Short needles like "jumb" match there by chance.
            try:
                metadata = _png_text_payload(ctype, payload) if ctype in (b"tEXt", b"zTXt", b"iTXt") else payload
            except (ValueError, zlib.error) as exc:
                # Never report unchecked metadata as clean.
                has_ai = True
                findings.append(f"PNG {name}: unreadable text metadata ({exc})")
                metadata = b""
            hits = _contains_any(metadata, AI_META_HINTS + C2PA_MARKERS)
            if hits:
                has_ai = True
                if any(
                    h.lower() in ("c2pa", "contentcredentials", "jumb", "contentauth")
                    for h in hits
                ):
                    has_c2pa = True
                findings.append(f"PNG {name}: {', '.join(hits[:8])}")
        if ctype == b"IEND":
            break
        pos = chunk_end + 4  # skip CRC
    return has_c2pa, has_ai or has_c2pa, findings


def inspect_jpeg(data: bytes) -> tuple[bool, bool, list[str]]:
    findings: list[str] = []
    has_c2pa = False
    has_ai = False
    if not data.startswith(JPEG_SOI):
        return False, False, ["not a JPEG"]
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        # skip fill
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9):  # SOI/EOI
            continue
        if marker == 0xDA:  # SOS — image data follows
            break
        if marker >= 0xD0 and marker <= 0xD7:  # RSTn
            continue
        if i + 2 > n:
            break
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            findings.append(f"bad segment length at marker 0x{marker:02X}")
            break
        payload = data[i + 2 : i + seglen]
        i += seglen

        # APP11 (0xEB) often holds JUMBF/C2PA
        if marker == 0xEB:
            has_c2pa = True
            findings.append("JPEG APP11 segment (JUMBF/C2PA common)")
        if marker in (0xE1, 0xE2, 0xED, 0xEE, 0xEB):  # APP1,2,13,14,11
            hits = _contains_any(
                payload, _provenance_needles(_jpeg_app_is_render(marker, payload))
            )
            if hits:
                has_ai = True
                if any(
                    h.lower() in ("c2pa", "contentcredentials", "jumb", "contentauth")
                    for h in hits
                ):
                    has_c2pa = True
                findings.append(f"JPEG APP{marker - 0xE0}: {', '.join(hits[:8])}")

    return has_c2pa, has_ai or has_c2pa, findings


def _c2pa_python_probe(path: Path) -> dict[str, Any] | None:
    """Read C2PA manifests via the c2pa binding, standing in for the c2patool CLI."""
    try:
        from c2pa import Reader
    except ImportError:
        return None
    try:
        reader = Reader.try_create(str(path))
    except Exception as e:
        return {"available": True, "error": str(e)}
    if reader is None:
        return {"available": True, "has_manifest": False}
    with reader:
        return {
            "available": True,
            "has_manifest": True,
            "embedded": reader.is_embedded(),
            "remote_url": reader.get_remote_url(),
            "snippet": reader.json()[:2000],
        }


def c2patool_has_manifest(out: str) -> bool:
    """A negative c2patool report still contains the words it is denying."""
    lower = out.lower()
    if any(phrase in lower for phrase in ("no claim", "no jumbf", "no manifest")):
        return False
    return any(word in lower for word in ("claim", "c2pa", "manifest"))


def run_optional_tools(path: Path) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    c2patool = which("c2patool")
    if c2patool:
        try:
            r = subprocess.run(
                [c2patool, tool_arg(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = (r.stdout or "") + (r.stderr or "")
            tools["c2patool"] = {
                "available": True,
                "returncode": r.returncode,
                "snippet": out[:2000],
                "has_manifest": c2patool_has_manifest(out),
            }
        except Exception as e:
            tools["c2patool"] = {"available": True, "error": str(e)}
    else:
        tools["c2patool"] = _c2pa_python_probe(path) or {"available": False}

    exiftool = which("exiftool")
    if exiftool:
        try:
            r = subprocess.run(
                [exiftool, "-G1", "-a", "-s", tool_arg(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = r.stdout or ""
            interesting = [
                line
                for line in out.splitlines()
                if re.search(
                    r"c2pa|content.?credential|AIGC|digitalSource|XMP|EXIF|IPTC|jumb",
                    line,
                    re.I,
                )
            ]
            tools["exiftool"] = {
                "available": True,
                "interesting_lines": interesting[:50],
            }
        except Exception as e:
            tools["exiftool"] = {"available": True, "error": str(e)}
    else:
        tools["exiftool"] = {"available": False}
    return tools


def inspect_image(path: Path) -> ImageInspectReport:
    data = path.read_bytes()
    fmt = detect_image_format(data)
    error = ""
    if fmt == "png":
        has_c2pa, has_ai, findings = inspect_png(data)
    elif fmt == "jpeg":
        has_c2pa, has_ai, findings = inspect_jpeg(data)
    else:
        has_c2pa, has_ai, findings = False, False, ["unsupported format (MVP: PNG/JPEG)"]
        error = findings[0]

    tools = run_optional_tools(path)
    # Elevate flags from tools
    ct = tools.get("c2patool") or {}
    if ct.get("has_manifest"):
        has_c2pa = True
        findings.append("c2patool reports a C2PA-related manifest")

    return ImageInspectReport(
        path=str(path),
        format=fmt,
        has_c2pa=has_c2pa,
        has_ai_metadata=has_ai,
        findings=findings,
        tools=tools,
        error=error,
    )


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def strip_png(data: bytes, *, strip_all_text: bool = True) -> tuple[bytes, list[str]]:
    if not data.startswith(PNG_SIG):
        raise ValueError("not PNG")
    actions: list[str] = []
    out = bytearray(PNG_SIG)
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk_start = pos + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            break
        payload = data[chunk_start:chunk_end]
        crc_bytes = data[chunk_end : chunk_end + 4]
        pos = chunk_end + 4
        name = ctype.decode("latin-1", errors="replace")

        drop = False
        if ctype in (b"eXIf", b"caBX") or ctype.startswith(b"c2"):
            drop = True
            actions.append(f"drop chunk {name}")
        elif ctype in (b"tEXt", b"zTXt", b"iTXt"):
            try:
                metadata = _png_text_payload(ctype, payload)
            except (ValueError, zlib.error) as exc:
                drop = True
                actions.append(f"drop chunk {name} (unreadable text metadata: {exc})")
                metadata = b""
            if not drop and (strip_all_text or _contains_any(metadata, AI_META_HINTS + C2PA_MARKERS)):
                drop = True
                actions.append(f"drop chunk {name}")
        elif _contains_any(ctype + payload, C2PA_MARKERS) and ctype not in (
            b"IHDR",
            b"IDAT",
            b"IEND",
            b"PLTE",
            b"tRNS",
            b"gAMA",
            b"pHYs",
            b"sRGB",
            b"cHRM",
            b"iCCP",
        ):
            drop = True
            actions.append(f"drop chunk {name} (C2PA marker in payload)")

        if not drop:
            out.extend(struct.pack(">I", length) + ctype + payload + crc_bytes)
        if ctype == b"IEND":
            break
    if not actions:
        actions.append("no PNG metadata chunks removed (already clean or none matched)")
    return bytes(out), actions


def strip_jpeg(data: bytes, *, strip_all_app: bool = True) -> tuple[bytes, list[str]]:
    if not data.startswith(JPEG_SOI):
        raise ValueError("not JPEG")
    actions: list[str] = []
    out = bytearray(JPEG_SOI)
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            # unexpected; copy rest
            out.extend(data[i:])
            actions.append("copied remainder after non-marker byte")
            break
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1

        if marker == 0xD9:  # EOI
            out.extend(b"\xff\xd9")
            break
        if marker == 0xD8:  # nested SOI?
            continue
        if 0xD0 <= marker <= 0xD7:
            out.extend(bytes([0xFF, marker]))
            continue
        # SOS: entropy-coded data runs to EOI and carries no metadata, so copy it verbatim
        # rather than trying to parse past the stuffed FF 00 bytes inside it.
        if marker == 0xDA:
            if i + 2 > n:
                break
            out.extend(b"\xff\xda")
            out.extend(data[i:])
            actions.append("preserved entropy-coded scan (SOS→EOF)")
            break

        if i + 2 > n:
            break
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            out.extend(data[i - 2 :])  # best effort
            actions.append("truncated segment; copied remainder")
            break
        payload = data[i + 2 : i + seglen]
        next_i = i + seglen

        # APP0 (JFIF), ICC APP2, and Adobe APP14 are needed to render the image.
        keep = False
        drop = False
        if 0xE0 <= marker <= 0xEF:  # APPn
            if marker == 0xEB:  # APP11 JUMBF/C2PA
                drop = True
                actions.append("drop APP11 (C2PA/JUMBF)")
            elif _contains_any(
                payload, _provenance_needles(_jpeg_app_is_render(marker, payload))
            ):
                drop = True
                actions.append(f"drop APP{marker - 0xE0} (AI/C2PA markers)")
            elif marker == 0xE0 or _jpeg_app_is_render(marker, payload):
                keep = True
            elif strip_all_app:
                drop = True
                actions.append(f"drop APP{marker - 0xE0}")
            else:
                keep = True
        elif marker in (0xFE,):  # COM
            drop = True
            actions.append("drop COM comment")
        else:
            keep = True

        if keep and not drop:
            out.extend(bytes([0xFF, marker]))
            out.extend(data[i : i + seglen])
        i = next_i

    if not actions:
        actions.append("no JPEG APP segments removed")
    return bytes(out), actions


def clean_image(
    path: Path,
    dest: Path,
    *,
    strip_all_metadata: bool = True,
) -> dict[str, Any]:
    data = path.read_bytes()
    fmt = detect_image_format(data)
    if fmt == "png":
        cleaned, actions = strip_png(data, strip_all_text=strip_all_metadata)
    elif fmt == "jpeg":
        cleaned, actions = strip_jpeg(data, strip_all_app=strip_all_metadata)
    else:
        raise ValueError(f"unsupported format: {fmt}")

    # Optional exiftool pass for residual tags
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(cleaned)
    exiftool = which("exiftool")
    if exiftool and strip_all_metadata:
        # -all= deletes an ICC profile. JPEG can exclude it and still keep APP14.
        # On PNG that same exclude drops gAMA, so PNG only deletes the metadata groups.
        if fmt == "png":
            exif_args = [
                "-EXIF:all=",
                "-XMP:all=",
                "-IPTC:all=",
                "-Photoshop:all=",
                "-Comment=",
            ]
        else:
            exif_args = ["-all=", "--icc_profile:all"]
        try:
            r = subprocess.run(
                [exiftool, *exif_args, "-overwrite_original", tool_arg(dest)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            actions.append(f"exiftool pass (rc={r.returncode})")
        except Exception as e:
            actions.append(f"exiftool failed: {e}")

    after = inspect_image(dest)
    return {
        "input": str(path),
        "output": str(dest),
        "format": fmt,
        "actions": actions,
        "bytes_in": len(data),
        "bytes_out": dest.stat().st_size,
        "still_has_c2pa": after.has_c2pa,
        "still_has_ai_metadata": after.has_ai_metadata,
        "post_findings": after.findings,
    }
