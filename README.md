# OMIS107_HW4 — SCU Prerequisite Map

The home page asks for a course (for example `OMIS 107`). If the course isn't in
the bulletin, a "Course not found" message appears. Otherwise the map page draws a
top-down diagram: the courses you must take before it are above, and the courses
it unlocks are below. **← Back to home** returns to the home page.

You can also pick your major on the home page. This is optional. With a major
chosen, the map shows only the courses listed in that major's requirements. If the
course you type isn't one of them, a "Not in your major" message lets you map it
for any major instead. You can change the major on the map page too.

The data comes from the course descriptions in the public
[SCU Undergraduate Bulletin](https://www.scu.edu/bulletin/undergraduate/).

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Then open <http://127.0.0.1:8001>.

The first time it starts, the server reads the bulletin's department pages in the
background. That takes about 8 minutes, and the page shows its progress. The
result is saved in `cache/`, so later starts are instant. Pages are re-downloaded
only after a week.

## How it reads the bulletin politely

`fetcher.py` handles every request to scu.edu:

- It checks `robots.txt` before each request and skips disallowed URLs.
- It sends the User-Agent `SCU-Prereq-Map/1.0 (student course project for OMIS 107; …)`.
- It waits 6 seconds between any two requests, so a full read is about 10 requests a
  minute across the whole site, and no single page is requested more than
  3 times a minute.
- It caches every page on disk, so each page is normally downloaded once.

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

## How majors are read

`bulletin.py` reads the "Requirements for the Major(s)" section on each department
page. Lines such as "Major in Business Analytics" or "Bachelor of Science in
Biochemistry" start a new major. Every course code in those lists counts toward
the major, including ranges like `CSEN 101–180`. When a major course's prerequisite
is outside the major, the map skips that course and links to its prerequisites
instead.

Limits: majors that describe requirements only in words ("five upper-division
courses in Spanish") are left out of the menu. School-wide requirements, such as
the Leavey business core, aren't counted unless the major page lists them.

Course headings in the bulletin show only a number ("107. Systems Programming").
`bulletin.py` works out the subject code (OMIS, CSCI, …) from how the page refers to
those numbers elsewhere.

## Files

- `app.py`: Flask server: pages (`/`, `/map?course=…&major=…`) and JSON API (`/api/map`, `/api/majors`, `/api/search`, `/api/status`)
- `bulletin.py`: finds department pages, parses courses and majors, builds the index
- `prereqs.py`: prerequisite sentence parser
- `fetcher.py`: rate-limited, robots-aware, cached HTTP client
- `static/home.html`: the home page with the course box
- `static/index.html`: the diagram page (layout by [dagre](https://github.com/dagrejs/dagre))
