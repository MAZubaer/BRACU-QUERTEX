"""Offline tests for scripts/sync_search.py (Tavily fixtures only: no API, no database).

    python -m unittest discover -s tests -v
"""
import glob
import json
import re
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

import sync_search as s                       # noqa: E402
from announcements_store import publish_date  # noqa: E402

TODAY = date(2026, 10, 29)
FIXTURES = sorted(glob.glob(str(ROOT / 'tests' / 'fixtures' / 'tavily' / '*.json')))


def fixture_results():
    return [r for f in FIXTURES for r in json.loads(Path(f).read_text(encoding='utf-8'))['results']]


def result(url='https://www.bracu.ac.bd/some-new-notice-2026', title='Some new notice',
           content='', published_date=None):
    return {'url': url, 'title': title, 'content': content, 'published_date': published_date}


class Filter(unittest.TestCase):
    def test_fixtures_keep_exactly_the_posts(self):
        new, again, skipped = s.select(fixture_results(), set(), TODAY)
        self.assertEqual(sorted(a['url'] for a in new), [
            'https://www.bracu.ac.bd/brac-university-host-second-khondker-mahbubuddin-ahmed-'
            'memorial-international-moot-court-competition',
            'https://www.bracu.ac.bd/class-schedule-fall-2026',
            'https://www.bracu.ac.bd/first-year-advising-team-fyat-fall-2026',
            'https://www.bracu.ac.bd/llb-mid-term-schedule-fall-2026',
            'https://www.bracu.ac.bd/notice-application-18th-convocation-2026',
        ])
        self.assertEqual(skipped['other host'], 12)         # cse., ocsar., bigd., helpdesk
        self.assertEqual(skipped['file (privacy guard)'], 5)
        self.assertEqual(skipped['news (not an announcement)'], 1)
        self.assertEqual(skipped['not a top-level post'], 1)  # /event/durga-puja-...
        for a in new:                                         # what the store gets
            self.assertEqual((a['source'], a['full_text'], a['listed'], a['rag_priority']),
                             ('search', False, False, 3))
            self.assertEqual(a['note'], s.NOTE)

    def test_url_rules(self):
        cases = {
            'https://www.bracu.ac.bd/llb-mid-term-schedule-fall-2026': (True, ''),
            'https://www.bracu.ac.bd/llb-mid-term-schedule-fall-2026/': (True, ''),
            'https://www.bracu.ac.bd/event/durga-puja-university-closed': (False, 'not a top-level post'),
            'https://www.bracu.ac.bd/news/brac-university-holds-club-fair-fall-2026':
                (False, 'news (not an announcement)'),
            'https://cse.bracu.ac.bd/faculty_profile/539/x': (False, 'other host'),
            'https://bracu.ac.bd/llb-mid-term-schedule-fall-2026': (False, 'other host'),
            'https://www.bracu.ac.bd/sites/default/files/uploads/2026/10/03/x.pdf':
                (False, 'file (privacy guard)'),
            'https://www.bracu.ac.bd/sites/default/files/registrar/calendar.pdf':
                (False, 'file (privacy guard)'),
            'https://www.bracu.ac.bd/notice-x-y.docx': (False, 'file (privacy guard)'),
            'https://www.bracu.ac.bd/': (False, 'home page'),
            'https://www.bracu.ac.bd/search': (False, 'search or node page'),
            'https://www.bracu.ac.bd/search/node': (False, 'search or node page'),
            'https://www.bracu.ac.bd/admissions': (False, 'section page'),
            'https://www.bracu.ac.bd/news-archive': (False, 'section page'),
            'https://www.bracu.ac.bd/contact': (False, 'not a post slug'),
            'https://www.bracu.ac.bd/cel-news': (False, 'not a post slug'),
            'https://www.bracu.ac.bd/fablab-recruitment_circular': (True, ''),   # on the listing
            'https://www.bracu.ac.bd/academics/departments/cse': (False, 'not a top-level post'),
            'https://www.bracu.ac.bd/about/people/vc': (False, 'not a top-level post'),
            'https://www.bracu.ac.bd/node/12345': (False, 'search or node page'),
        }
        for url, want in cases.items():
            self.assertEqual(s.check_url(url), want, url)

    def test_query_strings_are_stripped_not_dropped(self):
        post = 'https://www.bracu.ac.bd/class-schedule-fall-2026'
        rs = [result(url=post + '?utm_source=x', content='Publish Date: Saturday, October 3, 2026 - 20:00'),
              result(url=post + '#top'),
              result(url=post + '/?page=1'),
              result(url='https://www.bracu.ac.bd/search/node?keys=notice'),
              result(url='https://www.bracu.ac.bd/node/123?x=1'),
              result(url='https://www.bracu.ac.bd/news-archive/announcements?page=2'),
              result(url='https://www.bracu.ac.bd/admissions?tab=1'),
              result(url='https://www.bracu.ac.bd/sites/default/files/x.pdf?download=1'),
              result(url='https://www.bracu.ac.bd/?q=node/12')]
        log = []
        new, _, skipped = s.select(rs, set(), TODAY, log)
        self.assertEqual([a['url'] for a in new], [post])              # one entry, clean URL
        self.assertEqual(new[0]['date_source'], 'snippet')             # the copy with a date won
        self.assertEqual(skipped['duplicate in this run'], 2)
        self.assertEqual(skipped['search or node page'], 2)
        self.assertEqual(skipped['not a top-level post'], 1)          # /news-archive/announcements
        self.assertEqual(skipped['section page'], 1)
        self.assertEqual(skipped['file (privacy guard)'], 1)
        self.assertEqual(skipped['home page'], 1)
        canon = [(a, b) for k, a, b in log if k == 'canonical']
        self.assertEqual(len(canon), len(rs))                          # every one was rewritten
        self.assertIn((post + '?utm_source=x', post), canon)
        self.assertFalse(any('?' in b or '#' in b for _, _, b in log))  # only clean URLs after

    def test_already_stored_and_duplicates(self):
        url = 'https://www.bracu.ac.bd/class-schedule-fall-2026'
        new, again, skipped = s.select(fixture_results(), {url}, TODAY)
        self.assertNotIn(url, [a['url'] for a in new])
        self.assertEqual(again, {url})
        # the duplicate class-schedule results collapse to the one with a date
        new, _, skipped = s.select(fixture_results(), set(), TODAY)
        cls = [a for a in new if a['url'] == url]
        self.assertEqual(len(cls), 1)
        self.assertEqual((cls[0]['published_at'], cls[0]['date_source']), (date(2026, 10, 3), 'snippet'))
        self.assertEqual(skipped['duplicate in this run'], 1)


class Dates(unittest.TestCase):
    def by_url(self):
        new, _, _ = s.select(fixture_results(), set(), TODAY)
        return {a['url'].rsplit('/', 1)[-1]: (a['published_at'], a['date_source']) for a in new}

    def test_fixture_dates(self):
        d = self.by_url()
        self.assertEqual(d['llb-mid-term-schedule-fall-2026'], (date(2026, 10, 27), 'snippet'))
        self.assertEqual(d['notice-application-18th-convocation-2026'], (date(2026, 9, 30), 'snippet'))
        self.assertEqual(d['first-year-advising-team-fyat-fall-2026'], (date(2026, 9, 28), 'snippet'))
        self.assertEqual(d['brac-university-host-second-khondker-mahbubuddin-ahmed-memorial-'
                           'international-moot-court-competition'], (TODAY, 'found'))

    def test_formats(self):
        self.assertEqual(publish_date('Publish Date: October 28th, 2026 - 9:15am'), date(2026, 10, 28))
        self.assertEqual(publish_date('x Publish Date: Tuesday, October 27, 2026 - 11:30 y'),
                         date(2026, 10, 27))
        self.assertEqual(s.snippet_date('Sep 30, 2026 — Notice : Application'), date(2026, 9, 30))
        self.assertEqual(s.snippet_date('Sept 3, 2026 — x'), date(2026, 9, 3))
        self.assertEqual(s.snippet_date('Date: Saturday, October 3, 2026 - 20:00'), date(2026, 10, 3))
        self.assertIsNone(s.snippet_date('Decision 5, 2026 was taken'))   # not a month
        self.assertIsNone(s.snippet_date('Classes of Fall 2026 begin'))
        self.assertEqual(s.search_date('2026-10-21T08:00:00Z'), date(2026, 10, 21))
        self.assertEqual(s.search_date('Wed, 21 Oct 2026 08:00:00 GMT'), date(2026, 10, 21))
        self.assertIsNone(s.search_date('not a date'))

    def test_date_order(self):
        r = result(content='Publish Date: Tuesday, October 27, 2026 - 11:30',
                   title='Notice of 20th October 2026', published_date='2026-10-26')
        self.assertEqual(s.result_date(r, TODAY), (date(2026, 10, 26), 'search'))
        r['published_date'] = None
        self.assertEqual(s.result_date(r, TODAY), (date(2026, 10, 27), 'snippet'))
        r['content'] = 'no date here'
        self.assertEqual(s.result_date(r, TODAY), (date(2026, 10, 20), 'title'))
        r['title'] = 'No date'
        self.assertEqual(s.result_date(r, TODAY), (TODAY, 'found'))

    def test_future_title_date_is_an_event_date(self):
        r = result(title='Admission Test for the Certificate Courses - to be held on 30th October 2026')
        self.assertEqual(s.result_date(r, TODAY), (TODAY, 'found'))
        self.assertEqual(s.result_date(r, date(2026, 11, 2)), (date(2026, 10, 30), 'title'))


class Cutoff(unittest.TestCase):
    def test_60_days(self):
        old = result(content='Publish Date: Sunday, August 30, 2026 - 10:00')     # exactly 60 days
        older = result(url='https://www.bracu.ac.bd/older-notice-of-2026',
                       content='Publish Date: Saturday, August 29, 2026 - 10:00')  # 61 days
        new, _, skipped = s.select([old, older], set(), TODAY)
        self.assertEqual([a['url'] for a in new], [old['url']])
        self.assertEqual(skipped[f'older than {s.CUTOFF_DAYS} days'], 1)

    def test_undated_result_with_an_old_year(self):
        r = result(url='https://www.bracu.ac.bd/final-result-emba-fall-2020', title='EMBA result 2020')
        new, _, skipped = s.select([r], set(), TODAY)
        self.assertEqual(new, [])
        self.assertEqual(skipped[f'older than {s.CUTOFF_DAYS} days'], 1)

    def test_undated_result_without_year_is_kept_as_found(self):
        new, _, _ = s.select([result(title='Some new notice')], set(), TODAY)
        self.assertEqual((new[0]['published_at'], new[0]['date_source']), (TODAY, 'found'))


class Privacy(unittest.TestCase):
    def test_personal_data_snippet_is_dropped(self):
        for content in ('SL No Student ID Name 1 1000063561 IRTIZA NAOWAR CSE',
                        'Contact the student at someone.name@g.bracu.ac.bd',
                        'Call 01712-345678 for details',
                        'Call +880 1712345678 for details'):
            new, _, _ = s.select([result(content=content)], set(), TODAY)
            self.assertEqual(new[0]['snippet'], '', content)
            self.assertTrue(new[0]['snippet_dropped'])
            self.assertEqual(new[0]['title'], 'Some new notice')

    def test_office_contacts_are_not_personal_data(self):
        for content in ('Tel: +88 09638464646. Email: info@bracu.ac.bd',
                        'contact academic.records@bracu.ac.bd',
                        'Publish Date: Tuesday, October 27, 2026 - 11:30'):
            self.assertFalse(s.has_personal_data(content), content)

    def test_redacted_fixture_files_never_reach_the_store(self):
        new, _, _ = s.select(fixture_results(), set(), TODAY)
        self.assertFalse(any('/sites/' in a['url'] for a in new))


class Cleaning(unittest.TestCase):
    def test_snippet_and_title(self):
        self.assertEqual(
            s.clean_snippet('LLB Mid Term schedule for Fall 2026 Publish Date: Tuesday, October 27, 2026 '
                            '- 11:30 Exam schedule PDF Contact Us. Tel: +88 09638464646. Email: info@bracu.ac.bd'),
            'LLB Mid Term schedule for Fall 2026 Publish Date: Tuesday, October 27, 2026 - 11:30 '
            'Exam schedule PDF')
        self.assertEqual(s.clean_snippet('Sep 28, 2026 — You will get the Group name ...Read more'),
                         'You will get the Group name')
        self.assertEqual(s.clean_title('Class Schedule for Fall 2026 - BRAC University'),
                         'Class Schedule for Fall 2026')
        self.assertEqual(s.clean_title('Durga Puja (Bijoya Dashami) - University Closed'),
                         'Durga Puja (Bijoya Dashami) - University Closed')
        self.assertEqual(s.clean_snippet('2 days ago — LLB Mid Term schedule for Fall 2026 Publish Date: '
                                         'Tuesday, October 27, 2026 - 11:30 Exam schedule PDF'),
                         'LLB Mid Term schedule for Fall 2026 Publish Date: Tuesday, October 27, 2026 '
                         '- 11:30 Exam schedule PDF')

    def test_address_footer_removed(self):
        # the LLB snippet as stored by the first real run
        self.assertEqual(
            s.clean_snippet('LLB Mid Term schedule for Fall 2026. Publish Date: Tuesday, October 27, 2026 '
                            '- 11:30. Exam schedule, PDF. Address: Kha 224 Pragati Sarani,'),
            'LLB Mid Term schedule for Fall 2026. Publish Date: Tuesday, October 27, 2026 - 11:30. '
            'Exam schedule, PDF')

    def test_truncated_title_completed_from_snippet(self):
        # as returned by the live dry run on 29 Oct 2026
        r = result(url='https://www.bracu.ac.bd/admission-test-certificate-courses-be-held-30th-october-2026',
                   title='Admission Test for the Certificate Courses- to be held on ...',
                   content='Admission Test for the Certificate Courses- to be held on 30th October 2026. '
                           'Publish Date: Wednesday, October 28, 2026 - 15:00 Contact Us.')
        new, _, _ = s.select([r], set(), TODAY)
        self.assertEqual(new[0]['title'],
                         'Admission Test for the Certificate Courses- to be held on 30th October 2026')
        self.assertEqual((new[0]['published_at'], new[0]['date_source']), (date(2026, 10, 28), 'snippet'))
        # a truncated title that the snippet doesn't start with stays as it is
        self.assertEqual(s.clean_title('BRAC University to Host Second Khondker Mahbubuddin Ahmed ...',
                                       'Interested teams are invited'),
                         'BRAC University to Host Second Khondker Mahbubuddin Ahmed ...')

    def test_skip_log_lists_urls(self):
        log = []
        s.select(fixture_results(), set(), TODAY, log)
        skips = [e for e in log if e[0] == 'skip']
        self.assertEqual(len(skips), 20)                # 12 hosts + 5 files + 1 news + 1 event + 1 duplicate
        self.assertIn(('skip', 'news (not an announcement)',
                       'https://www.bracu.ac.bd/news/brac-university-holds-club-fair-fall-2026'), skips)


class HardRules(unittest.TestCase):
    def test_errors_are_safe_for_public_logs(self):
        e = Exception('connection to server at "ep-cool-name-123456-pooler.ap-southeast-1.aws.neon.tech" '
                      '(13.228.1.2), port 5432 failed: FATAL:  password authentication failed for user '
                      '"neondb_owner"\nmore detail')
        msg = s.safe_error(e)
        for leak in ('ep-cool', 'neon.tech', '13.228', 'neondb_owner', 'more detail'):
            self.assertNotIn(leak, msg)
        self.assertIn('password authentication failed', msg)
        self.assertNotIn('tvly-abc123', s.safe_error(ValueError('bad key tvly-abc123XYZ')))
        self.assertNotIn('secret', s.safe_error(ValueError('postgresql://u:secret@h/db')))

    def test_search_endpoint_only(self):
        # no script may call Tavily extract/crawl/map or fetch pages itself
        # (fetch_model.py downloads the embedding model from Hugging Face, not BRACU)
        for path in (ROOT / 'scripts').glob('*.py'):
            code = re.sub(r'"""[\s\S]*?"""', '', path.read_text(encoding='utf-8'))   # ignore docstrings
            bad = ['.extract(', '.crawl(', '.map(', 'include_raw_content=True', 'include_answer=True']
            if path.name != 'fetch_model.py':
                bad += ['import requests', 'urllib.request', 'httpx', 'urlopen']
            for b in bad:
                self.assertNotIn(b, code, f'{path.name}: {b}')
        self.assertEqual(s.SEARCH['search_depth'], 'basic')
        self.assertIs(s.SEARCH['include_raw_content'], False)
        self.assertIs(s.SEARCH['include_answer'], False)
        self.assertLessEqual(len(s.QUERIES), 5)


if __name__ == '__main__':
    unittest.main()
