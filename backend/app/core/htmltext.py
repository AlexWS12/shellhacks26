# Visible text of a web page, split into page-sized chunks for the Reader. Scripts and styles are dropped.

import re
from html.parser import HTMLParser

BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"}
# td/th stay inline and are joined with " | ", so a table row (name, date, ...) stays on one line
CHUNK = 6000


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        elif tag in BLOCK:
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n +", "\n", text).replace("\n| ", "\n")  # a row starts with its first cell, not a bar
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def pages(text: str) -> list[str]:
    # Break on paragraph boundaries into ~6000-character "pages", so citations point to a place in the page.
    out, cur = [], ""
    parts = [line for para in text.split("\n\n") for line in (para.split("\n") if len(para) > CHUNK else [para])]
    for part in parts:  # whole paragraphs, or single lines (table rows) when a paragraph is too long
        if cur and len(cur) + len(part) > CHUNK:
            out.append(cur)
            cur = ""
        cur += part + "\n\n"
    if cur.strip():
        out.append(cur)
    return out
