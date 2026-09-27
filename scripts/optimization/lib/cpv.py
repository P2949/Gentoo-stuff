"""Small Portage-independent CPV structure helper for portable inventory tools."""
from __future__ import annotations
import re

def catpkgsplit(pf: str):
    """Return (PN, PV, PR) shape compatible with Portage for common CPVs.

    Portage remains authoritative when available; this fallback only supplies
    structural parsing for fixtures and environments without Portage.
    """
    match = re.match(r"^(?P<pn>.+)-(?P<pv>(?:\d|9999)[A-Za-z0-9._+~-]*?)(?:-r(?P<pr>\d+))?$", pf)
    if not match:
        return ("null", "null", "null")
    return (match.group("pn"), match.group("pv"), "r" + (match.group("pr") or "0"))
