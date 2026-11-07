"""Offline tests for the prerequisite parser in scripts/build_course_catalog.py
(no corpus, no database: small documents written here)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from build_course_catalog import build, parse_prereq        # noqa: E402

TITLES = {'ECE 203': ['Electrical Circuits II'], 'ECE 203L': ['Electrical Circuits II Laboratory'],
          'MAT 216': ['MATH IV: Linear Algebra and Fourier Analysis']}


def rule(text):
    return parse_prereq(text, lambda code: TITLES.get(code, ()))


class Parse(unittest.TestCase):
    def test_and_with_or_permission(self):
        r = rule('CSE 221, and CSE 331 or permission of the instructor')
        self.assertEqual(r['status'], 'parsed')
        self.assertEqual(r['all_of'], ['CSE 221', {'any_of': ['CSE 331', 'PERMISSION']}])
        self.assertTrue(r['permission'])

    def test_consent_counts_as_permission(self):
        r = rule('CSE 111, and CSE 422 or consent of the instructor)')
        self.assertEqual(r['all_of'], ['CSE 111', {'any_of': ['CSE 422', 'PERMISSION']}])

    def test_permission_only(self):
        self.assertEqual(rule('Permission of instructor)')['all_of'], ['PERMISSION'])

    def test_course_and_permission(self):
        self.assertEqual(rule('ECO515 and permission of the instructor')['all_of'], ['ECO 515', 'PERMISSION'])

    def test_bracketed_or_permission_applies_to_the_list(self):
        r = rule('ECO 208, ECO209 (or permission of instructor)')
        self.assertEqual(r['all_of'], [{'any_of': [{'all_of': ['ECO 208', 'ECO 209']}, 'PERMISSION']}])

    def test_slash_means_one_of(self):
        self.assertEqual(rule('MAT101/ MAT105/ MAT110, ECO101')['all_of'],
                         [{'any_of': ['MAT 101', 'MAT 105', 'MAT 110']}, 'ECO 101'])

    def test_titles_after_codes_are_dropped(self):
        r = rule('ECE 203 Electrical Circuits II ECE 203L Electrical Circuits II Laboratory '
                 'MAT 216 Mathematics IV Linear Algebra and Fourier Analysis')
        self.assertEqual((r['status'], r['all_of']), ('parsed', ['ECE 203', 'ECE 203L', 'MAT 216']))
        r = rule('BUS526 Financial System and Managerial Finance and BUS527 Organizational Behavior, '
                 'Leadership and Ethics')
        self.assertEqual(r['all_of'], ['BUS 526', 'BUS 527'])

    def test_qualified_code_is_unparsed_and_keeps_the_text(self):
        r = rule('CSE 320 for CCNA certification.')
        self.assertEqual(r['status'], 'unparsed')
        self.assertEqual(r['text'], 'CSE 320 for CCNA certification')
        self.assertEqual((r['all_of'], r['codes']), ([], ['CSE 320']))

    def test_words_only_is_unparsed(self):
        self.assertEqual(rule('All core Courses')['status'], 'unparsed')

    def test_alternative_in_words_is_partial(self):
        r = rule('PHY 112 or appropriate experience in electronic circuits)')
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(r['all_of'], [{'any_of': ['PHY 112', {'text': 'appropriate experience in electronic circuits'}]}])

    def test_or_equivalent_flag(self):
        r = rule('MAT 110 or equivalent')
        self.assertTrue(r['or_equivalent'])
        self.assertEqual(r['all_of'], ['MAT 110'])

    def test_none(self):
        self.assertEqual(rule('None')['status'], 'none')


DOC = """# Bachelor of Science in Computer Science & Engineering (CSE)

#### Course Details

**CSE 340: Computer Architecture (3 credits)**

A systematic study of the various elements in computer design, including circuit design, storage mechanisms and addressing schemes. (Pre req. CSE 260)

**CSE 490: WAN Routing and Technologies (Special Topics) (3 credits)**

Routing protocols, WAN technologies and network design for students who want to work towards the CCNA certification.

Prereq.: CSE 320 and CSE 421

**CSE 490: Special Topics (3 credits)**

Selected topics of current interest in computer science and engineering, chosen by the department each semester.

(Prereq.: Permission of instructor)
"""


class Build(unittest.TestCase):
    def setUp(self):
        self.cards = build([('cse_page', 'Bachelor of Science in Computer Science & Engineering (CSE)',
                             'webpage', DOC, '')])

    def test_prerequisite_at_the_end_of_a_paragraph(self):
        p = self.cards['CSE 340']['prerequisites']
        self.assertEqual([x['rule']['all_of'] for x in p], [['CSE 260']])
        self.assertNotIn('Pre req', self.cards['CSE 340']['descriptions'][0]['text'])

    def test_one_code_two_courses(self):
        p = {x['offering']: x['rule']['all_of'] for x in self.cards['CSE 490']['prerequisites']}
        self.assertEqual(p, {'WAN Routing and Technologies (Special Topics)': ['CSE 320', 'CSE 421'],
                             'Special Topics': ['PERMISSION']})


if __name__ == '__main__':
    unittest.main()
