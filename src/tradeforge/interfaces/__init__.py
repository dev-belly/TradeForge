"""Interfaces: CLI, HTTP API, dashboard and report generation.

This layer may import the application layer. Nothing below it may import this
package - the dependency direction is enforced by an architecture test.
"""

from .console import caveat_block, provenance_block, render_mapping, render_table
from .reports import HtmlReport, HtmlReportBuilder

__all__ = [
    "HtmlReport",
    "HtmlReportBuilder",
    "caveat_block",
    "provenance_block",
    "render_mapping",
    "render_table",
]
