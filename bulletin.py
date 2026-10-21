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
INDEX_VERSION = 2  # 2: adds majors

# "Requirements for the Majors", "Requirements for the Major in Civil Engineering",
# "Major in Electrical Engineering"
MAJOR_SECTION_RE = re.compile(r"^(?:requirements for the majors?|major)(?:\s+in\s+(.+?))?\s*$", re.I)
# A paragraph that starts one major inside a section: "Major in Studio Art",
# "Bachelor of Science in Biochemistry, ACS Certified"
MAJOR_MARKER_RE = re.compile(r"^(?:major in|bachelor of (?:science|arts) in)\s+(.+?)\s*:?\s*$", re.I)
MINOR_RE = re.compile(r"^(?:requirements for the )?minors?\b", re.I)
# "OMIS 105, 106, and 107", "CSEN 101–180", "ARTS 74 or 174, 75 or 175, and 177"
CODE_LIST_RE = re.compile(
    r"\b([A-Z]{2,5})\s?(\d{1,3}[A-Z]{0,2})\b"
    r"((?:(?:\s*(?:,|and|or|/|\+|&|–|-|to|through))+\s*\d{1,3}[A-Z]{0,2}\b)*)")
LIST_TAIL_RE = re.compile(r"((?:\s*(?:,|and|or|/|\+|&|–|-|to|through))+)\s*(\d{1,3}[A-Z]{0,2})\b")

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


def major_refs(text):
    """Course codes and number ranges named in one line of major requirements.

    Returns (codes, ranges) where ranges are (subject, low, high) for wording
    like "CSEN 101–180". Codes are not checked against the index here.
    """
    codes, ranges = [], []
    # "ECEN 21 (or CSEN 21), 50, 153": the 50 and 153 belong to ECEN, so read
    # parentheticals on their own and take them out of the main sentence.
    parts = re.findall(r"\(([^()]*)\)", text) + [re.sub(r"\([^()]*\)", " ", text)]
    for part in parts:
        for m in CODE_LIST_RE.finditer(part):
            subj = m.group(1)
            if subj in NOT_SUBJECTS:
                continue
            prev = m.group(2)
            codes.append(f"{subj} {prev}")
            for sep, num in LIST_TAIL_RE.findall(m.group(3)):
                if re.search(r"–|-|\bto\b|through", sep) and prev.isdigit() and num.isdigit():
                    ranges.append((subj, int(prev), int(num)))
                else:
                    codes.append(f"{subj} {num}")
                prev = num
    return codes, ranges


def parse_majors(html, url):
    """Majors described on a department page and the courses each one names."""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with(" ")
    main = soup.find("h1")
    if not main:
        return []
    dept = _text(main)
    dept_name = re.sub(r"^(the\s+)?department of\s+|\s+(department|program)$", "",
                       dept.strip(), flags=re.I)

    majors = {}            # name -> {"codes": [], "ranges": []}
    in_section = False
    current = None         # major name, or None for text shared by the section
    shared = ([], [])      # codes/ranges before the first named major
    section_majors = []

    def close_section():
        # Text before the first "Major in …" applies to every major in the section;
        # a section with no named majors is the department's one major.
        if not in_section:
            return
        names = section_majors or ([dept_name] if shared[0] or shared[1] else [])
        for n in names:
            m = majors.setdefault(n, {"codes": [], "ranges": []})
            m["codes"] += shared[0]
            m["ranges"] += shared[1]

    for el in main.parent.find_all(["h1", "h2", "h3", "h4", "p", "li", "td"]):
        if el.name == "p" and el.find_parent(["li", "td"]) is not None:
            continue
        txt = _text(el)
        if el.name in ("h1", "h2"):
            close_section()
            m = MAJOR_SECTION_RE.match(txt)
            in_section = bool(m) and el.name == "h2"
            current = m.group(1) if in_section and m.group(1) else None
            shared, section_majors = ([], []), []
            if current:
                section_majors.append(current)
            continue
        if not in_section or not txt:
            continue
        if MINOR_RE.match(txt) and el.name != "li":
            close_section()
            in_section = False
            continue
        marker = MAJOR_MARKER_RE.match(txt) if el.name != "li" and len(txt) < 90 else None
        if marker:
            current = re.sub(r"\s*\([A-Z]{2,5}\)$", "", marker.group(1))
            if current not in section_majors:
                section_majors.append(current)
            continue
        codes, ranges = major_refs(CROSSLIST_RE.sub(" ", txt))
        if current is None:
            shared[0].extend(codes)
            shared[1].extend(ranges)
        else:
            m = majors.setdefault(current, {"codes": [], "ranges": []})
            m["codes"] += codes
            m["ranges"] += ranges
    close_section()
    school = re.search(r"/chapter-\d+-([^/]+)/", url)
    school = re.sub(r"\b(Of|And)\b", lambda w: w.group().lower(),
                    school.group(1).replace("-", " ").title()) if school else ""
    return [{"name": n, "school": school, "department": dept, "url": url, **refs}
            for n, refs in majors.items()]


class BulletinIndex:
    """Builds the course index in a background thread and answers queries."""

    def __init__(self, fetcher=None):
        self.fetcher = fetcher or PoliteFetcher()
        self.courses = {}
        self.unlocks = defaultdict(list)
        self.majors = {}
        self.status = {"state": "idle", "done": 0, "total": 0, "error": None,
                       "built_at": None, "source": BULLETIN_URL}
        self._lock = threading.Lock()
        self._load_saved()

    # ---- building ---------------------------------------------------------
    def _load_saved(self):
        if INDEX_PATH.exists():
            data = json.loads(INDEX_PATH.read_text())
            # An index saved by an older version is missing data; build()
            # re-reads it (from the page cache when the pages are fresh).
            if data.get("version") != INDEX_VERSION:
                return
            self._set_courses(data["courses"])
            self._set_majors(data["majors"])
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

    def _set_majors(self, major_list):
        """Resolve each major's codes and ranges against the course index."""
        by_number = defaultdict(list)
        for code, c in self.courses.items():
            n = re.match(r"\d+", c["number"])
            by_number[c["subject"]].append((int(n.group()), code))
        merged = {}
        for m in major_list:
            # Two pages can describe the same major (AIS is on both the
            # accounting and information systems pages).
            entry = merged.setdefault((m["name"], m["school"]), {**m, "courses": set()})
            entry["courses"].update(c for c in m["codes"] if c in self.courses)
            for subj, lo, hi in m["ranges"]:
                entry["courses"].update(code for n, code in by_number[subj] if lo <= n <= hi)
        name_count = Counter(name for name, _ in merged)
        majors = {}
        for (name, school), m in merged.items():
            if not m["courses"]:  # "five upper-division courses" names none
                continue
            label = f"{name} ({school})" if name_count[name] > 1 else name
            mid = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            majors[mid] = {"id": mid, "name": label, "school": school,
                           "department": m["department"], "url": m["url"],
                           "courses": m["courses"]}
        with self._lock:
            self.majors = majors

    def major_list(self):
        return sorted(({"id": m["id"], "name": m["name"], "school": m["school"],
                        "courses": len(m["courses"])} for m in self.majors.values()),
                      key=lambda m: m["name"])

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
            all_courses, all_majors = [], []
            for url in urls:
                html = self.fetcher.get(url)
                all_courses.extend(parse_department(html, url))
                all_majors.extend(parse_majors(html, url))
                self.status["done"] += 1
            self._set_courses(all_courses)
            self._set_majors(all_majors)
            built_at = time.strftime("%Y-%m-%d %H:%M")
            INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            INDEX_PATH.write_text(json.dumps(
                {"version": INDEX_VERSION, "built_at": built_at, "pages": len(urls),
                 "courses": all_courses, "majors": all_majors}))
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

    def _only(self, tree, keep, through=None):
        """`tree` reduced to the courses in `keep`.

        A dropped course is replaced by its own (reduced) prerequisites, so a
        major course reached through a non-major one stays connected. Pass
        through=None to drop courses without looking past them (corequisites).
        """
        if tree is None:
            return None
        if tree["op"] == "course":
            code = tree["code"]
            if code in keep:
                return tree
            c = self.courses.get(code)
            if through is None or code in through or not c:
                return None
            return self._only(c["prereq"], keep, through | {code})
        args = [a for a in (self._only(a, keep, through) for a in tree["args"]) if a]
        if not args:
            return None
        return args[0] if len(args) == 1 else {"op": tree["op"], "args": args}

    def map_for(self, code, up_depth=6, down_depth=1, major=None):
        """Graph around one course: its prerequisite chain and what it unlocks.

        With `major` (an id from self.majors), only courses that major names
        are shown.
        """
        target = self.courses.get(code)
        nodes, prereq_of, unlocked = {}, {}, []
        keep = self.majors[major]["courses"] | {code} if major else None

        # Upward: follow prerequisite trees recursively.
        frontier, seen = [(code, 0)], {code}
        while frontier:
            cur, depth = frontier.pop(0)
            nodes[cur] = self.summary(cur)
            c = self.courses.get(cur)
            if not c or depth >= up_depth:
                continue
            if keep is None:
                pre, co = c["prereq"], c["coreq"]
            else:
                pre, co = self._only(c["prereq"], keep, frozenset()), self._only(c["coreq"], keep)
            prereq_of[cur] = {"prereq": pre, "coreq": co}
            for req in tree_codes(pre) + tree_codes(co):
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
                if keep is not None and nxt not in keep:
                    continue
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
