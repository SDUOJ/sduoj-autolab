import importlib.util
import sys
import types
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "auto_task" / "document_parser.py"


@pytest.fixture
def parser_module(monkeypatch):
    fake_pkg = types.ModuleType("auto_task")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_llm = types.ModuleType("auto_task.llm_client")

    async def _describe_images_to_text(file_ids, task_id=None):
        return ["" for _ in file_ids]

    fake_llm.describe_images_to_text = _describe_images_to_text

    fake_api = types.ModuleType("sduojApi")

    async def _download_file(_):
        raise NotImplementedError

    async def _upload_files(*_, **__):
        return []

    fake_api.downloadFile = _download_file
    fake_api.uploadFiles = _upload_files

    fake_fitz = types.ModuleType("fitz")

    class _DummyDoc:
        def __len__(self):
            return 0

    fake_fitz.open = lambda *args, **kwargs: _DummyDoc()

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = lambda *_args, **_kwargs: None
    fake_docx_oxml_ns = types.ModuleType("docx.oxml.ns")
    fake_docx_oxml_ns.qn = lambda x: x
    fake_docx_table = types.ModuleType("docx.table")
    fake_docx_table.Table = type("Table", (), {})
    fake_docx_paragraph = types.ModuleType("docx.text.paragraph")
    fake_docx_paragraph.Paragraph = type("Paragraph", (), {})

    fake_pil = types.ModuleType("PIL")
    fake_pil_image = types.ModuleType("PIL.Image")
    fake_pil_image.open = lambda *_args, **_kwargs: None
    fake_pil.Image = fake_pil_image

    monkeypatch.setitem(sys.modules, "auto_task", fake_pkg)
    monkeypatch.setitem(sys.modules, "auto_task.llm_client", fake_llm)
    monkeypatch.setitem(sys.modules, "sduojApi", fake_api)
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "docx", fake_docx)
    monkeypatch.setitem(sys.modules, "docx.oxml.ns", fake_docx_oxml_ns)
    monkeypatch.setitem(sys.modules, "docx.table", fake_docx_table)
    monkeypatch.setitem(sys.modules, "docx.text.paragraph", fake_docx_paragraph)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil_image)

    spec = importlib.util.spec_from_file_location("document_parser_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_markdown_oj_link_downloads_and_parses_md(parser_module, monkeypatch):
    async def _download(file_id):
        assert file_id == "780381851582857230"
        headers = {"Content-Disposition": 'attachment; filename="homework.md"'}
        return 200, b"# Homework\n\nThis is markdown content.", headers

    monkeypatch.setattr(parser_module, "downloadFile", _download)

    text = "[homework.md](https://oj.qd.sdu.edu.cn/api/filesys/download/780381851582857230/homework.md)"
    rendered = await parser_module.convert_document_to_markdown(text, None, 1)

    assert "# Homework" in rendered
    assert "This is markdown content." in rendered


@pytest.mark.asyncio
async def test_uploaded_md_file_is_parsed_as_markdown(parser_module, monkeypatch):
    async def _download(file_id):
        assert file_id == "780381851582857230"
        return 200, b"## Upload Answer\n\n- item1\n- item2", {"Content-Disposition": ""}

    monkeypatch.setattr(parser_module, "downloadFile", _download)

    rendered = await parser_module.convert_document_to_markdown(
        "780381851582857230",
        "homework.md",
        1,
    )

    assert "## Upload Answer" in rendered
    assert "- item1" in rendered
