"""pypdf 逐页抽取(freeze 步用):只取文本层——不做版式还原、不做 OCR、不做表格抽取。

页码随页保留(空页丢弃但页码不失真),供切块的「章节或页码」溯源。
"""

from __future__ import annotations

import io

from pypdf import PdfReader

from python_backend.corpus.schema import Page


def extract_pages(data: bytes) -> tuple[Page, ...]:
    reader = PdfReader(io.BytesIO(data))
    pages: list[Page] = []
    for number, page in enumerate(reader.pages, start=1):
        # 行尾空白剥掉:抽取器会带出排版空格,留着既脏 diff 又让 YAML 写不成字面块
        text = "\n".join(line.rstrip() for line in (page.extract_text() or "").splitlines()).strip()
        if text:
            pages.append(Page(number=number, text=text))
    return tuple(pages)
