"""Run with:  .venv/bin/python -m unittest test_prereqs"""

import unittest

from prereqs import extract, parse_requirement, tree_to_text

CASES = {
    "OMIS 30": "OMIS 30",
    "OMIS 41 or ECON 41 and 42": "(OMIS 41 OR (ECON 41 AND ECON 42))",
    "OMIS 41 or ECON 41+42": "(OMIS 41 OR (ECON 41 AND ECON 42))",
    "MATH 11 or 30, and OMIS 15 or 17": "((MATH 11 OR MATH 30) AND (OMIS 15 OR OMIS 17))",
    "MATH 30 or MATH 11 and OMIS 40": "((MATH 30 OR MATH 11) AND OMIS 40)",
    "ECON 1 and OMIS 108/108E/108S": "(ECON 1 AND (OMIS 108 OR OMIS 108E OR OMIS 108S))",
    "OMIS 30 and 105": "(OMIS 30 AND OMIS 105)",
    "OMIS 105, 106, or 107": "(OMIS 105 OR OMIS 106 OR OMIS 107)",
    "CSCI 61, 62 or MATH 51": "(CSCI 61 OR CSCI 62 OR MATH 51)",
    "CSCI 10, MATH 13, and MATH 51": "(CSCI 10 AND MATH 13 AND MATH 51)",
    "A grade of C- or better in CHEM 11; MATH 11 or 30": "(CHEM 11 AND (MATH 11 OR MATH 30))",
    "a grade of C- or better in ECON 41 and 42; or OMIS 30, 40, and 41":
        "((ECON 41 AND ECON 42) OR (OMIS 30 AND OMIS 40 AND OMIS 41))",
    "CHEM 11 or CHEM 11T or equivalent, CENG 143, AMTH 106, and junior standing, "
    "or instructor’s consent": "((CHEM 11 OR CHEM 11T) AND CENG 143 AND AMTH 106)",
    "FNCE 121 or 121S, FNCE 124, and FNCE 125.(5 units)":
        "((FNCE 121 OR FNCE 121S) AND FNCE 124 AND FNCE 125)",
    "a grade of C- or better in Econ 41 and 42": "(ECON 41 AND ECON 42)",
    "ITAL 1 or equivalent course abroad or scoring at level 2 in the SCU placement exam": "ITAL 1",
    "SPAN 21 and at least 2 units of SPAN 22": "(SPAN 21 AND SPAN 22)",
}


class ParseTests(unittest.TestCase):
    def test_trees(self):
        for text, expected in CASES.items():
            with self.subTest(text=text):
                tree, _ = parse_requirement(text)
                self.assertEqual(tree_to_text(tree), expected)

    def test_notes(self):
        tree, notes = parse_requirement("MATH 11 or permission of instructor")
        self.assertEqual(tree_to_text(tree), "MATH 11")
        self.assertEqual(notes, ["permission of instructor"])
        tree, notes = parse_requirement("Upper-division standing")
        self.assertIsNone(tree)
        self.assertEqual(notes, ["Upper-division standing"])

    def test_extract(self):
        desc = ("Survey of methods. Prerequisite: OMIS 41 or ECON 41 and 42. "
                "This section is equivalent to OMIS 108E. (5 units)")
        self.assertEqual(extract(desc), ("OMIS 41 or ECON 41 and 42", ""))
        # statements about *other* courses are not this course's prerequisites
        desc = ("Note: MATH 11 is not a suitable prerequisite for MATH 31 without "
                "additional preparation. Prerequisite: MATH 30. (4 units)")
        self.assertEqual(extract(desc), ("MATH 30", ""))
        self.assertEqual(extract("C&I I (ANTH 11A) is a prerequisite for C&I II (ANTH 12A)."), ("", ""))


if __name__ == "__main__":
    unittest.main()
