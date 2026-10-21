# OMIS107_HW4 — SCU Prerequisite Map

Pick a course (for example `OMIS 107`) and, if you like, your major. The app draws a
top-down diagram: the courses you must take before it are above, and the courses
it unlocks are below.

The data comes from the course descriptions and major requirements in the public
[SCU Undergraduate Bulletin](https://www.scu.edu/bulletin/undergraduate/).

## Run it

You need Python 3.9 or newer.

**macOS (Terminal):**

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

**Windows (Command Prompt or PowerShell):**

```bat
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

If `py` isn't found, install Python from <https://www.python.org/downloads/> and
tick "Add python.exe to PATH" during setup.

Then open <http://127.0.0.1:8001>. Press `Ctrl+C` in the terminal to stop the server.

The first time it starts, the server reads the bulletin's department pages in the
background. That takes about 8 minutes, and the page shows its progress. The
result is saved in `cache/`, so later starts are instant. Pages are re-downloaded
only after a week. If the saved index came from an older version of the app (for
example, one without majors), it is rebuilt automatically. The rebuild reads pages
from `cache/` when they are less than a week old.

## Using it

### Home page

1. **Your major (optional):** pick one from the menu, or leave it on "Any major" to
   see every course. The browser remembers your choice.
2. **Course:** type a subject and number, such as `CSCI 61`. Suggestions appear as
   you type.
3. Press **Find prerequisites**.

- If the course isn't in the bulletin, a **Course not found** message appears and
  the box is cleared.
- If you picked a major and the course isn't one of its requirements, a **Not in
  your major** message appears. Choose **Show it for any major** to map it anyway,
  or **OK** to try another course.

### Map page

- **← Back to home** returns to the home page.
- The header has the same course box and **Major** menu. Change either one, or
  both, then press **Map it**:
  - To change only the major, pick it and press **Map it** with the course box
    empty. The current course is re-mapped for the new major.
  - If the new course isn't in the new major, **OK** keeps your new major and puts
    the course back in the box, so you can fix either one.
- **Prerequisites** (direct only or full chain) and **Unlocks** (1–3 levels) re-map
  the current course as soon as you change them.
- Click any box in the diagram, or any course under "Unlocks" in the side panel,
  to map that course.
- Use **+**, **−** and **Fit**, the mouse wheel, or drag to move around the diagram.
- With a major chosen, the side panel says the map is filtered and links to the
  major's requirements in the bulletin.

The link in the address bar (`/map?course=OMIS%20107&major=…`) opens the same map
again.

## How it reads the bulletin politely

`fetcher.py` handles every request to scu.edu:

- It checks `robots.txt` before each request and skips disallowed URLs.
- It sends the User-Agent `SCU-Prereq-Map/1.0 (student course project for OMIS 107; …)`.
- It waits 6 seconds between any two requests, so a full read is about 10 requests a
  minute across the whole site, and no single page is requested more than
  3 times a minute.
- It caches every page on disk, so each page is normally downloaded once.

Courses and majors are read from the same department pages, so the major feature
adds no extra requests. The web pages only call this app's own API, never scu.edu.

## How prerequisites are interpreted

`prereqs.py` turns sentences like "Prerequisite: OMIS 41 or ECON 41 and 42" into
AND/OR logic:

| Bulletin wording | Read as |
|---|---|
| `MATH 11 or 30` | MATH 11 **or** MATH 30 (a bare number keeps the subject) |
| `ECON 41 and 42`, `ECON 41+42`, `OMIS 108/108E` | grouped tightly |
| `OMIS 41 or ECON 41 and 42` | OMIS 41 **or** (ECON 41 **and** ECON 42) |
| `MATH 30 or MATH 11 and OMIS 40` | (MATH 30 **or** MATH 11) **and** OMIS 40 |
| `OMIS 105, 106, or 107` | a comma list takes its last connector |
| `upper-division standing`, `permission of instructor` | shown as a note |

The diagram shows "ONE OF" boxes where any one path is enough. The side panel
always shows the bulletin's exact wording, because automatic parsing can be wrong.

Course headings in the bulletin show only a number ("107. Systems Programming").
`bulletin.py` works out the subject code (OMIS, CSCI, …) from how the page refers to
those numbers elsewhere.

## How majors are read

`bulletin.py` reads the "Requirements for the Major(s)" section on each department
page. Lines such as "Major in Business Analytics" or "Bachelor of Science in
Biochemistry" start a new major. Every course code in those lists counts toward
the major, including ranges like `CSEN 101–180`. A major that appears in two
schools, such as Economics, is listed once for each, with the school in brackets.

With a major chosen, courses outside the major are left off the map. When a major
course's prerequisite is outside the major, the map skips that course and links
to its prerequisites instead.

Limits:

- Majors that describe requirements only in words ("five upper-division courses
  in Spanish") are left out of the menu.
- School-wide requirements, such as the Leavey business core, aren't counted
  unless the major page lists them.
- Labs described as "with associated labs" rather than by course code are
  filtered out.
- If one option of a "ONE OF" choice is outside the major, it is dropped, so the
  remaining option can look required. Check the bulletin wording in the side panel.

## Tests

macOS:

```sh
.venv/bin/python -m unittest test_prereqs test_majors
```

Windows:

```bat
.venv\Scripts\python -m unittest test_prereqs test_majors
```

## Files

- `app.py`: Flask server: pages (`/`, `/map?course=…&major=…`) and JSON API (`/api/map`, `/api/majors`, `/api/search`, `/api/status`)
- `bulletin.py`: finds department pages, parses courses and majors, builds the index
- `prereqs.py`: prerequisite sentence parser
- `fetcher.py`: rate-limited, robots-aware, cached HTTP client
- `static/home.html`: the home page with the major menu and course box
- `static/index.html`: the diagram page (layout by [dagre](https://github.com/dagrejs/dagre))
- `test_prereqs.py`, `test_majors.py`: unit tests for the prerequisite and major parsers
