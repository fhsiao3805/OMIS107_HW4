"""Run with:  .venv/bin/python -m unittest test_majors"""

import unittest

from bulletin import BulletinIndex, major_refs, parse_majors

PAGE = """<html><body><div>
<h1>Department of Information Systems and Analytics</h1>
<h2>Requirements for the Majors</h2>
<p>In addition to fulfilling the Core Curriculum requirements, students must complete:</p>
<p>Major in Business Analytics (BA)</p>
<ul><li>OMIS 30</li><li>OMIS 109, 115, 163, and 164</li></ul>
<p>Major in Management Information Systems (MIS)</p>
<ul><li>OMIS 105, 106, and 107</li><li>Three courses from OMIS 111–118</li></ul>
<h2>Requirements for the Minor</h2>
<ul><li>OMIS 20</li></ul>
</div></body></html>"""


class MajorRefTests(unittest.TestCase):
    def test_lists(self):
        self.assertEqual(major_refs("ARTS 74 or 174, 75 or 175, and 177")[0],
                         ["ARTS 74", "ARTS 174", "ARTS 75", "ARTS 175", "ARTS 177"])

    def test_parenthetical_keeps_subject(self):
        codes, _ = major_refs("ECEN 21 (or CSEN 21), 50, 153")
        self.assertEqual(sorted(codes), ["CSEN 21", "ECEN 153", "ECEN 21", "ECEN 50"])

    def test_range(self):
        self.assertEqual(major_refs("selected from CSEN 101–180")[1], [("CSEN", 101, 180)])


class ParseMajorsTests(unittest.TestCase):
    def test_sections(self):
        url = "https://www.scu.edu/bulletin/undergraduate/chapter-4-leavey-school-of-business/isa.html"
        majors = {m["name"]: m for m in parse_majors(PAGE, url)}
        self.assertEqual(sorted(majors), ["Business Analytics", "Management Information Systems"])
        self.assertIn("OMIS 164", majors["Business Analytics"]["codes"])
        self.assertNotIn("OMIS 20", majors["Management Information Systems"]["codes"])
        self.assertEqual(majors["Management Information Systems"]["ranges"], [("OMIS", 111, 118)])
        self.assertEqual(majors["Business Analytics"]["school"], "Leavey School of Business")


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.index = BulletinIndex.__new__(BulletinIndex)
        c = lambda code, prereq=None: {"code": code, "prereq": prereq, "coreq": None}
        leaf = lambda code: {"op": "course", "code": code}
        self.index.courses = {
            "A 1": c("A 1"),
            "B 1": c("B 1", leaf("A 1")),
            "A 2": c("A 2", {"op": "or", "args": [leaf("B 1"), leaf("C 1")]}),
            "C 1": c("C 1"),
        }

    def test_skips_through_non_major_course(self):
        tree = self.index._only(self.index.courses["A 2"]["prereq"], {"A 1", "A 2"}, frozenset())
        self.assertEqual(tree, {"op": "course", "code": "A 1"})

    def test_drops_coreq_outside_major(self):
        self.assertIsNone(self.index._only({"op": "course", "code": "C 1"}, {"A 1"}))


if __name__ == "__main__":
    unittest.main()
