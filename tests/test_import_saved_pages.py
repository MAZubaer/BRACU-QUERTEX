"""Offline tests for scripts/import_saved_pages.py (saved-page fixtures; no database).

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

import import_saved_pages as imp                  # noqa: E402
from announcements_store import build_chunks      # noqa: E402

FIX = ROOT / 'tests' / 'fixtures'
LISTING = FIX / 'Announcements _ BRAC University.html'
CHALLENGE = FIX / 'cloudflare_challenge_403.html'
POST = FIX / 'post_synthetic_llb_mid_term.html'
TODAY = date(2026, 10, 29)
SITE = 'https://www.bracu.ac.bd/'

LISTED = [  # (publish date, slug) exactly as on page 1 of the saved listing
    (date(2026, 10, 28), 'admission-test-certificate-courses-be-held-30th-october-2026'),
    (date(2026, 10, 27), 'llb-mid-term-schedule-fall-2026'),
    (date(2026, 10, 14), 'brac-university-host-second-khondker-mahbubuddin-ahmed-memorial-'
                         'international-moot-court-competition'),
    (date(2026, 10, 3), 'class-schedule-fall-2026'),
    (date(2026, 9, 30), 'notice-application-18th-convocation-2026'),
    (date(2026, 9, 28), 'first-year-advising-team-fyat-fall-2026'),
    (date(2026, 9, 24), 'self-registration-schedule-fall-2026'),
    (date(2026, 9, 17), 'notice-pre-university-summer-2026'),
    (date(2026, 9, 13), 'admission-test-results-notice-fall-2026'),
    (date(2026, 9, 7), 'final-exam-schedule-summer-2026-except-phr'),
    (date(2026, 8, 25), 'ug-pre-registration-phase-2-schedule-fall-2026'),
    (date(2026, 8, 24), 'fablab-recruitment_circular'),
    (date(2026, 8, 3), 'announcement-18th-convocation-2026'),
    (date(2026, 7, 20), 'mid-term-exam-schedule-summer-2026'),
    (date(2026, 7, 12), 'public-reporting-contract-signing-b-113-icseteprgdbracgd2'),
    (date(2026, 7, 12), 'public-reporting-contract-signing-b-113-icseteprgdbracgd1'),
]
ADMISSION = SITE + LISTED[0][1]
LLB = SITE + LISTED[1][1]
SAVED_AT = datetime(2026, 10, 28, 20, 26, tzinfo=timezone.utc)


def row(published, source='search', listed=False, full_text=False, first_seen=None, title='x'):
    return {'source': source, 'listed': listed, 'full_text': full_text, 'published_at': published,
            'first_seen': first_seen or datetime(2026, 10, 28, 19, 26, tzinfo=timezone.utc),
            'snippet': 'snippet text', 'title': title}


class Listing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.soup = imp.load(LISTING)
        cls.listing = imp.parse_listing(cls.soup)

    def test_classified_as_listing(self):
        self.assertEqual(imp.classify(imp.load(LISTING)),
                         ('listing', 'https://www.bracu.ac.bd/news-archive/announcements'))

    def test_exactly_the_post_links_and_dates(self):
        got = [(p['published_at'], p['url']) for p in self.listing['posts']]
        self.assertEqual(got, [(d, SITE + s) for d, s in LISTED])
        # nothing from the menu, filter form, pager or footer
        self.assertFalse(any('news-archive' in u or '?' in u for _, u in got))

    def test_every_listed_post_passes_the_search_filter(self):
        # so a search entry for any listed post can later be confirmed (or removed)
        for p in self.listing['posts']:
            self.assertEqual(imp.check_url(p['url']), (True, ''), p['url'])

    def test_exact_titles(self):
        titles = {p['url']: p['title'] for p in self.listing['posts']}
        self.assertEqual(titles[ADMISSION],
                         'Admission Test for the Certificate Courses- to be held on 30th October 2026')
        self.assertEqual(titles[LLB], 'LLB Mid Term schedule for Fall 2026')
        self.assertEqual(titles[SITE + LISTED[2][1]], 'BRAC University to Host Second Khondker '
                         'Mahbubuddin Ahmed Memorial International Moot Court Competition 2026')

    def test_pager(self):
        self.assertEqual((self.listing['page'], self.listing['has_next']), (1, True))
        self.assertEqual(imp.reconcile_range(self.listing), (date(2026, 7, 12), date(2026, 10, 28), False))

    def test_both_existing_posts_confirmed(self):
        existing = {ADMISSION: row(date(2026, 10, 28)), LLB: row(date(2026, 10, 27))}
        actions = imp.plan_listing(self.listing, existing, SAVED_AT)
        kinds = [(k, u) for k, u, _ in actions]
        self.assertIn(('confirm', ADMISSION), kinds)
        self.assertIn(('confirm', LLB), kinds)
        self.assertEqual(sum(k == 'add' for k, _ in kinds), 14)
        self.assertFalse(any(k == 'delete' for k, _ in kinds))
        d = next(d for k, u, d in actions if u == LLB)
        ann = imp.listing_ann(d, d['row'])
        self.assertEqual((ann['date_source'], ann['listed'], ann['rag_priority'], ann['source'],
                          ann['full_text']), ('page', True, 2, 'search', False))
        # a confirmed search row keeps its snippet, without the "(link only)" header
        self.assertEqual(build_chunks(ann, ann['snippet'])[0]['context_text'],
                         'Announcement: LLB Mid Term schedule for Fall 2026 | Published: 27 October 2026'
                         '\n\nsnippet text')

    def test_new_listing_post_context(self):
        p = self.listing['posts'][2]
        c = build_chunks(imp.listing_ann(p), '')[0]
        self.assertEqual(c['context_text'], f"Announcement: {p['title']} | Published: 14 October 2026")
        self.assertEqual((c['rag_priority'], c['origin'], c['text']), (2, 'inbox', p['title']))

    def test_reconciliation(self):
        gone = SITE + 'old-provisional-notice-2026'
        existing = {
            LLB: row(date(2026, 10, 27)),
            SITE + 'not-on-page-inside-range': row(date(2026, 8, 1)),            # deleted
            SITE + 'not-on-page-newest-date': row(date(2026, 10, 28)),          # newest: inclusive
            gone: row(date(2026, 7, 12)),                                        # oldest: exclusive
            SITE + 'older-than-range-notice': row(date(2026, 7, 11)),
            SITE + 'newer-than-range-notice': row(date(2026, 10, 29)),
            SITE + 'full-text-import-notice': row(date(2026, 8, 1), source='inbox', full_text=True),
            SITE + 'listed-elsewhere-notice': row(date(2026, 8, 1), listed=True),
            SITE + 'found-after-saving-notice': row(date(2026, 10, 28),
                                                    first_seen=datetime(2026, 10, 29, tzinfo=timezone.utc)),
        }
        actions = imp.plan_listing(self.listing, existing, SAVED_AT)
        deleted = sorted(u for k, u, _ in actions if k == 'delete')
        self.assertEqual(deleted, [SITE + 'not-on-page-inside-range', SITE + 'not-on-page-newest-date'])
        self.assertEqual([u for k, u, _ in actions if k == 'spare'], [SITE + 'found-after-saving-notice'])
        # on the LAST page (no "next" link) the oldest date counts too
        last = {**self.listing, 'has_next': False}
        deleted = sorted(u for k, u, _ in imp.plan_listing(last, existing, SAVED_AT) if k == 'delete')
        self.assertEqual(deleted, sorted([gone, SITE + 'not-on-page-inside-range',
                                          SITE + 'not-on-page-newest-date']))

    def test_full_text_import_is_kept_and_marked_listed(self):
        existing = {LLB: row(date(2026, 10, 27), source='inbox', full_text=True)}
        actions = imp.plan_listing(self.listing, existing, SAVED_AT)
        self.assertIn(('keep', LLB), [(k, u) for k, u, _ in actions])

    def test_email_obfuscation(self):
        self.assertEqual(imp.decode_cf_email('0f666169604f6d7d6e6c7a216e6c216d6b'), 'info@bracu.ac.bd')


class Rejected(unittest.TestCase):
    def test_cloudflare_challenge(self):
        with self.assertRaises(imp.NotImportable) as e:
            imp.classify(imp.load(CHALLENGE))
        self.assertIn('Cloudflare challenge', str(e.exception))

    def test_other_pages(self):
        for html, why in (('<html><head><title>x</title></head><body>hi</body></html>', 'no canonical'),
                          ('<link rel="canonical" href="https://cse.bracu.ac.bd/x-y-z">', 'other host'),
                          ('<link rel="canonical" href="https://www.bracu.ac.bd/news/a-b-c">', 'news'),
                          ('<link rel="canonical" href="https://www.bracu.ac.bd/news-archive/announcements">',
                           'without any posts')):
            with self.assertRaises(imp.NotImportable) as e:
                imp.classify(imp.BeautifulSoup(html, 'lxml'))
            self.assertIn(why, str(e.exception))


class Post(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        soup = imp.load(POST)
        cls.kind, cls.url = imp.classify(soup)
        cls.post = imp.parse_post(soup, cls.url, TODAY)

    def test_classified_as_post(self):
        self.assertEqual((self.kind, self.url), ('post', LLB))

    def test_title_and_own_publish_date(self):
        self.assertEqual(self.post['title'], 'LLB Mid Term schedule for Fall 2026')
        # the post's own Publish Date, not the sidebar's (1 October)
        self.assertEqual((self.post['published_at'], self.post['date_source']), (date(2026, 10, 27), 'page'))

    def test_main_text(self):
        t = self.post['text']
        self.assertIn('| Date | Course | Time |\n|---|---|---|\n| Saturday, 7 November 2026 | LAW 101 | 10:00 am |', t)
        self.assertIn('### Schedule', t)
        self.assertIn('- Bring your student ID card.', t)
        self.assertIn('info@bracu.ac.bd', t)                     # Cloudflare email decoded
        for not_main in ('Admissions', 'Recent announcements', 'Some other notice', 'Kha 224', 'Contact'):
            self.assertNotIn(not_main, t)

    def test_file_links_recorded_not_downloaded(self):
        self.assertEqual(self.post['pdf_links'], [
            {'title': 'PDF', 'url': 'https://www.bracu.ac.bd/sites/default/files/uploads/2026/10/27/'
                                    'LLB%20Mid%20Term%20Fall%202026.pdf'},
            {'title': 'Seat plan', 'url': 'https://www.bracu.ac.bd/sites/default/files/registrar/'
                                          'seat-plan-llb-fall-2026.pdf', 'note': 'link only — registrar file'}])

    def test_full_text_entry_replaces_link_only(self):
        ann, text = imp.post_ann(self.post)
        self.assertEqual((ann['full_text'], ann['source'], ann['rag_priority'], ann['date_source']),
                         (True, 'inbox', 2, 'page'))
        chunks = build_chunks(ann, text)
        self.assertTrue(chunks[0]['context_text'].startswith(
            'Announcement: LLB Mid Term schedule for Fall 2026 | Published: 27 October 2026\n\n'))
        self.assertEqual(chunks[0]['doc_id'], build_chunks(imp.listing_ann(
            {'url': LLB, 'title': 'x', 'published_at': date(2026, 10, 27)}), '')[0]['doc_id'])

    def test_privacy_guard(self):
        few = {**self.post, 'text': 'Call 01712-345678 or ID 1000063561.', 'personal': 2}
        ann, text = imp.post_ann(few)
        self.assertEqual(text, 'Call [removed] or ID [removed].')
        self.assertTrue(ann['full_text'])
        many = {**self.post, 'personal': imp.PERSONAL_LIMIT + 1}
        ann, text = imp.post_ann(many)
        self.assertEqual((ann['full_text'], text), (False, ''))
        self.assertIn('personal data', ann['note'])


if __name__ == '__main__':
    unittest.main()
