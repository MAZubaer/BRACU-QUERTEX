"""Tokenizer tests (bracu/text.py): letter/digit splitting, and everything else unchanged."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bracu.text import tokenize   # noqa: E402


class CourseCodes(unittest.TestCase):
    def test_with_and_without_space(self):
        for a, b in (('CSE420', 'CSE 420'), ('bus524', 'BUS 524'), ('ENG103', 'eng 103'),
                     ('MAT312', 'MAT 312')):
            self.assertEqual(tokenize(a), tokenize(b), a)
        self.assertEqual(tokenize('CSE420'), ['cse', '420'])

    def test_letter_suffixes(self):
        self.assertEqual(tokenize('EEE 400D'), ['eee', '400', 'd'])
        self.assertEqual(tokenize('eee400d'), ['eee', '400', 'd'])
        self.assertEqual(tokenize('EEE341L'), tokenize('EEE 341L'))
        self.assertEqual(tokenize('EEE101/101L'), ['eee', '101', '101', 'l'])

    def test_in_a_question(self):
        self.assertEqual(tokenize('Tell me about CSE420'), ['tell', 'cse', '420'])


class Dates(unittest.TestCase):
    def test_semesters_and_ordinals(self):
        self.assertEqual(tokenize('fall2026'), tokenize('Fall 2026'))
        self.assertEqual(tokenize('fall2026'), ['fall', '2026'])
        self.assertEqual(tokenize('30th October 2026'), tokenize('30 October 2026'))
        self.assertEqual(tokenize('1st, 2nd and 3rd year'), ['1', '2', '3', 'year'])
        self.assertEqual(tokenize('2026-10-28'), ['2026', '10', '28'])          # unchanged

    def test_ordinal_only_after_digits(self):
        self.assertEqual(tokenize('the 4th st'), ['4', 'st'])                    # a real "st" stays


class Unchanged(unittest.TestCase):
    """Tokens without a letter-digit mix tokenize exactly as before (frozen outputs)."""

    def test_frozen_sentences(self):
        cases = {
            'What is the fine for smoking on campus?': ['fin', 'smok', 'campus'],
            'Payments are submitted using the portal': ['pay', 'submit', 'use', 'portal'],
            'Contact info@bracu.ac.bd for details': ['contact', 'info', 'bracu', 'ac', 'bd', 'detail',
                                                    'email'],
            'পরবর্তী ছুটি কবে?': ['পরবর্তী', 'ছুটি', 'কবে'],
            'Tk 8,250 per credit': ['tk', '8', '250', 'credit'],
        }
        for text, want in cases.items():
            self.assertEqual(tokenize(text), want, text)

    def test_emails(self):
        self.assertEqual(tokenize('student123@g.bracu.ac.bd'),
                         ['student', '123', 'g', 'bracu', 'ac', 'bd', 'email'])
        self.assertEqual(tokenize('info@bracu.ac.bd').count('email'), 1)


if __name__ == '__main__':
    unittest.main()
