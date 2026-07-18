"""安全提取常见任务文件文本，并生成可编辑的要求分析。"""

from __future__ import annotations

import io
import re
from pathlib import Path

MAX_FILE_SIZE = 15 * 1024 * 1024
MAX_TEXT_CHARS = 60000
SUPPORTED = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx"}


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if len(content) > MAX_FILE_SIZE:
        raise ValueError("文件超过 15MB 限制")
    if suffix not in SUPPORTED:
        raise ValueError("暂不支持该格式；支持 PDF、Word、TXT、Markdown、Excel、PowerPoint")
    try:
        if suffix in {".txt", ".md"}:
            text = content.decode("utf-8-sig", errors="replace")
        elif suffix == ".pdf":
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        elif suffix == ".docx":
            from docx import Document
            doc = Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs)
            text += "\n" + "\n".join(" | ".join(c.text for c in row.cells) for table in doc.tables for row in table.rows)
        elif suffix == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            text = "\n".join(" | ".join("" if v is None else str(v) for v in row)
                             for ws in wb.worksheets for row in ws.iter_rows(values_only=True))
        else:
            from pptx import Presentation
            prs = Presentation(io.BytesIO(content))
            text = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text"))
    except Exception as exc:
        raise ValueError(f"文件解析失败：{type(exc).__name__}") from exc
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        raise ValueError("文件中未提取到可分析文字（扫描版 PDF/图片暂不支持 OCR）")
    return cleaned[:MAX_TEXT_CHARS]


def fallback_analysis(text: str) -> dict:
    """LLM 不可用时仍提供可编辑摘要；不记录或返回完整原文。"""
    snippet = text[:1800]
    return {
        "project_goal": snippet[:300],
        "core_tasks": [],
        "deliverables": [],
        "time_requirements": [],
        "format_requirements": [],
        "constraints": [],
        "evaluation_criteria": [],
        "important_people": [],
        "questions": ["请确认系统提取的项目目标，并补充交付物、时间与评价标准。"],
        "summary": snippet,
    }
