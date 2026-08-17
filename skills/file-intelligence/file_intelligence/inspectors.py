from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree


TEXT_EXTENSIONS = {
    ".py", ".ps1", ".bat", ".cmd", ".md", ".txt", ".json", ".yaml", ".yml",
    ".xml", ".java", ".ipynb", ".csv", ".toml", ".ini", ".cfg", ".m",
}
REFERENCE_EXTENSIONS = {
    ".py", ".ps1", ".bat", ".cmd", ".md", ".txt", ".json", ".yaml", ".yml",
    ".xml", ".java", ".ipynb", ".csv", ".mph", ".cas", ".h5", ".dat",
    ".msh", ".docx", ".pptx", ".pdf", ".xlsx", ".mat", ".zip", ".step",
    ".stp", ".scdoc", ".scdocx", ".png", ".jpg", ".jpeg", ".svg", ".gif",
}
WINDOWS_ABSOLUTE_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]:[\\/][^\r\n\"'<>|*?]+)")
FILE_URI_RE = re.compile(r"file:///[A-Za-z]:/[^\s\"'<>]+", re.I)
QUOTED_PATH_RE = re.compile(r"[\"']([^\"'\r\n]{1,600}\.[A-Za-z0-9.]{1,12})[\"']")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\r\n]{1,600})\)")
BACKTICK_PATH_RE = re.compile(r"`([^`\r\n]{1,600}\.[A-Za-z0-9.]{1,12})`")
AUTHORITY_LINE_RE = re.compile(
    r"(?i)(canonical|authority|authoritative|source\s+of\s+truth|final\s+(?:model|manuscript|ppt|presentation)|"
    r"official\s+(?:model|document|presentation)|权威|正式(?:模型|稿件|报告|数据|文件|PPT|演示)|最终|基准模型|源模型).{0,240}"
)
NEGATED_AUTHORITY_RE = re.compile(
    r"(?i)(not\s+(?:canonical|authoritative|final)|do\s+not\s+use|failed|failure|rejected|retired|"
    r"non.authoritative|not\s+as.{0,100}authorit|without.{0,80}authorit|authority\s+candidates?|"
    r"candidate\s+only|human\s+confirmation\s+required|confirm\s+whether|review\s+only|reject(?:ed)?|"
    r"不可作为|不能作为|非权威|失败|废弃|淘汰)"
)


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _xml_text(raw: bytes) -> str:
    try:
        root = ElementTree.fromstring(raw)
        return "\n".join(value.strip() for value in root.itertext() if value and value.strip())
    except ElementTree.ParseError:
        return re.sub(r"<[^>]+>", " ", _decode_text(raw))


def _safe_zip_members(archive: zipfile.ZipFile, *, max_members: int = 5000) -> list[zipfile.ZipInfo]:
    return archive.infolist()[:max_members]


def _read_zip_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, limit: int) -> bytes:
    if member.file_size > limit:
        with archive.open(member) as handle:
            return handle.read(limit)
    return archive.read(member)


def _extract_docx(path: Path, limit: int) -> tuple[str, dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        members = {item.filename: item for item in _safe_zip_members(archive)}
        selected = [name for name in members if name == "word/document.xml" or name.startswith("word/header")]
        text = "\n".join(_xml_text(_read_zip_member(archive, members[name], limit)) for name in selected)
        return text[:limit], {"format": "docx", "members": len(members), "text_parts": len(selected)}


def _slide_number(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _extract_pptx(path: Path, limit: int) -> tuple[str, dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        members = {item.filename: item for item in _safe_zip_members(archive)}
        selected = sorted(
            (name for name in members if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_slide_number,
        )
        chunks: list[str] = []
        for name in selected:
            chunks.append(f"[Slide {_slide_number(name)}]\n{_xml_text(_read_zip_member(archive, members[name], limit))}")
            if sum(len(chunk) for chunk in chunks) >= limit:
                break
        return "\n".join(chunks)[:limit], {"format": "pptx", "members": len(members), "slides": len(selected)}


def _extract_pdf(path: Path, limit: int, *, max_pages: int = 40) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {"format": "pdf", "text_backend": "unavailable"}
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return "", metadata
    reader = PdfReader(str(path), strict=False)
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        chunks.append(page.extract_text() or "")
        if sum(len(chunk) for chunk in chunks) >= limit:
            break
    metadata.update({"pages": len(reader.pages), "pages_inspected": min(len(reader.pages), max_pages), "text_backend": "pypdf"})
    return "\n".join(chunks)[:limit], metadata


def _inspect_zip(path: Path) -> tuple[str, dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        members = _safe_zip_members(archive)
        names = [item.filename for item in members]
        metadata = {
            "format": "zip",
            "members": len(archive.infolist()),
            "listed_members": len(names),
            "uncompressed_size": sum(item.file_size for item in members),
            "member_sample": names[:80],
        }
        return "\n".join(names[:200]), metadata


def _inspect_hdf5(path: Path) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {"format": "hdf5", "metadata_backend": "unavailable"}
    try:
        import h5py  # type: ignore
    except ImportError:
        return "", metadata
    names: list[str] = []
    attrs: dict[str, str] = {}
    with h5py.File(path, "r") as handle:
        def visitor(name: str) -> str | None:
            if len(names) >= 500:
                return "metadata-limit-reached"
            names.append(name)
            return None

        handle.visit(visitor)
        for key, value in list(handle.attrs.items())[:50]:
            attrs[str(key)] = str(value)[:500]
    metadata.update({"metadata_backend": "h5py", "objects_sampled": len(names), "attributes": attrs})
    return "\n".join(names), metadata


def _binary_magic(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        head = handle.read(16)
    kind = "binary"
    if head.startswith(b"PK\x03\x04"):
        kind = "zip_container"
    elif head.startswith(b"\x89HDF\r\n\x1a\n"):
        kind = "hdf5_container"
    elif head.startswith(b"%PDF"):
        kind = "pdf"
    elif head.startswith(b"MATLAB"):
        kind = "matlab_data"
    return {"format": kind, "magic_hex": head.hex()}


def _candidate_reference_strings(text: str) -> Iterable[tuple[str, int]]:
    seen: set[tuple[str, int]] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        candidates: list[str] = []
        candidates.extend(match.group(1) for match in WINDOWS_ABSOLUTE_RE.finditer(line))
        candidates.extend(match.group(0) for match in FILE_URI_RE.finditer(line))
        candidates.extend(match.group(1) for match in QUOTED_PATH_RE.finditer(line))
        candidates.extend(match.group(1) for match in MARKDOWN_LINK_RE.finditer(line))
        candidates.extend(match.group(1) for match in BACKTICK_PATH_RE.finditer(line))
        for value in candidates:
            value = value.strip().strip("` \t,;:)")
            marker = (value, line_no)
            if value and marker not in seen:
                seen.add(marker)
                yield marker


def _path_from_reference(value: str, source: Path, project_root: Path) -> tuple[str | None, str]:
    parsed = urlparse(value)
    if parsed.scheme.casefold() == "file":
        value = unquote(parsed.path).lstrip("/")
    value = value.replace("/", "\\") if re.match(r"^[A-Za-z]:", value) else value
    suffix = Path(value.split("#", 1)[0].split("?", 1)[0]).suffix.casefold()
    if suffix and suffix not in REFERENCE_EXTENSIONS and not any(value.casefold().endswith(ext) for ext in REFERENCE_EXTENSIONS):
        return None, "not_file_like"
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate
    else:
        local = (source.parent / candidate)
        root_local = project_root / candidate
        resolved = local if local.exists() or not root_local.exists() else root_local
    try:
        normalized = resolved.resolve(strict=False)
    except OSError:
        normalized = resolved.absolute()
    return str(normalized), "resolved_existing" if normalized.exists() else "resolved_missing"


def extract_references(text: str, source: Path, project_root: Path) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for value, line_no in _candidate_reference_strings(text):
        resolved, status = _path_from_reference(value, source, project_root)
        if resolved is None:
            continue
        references.append(
            {
                "target_text": value,
                "resolved_path": resolved,
                "resolution_status": status,
                "line_no": line_no,
                "relation": "REFERENCES",
                "evidence_type": "document_evidence",
                "confidence": 0.96 if status == "resolved_existing" else 0.7,
            }
        )
    return references


def extract_authority_declarations(text: str, source: Path, project_root: Path) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    lines = text.splitlines()
    authority_section: str | None = None
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            authority_section = stripped.lstrip("#").strip() if AUTHORITY_LINE_RE.search(stripped) and not NEGATED_AUTHORITY_RE.search(stripped) else None
        direct = bool(AUTHORITY_LINE_RE.search(line) and not NEGATED_AUTHORITY_RE.search(line))
        section_target = bool(authority_section and stripped.startswith(("-", "*", "+", "|")))
        if not (direct or section_target):
            continue
        if NEGATED_AUTHORITY_RE.search(line):
            continue
        references = extract_references(line, source, project_root)
        if section_target and not references:
            continue
        declarations.append(
            {
                "line_no": line_no,
                "excerpt": (f"[{authority_section}] " if section_target else "") + line.strip()[:500],
                "targets": [item["resolved_path"] for item in references],
                "evidence_type": "explicit_project_metadata",
                "confidence": 0.98 if section_target and references else (0.97 if references else 0.82),
            }
        )
    return declarations


def inspect_file(path: Path, project_root: Path, *, text_limit: int = 2 * 1024 * 1024) -> dict[str, Any]:
    """Perform bounded, read-only structural/content inspection."""
    started = time.perf_counter()
    extension = path.suffix.casefold()
    lower_name = path.name.casefold()
    result: dict[str, Any] = {
        "inspector": "binary-metadata",
        "metadata": {},
        "text": "",
        "references": [],
        "authority_declarations": [],
        "error": None,
    }
    try:
        if extension in TEXT_EXTENSIONS or lower_name.startswith("readme"):
            raw = path.read_bytes()[:text_limit]
            text = _decode_text(raw)
            if extension == ".ipynb":
                try:
                    notebook = json.loads(text)
                    text = "\n".join(
                        "".join(cell.get("source", []))
                        for cell in notebook.get("cells", [])
                        if cell.get("cell_type") in {"markdown", "code"}
                    )[:text_limit]
                except (json.JSONDecodeError, TypeError):
                    pass
            result.update({"inspector": "bounded-text", "metadata": {"format": "text", "characters": len(text)}, "text": text})
        elif extension == ".docx":
            text, metadata = _extract_docx(path, text_limit)
            result.update({"inspector": "office-open-xml", "metadata": metadata, "text": text})
        elif extension == ".pptx":
            text, metadata = _extract_pptx(path, text_limit)
            result.update({"inspector": "office-open-xml", "metadata": metadata, "text": text})
        elif extension == ".pdf":
            if path.stat().st_size > 64 * 1024 * 1024:
                text, metadata = "", {"format": "pdf", "text_backend": "skipped_size_budget", "size_budget": 64 * 1024 * 1024}
            else:
                text, metadata = _extract_pdf(path, text_limit)
            result.update({"inspector": "pdf-lightweight", "metadata": metadata, "text": text})
        elif extension in {".zip", ".scdocx", ".mph"} and zipfile.is_zipfile(path):
            text, metadata = _inspect_zip(path)
            metadata["domain_extension"] = extension
            result.update({"inspector": "zip-structure", "metadata": metadata, "text": text})
        elif extension in {".h5", ".cas.h5", ".dat.h5", ".msh.h5", ".mat"}:
            text, metadata = _inspect_hdf5(path)
            metadata["domain_extension"] = extension
            result.update({"inspector": "hdf5-structure", "metadata": metadata, "text": text})
        else:
            result["metadata"] = _binary_magic(path)
        text = result["text"]
        result["text_digest"] = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest() if text else None
        result["references"] = extract_references(text, path, project_root) if text else []
        result["authority_declarations"] = extract_authority_declarations(text, path, project_root) if text else []
    except (OSError, PermissionError, zipfile.BadZipFile, ValueError, RuntimeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["text_digest"] = None
    result["metadata"]["inspection_elapsed_seconds"] = round(time.perf_counter() - started, 6)
    return result
