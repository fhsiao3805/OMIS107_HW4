"""Read the SCU undergraduate bulletin and build a course/prerequisite index."""

import json
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from fetcher import PoliteFetcher
from prereqs import NOT_SUBJECTS, extract, parse_requirement, tree_codes

BULLETIN_URL = "https://www.scu.edu/bulletin/undergraduate/"
# Chapters 3-6 hold the academic departments and their course descriptions.
DEPT_LINK_RE = re.compile(r'href="(\./chapter-[3-6][^"#]*\.html)')
HEADING_RE = re.compile(r"^\s*(?:([A-Z]{2,5})\s)?(\d{1,3}[A-Z]{0,2})\s*\.\s+(.+?)\s*$", re.S)
TITLE_CODE_RE = re.compile(r"\(([A-Z]{2,5})\)\s*$")
DIVISION_RE = re.compile(r"^\s*(lower|upper)-division courses?\b\s*:?\s*", re.I)
UNITS_RE = re.compile(r"\(([\d.–\-]+(?:\s*(?:–|-|or|to)\s*[\d.]+)?)\s+units?\)")
CROSSLIST_RE = re.compile(
    r"(also listed as|cross-?listed (?:with|as)|same as|formerly|equivalent to|"
    r"may not (?:take|receive credit)|credit for|in place of)[^.]*", re.I)
INDEX_PATH = Path(__file__).parent / "cache" / "index.json"

# Topics the automatic guess gets wrong, checked by hand against the bulletin.
# Keyed by (page slug, topic) where topic is the part of the section title
# after the colon, lower-cased ("" for untitled Lower/Upper-Division sections).
SUBJECT_OVERRIDES = {
    # Studio Art (ARTS) shares most upper-division numbers with Art History.
    ("art-and-art-history", "art history"): "ARTH",
}


def _text(el):
    # Spans inside one paragraph are often split mid-word ("OMIS 3</span><span>0"),
    # so join them with no separator.
    return re.sub(r"\s+", " ", el.get_text("")).strip()


def _guess_subject(numbers, ref_sets, fallback=None):
    """Pick the subject whose references cover the most of `numbers`.

    `ref_sets` is a list of (weight, {subject: numbers}) so mentions inside
    the section itself can count for more than mentions elsewhere on the page.
    """
    score = Counter()
    for weight, refs in ref_sets:
        for subj, nums in refs.items():
            score[subj] += weight * len(numbers & nums)
    if not score:
        return fallback
    subj, hits = score.most_common(1)[0]
    if fallback is None:
        return subj if hits >= 1 else None
    # Moving away from the subject in force needs clear evidence; stray
    # mentions of other departments' courses are common.
    if subj != fallback and hits >= 4 and hits >= 2 * score[fallback]:
        return subj
    return fallback


def _page_refs(text):
    """Map subject -> set of course numbers mentioned (bare numbers inherit)."""
    refs = defaultdict(set)
    for m in re.finditer(r"\b([A-Z]{2,5})\s?(\d{1,3}[A-Z]{0,2})((?:\s*(?:,|and|or|/|\+)\s*\d{1,3}[A-Z]{0,2}\b)*)",
                         text):
        subj = m.group(1)
        if subj in NOT_SUBJECTS:
            continue
        refs[subj].add(m.group(2))
        for n in re.findall(r"\d{1,3}[A-Z]{0,2}", m.group(3)):
            refs[subj].add(n)
    return refs


def parse_department(html, url):
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with(" ")
    main = soup.find("h1")
    root = main.parent if main else soup
    dept = _text(main) if main else ""
    # Cross-listing notes name another department's number for the same
    # course; they would mislead the subject guess, so leave them out.
    page_text = CROSSLIST_RE.sub(" ", " ".join(
        _text(el) for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li"])))
    page_refs = _page_refs(page_text)

    # Walk headings in document order, grouping course headings by section.
    sections = []          # [{title, courses: [...]}]
    current = {"title": "", "courses": []}
    sections.append(current)
    last_course = None
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol"]):
        if el.find_parent(["ul", "ol"]) is not None and el.name == "p":
            continue
        name = el.name
        txt = _text(el)
        if name in ("h1", "h2"):
            current = {"title": txt, "courses": []}
            sections.append(current)
            last_course = None
            continue
        if name in ("h3", "h4"):
            m = HEADING_RE.match(txt)
            if m:
                anchor = el.get("id") or (el.find_previous_sibling("a") or {}).get("id")
                last_course = {
                    # "NEW 160." marks a new course, not a subject
                    "explicit_subject": m.group(1) if m.group(1) != "NEW" else None,
                    "number": m.group(2),
                    "title": m.group(3).rstrip("."),
                    "description": "",
                    "anchor": anchor,
                }
                current["courses"].append(last_course)
            else:
                last_course = None
            continue
        if last_course is not None and txt:
            sep = " " if last_course["description"] else ""
            last_course["description"] += sep + txt

    page_numbers = {c["number"] for s in sections for c in s["courses"]}
    page_subject = _guess_subject(page_numbers, [(1, page_refs)])
    slug = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")

    # Lower- and upper-division sections on the same topic share a subject
    # ("Lower-Division Courses: Art History" / "Upper-Division ...: Art History"),
    # so decide once per topic using all of its course numbers.
    dept_name = re.sub(r"^(the\s+)?department of\s+|\s+(department|program)$", "",
                       dept.strip(), flags=re.I).lower()

    def topic(title):
        t = title.split(":", 1)[1] if ":" in title else DIVISION_RE.sub("", title)
        t = t.strip().lower()
        return "" if t == dept_name else t

    by_topic = defaultdict(list)
    for sec in sections:
        if sec["courses"]:
            by_topic[topic(sec["title"])].append(sec)
    topic_subject = {}
    default = page_subject
    for t, secs in by_topic.items():
        override = next((subj for (oslug, words), subj in SUBJECT_OVERRIDES.items()
                         if oslug == slug and words.lower() == t), None)
        in_title = next((m.group(1) for sec in secs
                         if (m := TITLE_CODE_RE.search(sec["title"]))), None)
        if override or in_title:
            topic_subject[t] = default = override or in_title
            continue
        cs = [c for sec in secs for c in sec["courses"]]
        nums = {c["number"] for c in cs}
        local = _page_refs(CROSSLIST_RE.sub(" ", " ".join(c["description"] for c in cs)))
        default = topic_subject[t] = _guess_subject(
            nums, [(2, local), (1, page_refs)], default)

    courses = []
    for sec in sections:
        if not sec["courses"]:
            continue
        subject = topic_subject[topic(sec["title"])]
        if subject is None:
            continue
        for c in sec["courses"]:
            subject = c["explicit_subject"] or topic_subject[topic(sec["title"])]
            code = f"{subject} {c['number']}"
            pre_text, co_text = extract(c["description"])
            pre_tree, pre_notes = parse_requirement(pre_text, subject) if pre_text else (None, [])
            co_tree, co_notes = parse_requirement(co_text, subject) if co_text else (None, [])
            units = UNITS_RE.search(c["description"])
            courses.append({
                "code": code,
                "subject": subject,
                "number": c["number"],
                "title": c["title"],
                "units": units.group(1) if units else None,
                "description": c["description"],
                "prereq_text": pre_text,
                "prereq": pre_tree,
                "prereq_notes": pre_notes,
                "coreq_text": co_text,
                "coreq": co_tree,
                "department": dept,
                "section": sec["title"],
                "url": url + (f"#{c['anchor']}" if c["anchor"] else ""),
            })
    return courses


class BulletinIndex:
    """Builds the course index in a background thread and answers queries."""

    def __init__(self, fetcher=None):
        self.fetcher = fetcher or PoliteFetcher()
        self.courses = {}
        self.unlocks = defaultdict(list)
        self.status = {"state": "idle", "done": 0, "total": 0, "error": None,
                       "built_at": None, "source": BULLETIN_URL}
        self._lock = threading.Lock()
        self._load_saved()

    # ---- building ---------------------------------------------------------
    def _load_saved(self):
        if INDEX_PATH.exists():
            data = json.loads(INDEX_PATH.read_text())
            self._set_courses(data["courses"])
            self.status.update(state="ready", built_at=data["built_at"],
                               done=data["pages"], total=data["pages"])

    def _set_courses(self, course_list):
        courses, unlocks = {}, defaultdict(list)
        for c in course_list:
            # A course can appear on two pages (e.g. cross-listed ECON pages);
            # keep the first full description.
            courses.setdefault(c["code"], c)
        for c in courses.values():
            for req in tree_codes(c["prereq"]) + tree_codes(c["coreq"]):
                if c["code"] not in unlocks[req] and req != c["code"]:
                    unlocks[req].append(c["code"])
        with self._lock:
            self.courses, self.unlocks = courses, unlocks

    def build(self, force=False):
        if self.status["state"] == "building":
            return
        if self.status["state"] == "ready" and not force:
            return
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        self.status.update(state="building", done=0, total=0, error=None)
        try:
            index_html = self.fetcher.get(BULLETIN_URL)
            links = sorted(set(DEPT_LINK_RE.findall(index_html)))
            urls = [urljoin(BULLETIN_URL, h) for h in links]
            self.status["total"] = len(urls)
            all_courses = []
            for url in urls:
                html = self.fetcher.get(url)
                all_courses.extend(parse_department(html, url))
                self.status["done"] += 1
            self._set_courses(all_courses)
            built_at = time.strftime("%Y-%m-%d %H:%M")
            INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            INDEX_PATH.write_text(json.dumps(
                {"built_at": built_at, "pages": len(urls), "courses": all_courses}))
            self.status.update(state="ready", built_at=built_at)
        except Exception as e:  # surface the failure to the UI
            self.status.update(state="error", error=str(e))

    # ---- queries ----------------------------------------------------------
    @staticmethod
    def normalize(code):
        m = re.match(r"^\s*([A-Za-z]{2,5})\s*-?\s*(\d{1,3}[A-Za-z]{0,2})\s*$", code or "")
        return f"{m.group(1).upper()} {m.group(2).upper()}" if m else None

    def summary(self, code):
        c = self.courses.get(code)
        if not c:
            return {"code": code, "known": False}
        return {"code": code, "known": True, "title": c["title"], "units": c["units"],
                "url": c["url"], "department": c["department"],
                "prereq_text": c["prereq_text"], "prereq_notes": c["prereq_notes"],
                "coreq_text": c["coreq_text"]}

    def map_for(self, code, up_depth=6, down_depth=1):
        """Graph around one course: its prerequisite chain and what it unlocks."""
        target = self.courses.get(code)
        nodes, prereq_of, unlocked = {}, {}, []

        # Upward: follow prerequisite trees recursively.
        frontier, seen = [(code, 0)], {code}
        while frontier:
            cur, depth = frontier.pop(0)
            nodes[cur] = self.summary(cur)
            c = self.courses.get(cur)
            if not c or depth >= up_depth:
                continue
            prereq_of[cur] = {"prereq": c["prereq"], "coreq": c["coreq"]}
            for req in tree_codes(c["prereq"]) + tree_codes(c["coreq"]):
                if req not in seen:
                    seen.add(req)
                    frontier.append((req, depth + 1))

        # Downward: courses that list this one (directly, then their dependents).
        frontier, seen_down = [(code, 0)], {code}
        while frontier:
            cur, depth = frontier.pop(0)
            if depth >= down_depth:
                continue
            for nxt in sorted(self.unlocks.get(cur, [])):
                tree = self.courses[nxt]["prereq"]
                kind = "coreq" if cur in tree_codes(self.courses[nxt]["coreq"]) \
                    and cur not in tree_codes(tree) else "prereq"
                unlocked.append({"from": cur, "to": nxt, "kind": kind,
                                 "sole": _is_required(tree, cur)})
                if nxt not in seen_down:
                    seen_down.add(nxt)
                    if nxt not in nodes:
                        nodes[nxt] = self.summary(nxt)
                    frontier.append((nxt, depth + 1))

        return {"target": code, "found": target is not None, "nodes": nodes,
                "prereq_of": prereq_of, "unlocks": unlocked}

    def search(self, q, limit=12):
        q = q.strip().upper()
        if not q:
            return []
        qs = q.replace(" ", "")
        hits = []
        for code, c in self.courses.items():
            if code.replace(" ", "").startswith(qs) or q in c["title"].upper():
                hits.append({"code": code, "title": c["title"]})
        hits.sort(key=lambda h: (not h["code"].replace(" ", "").startswith(qs),
                                 h["code"].split()[0],
                                 int(re.match(r"\d+", h["code"].split()[1]).group())))
        return hits[:limit]


def _is_required(tree, code):
    """True if `code` must be taken (not merely one option of an OR)."""
    if tree is None:
        return False
    if tree["op"] == "course":
        return tree["code"] == code
    if tree["op"] == "and":
        return any(_is_required(a, code) for a in tree["args"])
    return all(_is_required(a, code) for a in tree["args"])
