"""Presentation layer: Markdown / NotebookLM-friendly export (HTML/PDF later)."""

from .diff_markdown import render_diff_report
from .html import render_detail, render_index
from .markdown import DISCLAIMER, render_report

__all__ = ["DISCLAIMER", "render_detail", "render_diff_report", "render_index", "render_report"]
