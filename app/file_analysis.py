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


def analyze_locally(text: str) -> dict:
    """毫秒级提炼常见要求，避免文件分析与任务拆解串行调用两次 LLM。

    这里负责抽取事实和压缩原文；Planner 随后只调用一次 LLM 完成专业任务拆解。
    """
    cleaned = re.sub(r"\s+", " ", text).strip()
    sentences = [
        part.strip(" ：:;；,.，。")
        for part in re.split(r"[。！？\n;；]+", cleaned)
        if part.strip()
    ][:120]

    def matched(*keywords: str, limit: int = 8) -> list[str]:
        values = [
            sentence for sentence in sentences
            if any(keyword in sentence.lower() for keyword in keywords)
        ]
        return list(dict.fromkeys(values))[:limit]

    goals = matched("目标", "目的", "旨在", "需要完成", "项目背景", limit=4)
    deliverables = matched("交付", "提交", "成果", "报告", "推送", "作品", "文档", limit=10)
    times = matched("截止", "日期", "时间", "之前", "实践前", "实践中", "实践后", limit=10)
    formats = matched("格式", "字数", "页数", "pdf", "word", "ppt", "秀米", "排版", limit=10)
    constraints = matched("必须", "不得", "限制", "要求", "禁止", "至少", "不超过", limit=10)
    criteria = matched("评分", "评价", "考核", "标准", "占比", limit=10)
    people = matched("负责人", "成员", "老师", "导师", "联系人", "团队", limit=8)
    core = matched("任务", "完成", "制作", "撰写", "拍摄", "收集", "发布", "设计", limit=12)
    summary_parts = (goals + deliverables + times + formats + constraints)[:16]
    summary = "；".join(summary_parts) if summary_parts else cleaned[:2200]
    return {
        "project_goal": "；".join(goals) or cleaned[:300],
        "core_tasks": core,
        "deliverables": deliverables,
        "time_requirements": times,
        "format_requirements": formats,
        "constraints": constraints,
        "evaluation_criteria": criteria,
        "important_people": people,
        "questions": [] if goals and deliverables else [
            "请确认项目目标和最终交付物是否完整。"
        ],
        "summary": summary[:4000],
    }
