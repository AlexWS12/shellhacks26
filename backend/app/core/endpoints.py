import re

PREFIX_RE = re.compile(r"^(SAV|GTC|MEAG|DU|CC|GRID)\s*[:\-]\s*", re.I)
KV_RE = re.compile(r"\d+(\.\d+)?(\s?/\s?\d+(\.\d+)?)*\s?-?\s?kv\b.*$", re.I)  # '230/115kV Relay ...' -> ''
STOP_RE = re.compile(
    r"\b(\d+(\.\d+)?\s?-?\s?\d*\.?\d*\s?kv|rebuild|rebuilds|construct|reconductor|upgrades?|line|tie|sub|"
    r"substation|tap|replace|improvements?|reactors?|breaker (and|&) (a )?half( station)?|and|&|"
    r"bus|jumpers?|relay(ing)?|protective|modernization|installation|removal|replacement|equipment|breakers?|capacitor|"
    r"cap bank|bank( [a-z]| #?\d+)?|autobank|auto transformer|transformers?|statcom|system|smart valves?|"
    r"switching station|area|strategic|solution|transmission|needs|network|customer|\d+(st|nd|rd|th))\b", re.I)
AKA_RE = re.compile(r"\baka\b\.?.*$", re.I)  # 'Hyundai Motors Savannah Aka. Project Ea' -> 'Hyundai Motors Savannah'
NUMBER_RE = re.compile(r"#\s?\d+")  # 'Talbot #2', 'Bowen #10'
# Standard transmission voltages written without 'kV': 'Union Pier 115', 'Cass Pine 230/25'. 'Highway 112' stays.
BARE_VOLTAGE_RE = re.compile(r"(\s(46|69|115|138|161|230|500)(/\d{2,3})*)+$")


def letters(part: str) -> int:
    return sum(c.isalpha() for c in part)


def clean_endpoint(part: str) -> str:
    # One endpoint name, from the regex split or from Gemini. '' means nothing usable is left.
    part = AKA_RE.sub("", part)
    part = KV_RE.sub("", part)
    part = STOP_RE.sub("", NUMBER_RE.sub("", part))
    part = re.sub(r"\s+", " ", part).strip(" ,/.-")
    part = re.sub(r"^(at|of|for)\s+", "", part, flags=re.I)  # 'Smart Valves At East Villa Rica ...'
    part = re.sub(r"\s+new$", "", part, flags=re.I)  # 'Rice Hope New Auto Transformer'; 'New Cavender Drive' stays
    part = BARE_VOLTAGE_RE.sub("", part).strip(" ,/.-")
    if letters(part) < 2 or letters(part) < len(part.replace(" ", "")) / 2:  # '13.', '#2', '230/25'
        return ""
    return part.title()


def split_endpoints(name: str) -> list[str]:
    # 'Jasper – Okatie 230 kV #2' -> ['Jasper', 'Okatie']
    n = PREFIX_RE.sub("", name)
    n = PREFIX_RE.sub("", n)  # 'SAV: CC - ...' has two prefixes
    n = n.split(":")[0]
    n = re.sub(r"\(.*?\)", "", n)
    out: list[str] = []
    for part in re.split(r"[-–]", n):
        part = clean_endpoint(part)
        if len(part) > 2:
            out.append(part)
    return out[:2]


def looks_awkward(name: str, parts: list[str]) -> bool:
    # Names worth sending to Gemini.
    if not parts:
        return True
    if "," in name or len(re.split(r"[-–]", name.split(":")[-1])) > 3:
        return True
    return (any(re.search(r"\d", p) for p in parts) or any(len(p.split()) > 4 for p in parts)
            or any(letters(p) <= 3 for p in parts))  # 'Skc': an acronym, not a findable place name
