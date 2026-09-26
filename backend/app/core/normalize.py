import re
from datetime import date, datetime, timedelta

MONEY_RE = re.compile(r"^\$\d{1,3}(,\d{3})*$")
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def excel_date(value: object) -> date | None:
    # The sponsor file mixes real dates, Excel serials and text.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(value))
    if isinstance(value, str):
        return parse_date(value)
    return None


def parse_money(text: str) -> int | None:
    # '$19,00,181' -> None. We never guess.
    if not MONEY_RE.match(text.strip()):
        return None
    return int(text.strip().replace("$", "").replace(",", ""))


def title_case(s: str) -> str:
    # Georgia names are all caps in the filing.
    if s != s.upper():
        return s
    t = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), s.lower())
    t = re.sub(r"(\d)kv\b", r"\1kV", t, flags=re.I)
    t = re.sub(r"\bkv\b", "kV", t, flags=re.I)
    return re.sub(r"\b(Gtc|Sav|Meag|Du|Cc|Usa|Gpc|Its)\b", lambda m: m.group(1).upper(), t)


def norm_key(name: str) -> str:
    s = name.lower().replace("–", "-").replace("—", "-").replace("(usa)", "").replace("(sav)", "")
    s = re.sub(r"\b(sub|substation|primary|pri|tap|jct|switching station|ss)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s\d+$", "", " ".join(s.split()))
    return s.strip()
