import re

from bwtft_bot.schemas import StoryDraft, StoryDraftPage


PAGE_HEADER_RE = re.compile(
    r"""(?imx)
    ^\s*
    (?:
        (?:страница|page)\s*(?:№|\#)?\s*(\d+)
        |
        (?:№|\#)\s*(\d+)
        |
        (\d+)\s*(?:страница|page)
        |
        (\d+)[.)]
    )
    \s*[:.\-–—]?\s*(.*)$
    """
)

APPEND_TEXT_RE = re.compile(
    r"""(?isx)
    ^\s*
    (?:
        продолжи(?:\s+сказку)?\s+(?:этим|следующим)\s+текстом
        |
        добавь\s+(?:этот|следующий)\s+текст
        |
        присоедини\s+(?:этот|следующий)\s+текст
    )
    \s*[:.\-–—]?\s*
    (?P<text>.+)
    \s*$
    """
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
        marker_number = int(next(group for group in match.groups()[:4] if group))
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        inline_text = match.group(5)
        if inline_text:
            page_start = match.start(5)
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


def _find_split_index(text: str, start: int, target: int, end: int) -> int:
    if target >= end:
        return end

    candidates: list[int] = []
    for pattern in (r"\n\s*\n", r"\n", r"(?<=[.!?;:])\s+"):
        for match in re.finditer(pattern, text[start:end]):
            split_index = start + match.end()
            if split_index > start:
                candidates.append(split_index)

    if candidates:
        return min(candidates, key=lambda index: abs(index - target))

    return target


def split_plain_text_into_pages(text: str, pages_count: int) -> StoryDraft:
    source = text.strip()
    if pages_count <= 1 or len(source) <= 1:
        return StoryDraft(pages=[StoryDraftPage(page_number=1, page_text=source)])

    pages: list[StoryDraftPage] = []
    start = 0
    total = len(source)
    for page_number in range(1, pages_count + 1):
        remaining_pages = pages_count - page_number + 1
        remaining_chars = total - start
        if remaining_pages <= 1 or remaining_chars <= 1:
            page_text = source[start:].strip()
            if page_text:
                pages.append(StoryDraftPage(page_number=len(pages) + 1, page_text=page_text))
            break

        target = start + max(1, remaining_chars // remaining_pages)
        split_index = _find_split_index(source, start, target, total)
        page_text = source[start:split_index].strip()
        if page_text:
            pages.append(StoryDraftPage(page_number=len(pages) + 1, page_text=page_text))
        start = split_index

    return StoryDraft(pages=pages)


def extract_append_text(revision_text: str) -> str | None:
    match = APPEND_TEXT_RE.match(revision_text)
    if not match:
        return None
    text = match.group("text").strip()
    return text or None


def append_text_to_story(current_story: StoryDraft, append_text: str) -> StoryDraft:
    prescribed = parse_prescribed_pages(append_text)
    pages = [
        StoryDraftPage(page_number=page.page_number, page_text=page.page_text)
        for page in current_story.pages
    ]
    if prescribed is not None:
        pages.extend(
            StoryDraftPage(page_number=len(pages) + 1, page_text=page.page_text)
            for page in prescribed.pages
        )
    else:
        pages.append(
            StoryDraftPage(page_number=len(pages) + 1, page_text=append_text.strip())
        )

    normalized_pages = [
        StoryDraftPage(page_number=index + 1, page_text=page.page_text)
        for index, page in enumerate(pages)
    ]
    return StoryDraft(child_name=current_story.child_name, pages=normalized_pages)
