from __future__ import annotations

import logging
from io import BytesIO

import trafilatura
from pypdf import PdfReader

from daily_intel.core.models import Document
from daily_intel.infrastructure.http import http_get, install_proxy_fallback
from daily_intel.intelligence.sources.common import USER_AGENT

logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pypdf._reader").setLevel(logging.ERROR)


def enrich_document(document: Document, timeout: int, max_chars: int) -> Document:
    if document.extraction_quality == "full" or not document.metadata.get("fetch_full_text"):
        return document
    install_proxy_fallback()
    try:
        if document.content_type == "paper" and document.metadata.get("pdf_url"):
            response = http_get(
                document.metadata["pdf_url"], timeout=timeout, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            reader = PdfReader(BytesIO(response.content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        else:
            response = http_get(document.url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            text = trafilatura.extract(
                response.text, include_links=False, include_images=False, include_comments=False
            ) or ""
        text = text.strip()[:max_chars]
        if len(text) >= 200:
            return document.model_copy(update={"content": text, "extraction_quality": "full"})
    except Exception as exc:
        metadata = {**document.metadata, "extraction_error": f"{type(exc).__name__}: {exc}"[:300]}
        return document.model_copy(update={"metadata": metadata})
    return document
