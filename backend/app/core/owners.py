# Who owns a project and which state it is in. Dominion and Georgia are built in; submitted plans add more owners.

from app.core.models import Project

BUILT_IN = {"DESC": "Dominion Energy South Carolina", "GA": "Georgia Power"}
GA_OWNERS = {"GPC": "Georgia Power", "SAV": "Georgia Power (Savannah)", "GTC": "Georgia Transmission",
             "MEAG": "MEAG Power", "DU": "Dalton Utilities"}
ORDER = ("DESC", "GA")  # built-in owners first, so Dominion-Georgia pairs keep their ids ('DESC-...|GA-...')


def owner_name(p: Project) -> str:
    if p.utility == "DESC":
        return BUILT_IN["DESC"]
    if p.utility == "GA":
        return GA_OWNERS.get(p.sponsor, p.sponsor)
    return p.sponsor  # submitted plans keep the owner's name here


def state_of(p: Project) -> str:
    if p.state:
        return p.state
    return "SC" if p.utility == "DESC" else "GA"


def owner_rank(utility: str) -> tuple[int, str]:
    return (ORDER.index(utility), "") if utility in ORDER else (len(ORDER), utility)
