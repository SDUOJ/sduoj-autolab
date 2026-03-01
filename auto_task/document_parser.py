import asyncio
import base64
import io
import logging
import os
import re
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import parse_qs, quote, unquote, urlparse

import fitz
import requests
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image

try:  # pragma: no cover - optional dependency
    import pypandoc  # type: ignore
except ImportError:  # pragma: no cover
    pypandoc = None

from auto_task.llm_client import describe_images_to_text
from sduojApi import downloadFile, uploadFiles

IMAGE_PLACEHOLDER = "[[IMAGE:{}]]"
MD_LINK_PATTERN = re.compile(r"(!)?\[[^\]]*]\(([^)]+)\)")
# 匹配中文括号的 markdown 图片语法（非规范写法）
MD_LINK_CN_PATTERN = re.compile(r"(!)?\[[^\]]*]（([^）]+)）")
HTML_IMG_PATTERN = re.compile(r'<img\s+[^>]*src="([^"]+)"[^>]*>', re.IGNORECASE)
FILESYS_DOWNLOAD_RE = re.compile(r"/api/filesys/download/(\d+)(?:/([^/?#]+))?")
# 直接匹配裸露的文件系统下载链接
BARE_FILESYS_URL_PATTERN = re.compile(
    r'https?://[^/\s]+/api/filesys/download/\d+(?:/[^\s)）?#]+)?(?:\?[^\s)）]+)?'
)
FILE_ID_LITERAL_RE = re.compile(r"^\d{15,}$")



MAX_FILE_SIZE = 16 * 1024 * 1024
MAX_RECURSIVE_DOCS = 8
DOC_EXTENSIONS = {".md", ".markdown", ".doc", ".docx", ".pdf"}
URL_FILENAME_QUERY_KEYS = ("filename", "fileName", "name")
TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".text"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

logger = logging.getLogger(__name__)

@dataclass
class ImageResource:
    data: Optional[bytes] = None
    file_id: Optional[str] = None
    filename: Optional[str] = None


async def convert_document_to_markdown(
        data: Union[str, bytes],
        filename: Optional[str],
        user_id: int,
        depth: int = 0,
        task_id: Optional[str] = None) -> str:
    """Convert Markdown/Docx/PDF/text into Markdown while preserving images."""

    if user_id is None:
        raise ValueError("user_id 不能为空")
    if isinstance(data, bytes) and not data:
        raise ValueError("文件内容为空")

    if isinstance(data, str) and not filename and not _is_document_reference_input(data):
        # treat pure markdown text input
        content = data
        content, images, doc_links = await _parse_markdown(content, user_id, depth)
    else:
        file_bytes, inferred_name = await _load_document_bytes(data, filename)
        content, images, doc_links = await _convert_loaded_file_to_markdown(
            file_bytes=file_bytes,
            inferred_name=(filename or inferred_name) or "",
            user_id=user_id,
            depth=depth,
        )
    content = await _process_nested_documents(content, doc_links, user_id, depth, task_id)
    return await _embed_images(content, images, user_id, task_id)


async def _load_document_bytes(data: Union[str, bytes], filename: Optional[str]) -> Tuple[bytes, str]:
    if isinstance(data, bytes):
        return data[:MAX_FILE_SIZE], filename or ""
    source = _normalize_oj_file_url(data)
    file_id, linked_name = _extract_file_reference(source)
    if file_id:
        status, content, headers = await downloadFile(file_id)
        if status != 200:
            raise RuntimeError(f"下载文件 {data} 失败，状态码 {status}")
        if len(content) > MAX_FILE_SIZE:
            raise ValueError("文件过大，超过 16MB")
        inferred_name = filename or linked_name or _extract_filename_from_headers(headers) or _guess_filename_from_url(source)
        return content, inferred_name
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        resp = await _fetch_url(source)
        content = resp.content
        if len(content) > MAX_FILE_SIZE:
            raise ValueError("文件过大，超过 16MB")
        inferred_name = filename or _extract_filename_from_headers(dict(resp.headers)) or _guess_filename_from_url(source)
        return content, inferred_name
    status, content, headers = await downloadFile(source)
    if status != 200:
        raise RuntimeError(f"下载文件 {data} 失败，状态码 {status}")
    if len(content) > MAX_FILE_SIZE:
        raise ValueError("文件过大，超过 16MB")
    inferred_name = filename or _extract_filename_from_headers(headers)
    return content, inferred_name


async def _convert_loaded_file_to_markdown(
        file_bytes: bytes,
        inferred_name: str,
        user_id: int,
        depth: int) -> Tuple[str, List[ImageResource], List[str]]:
    ext = Path(inferred_name).suffix.lower()
    preferred: List[str] = []
    if ext in TEXT_EXTENSIONS:
        preferred.append("text")
    elif ext in {".doc", ".docx"}:
        preferred.append("docx")
    elif ext == ".pdf":
        preferred.append("pdf")
    elif ext in IMAGE_EXTENSIONS:
        preferred.append("image")

    ordered_strategies: List[str] = []
    for strategy in preferred + ["text", "image", "docx", "pdf"]:
        if strategy not in ordered_strategies:
            ordered_strategies.append(strategy)

    parse_errors: List[str] = []
    for strategy in ordered_strategies:
        try:
            if strategy == "text":
                text = _decode_text_bytes(file_bytes)
                parsed_content, images, doc_links = await _parse_markdown(text, user_id, depth)
                return parsed_content, images, doc_links
            if strategy == "image":
                image = _try_parse_image_bytes(file_bytes, inferred_name or None)
                return IMAGE_PLACEHOLDER.format(0), [image], []
            if strategy == "docx":
                content, images = _parse_docx(file_bytes)
                return content, images, []
            if strategy == "pdf":
                content, images = _parse_pdf(file_bytes)
                return content, images, []
        except Exception as exc:
            parse_errors.append(f"{strategy}: {exc}")
            continue
    raise ValueError(f"文件解析失败: {', '.join(parse_errors) if parse_errors else '未知错误'}")


async def _parse_markdown(text: str, user_id: int, depth: int) -> Tuple[str, List[ImageResource], List[str]]:
    images: List[ImageResource] = []
    doc_links: List[str] = []
    rebuilt: List[str] = []
    last = 0

    matches: List[Tuple[str, re.Match]] = []
    matches.extend([("md", match) for match in MD_LINK_PATTERN.finditer(text)])
    matches.extend([("md_cn", match) for match in MD_LINK_CN_PATTERN.finditer(text)])
    matches.extend([("html_img", match) for match in HTML_IMG_PATTERN.finditer(text)])
    matches.extend([("bare_url", match) for match in BARE_FILESYS_URL_PATTERN.finditer(text)])
    matches.sort(key=lambda item: item[1].start())

    for kind, match in matches:
        if match.start() < last:
            continue
        rebuilt.append(text[last:match.start()])
        if kind == "md":
            replacement = await _process_markdown_link(
                match,
                user_id,
                depth,
                images,
                doc_links,
            )
        elif kind == "md_cn":
            replacement = await _process_markdown_link_cn(
                match,
                user_id,
                depth,
                images,
                doc_links,
            )
        elif kind == "html_img":
            replacement = await _process_html_image(
                match,
                user_id,
                depth,
                images,
                doc_links,
            )
        else:  # bare_url
            replacement = await _process_bare_url(
                match,
                user_id,
                depth,
                images,
                doc_links,
            )
        rebuilt.append(replacement)
        last = match.end()
    rebuilt.append(text[last:])
    return "".join(rebuilt), images, doc_links


def _parse_docx(file_bytes: bytes) -> Tuple[str, List[ImageResource]]:
    if pypandoc is not None:
        try:
            return _parse_docx_with_pandoc(file_bytes)
        except Exception as exc:  # pragma: no cover - optional fallback
            logger.warning("Pandoc conversion failed (%s), fallback to internal parser", exc)
    return _parse_docx_fallback(file_bytes)


def _parse_docx_with_pandoc(file_bytes: bytes) -> Tuple[str, List[ImageResource]]:
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        markdown_text = pypandoc.convert_file(tmp_path, "gfm", format="docx")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    media = _extract_docx_media(file_bytes)
    images: List[ImageResource] = []

    def _replace_src(path: str, original: str) -> str:
        key = os.path.basename(path)
        data = media.get(key)
        if data is None:
            return original
        idx = len(images)
        images.append(ImageResource(data=data, filename=key))
        return IMAGE_PLACEHOLDER.format(idx)

    def _replace_md(match):
        path = match.group(2).strip()
        return _replace_src(path, match.group(0))

    def _replace_html(match):
        src = match.group(1).strip()
        return _replace_src(src, match.group(0))

    converted = re.sub(MD_LINK_PATTERN, _replace_md, markdown_text)
    converted = re.sub(HTML_IMG_PATTERN, _replace_html, converted)
    return converted.strip(), images


def _extract_docx_media(file_bytes: bytes) -> Dict[str, bytes]:
    media = {}
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/"):
                media[os.path.basename(name)] = zf.read(name)
    return media


def _parse_docx_fallback(file_bytes: bytes) -> Tuple[str, List[ImageResource]]:
    document = Document(io.BytesIO(file_bytes))
    images: List[ImageResource] = []
    seen_embeds: set = set()
    numbering_state: Dict[str, Dict[int, int]] = defaultdict(dict)
    blocks = []
    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = _extract_paragraph_text(block, images, seen_embeds, numbering_state)
            if text:
                blocks.append(text)
        elif isinstance(block, Table):
            rows_content = []
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    cell_parts = []
                    for paragraph in cell.paragraphs:
                        cell_text = _extract_paragraph_text(paragraph, images, seen_embeds, numbering_state)
                        if cell_text:
                            cell_parts.append(cell_text)
                    cells.append("\n".join(cell_parts))
                rows_content.append(" | ".join(cells))
            blocks.append("\n".join(rows_content))
    return "\n\n".join(blocks), images


def _parse_pdf(file_bytes: bytes) -> Tuple[str, List[ImageResource]]:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text_parts: List[str] = []
    images: List[ImageResource] = []
    for page_index in range(len(doc)):
        page = doc.load_page(page_index)
        text_parts.append(page.get_text("text"))
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = doc.extract_image(xref)
            if not base_image:
                continue
            img_bytes = base_image.get("image")
            if not img_bytes:
                continue
            idx = len(images)
            images.append(ImageResource(data=img_bytes, filename=f"pdf_{page_index}_{img_index}.png"))
            text_parts.append(f"\n{IMAGE_PLACEHOLDER.format(idx)}\n")
        text_parts.append("\n")
    return "".join(text_parts).strip(), images


async def _embed_images(base_text: str, images: Sequence[ImageResource], user_id: int, task_id: Optional[str]) -> str:
    if not images:
        return base_text

    upload_payload = []
    pending_indices: List[int] = []
    for idx, image in enumerate(images):
        if image.file_id or not image.data:
            continue
        png_data = _ensure_png(image.data)
        upload_payload.append({
            "filename": image.filename or f"doc_image_{idx}.png",
            "content": png_data,
            "content_type": "image/png",
        })
        pending_indices.append(idx)

    if upload_payload:
        upload_results = await uploadFiles(upload_payload, user_id)
        for offset, idx in enumerate(pending_indices):
            images[idx].file_id = str(upload_results[offset]["id"])

    file_indices = [idx for idx, image in enumerate(images) if image.file_id]
    if not file_indices:
        return base_text
    
    print(f"找到{len(file_indices)}张图片需要描述，开始调用 LLM 进行描述...")
    
    # 每次最多批量处理 8 张图片
    BATCH_SIZE = 8
    target_file_ids = [images[idx].file_id for idx in file_indices]
    all_descriptions = []
    
    # 分批处理图片
    for batch_start in range(0, len(target_file_ids), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(target_file_ids))
        batch_file_ids = target_file_ids[batch_start:batch_end]
        
        print(f"正在处理第 {batch_start // BATCH_SIZE + 1} 批图片 (共 {len(batch_file_ids)} 张)...")
        batch_descriptions = await describe_images_to_text(batch_file_ids, task_id=task_id)
        all_descriptions.extend(batch_descriptions)

    markdown = base_text
    for order, idx in enumerate(file_indices):
        desc = all_descriptions[order]
        replacement = desc.strip() + "\n"
        markdown = markdown.replace(IMAGE_PLACEHOLDER.format(idx), replacement, 1)
    return markdown


async def _process_nested_documents(
        content: str,
        doc_links: Sequence[str],
        user_id: int,
        depth: int,
        task_id: Optional[str]) -> str:
    if not doc_links:
        return content

    tasks = []
    for link in doc_links:
        tasks.append(convert_document_to_markdown(link, None, user_id, depth + 1, task_id))
    resolved = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, result in enumerate(resolved):
        placeholder = f"[[DOC:{idx}]]"
        if isinstance(result, Exception):
            replacement = f"\n> 无法解析嵌入文档：{result}\n"
        else:
            replacement = f"\n{result}\n"
        content = content.replace(placeholder, replacement, 1)
    return content


def _extract_paragraph_text(paragraph: Paragraph, images: List[ImageResource], seen_embeds: set,
                            numbering_state: Dict[str, Dict[int, int]]) -> str:
    parts: List[str] = []
    prefix = _get_list_prefix(paragraph, numbering_state)
    if prefix:
        parts.append(prefix)
    for run in paragraph.runs:
        math_nodes = run._element.xpath('.//m:oMath | .//m:oMathPara')
        if math_nodes:
            for node in math_nodes:
                parts.append(_extract_math_text(node))
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
        blips = run._element.xpath('.//a:blip')
        for blip in blips:
            r_embed = blip.get(qn('r:embed'))
            if not r_embed:
                continue
            if r_embed in seen_embeds:
                continue
            image_part = paragraph.part.related_parts.get(r_embed)
            if image_part is None:
                continue
            idx = len(images)
            seen_embeds.add(r_embed)
            images.append(ImageResource(data=image_part.blob, filename=image_part.partname or f"docx_{idx}.png"))
            parts.append(IMAGE_PLACEHOLDER.format(idx))
        if run.text:
            parts.append(run.text)
    text = ''.join(parts).strip()
    if paragraph.style and paragraph.style.name and paragraph.style.name.lower().startswith("list"):
        return f"- {text}"
    return text


def _iter_block_items(parent):
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    parent_elm = parent.element.body
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


async def _process_markdown_link(
        match: re.Match,
        user_id: int,
        depth: int,
        images: List[ImageResource],
        doc_links: List[str]) -> str:
    original = match.group(0)
    is_image = bool(match.group(1))
    url = _normalize_oj_file_url(match.group(2).strip())
    allow_document = depth < 1 and len(doc_links) < MAX_RECURSIVE_DOCS
    try:
        resource_type, payload = await _load_external_resource(url, user_id, allow_document, is_image)
    except Exception:
        if _is_filesys_download_url(url):
            return ""
        return original

    if resource_type == "image":
        idx = len(images)
        images.append(payload)
        return IMAGE_PLACEHOLDER.format(idx)
    if resource_type == "document" and allow_document:
        doc_links.append(payload)
        return f"[[DOC:{len(doc_links) - 1}]]"
    return original


async def _process_html_image(
        match: re.Match,
        user_id: int,
        depth: int,
        images: List[ImageResource],
        doc_links: List[str]) -> str:
    url = _normalize_oj_file_url(match.group(1).strip())
    allow_document = depth < 1 and len(doc_links) < MAX_RECURSIVE_DOCS
    try:
        resource_type, payload = await _load_external_resource(url, user_id, allow_document, True)
    except Exception:
        if _is_filesys_download_url(url):
            return ""
        return match.group(0)

    if resource_type == "image":
        idx = len(images)
        images.append(payload)
        return IMAGE_PLACEHOLDER.format(idx)
    if resource_type == "document" and allow_document:
        doc_links.append(payload)
        return f"[[DOC:{len(doc_links) - 1}]]"
    return match.group(0)


async def _process_markdown_link_cn(
        match: re.Match,
        user_id: int,
        depth: int,
        images: List[ImageResource],
        doc_links: List[str]) -> str:
    """处理中文括号的 markdown 链接（非规范写法）"""
    original = match.group(0)
    is_image = bool(match.group(1))
    url = _normalize_oj_file_url(match.group(2).strip())
    allow_document = depth < 1 and len(doc_links) < MAX_RECURSIVE_DOCS
    try:
        resource_type, payload = await _load_external_resource(url, user_id, allow_document, is_image)
    except Exception:
        if _is_filesys_download_url(url):
            return ""
        return original

    if resource_type == "image":
        idx = len(images)
        images.append(payload)
        return IMAGE_PLACEHOLDER.format(idx)
    if resource_type == "document" and allow_document:
        doc_links.append(payload)
        return f"[[DOC:{len(doc_links) - 1}]]"
    return original


async def _process_bare_url(
        match: re.Match,
        user_id: int,
        depth: int,
        images: List[ImageResource],
        doc_links: List[str]) -> str:
    """处理裸露的文件系统下载链接"""
    url = _normalize_oj_file_url(match.group(0).strip())
    allow_document = depth < 1 and len(doc_links) < MAX_RECURSIVE_DOCS
    try:
        # 由于是 /api/filesys/download/ 链接，通常是图片
        resource_type, payload = await _load_external_resource(url, user_id, allow_document, True)
    except Exception:
        if _is_filesys_download_url(url):
            return ""
        return match.group(0)

    if resource_type == "image":
        idx = len(images)
        images.append(payload)
        return IMAGE_PLACEHOLDER.format(idx)
    if resource_type == "document" and allow_document:
        doc_links.append(payload)
        return f"[[DOC:{len(doc_links) - 1}]]"
    return match.group(0)



async def _load_external_resource(
        url: str,
        user_id: int,
        allow_document: bool,
        is_image_hint: bool) -> Tuple[str, Union[ImageResource, str]]:
    file_id, file_name = _extract_file_reference(url)
    if file_id:
        if allow_document:
            return "document", _build_filesys_download_url(file_id, file_name)
        if is_image_hint:
            return "image", ImageResource(file_id=file_id, filename=file_name)
        raise ValueError("文档数量超限")
    if url.startswith("data:"):
        header, encoded = url.split(',', 1)
        data = base64.b64decode(encoded)
        return "image", ImageResource(data=data)
    if url.startswith("http://") or url.startswith("https://"):
        guessed_name = _guess_filename_from_url(url)
        ext = Path(guessed_name).suffix.lower()
        if ext in DOC_EXTENSIONS and not allow_document:
            raise ValueError("文档数量超限")
        resp = await _fetch_url(url)
        content = resp.content
        if len(content) > MAX_FILE_SIZE:
            raise ValueError("文件过大")
        if ext in DOC_EXTENSIONS:
            upload_res = await uploadFiles([
                {
                    "filename": guessed_name or "document",
                    "content": content,
                    "content_type": resp.headers.get("Content-Type", "application/octet-stream"),
                }
            ], user_id)
            file_id = str(upload_res[0]["id"])
            return "document", file_id
        return "image", ImageResource(data=content, filename=guessed_name or None)
    raise ValueError("无法识别的资源来源")


def _ensure_png(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _decode_text_bytes(file_bytes: bytes) -> str:
    if not file_bytes:
        raise ValueError("空文件")
    candidates: List[str] = []
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    candidates.append(file_bytes.decode("utf-8", errors="ignore"))
    candidates.append(file_bytes.decode("gb18030", errors="ignore"))
    for text in candidates:
        stripped = text.strip()
        if not stripped:
            continue
        total = len(text)
        if total == 0:
            continue
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
        replacement = text.count("\ufffd")
        if printable / total < 0.8:
            continue
        if replacement / total > 0.1:
            continue
        return text
    raise ValueError("非文本内容")


def _try_parse_image_bytes(file_bytes: bytes, filename: Optional[str]) -> ImageResource:
    if not file_bytes:
        raise ValueError("空图片")
    with Image.open(io.BytesIO(file_bytes)) as img:
        img.verify()
    return ImageResource(data=file_bytes, filename=filename or None)


def _get_list_prefix(paragraph: Paragraph, numbering_state: Dict[str, Dict[int, int]]) -> Optional[str]:
    p = paragraph._p
    pPr = p.pPr
    if pPr is None or pPr.numPr is None:
        return None
    num_id = getattr(pPr.numPr.numId, "val", None)
    ilvl = getattr(pPr.numPr.ilvl, "val", None)
    if num_id is None or ilvl is None:
        return None
    ilvl = int(ilvl)
    state = numbering_state.setdefault(str(num_id), {})
    counter = state.get(ilvl, 0) + 1
    state[ilvl] = counter
    for level in list(state.keys()):
        if level > ilvl:
            state.pop(level, None)
    return f"{'    ' * ilvl}{counter}. "


def _extract_math_text(node) -> str:
    namespaces = node.nsmap or {}
    texts = []
    for child in node.xpath('.//w:t', namespaces=namespaces):
        if child.text:
            texts.append(child.text)
    raw = "".join(texts).strip()
    return f"$${raw or '公式'}$$"


async def _fetch_url(url: str) -> requests.Response:
    loop = asyncio.get_running_loop()

    def _request():
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp

    return await loop.run_in_executor(None, _request)


def _extract_file_reference(url: str) -> Tuple[Optional[str], Optional[str]]:
    url = _normalize_oj_file_url(url)
    parsed = urlparse(url)
    path = unquote(parsed.path or url)
    match = FILESYS_DOWNLOAD_RE.search(path)
    if match:
        file_name = match.group(2)
        if file_name:
            return match.group(1), unquote(file_name)
        query = parse_qs(parsed.query)
        for key in URL_FILENAME_QUERY_KEYS:
            if key in query and query[key]:
                return match.group(1), unquote(query[key][0])
        return match.group(1), None
    query = parse_qs(parsed.query)
    for key in ("fileId", "file_id"):
        if key in query and query[key]:
            return query[key][0], None
    return None, None


def _normalize_oj_file_url(url: str) -> str:
    url = url.strip()
    if (url.startswith("<") and url.endswith(">")) or (url.startswith('"') and url.endswith('"')):
        url = url[1:-1].strip()
    if url.startswith("oj-file://"):
        file_id = url.split("oj-file://", 1)[-1]
        return f"https://oj.qd.sdu.edu.cn/api/filesys/download/{file_id}/{file_id}"
    return url


def _guess_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    path_name = Path(unquote(parsed.path)).name if parsed.path else ""
    if path_name:
        return path_name
    query = parse_qs(parsed.query)
    for key in URL_FILENAME_QUERY_KEYS:
        if key in query and query[key]:
            return unquote(query[key][0])
    return ""


def _build_filesys_download_url(file_id: str, file_name: Optional[str]) -> str:
    name = file_name or file_id
    return f"https://oj.qd.sdu.edu.cn/api/filesys/download/{file_id}/{quote(name)}"


def _is_filesys_download_url(url: str) -> bool:
    file_id, _ = _extract_file_reference(url)
    return bool(file_id)


def _is_document_reference_input(data: str) -> bool:
    stripped = data.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return False
    if stripped.startswith("oj-file://"):
        return True
    if FILE_ID_LITERAL_RE.fullmatch(stripped):
        return True
    parsed = urlparse(stripped)
    if parsed.scheme in {"http", "https"}:
        file_id, _ = _extract_file_reference(stripped)
        return bool(file_id)
    return False


def _extract_filename_from_headers(headers: dict) -> str:
    for key in ("content-disposition", "Content-Disposition"):
        cd = headers.get(key)
        if not cd:
            continue
        match = re.search(r'filename\*=UTF-8\'"?([^";]+)', cd)
        if match:
            return match.group(1)
        match = re.search(r'filename="?([^";]+)', cd)
        if match:
            return match.group(1)
    return ""


__all__ = ["convert_document_to_markdown"]
