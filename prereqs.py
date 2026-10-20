"""Turn a bulletin prerequisite sentence into an AND/OR tree of course codes.

Tree nodes are dicts:
    {"op": "course", "code": "OMIS 30"}
    {"op": "and" | "or", "args": [node, ...]}

Bulletin phrasing rules this follows:
- "MATH 11 or 30"      a bare number reuses the previous subject -> MATH 30
- "ECON 41 and 42", "ECON 41+42", "OMIS 108/108E"
                       bind tightly (the second item continues the first)
- "A or B and C"       otherwise "or" binds tighter than "and": (A or B) and C
- "A, B, or C"         a comma list takes the connector of its last item
- ";" separates independent requirements (all required)
Text that names no course (e.g. "upper-division standing") is kept as a note.
"""

import re

COURSE_RE = re.compile(r"\b([A-Z]{2,5})\s?(\d{1,3}[A-Z]{0,2})\b")
TOKEN_RE = re.compile(
    r"(?P<course>\b[A-Z]{2,5}\s?\d{1,3}[A-Z]{0,2}\b)"
    r"|(?P<num>\b\d{1,3}[A-Z]{0,2}\b)"
    r"|(?P<and>\band\b|&|\+)"
    r"|(?P<or>\bor\b)"
    r"|(?P<slash>/)"
    r"|(?P<word>[A-Za-z][A-Za-z\-']*)"
)
# Capitalised words that look like subject codes but are not.
NOT_SUBJECTS = {"AND", "OR", "THE", "AP", "IB", "GPA", "SAT", "ACT", "UNIT", "UNITS"}
# A bare number right after/before these words is a quantity, not a course:
# "scoring at level 2", "3 quarters of Latin", "at least 2 units".
QUANTITY_BEFORE = {"level", "levels", "score", "scores", "scoring", "least", "minimum",
                   "grade", "gpa", "unit", "units", "quarter", "quarters", "year", "years"}
QUANTITY_AFTER = re.compile(r"\s*(units?|quarters?|years?|semesters?|courses?|classes|or more|\+?\s*credits?)\b", re.I)
LEAD_RE = re.compile(r"^\s*(and|or)\b", re.I)


def course(code):
    return {"op": "course", "code": code}


def _combine(op, args):
    flat = []
    for a in args:
        if a is None:
            continue
        if a["op"] == op:
            flat.extend(a["args"])
        else:
            flat.append(a)
    # drop duplicates while keeping order
    seen, out = set(), []
    for a in flat:
        key = repr(a)
        if key not in seen:
            seen.add(key)
            out.append(a)
    if not out:
        return None
    return out[0] if len(out) == 1 else {"op": op, "args": out}


def _tokens(text, last_subject=None):
    """Yield (kind, value) tokens; bare numbers inherit the last subject."""
    out = []
    prev_word = None
    for m in TOKEN_RE.finditer(text):
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "num" and (prev_word in QUANTITY_BEFORE or QUANTITY_AFTER.match(text, m.end())):
            prev_word = None
            continue
        prev_word = val.lower() if kind == "word" else None
        if kind == "course":
            subj, num = COURSE_RE.match(val).groups()
            if subj in NOT_SUBJECTS:
                continue
            last_subject = subj
            out.append(("course", f"{subj} {num}"))
        elif kind == "num":
            subj = last_subject
            if subj is None:
                continue  # a number with no subject in sight (e.g. "2 units")
            out.append(("num", f"{subj} {val}"))
        elif kind in ("and", "or", "slash"):
            out.append((kind, val))
        else:
            out.append(("word", val))
    return out


def _parse_chunk(text, carry=None):
    """Parse one comma-free piece like 'OMIS 41 or ECON 41 and 42'.

    `carry` is the subject in force from earlier text, so "105, 106" works.
    """
    toks = _tokens(LEAD_RE.sub("", text), carry)
    # 1) a connector followed by a bare number binds tightly to the item
    #    before it ("41 and 42", "108/108E", "11 or 30"); anything else
    #    starts a new loosely-joined item.
    items, ops = [], []
    pending_op = None
    for kind, val in toks:
        if kind in ("course", "num"):
            node = course(val)
            if items and pending_op and kind == "num":
                items[-1] = _combine(pending_op, [items[-1], node])
            else:
                if items:
                    ops.append(pending_op or "and")
                items.append(node)
            pending_op = None
        elif kind in ("and", "or", "slash"):
            pending_op = "and" if kind == "and" else "or"
    if not items:
        return None
    # 2) loose precedence: OR binds tighter than AND
    groups, current = [], [items[0]]
    for op, item in zip(ops, items[1:]):
        if op == "or":
            current.append(item)
        else:
            groups.append(_combine("or", current))
            current = [item]
    groups.append(_combine("or", current))
    return _combine("and", groups)


def _chunk_has_course(text):
    return any(m.group(1) not in NOT_SUBJECTS for m in COURSE_RE.finditer(text))


def _parse_clause(text, subject_hint, last_subject_box):
    """Parse a ';'-free clause that may contain a comma list."""
    chunks = [c for c in re.split(r",", text) if c.strip()]
    # carry the subject across commas: "OMIS 105, 106, or 107"
    parsed, leads = [], []
    carry = last_subject_box[0]
    for c in chunks:
        lead = LEAD_RE.match(c)
        leads.append(lead.group(1).lower() if lead else None)
        parsed.append(_parse_chunk(c, carry))
        subs = [m.group(1) for m in COURSE_RE.finditer(c) if m.group(1) not in NOT_SUBJECTS]
        if subs:
            carry = subs[-1]
    last_subject_box[0] = carry
    parsed_nodes = [p for p in parsed if p is not None]
    if not parsed_nodes:
        return None
    if len(parsed_nodes) == 1:
        return parsed_nodes[0]
    joiner = next((l for l in reversed(leads[1:]) if l), None)
    if joiner is None:
        # "A, B or C" without an Oxford comma: look at the last chunk
        last = parsed[-1]
        singles = all(p is not None and p["op"] == "course" for p in parsed[:-1])
        if singles and last is not None and last["op"] in ("or", "and") and \
                all(a["op"] == "course" for a in last["args"]):
            return _combine(last["op"], parsed_nodes)
        joiner = "and"
    return _combine(joiner, parsed_nodes)


NOTE_ALT_RE = re.compile(
    r",?\s*or\s+(?:by\s+)?(?:the\s+|an?\s+)?(?:written\s+)?"
    r"(?:permission|consent|approval|instructor|equivalent|placement|department)[^;]*$", re.I)
EQUIV_RE = re.compile(r"\(?\bor\s+(?:an?\s+)?equivalent\b\)?", re.I)


def parse_requirement(text, subject_hint=None):
    """Return (tree or None, notes list) for a requirement sentence."""
    notes = []
    box = [subject_hint]
    text = re.sub(r"\(\s*[\d.–\-]+\s+units?\s*\)", " ", text)
    text = re.sub(r"\b([A-Z][a-z]{2,3})(?=\s\d)", lambda m: m.group(1).upper(), text)  # "Econ 41"
    # "...; or OMIS 30, 40, and 41" offers an alternative to what came before
    groups = [[]]
    for clause in re.split(r";", text):
        clause = clause.strip()
        if not clause:
            continue
        if re.match(r"^or\b", clause, re.I) and groups[-1]:
            groups.append([])
            clause = clause[2:].strip()
        if not _chunk_has_course(clause):
            notes.append(clause.rstrip(". "))
            continue
        clause = EQUIV_RE.sub(" ", clause)
        # non-course alternatives such as "or permission of instructor":
        # take the earliest "or" whose remainder names no course
        for pos in (m.start() for m in re.finditer(r",?\s*\bor\b", clause)):
            tail = clause[pos:]
            if NOTE_ALT_RE.match(tail) and not _chunk_has_course(tail):
                notes.append(re.sub(r"^[\s,]*or\s+", "", tail).strip(" ,."))
                clause = clause[:pos]
                break
        node = _parse_clause(clause, subject_hint, box)
        if node:
            groups[-1].append(node)
    alternatives = [_combine("and", g) for g in groups]
    return _combine("or", alternatives), notes


def tree_codes(tree):
    """All course codes mentioned in a tree."""
    if tree is None:
        return []
    if tree["op"] == "course":
        return [tree["code"]]
    out = []
    for a in tree["args"]:
        for c in tree_codes(a):
            if c not in out:
                out.append(c)
    return out


def tree_to_text(tree):
    if tree is None:
        return ""
    if tree["op"] == "course":
        return tree["code"]
    inner = f" {tree['op'].upper()} ".join(tree_to_text(a) for a in tree["args"])
    return f"({inner})"


SENTENCE_END = r"(?:\.(?=\s*[A-Z(]|\s*$)|$)"
# Only "Prerequisite: ..." style statements count.  Phrases such as
# "MATH 11 is not a suitable prerequisite for MATH 31" or "C&I I is a
# prerequisite for C&I II" describe other courses and are ignored.
_LEAD = r"(?:\s+for\s+[^:.;]{1,40})?\s*(?::|,|\s+(?:is|are)\b|(?=\s+a\s+grade\b))\s*"
PREREQ_RE = re.compile(r"\bPre-?requisites?" + _LEAD + r"(.+?)" + SENTENCE_END, re.I | re.S)
COREQ_RE = re.compile(r"\bCo-?requisites?" + _LEAD + r"(.+?)" + SENTENCE_END, re.I | re.S)


def extract(description):
    """Find the prerequisite and corequisite sentences in a description."""
    pre = [m.group(1).strip() for m in PREREQ_RE.finditer(description)]
    co = [m.group(1).strip() for m in COREQ_RE.finditer(description)]
    return "; ".join(pre), "; ".join(co)
