"""Load identity.yaml and generate match variants for scrubbing and verification."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

# Separator allowed between tokens of a multi-word variant: whitespace and markdown
# emphasis markers, so line breaks and "**bold** **split**" names still match.
_SEP = r"[\s*]+"
_LEFT = r"(?<![A-Za-z0-9])"
_RIGHT = r"(?![A-Za-z0-9])"

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


@dataclass
class Variant:
    tag: str    # replacement tag, e.g. "NAME", "ID:health_card"
    count_key: str  # key used in per-category counts, e.g. "name", "health_card"
    text: str


@dataclass
class Identity:
    names: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    dob: str = ""
    addresses: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    ids: list = field(default_factory=list)  # list of (label, value)
    extra_terms: list = field(default_factory=list)

    def variants(self) -> list:
        """All match variants, de-duplicated, longest first."""
        out: list[Variant] = []
        for n in list(self.names) + list(self.aliases):
            out += [Variant("NAME", "name", v) for v in _name_variants(n)]
        if self.dob:
            out += [Variant("DOB", "dob", v) for v in _dob_variants(self.dob)]
        for p in self.phones:
            out += [Variant("PHONE", "phone", v) for v in _phone_variants(p)]
        for e in self.emails:
            out.append(Variant("EMAIL", "email", str(e).strip()))
        for a in self.addresses:
            out += [Variant("ADDRESS", "address", v) for v in _address_variants(a)]
        for label, value in self.ids:
            out += [Variant(f"ID:{label}", label, v) for v in _chunk_variants(value)]
        for t in self.extra_terms:
            if str(t).strip():
                out.append(Variant("TERM", "term", str(t).strip()))
        seen = set()
        uniq = []
        for v in out:
            key = (v.tag, v.text.lower())
            if v.text and key not in seen:
                seen.add(key)
                uniq.append(v)
        uniq.sort(key=lambda v: len(v.text), reverse=True)
        return uniq


def load_identity(path) -> Identity:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    def strs(key):
        return [str(x).strip() for x in (data.get(key) or []) if str(x).strip()]

    ids = []
    for item in data.get("ids") or []:
        if isinstance(item, dict) and str(item.get("value", "")).strip():
            label = re.sub(r"\W+", "_", str(item.get("label") or "id").strip()) or "id"
            ids.append((label, str(item["value"]).strip()))
    return Identity(
        names=strs("names"), aliases=strs("aliases"),
        dob=str(data.get("dob") or "").strip(),
        addresses=strs("addresses"), phones=strs("phones"), emails=strs("emails"),
        ids=ids, extra_terms=strs("extra_terms"),
    )


# ---------------------------------------------------------------- variant generators

def _name_variants(name: str) -> list:
    name = name.strip()
    if not name:
        return []
    if "," in name:  # given as "Last, First Middle"
        last, _, rest = name.partition(",")
        toks = rest.split() + last.split()
        first_toks, last_toks = rest.split(), last.split()
    else:
        toks = name.split()
        first_toks, last_toks = toks[:-1], toks[-1:]
    out = {name}
    clean = [t.strip(".,") for t in toks if t.strip(".,")]
    first = [t.strip(".,") for t in first_toks if t.strip(".,")]
    last = [t.strip(".,") for t in last_toks if t.strip(".,")]
    if first and last:
        f0, mids = first[0], first[1:]
        L = " ".join(last)
        out.add(" ".join(first + last))
        out.add(f"{f0} {L}")
        out.add(f"{L}, {f0}")
        out.add(f"{L}, {' '.join(first)}")
        out.add(f"{L} {f0}")
        out.add(f"{L} {' '.join(first)}")
        inits = [t[0] for t in first]
        out.add(f"{' '.join(i + '.' for i in inits)} {L}")
        out.add(f"{' '.join(inits)} {L}")
        out.add(f"{''.join(inits)} {L}")
        out.add(f"{f0[0]}. {L}")
        out.add(f"{f0[0]} {L}")
        out.add(f"{L}, {f0[0]}.")
        out.add(f"{L}, {f0[0]}")
        out.add(f"{L} {f0[0]}")
        if mids:
            out.add(f"{f0} {mids[0][0]}. {L}")
            out.add(f"{f0} {mids[0][0]} {L}")
    # every name token on its own (single letters excluded: they are not names)
    for t in clean:
        if len(t) >= 2:
            out.add(t)
        if "-" in t:
            out.update(p for p in t.split("-") if len(p) >= 2)
    return sorted(out)


def _dob_variants(dob: str) -> list:
    try:
        d = date.fromisoformat(dob[:10])
    except ValueError:
        return [dob]
    y, m, dd = d.year, d.month, d.day
    mon_full = _MONTHS[m - 1]
    mon_abbr = mon_full[:3]
    out = {dob}
    for mm, ddd in ((f"{m:02d}", f"{dd:02d}"), (str(m), str(dd)),
                    (f"{m:02d}", str(dd)), (str(m), f"{dd:02d}")):
        for sep in ("-", "/", ".", " "):
            out.add(f"{y}{sep}{mm}{sep}{ddd}")
            out.add(f"{ddd}{sep}{mm}{sep}{y}")
            out.add(f"{mm}{sep}{ddd}{sep}{y}")
        for sep in ("/", "-"):
            out.add(f"{ddd}{sep}{mm}{sep}{y % 100:02d}")
            out.add(f"{mm}{sep}{ddd}{sep}{y % 100:02d}")
    for ddd in (str(dd), f"{dd:02d}"):
        for mon in {mon_abbr, mon_full, mon_abbr + ".", "Sept" if m == 9 else mon_abbr}:
            out.add(f"{mon} {ddd}, {y}")
            out.add(f"{mon} {ddd} {y}")
            out.add(f"{ddd} {mon} {y}")
            out.add(f"{ddd}-{mon}-{y}")
            out.add(f"{ddd} {mon}, {y}")
    out.add(f"{y}{m:02d}{dd:02d}")
    return sorted(out)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_variants(phone: str) -> list:
    out = {phone.strip()}
    d = _digits(phone)
    out.add(d)
    nat = d[1:] if len(d) == 11 and d.startswith("1") else d
    if len(nat) == 10:
        a, b, c = nat[:3], nat[3:6], nat[6:]
        for cc in ("", "1 ", "1-", "1.", "+1 ", "+1-", "+1.", "+1", "1"):
            out.add(f"{cc}{a}-{b}-{c}")
            out.add(f"{cc}{a} {b} {c}")
            out.add(f"{cc}{a}.{b}.{c}")
            out.add(f"{cc}({a}) {b}-{c}")
            out.add(f"{cc}({a}){b}-{c}")
            out.add(f"{cc}{nat}")
        out.add(f"({a}) {b}-{c}")
        out.add(f"{b}-{c}")  # local form without area code
    return sorted(out)


def _chunk_variants(value: str) -> list:
    """Raw value plus versions with spaces/dashes/dots/no separator between alnum chunks."""
    value = value.strip()
    chunks = [c for c in re.split(r"[^A-Za-z0-9]+", value) if c]
    out = {value}
    if len(chunks) > 1:
        for sep in (" ", "-", ".", ""):
            out.add(sep.join(chunks))
    return sorted(out)


_POSTAL_RE = re.compile(r"[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d|\b\d{5}(?:-\d{4})?\b")


def _address_variants(addr: str) -> list:
    addr = addr.strip()
    out = {addr}
    first = addr.split(",")[0].strip()
    if first and first != addr:
        out.add(first)
    m = _POSTAL_RE.search(addr)
    if m:
        out.update(_chunk_variants(m.group(0)))
    return sorted(out)


# ---------------------------------------------------------------- compiled forms

def variant_regex(text: str) -> str:
    """Case-insensitive-ready regex body: flexible whitespace, alnum boundaries."""
    pieces = [re.escape(p) for p in text.split()]
    return _LEFT + _SEP.join(pieces) + _RIGHT


def compile_replacer(identity: Identity):
    """Single-pass regex (longest variants first) and a parallel list of Variants.

    Returns (compiled_regex | None, variants). Group i+1 corresponds to variants[i].
    """
    vs = identity.variants()
    if not vs:
        return None, vs
    body = "|".join(f"({variant_regex(v.text)})" for v in vs)
    return re.compile(body, re.IGNORECASE), vs


def normalize(s: str) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", s.lower())
