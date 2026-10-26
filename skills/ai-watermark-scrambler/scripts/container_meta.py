"""Inspect/clean AI provenance metadata in non-raster containers.

Formats: SVG, PDF (best-effort), DOCX, ODT, HTML, Markdown frontmatter.
Stdlib-first; PDF prefers optional exiftool/c2patool when present.
"""

from __future__ import annotations

import io
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from shutil import which

from common import tool_arg, write_text_file
from image_meta import AI_META_HINTS, C2PA_MARKERS, run_optional_tools
from text_unicode import clean_text

# Caps on decompressed zip content, so a crafted DOCX/ODT cannot exhaust memory.
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024


class ZipTooLarge(ValueError):
    pass


class _ZipBudget:
    """Reads zip members, refusing anything past the decompressed size caps."""

    def __init__(self, total: int | None = None, member: int | None = None) -> None:
        self.remaining = MAX_ZIP_TOTAL_BYTES if total is None else total
        self.member = MAX_ZIP_MEMBER_BYTES if member is None else member

    def read(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        limit = min(self.member, self.remaining)
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
        if len(data) > limit:
            raise ZipTooLarge(
                f"{info.filename}: decompressed size exceeds limit ({limit} bytes)"
            )
        self.remaining -= len(data)
        return data


# Frontmatter / meta keys that often carry AI provenance
AI_FRONTMATTER_KEYS = frozenset(
    {
        "generator",
        "ai",
        "ai_generated",
        "ai-generated",
        "claude",
        "anthropic",
        "openai",
        "gemini",
        "synthid",
        "c2pa",
        "content_credentials",
        "contentcredentials",
        "provenance",
        "digital_source_type",
        "digitalsourcetype",
        "created_with",
        "createdwith",
        "model",
        "llm",
    }
)

AI_META_NAME_RE = re.compile(
    r"generator|ai[-_ ]?generated|claude|anthropic|openai|gemini|synthid|"
    r"c2pa|content.?credential|provenance|digital.?source|aigc",
    re.I,
)

SVG_DROP_TAGS = frozenset(
    {
        "{http://www.w3.org/2000/svg}metadata",
        "metadata",
        "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF",
        "{adobe:ns:meta/}xmpmeta",
    }
)


@dataclass
class ContainerInspectReport:
    path: str
    format: str
    has_c2pa: bool
    has_ai_metadata: bool
    findings: list[str] = field(default_factory=list)
    tools: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "format": self.format,
            "has_c2pa": self.has_c2pa,
            "has_ai_metadata": self.has_ai_metadata,
            "findings": self.findings,
            "tools": self.tools,
            "details": self.details,
            "error": self.error,
        }


def detect_container_format(path: Path, data: bytes | None = None) -> str:
    ext = path.suffix.lower()
    if ext in (".svg",):
        return "svg"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".docx",):
        return "docx"
    if ext in (".odt",):
        return "odt"
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".md", ".markdown", ".mdx"):
        return "markdown"
    if data is not None:
        if data[:4] == b"%PDF":
            return "pdf"
        if data[:100].lstrip().startswith(b"<") and b"svg" in data[:500].lower():
            return "svg"
        if data[:2] == b"PK":
            # zip-based; sniff
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    names = set(zf.namelist())
                    if "word/document.xml" in names:
                        return "docx"
                    if "content.xml" in names and "meta.xml" in names:
                        return "odt"
            except zipfile.BadZipFile:
                pass
    return "unknown"


def _blob_hits(blob: bytes) -> tuple[bool, bool, list[str]]:
    lower = blob.lower()
    findings: list[str] = []
    has_c2pa = False
    has_ai = False
    for n in C2PA_MARKERS:
        if n.lower() in lower:
            has_c2pa = True
            findings.append(f"marker:{n.decode('ascii', errors='replace')}")
    for n in AI_META_HINTS:
        if n.lower() in lower:
            has_ai = True
            label = n.decode("ascii", errors="replace")
            if label not in {f.split(":", 1)[-1] for f in findings}:
                findings.append(f"ai:{label}")
    return has_c2pa, has_ai or has_c2pa, findings[:30]


# ---------------------------------------------------------------------------
# Markdown frontmatter
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def _parse_simple_yaml_keys(block: str) -> list[tuple[str, str, int]]:
    """Return list of (key, full_line, line_index) for top-level keys only."""
    rows: list[tuple[str, str, int]] = []
    for i, line in enumerate(block.splitlines()):
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line[0] in (" ", "\t", "-"):
            continue  # nested / list — leave alone
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:", line)
        if m:
            rows.append((m.group(1), line, i))
    return rows


def inspect_markdown(text: str) -> tuple[bool, bool, list[str], dict]:
    findings: list[str] = []
    has_ai = False
    m = _FM_RE.match(text)
    if not m:
        return False, False, [], {"has_frontmatter": False}
    block = m.group(1)
    keys = []
    for key, _line, _i in _parse_simple_yaml_keys(block):
        keys.append(key)
        if key.lower() in AI_FRONTMATTER_KEYS or AI_META_NAME_RE.search(key):
            has_ai = True
            findings.append(f"frontmatter key: {key}")
    c2pa = any("c2pa" in f.lower() or "content" in f.lower() for f in findings)
    return c2pa, has_ai, findings, {"has_frontmatter": True, "keys": keys}


def clean_markdown(text: str) -> tuple[str, list[str]]:
    actions: list[str] = []
    m = _FM_RE.match(text)
    if not m:
        return text, ["no YAML frontmatter"]
    body = text[m.end() :]
    kept: list[str] = []
    parent_dropped = False
    for line in m.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        if line[0] in (" ", "\t", "-"):
            if not parent_dropped:
                kept.append(line)
            continue
        km = re.match(r"^([A-Za-z0-9_.-]+)\s*:", line)
        if not km:
            kept.append(line)
            parent_dropped = False
            continue
        key = km.group(1)
        if key.lower() in AI_FRONTMATTER_KEYS or AI_META_NAME_RE.search(key):
            actions.append(f"drop frontmatter key: {key}")
            parent_dropped = True
        else:
            kept.append(line)
            parent_dropped = False
    if not actions:
        actions.append("no AI frontmatter keys removed")
    new_block = "\n".join(kept).strip("\n")
    if new_block:
        out = f"---\n{new_block}\n---\n{body}"
    else:
        out = body.lstrip("\n")
        actions.append("removed empty frontmatter block")
    return out, actions


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>.*?</script>",
    re.I | re.DOTALL,
)
_AI_TOOL_RE = re.compile(
    r"claude|anthropic|openai|chatgpt|\bgpt[-\d]|gemini|synthid",
    re.I,
)
_PROVENANCE_NAME_RE = re.compile(
    r"ai[-_ ]?generated|claude|anthropic|openai|chatgpt|gemini|synthid|"
    r"c2pa|content.?credential|provenance|digital.?source|aigc",
    re.I,
)
_JSONLD_PROVENANCE_RE = re.compile(
    r"""["'](?:digitalSourceType|trainedAlgorithmicMedia|"""
    r"""compositeWithTrainedAlgorithmicMedia|c2pa|contentCredentials)["']""",
    re.I,
)


class _StartTags(HTMLParser):
    """Collect real start tags and their source offsets without rewriting HTML."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[int, str, str, dict[str, str]]] = []
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", text))
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line, column = self.getpos()
        self.tags.append((
            self.lines[line - 1] + column, tag, self.get_starttag_text(),
            {name: value or "" for name, value in attrs},
        ))

    handle_startendtag = handle_starttag


def _attr(tag: str, attr: str) -> str:
    tags = _StartTags(tag).tags
    return tags[0][3].get(attr, "") if tags else ""


_HTML_ATTRIBUTE_RE = re.compile(
    r"""\s+([^\s=/>]+)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?"""
)


def _is_ai_attr(name: str) -> bool:
    return re.fullmatch(r"data-ai[\w-]*", name, re.I) is not None


def _meta_names(tag: str) -> str:
    return " ".join(
        _attr(tag, attr) for attr in ("name", "property", "itemprop", "http-equiv")
    ).strip()


def _meta_is_provenance(tag: str) -> tuple[bool, bool]:
    """Match the meta name or property, not the content. Generator drops only for an AI tool."""
    names = _meta_names(tag)
    if not names:
        return False, False
    if re.search(r"\bgenerator\b", names, re.I):
        if _AI_TOOL_RE.search(_attr(tag, "content")):
            return True, False
        names = re.sub(r"\bgenerator\b", "", names, flags=re.I).strip()
        if not names:
            return False, False
    if _PROVENANCE_NAME_RE.search(names):
        return True, bool(re.search(r"c2pa|content.?credential", names, re.I))
    return False, False


def _jsonld_is_provenance(blob: str) -> bool:
    return _JSONLD_PROVENANCE_RE.search(blob) is not None


def inspect_html(text: str) -> tuple[bool, bool, list[str], dict]:
    findings: list[str] = []
    has_ai = False
    has_c2pa = False
    tags = _StartTags(text).tags
    for _offset, name, tag, _attrs in tags:
        if name != "meta":
            continue
        drop, c2pa = _meta_is_provenance(tag)
        if drop:
            has_ai = True
            findings.append(f"meta: {tag[:120]}")
            if c2pa:
                has_c2pa = True
    for m in _JSONLD_RE.finditer(text):
        blob = m.group(0)
        if _jsonld_is_provenance(blob):
            has_ai = True
            findings.append("json-ld provenance-like block")
            if re.search(r"c2pa|contentcredential", blob, re.I):
                has_c2pa = True
    # data-ai* attributes
    for _offset, _name, _tag, attrs in tags:
        for name, value in attrs.items():
            if _is_ai_attr(name):
                has_ai = True
                findings.append(f"attr: {name}={value}"[:86])
    return has_c2pa, has_ai, findings, {}


def clean_html(text: str) -> tuple[str, list[str]]:
    actions: list[str] = []

    edits: list[tuple[int, int, str]] = []
    removed_attrs = 0
    for offset, name, tag, _attrs in _StartTags(text).tags:
        if name == "meta" and _meta_is_provenance(tag)[0]:
            actions.append(f"drop meta: {tag[:80]}")
            edits.append((offset, offset + len(tag), ""))
            continue

        def _attribute_sub(match: re.Match[str]) -> str:
            nonlocal removed_attrs
            if _is_ai_attr(match.group(1)):
                removed_attrs += 1
                return ""
            return match.group(0)

        cleaned = _HTML_ATTRIBUTE_RE.sub(_attribute_sub, tag)
        if cleaned != tag:
            edits.append((offset, offset + len(tag), cleaned))
    out = text
    for start, end, replacement in reversed(edits):
        out = out[:start] + replacement + out[end:]
    if removed_attrs:
        actions.append(f"drop data-ai* attributes x{removed_attrs}")

    def _jsonld_sub(m: re.Match[str]) -> str:
        blob = m.group(0)
        if _jsonld_is_provenance(blob):
            actions.append("drop json-ld provenance-like script")
            return ""
        return blob

    out = _JSONLD_RE.sub(_jsonld_sub, out)
    if not actions:
        actions.append("no HTML AI meta removed")
    return out, actions


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def inspect_svg(data: bytes) -> tuple[bool, bool, list[str], dict]:
    findings: list[str] = []
    has_c2pa, has_ai, hits = _blob_hits(data)
    findings.extend(hits)
    try:
        text = data.decode("utf-8", errors="replace")
        if re.search(r"<metadata[\s>]", text, re.I):
            findings.append("svg <metadata> present")
            has_ai = True  # often XMP; treat as inspect signal
        if re.search(r"xmpmeta|rdf:RDF|contentcredentials", text, re.I):
            has_ai = True
            findings.append("XMP/RDF-like content in SVG")
        if re.search(r"c2pa|jumbf", text, re.I):
            has_c2pa = True
    except Exception as e:
        findings.append(f"svg decode note: {e}")
    return has_c2pa, has_ai or has_c2pa, findings, {}


def clean_svg(data: bytes) -> tuple[bytes, list[str]]:
    actions: list[str] = []
    text = data.decode("utf-8", errors="surrogateescape")
    # Drop metadata blocks
    new, n = re.subn(
        r"<metadata\b[^>]*>.*?</metadata\s*>",
        "",
        text,
        flags=re.I | re.DOTALL,
    )
    if n:
        actions.append(f"drop <metadata> x{n}")
        text = new
    # Drop adobe xmp packets
    new, n = re.subn(
        r"<x:xmpmeta\b[^>]*>.*?</x:xmpmeta\s*>",
        "",
        text,
        flags=re.I | re.DOTALL,
    )
    if n:
        actions.append(f"drop xmpmeta x{n}")
        text = new
    # Drop comments that look like provenance
    def _cmt(m: re.Match[str]) -> str:
        body = m.group(0)
        if AI_META_NAME_RE.search(body):
            actions.append("drop SVG comment with AI markers")
            return ""
        return body

    text = re.sub(r"<!--.*?-->", _cmt, text, flags=re.DOTALL)
    new, n = re.subn(
        r'\s(inkscape:version|sodipodi:docname|generator)\s*=\s*"[^"]*"',
        "",
        text,
        flags=re.I,
    )
    if n:
        actions.append(f"drop generator-like attrs x{n}")
        text = new
    if not actions:
        actions.append("no SVG metadata removed")
    return text.encode("utf-8", errors="surrogateescape"), actions


# ---------------------------------------------------------------------------
# DOCX / ODT (zip + XML)
# ---------------------------------------------------------------------------

DOCX_META_PARTS = (
    "docProps/core.xml",
    "docProps/app.xml",
    "docProps/custom.xml",
)
DOCX_CUSTOM_PREFIXES = (
    "customXml/",
    "docProps/",
)

_CUSTOMXML_REL_RE = re.compile(
    r"[ \t]*<Relationship\b[^>]*\bTarget=\"[^\"]*customXml/[^\"]*\"[^>]*/>\s*",
    re.I,
)


def _layer_a_xml_text(
    xml: str, *, nfkc: bool = False, aggressive_homoglyphs: bool = False
) -> tuple[str, int, int]:
    """Run Layer A on XML text nodes. Tags, including attributes, are left as-is."""
    removed = 0
    replaced = 0
    out: list[str] = []
    for part in re.split(r"(<[^>]*>)", xml):
        if part.startswith("<"):
            out.append(part)
            continue
        cleaned, stats = clean_text(
            part, nfkc=nfkc, aggressive_homoglyphs=aggressive_homoglyphs
        )
        removed += stats["removed_count"]
        replaced += stats["replaced_count"]
        out.append(cleaned)
    return "".join(out), removed, replaced


def _docx_prose(name: str) -> bool:
    if not name.startswith("word/") or not name.endswith(".xml") or name.count("/") != 1:
        return False
    base = name[len("word/") :]
    return base == "document.xml" or base.startswith(
        ("header", "footer", "footnotes", "endnotes", "comments")
    )


def _scrub_prose(
    text: str, actions: list[str], enabled: bool, text_flags: dict[str, bool]
) -> str:
    if not enabled:
        return text
    new, stats = clean_text(text, **text_flags)
    if new == text:
        return text
    actions.append(
        f"layer A text: removed={stats['removed_count']} replaced={stats['replaced_count']}"
    )
    return new


def _apply_layer_a_xml(
    raw: bytes, actions: list[str], *, nfkc: bool, aggressive_homoglyphs: bool
) -> bytes:
    text = raw.decode("utf-8", errors="surrogateescape")
    new, removed, replaced = _layer_a_xml_text(
        text, nfkc=nfkc, aggressive_homoglyphs=aggressive_homoglyphs
    )
    if new == text:
        return raw
    actions.append(f"layer A text: removed={removed} replaced={replaced}")
    return new.encode("utf-8", errors="surrogateescape")


def _zip_namelist(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def inspect_docx(data: bytes) -> tuple[bool, bool, list[str], dict]:
    findings: list[str] = []
    has_c2pa = False
    has_ai = False
    parts: list[str] = []
    budget = _ZipBudget()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            parts = zf.namelist()
            for info in zf.infolist():
                name = info.filename
                raw = budget.read(zf, info)
                c2, ai, hits = _blob_hits(raw)
                if c2 or ai:
                    if c2:
                        has_c2pa = True
                    if ai:
                        has_ai = True
                    findings.append(f"{name}: {', '.join(hits[:6])}")
            # always flag customXml presence lightly
            custom = [n for n in parts if n.startswith("customXml/")]
            if custom:
                findings.append(f"customXml parts: {len(custom)}")
    except zipfile.BadZipFile:
        return False, False, ["not a valid DOCX zip"], {"error": "not a valid DOCX zip"}
    except ZipTooLarge as e:
        findings.append(f"refused oversized zip content: {e}")
        return has_c2pa, True, findings, {"parts": len(parts), "oversized": True}
    return has_c2pa, has_ai or has_c2pa, findings, {"parts": len(parts)}


def clean_docx(
    data: bytes, *, nfkc: bool = False, aggressive_homoglyphs: bool = False
) -> tuple[bytes, list[str]]:
    actions: list[str] = []
    budget = _ZipBudget()
    out_buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(
        out_buf, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            name = info.filename
            raw = budget.read(zin, info)
            # Drop customXml wholesale — a common provenance inject; the body stays in word/
            if name.startswith("customXml/"):
                actions.append(f"drop part {name}")
                continue
            if name.endswith(".rels"):
                text = raw.decode("utf-8", errors="surrogateescape")
                new, n = _CUSTOMXML_REL_RE.subn("", text)
                if n:
                    actions.append(f"drop {n} customXml relationship(s) in {name}")
                    raw = new.encode("utf-8", errors="surrogateescape")
            if _docx_prose(name):
                raw = _apply_layer_a_xml(
                    raw, actions, nfkc=nfkc, aggressive_homoglyphs=aggressive_homoglyphs
                )
            if name in DOCX_META_PARTS or name.startswith("docProps/"):
                text = raw.decode("utf-8", errors="replace")
                # Scrub known AI generator fields via simple regex on XML text nodes
                new = text
                for pat, repl, label in (
                    (
                        r"(<dc:creator[^>]*>)(.*?)(</dc:creator>)",
                        None,
                        "dc:creator",
                    ),
                    (
                        r"(<cp:lastModifiedBy[^>]*>)(.*?)(</cp:lastModifiedBy>)",
                        None,
                        "cp:lastModifiedBy",
                    ),
                    (
                        r"(<Application[^>]*>)(.*?)(</Application>)",
                        None,
                        "Application",
                    ),
                    (
                        r"(<AppVersion[^>]*>)(.*?)(</AppVersion>)",
                        None,
                        "AppVersion",
                    ),
                ):
                    def _sub(m: re.Match[str], _label=label) -> str:
                        inner = m.group(2)
                        if AI_META_NAME_RE.search(inner) or AI_META_NAME_RE.search(_label):
                            actions.append(f"scrub {name} field {_label}")
                            return m.group(1) + m.group(3)
                        # Always clear Application if it looks like AI
                        if _label in ("Application", "AppVersion") and re.search(
                            r"claude|openai|anthropic|gemini|chatgpt|synthid|copilot",
                            inner,
                            re.I,
                        ):
                            actions.append(f"scrub {name} field {_label}")
                            return m.group(1) + m.group(3)
                        return m.group(0)

                    new = re.sub(pat, _sub, new, flags=re.I | re.DOTALL)
                # Drop custom.xml entirely if AI-ish
                if name.endswith("custom.xml") and (
                    _blob_hits(raw)[1] or AI_META_NAME_RE.search(text)
                ):
                    actions.append(f"drop part {name}")
                    continue
                raw = new.encode("utf-8")
            # content types: leave as-is (removing overrides for dropped customXml is nice-to-have)
            if name == "[Content_Types].xml":
                text = raw.decode("utf-8", errors="replace")
                new, n = re.subn(
                    r'<Override\b[^>]*PartName="/customXml/[^"]*"[^>]*/>',
                    "",
                    text,
                )
                if n:
                    actions.append(f"drop Content_Types customXml overrides x{n}")
                    raw = new.encode("utf-8")
            zout.writestr(info, raw)
    if not actions:
        actions.append("no DOCX metadata parts removed")
    return out_buf.getvalue(), actions


def inspect_odt(data: bytes) -> tuple[bool, bool, list[str], dict]:
    findings: list[str] = []
    has_c2pa = False
    has_ai = False
    budget = _ZipBudget()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                name = info.filename
                raw = budget.read(zf, info)
                c2, ai, hits = _blob_hits(raw)
                if c2 or ai:
                    if c2:
                        has_c2pa = True
                    if ai:
                        has_ai = True
                    findings.append(f"{name}: {', '.join(hits[:6])}")
                if name == "meta.xml" and re.search(
                    r"generator|claude|openai|anthropic|gemini",
                    raw.decode("utf-8", errors="replace"),
                    re.I,
                ):
                    has_ai = True
                    findings.append("meta.xml generator-like fields")
    except zipfile.BadZipFile:
        return False, False, ["not a valid ODT zip"], {"error": "not a valid ODT zip"}
    except ZipTooLarge as e:
        findings.append(f"refused oversized zip content: {e}")
        return has_c2pa, True, findings, {"oversized": True}
    return has_c2pa, has_ai or has_c2pa, findings, {}


def clean_odt(
    data: bytes, *, nfkc: bool = False, aggressive_homoglyphs: bool = False
) -> tuple[bytes, list[str]]:
    actions: list[str] = []
    budget = _ZipBudget()
    out_buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(
        out_buf, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            name = info.filename
            raw = budget.read(zin, info)
            if name == "content.xml":
                raw = _apply_layer_a_xml(
                    raw, actions, nfkc=nfkc, aggressive_homoglyphs=aggressive_homoglyphs
                )
            if name == "meta.xml":
                text = raw.decode("utf-8", errors="replace")
                new, n = re.subn(
                    r"<meta:generator\b[^>]*>.*?</meta:generator\s*>",
                    "",
                    text,
                    flags=re.I | re.DOTALL,
                )
                if n:
                    actions.append("drop meta:generator")
                    text = new
                # scrub creator-like if AI
                def _creator(m: re.Match[str]) -> str:
                    if AI_META_NAME_RE.search(m.group(0)):
                        actions.append("scrub creator-like meta")
                        return ""
                    return m.group(0)

                text = re.sub(
                    r"<dc:creator\b[^>]*>.*?</dc:creator\s*>",
                    _creator,
                    text,
                    flags=re.I | re.DOTALL,
                )
                raw = text.encode("utf-8")
            else:
                c2, ai, _ = _blob_hits(raw)
                if (c2 or ai) and name not in (
                    "content.xml",
                    "styles.xml",
                    "mimetype",
                    "META-INF/manifest.xml",
                ):
                    actions.append(f"drop part {name} (AI/C2PA markers)")
                    continue
            zout.writestr(info, raw)
    if not actions:
        actions.append("no ODT metadata removed")
    return out_buf.getvalue(), actions


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def inspect_pdf(path: Path, data: bytes) -> tuple[bool, bool, list[str], dict]:
    findings: list[str] = []
    has_c2pa, has_ai, hits = _blob_hits(data)
    findings.extend(f"pdf-bytes:{h}" for h in hits)
    # XMP packet scan
    if b"<x:xmpmeta" in data or b"application/rdf+xml" in data:
        findings.append("XMP packet present")
        has_ai = has_ai or bool(
            re.search(
                rb"digitalSourceType|trainedAlgorithmicMedia|SoftwareAgent|c2pa",
                data,
                re.I,
            )
        )
    tools = run_optional_tools(path)
    ct = tools.get("c2patool") or {}
    if ct.get("has_manifest"):
        has_c2pa = True
        findings.append("c2patool reports C2PA-related manifest")
    return has_c2pa, has_ai or has_c2pa, findings, {"tools": tools}


def _pdf_name(value: object) -> str:
    if value is None:
        return ""
    return str(value).lstrip("/")


def _filespec_is_c2pa(spec: object) -> bool:
    """C2PA spec A.4: a manifest store is an embedded file with this relationship or subtype."""
    if spec is None or not hasattr(spec, "get"):
        return False
    if _pdf_name(spec.get("/AFRelationship")) == "C2PA_Manifest":
        return True
    ef = spec.get("/EF")
    if ef is None:
        return False
    for key in ("/F", "/UF"):
        stream = ef.get(key)
        if stream is not None and _pdf_name(stream.get("/Subtype")) == "application/c2pa":
            return True
    return False


def _strip_af(obj: object, actions: list[str], where: str) -> None:
    import pikepdf

    if obj is None or "/AF" not in obj:
        return
    af = obj["/AF"]
    items = list(af) if isinstance(af, pikepdf.Array) else [af]
    kept = [item for item in items if not _filespec_is_c2pa(item)]
    if len(kept) == len(items):
        return
    actions.append(f"pikepdf: drop C2PA associated file on {where}")
    if kept:
        obj["/AF"] = pikepdf.Array(kept)
    else:
        del obj["/AF"]


def _strip_pdf_c2pa(pdf: object, actions: list[str]) -> None:
    import pikepdf

    root = pdf.Root
    _strip_af(root, actions, "catalog")
    names = root.get("/Names")
    if names is not None and "/EmbeddedFiles" in names:
        tree = pikepdf.NameTree(names["/EmbeddedFiles"])
        for key in list(tree):
            if _filespec_is_c2pa(tree[key]):
                del tree[key]
                actions.append(f"pikepdf: drop C2PA embedded file {key}")
        if len(tree) == 0:
            del names["/EmbeddedFiles"]
    for i, page in enumerate(pdf.pages):
        obj = page.obj
        _strip_af(obj, actions, f"page {i}")
        resources = obj.get("/Resources")
        xobjects = resources.get("/XObject") if resources is not None else None
        if xobjects is not None:
            for xname, xo in xobjects.items():
                _strip_af(xo, actions, f"page {i} xobject {xname}")
        if "/Annots" not in obj:
            continue
        kept = []
        dropped = False
        for annot in obj["/Annots"]:
            fs = annot.get("/FS")
            if _pdf_name(annot.get("/Subtype")) == "FileAttachment" and _filespec_is_c2pa(fs):
                dropped = True
                continue
            _strip_af(annot, actions, f"page {i} annotation")
            kept.append(annot)
        if dropped:
            actions.append(f"pikepdf: drop C2PA file attachment on page {i}")
            if kept:
                obj["/Annots"] = pikepdf.Array(kept)
            else:
                del obj["/Annots"]


def _pikepdf_clean(path: Path, dest: Path, actions: list[str]) -> bool:
    """Strip PDF metadata and rewrite the file. Returns True when it ran.

    pikepdf bundles libqpdf and always writes a fresh file, so unlike an exiftool
    edit there is no prior revision left holding the metadata.
    """
    try:
        import pikepdf
    except ImportError:
        return False
    try:
        with pikepdf.open(path) as pdf:
            if "/Info" in pdf.trailer:
                del pdf.trailer["/Info"]
                actions.append("pikepdf: drop document info dictionary")
            if "/Metadata" in pdf.Root:
                del pdf.Root["/Metadata"]
                actions.append("pikepdf: drop XMP metadata")
            _strip_pdf_c2pa(pdf, actions)
            for i, page in enumerate(pdf.pages):
                if "/Metadata" in page.obj:
                    del page.obj["/Metadata"]
                    actions.append(f"pikepdf: drop page {i} XMP metadata")
            pdf.save(dest, linearize=True)
    except Exception as e:
        actions.append(f"pikepdf failed: {e}")
        return False
    actions.append("pikepdf: rewrote PDF without prior revisions")
    return True


def _qpdf_flatten(dest: Path, actions: list[str]) -> bool:
    """Rewrite the PDF without its prior revisions. Returns True on success.

    exiftool edits a PDF as an incremental update, so the metadata it "removed"
    is still readable in the previous revision until the file is rewritten.
    """
    qpdf = which("qpdf")
    if not qpdf:
        actions.append(
            "warning: exiftool leaves the old PDF revision in place; "
            "install qpdf so the stripped metadata cannot be recovered"
        )
        return False
    try:
        r = subprocess.run(
            [qpdf, "--linearize", "--replace-input", tool_arg(dest)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as e:
        actions.append(f"qpdf failed: {e}")
        return False
    # qpdf exits 3 on warnings but still writes a usable file.
    if r.returncode not in (0, 3):
        actions.append(f"qpdf rewrite failed (rc={r.returncode}); old revision may remain")
        return False
    actions.append(f"qpdf --linearize dropped prior revisions (rc={r.returncode})")
    return True


def clean_pdf(path: Path, dest: Path) -> tuple[list[str], dict]:
    """Best-effort PDF clean: pikepdf, else exiftool + qpdf, else a degraded XMP strip."""
    actions: list[str] = []
    data = path.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if _pikepdf_clean(path, dest, actions):
        return actions, {"mode": "pikepdf", "degraded": False}

    exiftool = which("exiftool")
    if exiftool:
        dest.write_bytes(data)
        try:
            r = subprocess.run(
                [
                    exiftool,
                    "-all=",
                    "-overwrite_original",
                    tool_arg(dest),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            actions.append(f"exiftool -all= (rc={r.returncode})")
        except Exception as e:
            actions.append(f"exiftool failed: {e}")
        flattened = _qpdf_flatten(dest, actions)
        c2patool = which("c2patool")
        # c2patool does not always strip; leave note
        if c2patool:
            actions.append("c2patool available for inspect; strip via exiftool/re-export")
        return actions, {"mode": "exiftool", "degraded": not flattened}

    # Degraded: strip obvious XMP packets between <?xpacket begin and end
    text = data
    new, n = re.subn(
        rb"<\?xpacket begin.*?<\?xpacket end[^?]*\?>",
        b"",
        text,
        flags=re.I | re.DOTALL,
    )
    if n:
        actions.append(f"stripped XMP xpacket x{n} (degraded; may leave offsets broken)")
        # PDF structural risk: document degraded mode clearly
        dest.write_bytes(new)
        actions.append("warning: pure-stdlib PDF strip is best-effort; prefer exiftool")
        return actions, {"mode": "stdlib-xmp", "degraded": True}

    dest.write_bytes(data)
    actions.append(
        "no PDF cleaner available (run ensure_tools.py --install for pikepdf); copied as-is"
    )
    return actions, {"mode": "copy", "degraded": True}


# ---------------------------------------------------------------------------
# Unified API
# ---------------------------------------------------------------------------

def inspect_container(path: Path) -> ContainerInspectReport:
    data = path.read_bytes()
    fmt = detect_container_format(path, data)
    tools: dict[str, Any] = {}
    details: dict[str, Any] = {}

    error = ""
    if fmt == "svg":
        has_c2pa, has_ai, findings, details = inspect_svg(data)
    elif fmt == "pdf":
        has_c2pa, has_ai, findings, details = inspect_pdf(path, data)
        tools = details.pop("tools", {})
    elif fmt == "docx":
        has_c2pa, has_ai, findings, details = inspect_docx(data)
    elif fmt == "odt":
        has_c2pa, has_ai, findings, details = inspect_odt(data)
    elif fmt == "html":
        text = data.decode("utf-8", errors="replace")
        has_c2pa, has_ai, findings, details = inspect_html(text)
    elif fmt == "markdown":
        text = data.decode("utf-8", errors="replace")
        has_c2pa, has_ai, findings, details = inspect_markdown(text)
    else:
        has_c2pa, has_ai, findings = False, False, [f"unsupported container: {fmt}"]
        error = findings[0]

    if isinstance(details, dict) and details.get("error"):
        error = str(details.pop("error"))

    if fmt in ("svg", "pdf", "docx") and not tools:
        tools = run_optional_tools(path)

    return ContainerInspectReport(
        path=str(path),
        format=fmt,
        has_c2pa=has_c2pa,
        has_ai_metadata=has_ai,
        findings=findings,
        tools=tools,
        details=details,
        error=error,
    )


def clean_container(
    path: Path,
    dest: Path,
    *,
    also_layer_a_text: bool = True,
    nfkc: bool = False,
    aggressive_homoglyphs: bool = False,
) -> dict[str, Any]:
    """Clean container metadata. Layer A also runs on HTML, Markdown, and document text."""
    data = path.read_bytes()
    fmt = detect_container_format(path, data)
    actions: list[str] = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {"format": fmt}
    text_flags = {"nfkc": nfkc, "aggressive_homoglyphs": aggressive_homoglyphs}

    if fmt == "svg":
        cleaned, actions = clean_svg(data)
        dest.write_bytes(cleaned)
    elif fmt == "pdf":
        actions, meta_extra = clean_pdf(path, dest)
        meta.update(meta_extra)
    elif fmt == "docx":
        cleaned, actions = clean_docx(data, **text_flags)
        dest.write_bytes(cleaned)
    elif fmt == "odt":
        cleaned, actions = clean_odt(data, **text_flags)
        dest.write_bytes(cleaned)
    elif fmt == "html":
        text = data.decode("utf-8", errors="surrogateescape")
        text, actions = clean_html(text)
        text = _scrub_prose(text, actions, also_layer_a_text, text_flags)
        write_text_file(dest, text)
    elif fmt == "markdown":
        text = data.decode("utf-8", errors="surrogateescape")
        text, actions = clean_markdown(text)
        text = _scrub_prose(text, actions, also_layer_a_text, text_flags)
        write_text_file(dest, text)
    else:
        raise ValueError(f"unsupported container format: {fmt}")

    after = inspect_container(dest)
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
        "meta": meta,
    }
