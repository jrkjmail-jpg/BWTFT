import re

from bwtft_bot.schemas import StoryDraft, StoryDraftPage


PAGE_HEADER_RE = re.compile(
    r"(?im)^\s*(?:страница|page)\s*(?:№|#)?\s*(\d+)\s*[:.\-–—]?\s*(.*)$"
)


def _clean_page_text(text: str) -> str:
    cleaned = text
    if cleaned.startswith("\r\n"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("\n"):
        cleaned = cleaned[1:]
    return cleaned.strip()


def parse_prescribed_pages(text: str) -> StoryDraft | None:
    matches = list(PAGE_HEADER_RE.finditer(text))
    if len(matches) < 2:
        return None

    pages: list[StoryDraftPage] = []
    for index, match in enumerate(matches):
        marker_number = int(match.group(1))
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        inline_text = match.group(2)
        if inline_text:
            page_start = match.start(2)
        else:
            page_start = match.end()
        page_text = _clean_page_text(text[page_start:next_start])
        if not page_text:
            return None
        pages.append(StoryDraftPage(page_number=marker_number, page_text=page_text))

    normalized_pages = [
        StoryDraftPage(page_number=index + 1, page_text=page.page_text)
        for index, page in enumerate(pages)
    ]
    return StoryDraft(pages=normalized_pages)
