"""Offline tests for the announcement rules in bracu/retriever.py and bracu/prompts.py
(no database: pure functions only)."""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bracu.prompts import announcement_meta, format_sources                 # noqa: E402
from bracu.retriever import announcement_weight, wants_latest               # noqa: E402

TODAY = date(2026, 10, 29)


def ann(days_old, listed=True):
    return {'published_at': date.fromordinal(TODAY.toordinal() - days_old), 'listed': listed}


class Weights(unittest.TestCase):
    def test_age_and_confirmation(self):
        self.assertEqual(announcement_weight(ann(0), TODAY), (1.1, False))
        self.assertEqual(announcement_weight(ann(89), TODAY), (1.1, False))
        self.assertEqual(announcement_weight(ann(90), TODAY), (0.8, False))
        self.assertEqual(announcement_weight(ann(365), TODAY), (0.8, False))
        self.assertEqual(announcement_weight(ann(366), TODAY), (1.0, True))     # like superseded
        w, old = announcement_weight(ann(2, listed=False), TODAY)
        self.assertAlmostEqual(w, 1.1 * 0.9)
        self.assertFalse(old)
        w, _ = announcement_weight(ann(120, listed=False), TODAY)
        self.assertAlmostEqual(w, 0.8 * 0.9)
        self.assertEqual(announcement_weight({'published_at': None, 'listed': True}, TODAY), (1.1, False))


class LatestMode(unittest.TestCase):
    def test_triggers(self):
        for q in ('What are the latest announcements?', 'any new notices?', 'Recent notice from BRACU',
                  'upcoming announcements this week', 'সর্বশেষ নোটিশ কী?', 'নতুন নোটিশ আছে?',
                  'today\'s notice'):
            self.assertTrue(wants_latest([q]), q)

    def test_no_trigger(self):
        for q in ('When is the LLB mid-term for Fall 2026?', 'Is there a notice about the mid-term?',
                  'What is new in the CSE curriculum?', 'What is the fine for smoking?',
                  'নতুন ছাত্রদের জন্য ফি কত?', ''):
            self.assertFalse(wants_latest([q]), q)


class Prompt(unittest.TestCase):
    def chunk(self, **kw):
        c = {'doc_type': 'announcement', 'title': 'LLB Mid Term schedule for Fall 2026',
             'section': 'LLB Mid Term schedule for Fall 2026', 'status': 'current',
             'url': 'https://www.bracu.ac.bd/llb-mid-term-schedule-fall-2026',
             'published_at': date(2026, 10, 27), 'date_source': 'snippet', 'full_text': False,
             'listed': False, 'text': 'LLB Mid Term schedule for Fall 2026. Exam schedule, PDF.'}
        c.update(kw)
        return c

    def test_link_only_provisional(self):
        meta = announcement_meta(self.chunk())
        self.assertEqual(meta[0], 'Published: 27 October 2026')
        self.assertIn('Link: https://www.bracu.ac.bd/llb-mid-term-schedule-fall-2026', meta)
        self.assertIn('LINK ONLY: title and short snippet; the full notice was not read', meta)
        self.assertTrue(any('not yet confirmed' in m for m in meta))

    def test_found_date_and_full_text(self):
        meta = announcement_meta(self.chunk(date_source='found', full_text=True, listed=True))
        self.assertEqual(meta[0], 'Published: around 27 October 2026')
        self.assertFalse(any('LINK ONLY' in m or 'not yet confirmed' in m for m in meta))

    def test_corpus_sources_unchanged(self):
        c = {'title': 'Examinations Policy', 'section': 'Fines', 'status': 'current', 'text': 'x',
             'doc_type': 'policy'}
        self.assertEqual(format_sources([c]), '[S1] Examinations Policy — Fines\n(Status: Current)\nx')


if __name__ == '__main__':
    unittest.main()
