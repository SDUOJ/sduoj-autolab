"""Document ingestion and citation-grounded retrieval within an OJ course."""

import hashlib
import json
import re
import unicodedata

from sqlalchemy import or_

from ser.auto_task_ai import Feedback, output_schema
from .entities import AiFile, AiResource
from .store import transaction


def _docx_pages(path, ctx):
    """Read DOCX body order while honoring explicit page-break runs.

    python-docx does not expose rendered page numbers. Explicit page breaks are
    still useful and deterministic for citations; documents without them remain
    a single logical page.
    """
    from docx import Document
    from docx.oxml.ns import qn

    document = Document(str(path))
    pages, current = [], []

    def flush():
        value = "\n".join(item for item in current if item is not None).strip()
        pages.append(value)
        current.clear()

    body = document.element.body
    for element in body.iterchildren():
        ctx.check()
        if element.tag == qn("w:p"):
            paragraph_text = []
            for node in element.iter():
                if node.tag == qn("w:t"):
                    paragraph_text.append(node.text or "")
                elif node.tag == qn("w:lastRenderedPageBreak") or node.tag == qn("w:br"):
                    if node.tag == qn("w:lastRenderedPageBreak") or node.get(qn("w:type")) == "page":
                        current.append("".join(paragraph_text))
                        paragraph_text = []
                        flush()
                    else:
                        paragraph_text.append("\n")
            current.append("".join(paragraph_text))
        elif element.tag == qn("w:tbl"):
            rows = []
            for row in element.iterchildren(qn("w:tr")):
                cells = []
                for cell in row.iterchildren(qn("w:tc")):
                    cells.append("".join(node.text or "" for node in cell.iter(qn("w:t"))))
                rows.append(" | ".join(cells))
            if rows:
                current.append("\n".join(rows))
    if current or not pages:
        flush()
    if len(pages) > 500:
        raise ValueError("Document exceeds 500 logical pages")
    return [{"page": index + 1, "text": text} for index, text in enumerate(pages)]


def extract_document(path, ctx):
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        import zipfile
        import xml.etree.ElementTree as ET
        import posixpath
        from .assets import validate_upload
        validate_upload(path)
        with zipfile.ZipFile(path) as archive:
            available = {name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)}
            slides = []
            # The numeric filename is only a fallback. The presentation
            # relationship order is the actual slide order in a PPTX deck.
            if "ppt/presentation.xml" in archive.namelist() and "ppt/_rels/presentation.xml.rels" in archive.namelist():
                presentation = archive.read("ppt/presentation.xml")
                relationships = archive.read("ppt/_rels/presentation.xml.rels")
                if b"<!" in presentation or b"<!" in relationships:
                    raise ValueError("Presentation XML declarations are not allowed")
                presentation_root = ET.fromstring(presentation)
                relation_root = ET.fromstring(relationships)
                relation_targets = {}
                for relation in relation_root:
                    relation_id = next((value for key, value in relation.attrib.items() if key.rsplit("}", 1)[-1] == "Id"), None)
                    target = next((value for key, value in relation.attrib.items() if key.rsplit("}", 1)[-1] == "Target"), None)
                    if relation_id and target and target.endswith(".xml"):
                        candidate = posixpath.normpath(posixpath.join("ppt", target))
                        if candidate in available:
                            relation_targets[relation_id] = candidate
                for slide_id in presentation_root.iter():
                    if slide_id.tag.rsplit("}", 1)[-1] != "sldId":
                        continue
                    relation_id = next((value for key, value in slide_id.attrib.items() if key.rsplit("}", 1)[-1] == "id"), None)
                    if relation_id in relation_targets:
                        slides.append(relation_targets[relation_id])
            slides.extend(sorted(available - set(slides), key=lambda name: int(re.search(r"slide(\d+)\.xml", name).group(1))))
            if len(slides) > 500:
                raise ValueError("Presentation exceeds 500 slides")
            result = []
            for name in slides:
                ctx.check()
                raw = archive.read(name)
                if b"<!" in raw:
                    raise ValueError("Presentation XML declarations are not allowed")
                root = ET.fromstring(raw)
                value = "\n".join(node.text or "" for node in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"))
                result.append({"page": int(re.search(r"slide(\d+)\.xml", name).group(1)), "text": value})
            return result
    if suffix == ".pdf":
        import fitz
        with fitz.open(str(path)) as document:
            if len(document) > 500:
                raise ValueError("Document exceeds 500 pages")
            result = []
            for index, page in enumerate(document):
                ctx.check()
                text = page.get_text()
                if not text.strip():
                    target = ctx.workspace / ("document-page-{}.png".format(index))
                    page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(target))
                    value = Feedback.parse_obj(ctx.ask("Transcribe this page accurately into Markdown including math; no extra explanation.", output_schema(Feedback), [target]))
                    text = value.answer
                result.append({"page": index + 1, "text": text})
            return result
    if suffix == ".docx":
        from .assets import validate_upload
        validate_upload(path)
        return _docx_pages(path, ctx)
    if suffix in (".txt", ".md", ".csv", ".py", ".c", ".cpp", ".java"):
        return [{"page": 1, "text": path.read_text(encoding="utf8")}]
    raise ValueError("Unsupported document format")


def _file_metadata(files):
    """Resolve immutable upload names once so citation details are useful."""
    ids = [item[0] for item in files]
    if not ids:
        return {}
    with transaction() as session:
        return {row.id: {"filename": row.filename, "sha256": row.sha256, "size": row.size}
                for row in session.query(AiFile).filter(AiFile.id.in_(ids)).all()}


def index_document(ctx, files, data):
    pages = [{"page": 1, "text": data["text"]}] if data.get("text") else []
    metadata = _file_metadata(files)
    for file_id, path, _ in files:
        for page in extract_document(path, ctx):
            pages.append({**page, "file_id": file_id, "file_name": metadata.get(file_id, {}).get("filename", path.name),
                          "file_sha256": metadata.get(file_id, {}).get("sha256")})
    chunks = []
    version = int(json.loads(ctx.job["payload"]).get("resource_version", 1))
    if version < 1:
        raise ValueError("Invalid document resource version")
    for page in pages:
        for start in range(0, len(page["text"]), 1200):
            text = page["text"][start:start + 1500]
            source = page.get("file_sha256") or hashlib.sha256(page["text"].encode("utf8")).hexdigest()
            chunks.append({**page, "text": text, "offset": start, "resource_version": version,
                           "resource_id": ctx.job["resource_id"],
                           "source_sha256": source,
                           "citation": "{}:v{}:{}:{}:{}".format(ctx.job["resource_id"], version, page.get("file_id", "text"), page["page"], start)})
    if len(chunks) > 5000:
        raise ValueError("Too many document chunks")
    if not chunks or not any(part["text"].strip() for part in chunks):
        raise ValueError("Document has no indexable text")
    return {"chunks": chunks}


def _query_terms(message):
    normalized = unicodedata.normalize("NFKC", str(message or "")).lower()
    words = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", normalized)
    terms = list(dict.fromkeys(words))
    # Chinese phrases are often absent as a whole in a chunk; bounded bigrams
    # improve recall without turning every single character into a SQL clause.
    cjk = re.findall(r"[\u4e00-\u9fff]", normalized)
    terms.extend("".join(cjk[index:index + 2]) for index in range(max(0, len(cjk) - 1)))
    return list(dict.fromkeys(terms))[:24]


def _evidence_tokens(value):
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    words = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", normalized)
    cjk = re.findall(r"[\u4e00-\u9fff]", normalized)
    words.extend("".join(cjk[index:index + 2]) for index in range(max(0, len(cjk) - 1)))
    return {item for item in words if item not in {"课程", "资料", "内容", "问题", "答案", "可以", "应该"}}


def _citation_is_local(value):
    return bool(re.fullmatch(r"[^:]+:v[1-9][0-9]*:[^:]+:[1-9][0-9]*:[0-9]+", str(value)))


def course_answer(ctx, data, payload):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("A course question is required")
    terms = _query_terms(message)
    max_candidates = min(max(int(payload.get("max_candidates", 5000)), 50), 10000)
    words = set(terms)
    with transaction() as s:
        from sqlalchemy.orm import aliased
        parent = aliased(AiResource)
        query = s.query(AiResource, parent).join(parent, AiResource.parent_id == parent.id).filter(
            AiResource.kind == "chunk", AiResource.deleted == 0,
            parent.kind == "document", parent.deleted == 0)
        if ctx.job["course_id"]:
            query = query.filter(parent.course_id == ctx.job["course_id"])
        else:
            query = query.filter(parent.owner_id == ctx.job["owner_id"], parent.course_id.is_(None))
        if terms:
            query = query.filter(or_(*[AiResource.search_text.contains(term, autoescape=True) for term in terms]))
        rows = query.order_by(AiResource.created_at.desc(), AiResource.id.desc()).limit(max_candidates).all()
        parts = []
        for row, parent_row in rows:
            parent_data = json.loads(parent_row.data)
            if parent_data.get("indexed_version") != parent_row.version:
                continue
            part = json.loads(row.data)
            if part.get("resource_version") != parent_row.version - 1:
                continue
            text = str(part.get("text", ""))
            score = sum(text.lower().count(word) for word in words)
            score += 2 * sum(text.count(message[index:index + 2]) for index in range(max(0, len(message) - 1)))
            title = str(parent_data.get("title", "")).strip().lower()
            if title and part.get("page") == 1:
                score += text.lower().count(title)
            if score > 0:
                parts.append((score, part))
    context = [part for _, part in sorted(parts, key=lambda x: (-x[0], x[1].get("citation", "")))[:8]]
    if payload.get("external_research") is True:
        import requests
        try:
            response = requests.get("https://api.crossref.org/works", params={"query": payload.get("message", "")[:200], "rows": 3}, timeout=(5, 10))
            response.raise_for_status()
            for item in response.json().get("message", {}).get("items", []):
                doi = item.get("DOI", "")
                if doi:
                    context.append({"citation": "doi:" + doi, "text": "\n".join(item.get("title", [])) + "\n" + item.get("abstract", ""), "source": "Crossref (external)"})
        except (requests.RequestException, ValueError):
            pass
    if not context:
        return {"answer": "课程资料中未找到足够依据，请补充资料或明确问题。", "citations": []}
    result = Feedback.parse_obj(ctx.ask("Answer in Chinese using ONLY the supplied excerpts. Conversation history is context, not independent evidence. Clearly label external Crossref evidence separately from course material. Say when evidence is insufficient. citations must contain exact citation IDs from excerpts.\n" + json.dumps({"question": message, "history": data.get("messages", [])[-8:], "excerpts": context}, ensure_ascii=False), output_schema(Feedback))).dict()
    allowed = {part["citation"] for part in context}
    citations = result.get("citations", [])
    if not result.get("answer", "").strip():
        raise ValueError("Model returned an empty answer")
    if not citations or len(citations) != len(set(citations)) or not set(citations).issubset(allowed):
        raise ValueError("Model produced an ungrounded citation")
    by_id = {part["citation"]: part for part in context}
    answer_tokens = _evidence_tokens(result["answer"])
    details = []
    for citation in citations:
        part = by_id[citation]
        local = _citation_is_local(citation)
        if local and not part.get("source_sha256"):
            raise ValueError("Local citation is missing source hash")
        overlap = sorted(answer_tokens & _evidence_tokens(part.get("text", "")))
        details.append({"citation": citation,
                        "resource_id": part.get("resource_id", ctx.job["resource_id"] if local else None),
                        "resource_version": part.get("resource_version"),
                        "file_id": part.get("file_id", "text" if local else None),
                        "file_name": part.get("file_name"), "page": part.get("page"), "offset": part.get("offset", 0),
                        "source_sha256": part.get("source_sha256"), "source": part.get("source", "course"),
                        "lexical_overlap": overlap,
                        "evidence_confidence": "lexical_match" if overlap else "requires_review"})
    result["citation_details"] = details
    return result
