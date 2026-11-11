"""Offline tests for bracu/degree_cost.py (fixture facts; no database, no LLM).

tests/fixtures/degree_costs.json is a copy of data/degree_costs.json written by
scripts/build_degree_costs.py; refresh it when the fee table or a program page changes.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from bracu import degree_cost as dc                     # noqa: E402
from bracu.prompts import build_messages, format_sources  # noqa: E402

DATA = json.loads((ROOT / 'tests' / 'fixtures' / 'degree_costs.json').read_text(encoding='utf-8'))


def card(q):
    return dc.card_for(q, DATA)


def total(q, key):
    p = DATA['programs'][key]
    y, s = dc.asked_length(q)
    e = dc.estimate(p, DATA['fees'], y, s)
    return e['low'], e['high'], e['semesters']


class Trigger(unittest.TestCase):
    def test_total_cost_questions(self):
        for q in ('What is the total expense I have to pay for graduation in 4 years in CSE?',
                  'How much does the LLB cost?', 'total cost of architecture',
                  'Pharmacy degree total fees', 'How much money do I need to complete a BBA... I mean EEE?',
                  'CSE vs EEE total cost', 'CSE পড়তে মোট খরচ কত?'):
            self.assertIsNotNone(card(q), q)

    def test_single_fee_questions_left_to_the_documents(self):
        for q in ('What is the semester fee for CSE?', 'tuition fee per credit for pharmacy',
                  'How much does it cost per credit in CSE?', 'What is the admission fee?',
                  'What is the total cost of the MBA?', 'Is there a scholarship for CSE?',
                  'What is the fine for smoking?', 'When is the CSE final exam?'):
            self.assertIsNone(card(q), q)

    def test_program_unclear(self):
        c = card('How much will it cost to complete my degree?')
        self.assertEqual(c['title'], 'Degree cost estimate: program unclear')
        self.assertIn('Ask the student which undergraduate program', c['text'])


class Numbers(unittest.TestCase):
    def test_cse_four_years(self):
        # 1,500 + 33,900 + 2,500 + 136 x 8,250 + 12 x 12,600 + RS 88,000
        self.assertEqual(total('in 4 years', 'CSE'), (1_399_100, 1_399_100, 12))
        self.assertEqual(total('', 'CSE'), (1_399_100, 1_399_100, 12))

    def test_three_a_year_except_pharmacy_and_llb(self):
        self.assertEqual(total('', 'PHR')[2], 8)
        self.assertEqual(total('', 'LLB')[2], 8)
        self.assertEqual(total('', 'EEE')[2], 12)
        self.assertEqual(total('', 'ARC')[2], 15)                      # 5 years as the page says
        self.assertEqual(total('in 5 years', 'PHR')[2], 10)             # 5 x 2
        self.assertEqual(total('in 10 semesters', 'ECO')[2], 10)

    def test_program_fee_groups(self):
        # LLB: 147 plan credits x 8,800 + 8 x 14,000 + one-time 37,900 + RS 88,000
        self.assertEqual(total('', 'LLB')[0], 37_900 + 147 * 8_800 + 8 * 14_000 + 88_000)
        # Pharmacy: 164 x 8,250 + 8 x 14,000
        self.assertEqual(total('', 'PHR')[0], 37_900 + 164 * 8_250 + 8 * 14_000 + 88_000)
        # all other programs: semester fee 9,350
        self.assertEqual(total('', 'MIC')[0], 37_900 + 136 * 8_250 + 12 * 9_350 + 88_000)
        # Architecture: lecture to studio rate (the split is not given)
        low, high, _ = total('', 'ARC')
        self.assertEqual((low, high), (37_900 + 207 * 8_250 + 15 * 12_600 + 88_000,
                                       37_900 + 207 * 8_800 + 15 * 12_600 + 88_000))


class Card(unittest.TestCase):
    def test_cse_card(self):
        c = card('What is the total expense I have to pay for graduation in 4 years in CSE?')
        self.assertEqual((c['doc_type'], c['doc_id'], c['title']), ('computed', 'degree_cost',
                                                                   'Degree cost estimate for CSE'))
        t = c['text']
        for part in ('| Residential Semester (RS) fee |', 'Tk 88,000', '12 semesters × Tk 12,600',
                     '136 credits × Tk 8,250', '**Tk 1,399,100**', '4 years (as given in the question) × 3',
                     'We have 3 semesters a year: Spring, Summer and Fall.', 'Not included',
                     'Fees can be changed'):
            self.assertIn(part, t)

    def test_llb_says_where_the_credits_come_from(self):
        t = card('How much does the whole LLB cost?')['text']
        self.assertIn('lists 147 credits; the page states no total', t)
        self.assertIn('8 = 4 years × 2 semesters a year', t)

    def test_years_different_from_the_program_length(self):
        t = card('total cost of architecture in 4 years')['text']
        self.assertIn("The program's own length is 5 years = 15 semesters", t)

    def test_cs_and_cse_are_different_programs(self):
        self.assertEqual(dc.find_programs('CSE total cost', DATA['programs']), ['CSE'])
        self.assertEqual(dc.find_programs('CS total cost', DATA['programs']), ['CS'])
        self.assertEqual(dc.find_programs('Computer Science and Engineering cost', DATA['programs']), ['CSE'])
        self.assertEqual(dc.find_programs('Applied Physics and Electronics cost', DATA['programs']), ['APE'])

    def test_prompt_note(self):
        c = card('How much does the LLB cost in total?')
        text = format_sources([c])
        self.assertIn('Degree cost estimate for LLB', text)
        msgs = build_messages('How much does the LLB cost in total?', [c])
        user = msgs[-1]['content']
        self.assertIn('S1 is a degree cost estimate', user)
        self.assertNotIn('prerequisite check', user)             # not the eligibility note
        self.assertIn('DEGREE COST', msgs[0]['content'])


if __name__ == '__main__':
    unittest.main()
