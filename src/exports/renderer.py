"""Playwright headless Chromium PDF renderer (design doc §7).

Run inside the exports-worker image (Dockerfile.exports-worker) — the
API container does NOT import this module so Chromium stays out of the
hot path.

The render is deterministic at the data level (vocab + canonical hash
gate inputs) but PDF bytes themselves are NOT deterministic (font
subsetting + Chromium output variation). Hashing happens BEFORE render
per design doc §2.2.
"""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

from jinja2 import Environment, FileSystemLoader, select_autoescape


_env = Environment(
    loader=FileSystemLoader(
        searchpath=__file__.replace("renderer.py", "templates")
    ),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html(template: str, ctx: Mapping[str, Any]) -> str:
    """Render the Jinja2 template to a single HTML string."""
    return _env.get_template(template).render(**ctx)


async def render_pdf(template: str, ctx: Mapping[str, Any]) -> bytes:
    """Render `template` to PDF bytes via headless Chromium.

    Footer carries: institution · type · as-of · v.<hash> · Page x / y.
    The footer template is HTML (Chromium's `footerTemplate`) — CSS @page
    can't reach the page numbers reliably.
    """
    # Late import — playwright is only in the worker image; importing it
    # eagerly in the API process would slow boot.
    from playwright.async_api import async_playwright

    html = render_html(template, ctx)

    footer_html = (
        '<div style="font-size:8px;color:#666;width:100%;padding:0 24px;'
        'display:flex;justify-content:space-between;">'
        f'<span>{ctx["cover"]["legal_name"]} · {ctx["report_type"]} · '
        f'{ctx["cover"]["as_of"]}</span>'
        f'<span>v.<span class="version">{ctx.get("version_hash", "")}</span></span>'
        '<span>Page <span class="pageNumber"></span> / '
        '<span class="totalPages"></span></span>'
        '</div>'
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        pdf = await page.pdf(
            format="Letter",
            margin={"top": "0.75in", "bottom": "0.75in",
                    "left": "0.75in", "right": "0.75in"},
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=footer_html,
            print_background=True,
        )
        await browser.close()
    return pdf


def render_pdf_sync(template: str, ctx: Mapping[str, Any]) -> bytes:
    """Synchronous wrapper for one-shot CLI/worker use."""
    return asyncio.get_event_loop().run_until_complete(render_pdf(template, ctx))
