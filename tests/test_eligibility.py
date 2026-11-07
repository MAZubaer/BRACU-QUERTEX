"""Offline tests for bracu/eligibility.py (no database: a small catalogue written here,
with rules from the real parser in scripts/build_course_catalog.py)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from bracu import eligibility as el                         # noqa: E402
from build_course_catalog import parse_prereq                # noqa: E402

CSE_DOCS = ['cse_page', 'cse_plan']


def course(title, *prereqs, docs=('cse_page',), equivalents=()):
    """prereqs: texts, or (offering, text) for one code that names two courses."""
    out = []
    for p in prereqs:
        offering, text = p if isinstance(p, tuple) else (None, p)
        r = parse_prereq(text)
        out.append({'text': r['text'], 'rule': r, 'docs': list(docs),
                    **({'offering': offering} if offering else {})})
    return {'title': title, 'prereqs': out, 'equivalents': list(equivalents)}


DATA = {
    'programs': {
        'CSE': {'key': 'CSE', 'name': 'Bachelor of Science in Computer Science & Engineering',
                'names': ['Bachelor of Science in Computer Science & Engineering', 'Computer Science & Engineering'],
                'level': 'undergraduate', 'prefix': 'CSE', 'docs': CSE_DOCS,
                'courses': {c: 'core' for c in ['CSE 110', 'CSE 111', 'CSE 161', 'CSE 220', 'CSE 221', 'CSE 230',
                                                 'CSE 250', 'CSE 260', 'CSE 321', 'CSE 331', 'CSE 340', 'CSE 370',
                                                 'CSE 420', 'CSE 421', 'CSE 490', 'MAT 120']}},
        'CS': {'key': 'CS', 'name': 'Bachelor of Science in Computer Science',
               'names': ['Bachelor of Science in Computer Science', 'Computer Science'],
               'level': 'undergraduate', 'prefix': 'CSE', 'docs': ['cs_page'], 'courses': {'CSE 110': 'core'}},
        'EEE': {'key': 'EEE', 'name': 'Bachelor of Science in Electrical and Electronic Engineering',
                'names': ['Electrical and Electronic Engineering'], 'level': 'undergraduate', 'prefix': 'EEE',
                'docs': ['eee_page'], 'courses': {'EEE 101': 'core'}},
    },
    'courses': {
        'CSE 110': course('Programming Language I', equivalents=['CSE 161']),
        'CSE 111': course('Programming Language II', 'CSE 110'),
        'CSE 161': course('Programming Language I', equivalents=['CSE 110']),
        'CSE 220': course('Data Structures', 'CSE 111'),
        'CSE 221': course('Algorithms', 'CSE 220'),
        'CSE 230': course('Discrete Mathematics'),
        'CSE 250': course('Circuits and Electronics', 'PHY 112 or appropriate experience in electronic circuits'),
        'CSE 260': course('Digital Logic Design'),
        'CSE 321': course('Operating Systems', 'CSE 221'),
        'CSE 331': course('Automata and Computability', 'CSE 221'),
        'CSE 340': course('Computer Architecture', 'CSE 260'),
        'CSE 370': course('Database Systems', 'CSE 221'),
        'CSE 420': course('Compiler Design', 'CSE 221, and CSE 331 or permission of the instructor'),
        'CSE 421': course('Computer Networks', 'CSE 320 for CCNA certification'),
        'CSE 490': course('Special Topics', ('WAN Routing and Technologies (Special Topics)', 'CSE 320 and CSE 421'),
                          ('Special Topics', 'Permission of instructor')),
        'EEE 101': course('Electrical Circuits I', docs=('eee_page',)),
        'MAT 120': course('MATH II', docs=('mat_page',)),
    },
    'docs': {'cse_page': {'title': 'Bachelor of Science in Computer Science & Engineering (CSE)',
                          'label': 'CSE program page', 'url': 'https://example.org/cse'},
             'cse_plan': {'title': 'Updated Degree Plan — B.Sc. in CSE', 'label': 'CSE degree plan', 'url': ''},
             'cs_page': {'title': 'Bachelor of Science in Computer Science (CS)', 'label': 'CS program page', 'url': ''},
             'eee_page': {'title': 'EEE', 'label': 'EEE program page', 'url': ''},
             'mat_page': {'title': 'MAT', 'label': 'MAT program page', 'url': ''}},
}


def groups(text):
    """{'a': [lines], ...} from a card's text."""
    out, g = {}, None
    for line in text.split('\n'):
        if line[:3] in ('(a)', '(b)', '(c)', '(d)', '(e)'):
            g = line[1]
            out[g] = []
        elif line.startswith('- ') and g:
            out[g].append(line)
    return out


def codes_in(lines):
    return {' '.join(x[2:].split()[:2]) for x in lines}


class Question(unittest.TestCase):
    PREFIXES = {'CSE', 'EEE', 'MAT'}

    def test_bare_numbers_continue_a_list(self):
        found = el.find_codes("I've finished CSE 110, 111, 220 and 221.", self.PREFIXES)
        self.assertEqual([c for c, _ in found], ['CSE 110', 'CSE 111', 'CSE 220', 'CSE 221'])

    def test_not_a_course(self):
        self.assertEqual(el.find_codes('Room 101 in 2026, 136 credits', self.PREFIXES), [])

    def test_roles(self):
        r = el.roles("Can I take CSE 420 if I've done CSE 221 but not CSE 331?", self.PREFIXES)
        self.assertEqual((r['want'], r['done'], r['not']), (['CSE 420'], ['CSE 221'], ['CSE 331']))
        r = el.roles("I haven't taken CSE 331. I'm a student who did CSE 221.", self.PREFIXES)
        self.assertEqual((r['not'], r['done']), (['CSE 331'], ['CSE 221']))


class Next(unittest.TestCase):
    def setUp(self):
        self.card = el.card_for("I've finished CSE 110, 111, 220 and 221. What can I take next?", DATA)
        self.g = groups(self.card['text'])

    def test_card(self):
        self.assertEqual(self.card['title'], 'Eligibility check for Bachelor of Science in Computer Science & '
                                             'Engineering (CSE)')
        self.assertEqual(self.card['section'], 'based on CSE program page; CSE degree plan')
        self.assertEqual(self.card['url'], 'https://example.org/cse')
        self.assertEqual(self.card['doc_type'], 'computed')

    def test_groups(self):
        self.assertEqual(codes_in(self.g['a']), {'CSE 321', 'CSE 331', 'CSE 370'})
        self.assertEqual(codes_in(self.g['b']), {'CSE 230', 'CSE 260'})
        self.assertEqual(codes_in(self.g['c']), {'CSE 420', 'CSE 490'})
        self.assertEqual(codes_in(self.g['d']), {'CSE 340', 'CSE 490'})
        self.assertEqual(codes_in(self.g['e']), {'CSE 250', 'CSE 421'})

    def test_missing_courses_and_original_text(self):
        self.assertIn('CSE 340 Computer Architecture (core). Prerequisite: CSE 260 [CSE program page]. '
                      'Missing: CSE 260', self.g['d'][0])
        self.assertTrue(any('"CSE 320 for CCNA certification"' in x for x in self.g['e']))

    def test_duplicate_code_two_courses(self):
        self.assertTrue(any(x.startswith('- CSE 490 Special Topics') for x in self.g['c']))
        self.assertTrue(any(x.startswith('- CSE 490 WAN Routing') and 'Missing: CSE 320; CSE 421' in x
                            for x in self.g['d']))

    def test_skips_completed_equivalent_and_other_departments(self):
        listed = set().union(*(codes_in(v) for v in self.g.values()))
        self.assertFalse(listed & {'CSE 110', 'CSE 111', 'CSE 220', 'CSE 221', 'CSE 161', 'MAT 120', 'EEE 101'})


class Single(unittest.TestCase):
    def test_check_needs_permission(self):
        card = el.card_for("Can I take CSE 420 if I've done CSE 221 but not CSE 331?", DATA)
        self.assertEqual(card['title'], 'Eligibility check for CSE 420 (CSE program)')
        self.assertIn("Result: (c) Needs the instructor's permission", card['text'])
        self.assertIn('Needs: CSE 331 or permission of the instructor', card['text'])

    def test_check_unlocked(self):
        card = el.card_for('I have completed CSE 221. Can I take CSE 331?', DATA)
        self.assertIn('Result: (a) Can take now', card['text'])

    def test_lookup(self):
        card = el.card_for('What do I need before CSE 340?', DATA)
        self.assertEqual(card['title'], 'Prerequisites of CSE 340 (CSE program)')
        self.assertIn('- CSE 260 [CSE program page]', card['text'])

    def test_lookup_shared_code(self):
        text = el.card_for('What is the prerequisite of CSE 490?', DATA)['text']
        self.assertIn('CSE 490 WAN Routing and Technologies (Special Topics). Prerequisites', text)
        self.assertIn('- Permission of instructor [CSE program page]', text)


class Program(unittest.TestCase):
    def test_named_in_the_question(self):
        card = el.card_for("I'm a CS student. I've done CSE 110. What's next?", DATA)
        self.assertEqual(card['title'], 'Eligibility check for Bachelor of Science in Computer Science (CS)')

    def test_unclear_asks(self):
        card = el.card_for('I finished EEE 101 and CSE 110. What can I take next?', DATA)
        self.assertEqual(card['title'], 'Eligibility check: program unclear')
        self.assertIn('Ask the student which program', card['text'])


class NotAboutPrerequisites(unittest.TestCase):
    def test_no_card(self):
        for q in ['What is the fee for CSE 110?', 'Can I drop CSE 110 before the deadline?',
                  'When is the next mid term exam for CSE?', 'What courses does CSE 110 cover?']:
            self.assertIsNone(el.card_for(q, DATA), q)

    def test_no_data_no_card(self):
        self.assertIsNone(el.card_for("I've finished CSE 110. What next?", {}))


if __name__ == '__main__':
    unittest.main()
