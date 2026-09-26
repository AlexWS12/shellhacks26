import re

PREFIX_RE = re.compile(r"^(SAV|GTC|MEAG|DU|CC|GRID)\s*[:\-]\s*", re.I)
STOP_RE = re.compile(
    r"\b(\d+(\.\d+)?\s?-?\s?\d*\.?\d*\s?kv|#\d+|rebuild|rebuilds|construct|reconductor|upgrade|line|tie|sub|"
    r"substation|tap|replace|improvements?|reactors?|and|&)\b", re.I)


def split_endpoints(name: str) -> list[str]:
    # 'Jasper – Okatie 230 kV #2' -> ['Jasper', 'Okatie']
    n = PREFIX_RE.sub("", name)
    n = PREFIX_RE.sub("", n)  # 'SAV: CC - ...' has two prefixes
    n = n.split(":")[0]
    n = re.sub(r"\(.*?\)", "", n)
    out: list[str] = []
    for part in re.split(r"[-–]", n):
        part = re.sub(r"\d+\s?kv.*$", "", part, flags=re.I)
        part = STOP_RE.sub("", part).strip(" ,/")
        part = re.sub(r"\s+", " ", part)
        if part and len(part) > 2:
            out.append(part.title())
    return out[:2]


def looks_awkward(name: str, parts: list[str]) -> bool:
    # Names worth sending to Gemini.
    if not parts:
        return True
    if "," in name or len(re.split(r"[-–]", name.split(":")[-1])) > 3:
        return True
    return any(re.search(r"\d", p) for p in parts) or any(len(p.split()) > 4 for p in parts)
