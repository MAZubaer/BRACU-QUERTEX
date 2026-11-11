"""Offline tests for scripts/import_class_schedule.py (rows copied from the Fall 2026
reference CSV; no PDF, no database)."""
import sys
import unittest
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

import import_class_schedule as imp            # noqa: E402

HEAD = ['SL', 'Course', 'Faculty', 'Section', 'Day', 'Start Time', 'End Time', 'Room']
SL1 = ['1', 'ACT201', 'SHO', '01', 'SUNDAY', '02:00 PM', '03:20 PM', 'SUN 2:00PM: 07A-08C; TUE 2:00PM: 09G-31T']
SL2 = ['2', 'ACT201', 'SHO', '01', 'TUESDAY', '02:00 PM', '03:20 PM', 'SUN 2:00PM: 07A-08C; TUE 2:00PM: 09G-31T']
SL3 = ['3', 'ACT201', '', '01 (ALL)', 'SUNDAY', '02:00 PM', '03:20 PM', 'SUN 2:00PM: 07A-08C; TUE 2:00PM: 09G-31T']
# page tables as pdfplumber returns them: title row, header, the repeated SL 1 row
PAGE1 = [[['BRAC University\nClass Schedule for FALL 2026'] + [None] * 7, HEAD, SL1, SL2]]
PAGE2 = [[HEAD, SL1, SL3]]


def norm(*cells):
    return imp.normalise(*cells)


class Pages(unittest.TestCase):
    def test_header_and_repeated_sl1_dropped(self):
        raw = imp.raw_rows_from_tables(PAGE1 + PAGE2)
        self.assertEqual(sorted(raw), [1, 2, 3])
        rows = imp.build_rows(raw)
        self.assertEqual([r['sl'] for r in rows], [1, 2, 3])

    def test_same_sl_with_other_cells_stops(self):
        other = [[HEAD, ['1', 'ACT201', 'SHO', '02', 'SUNDAY', '02:00 PM', '03:20 PM', '07A-08C']]]
        with self.assertRaises(imp.BadRow):
            imp.raw_rows_from_tables(PAGE1 + other)

    def test_missing_sl_stops(self):
        with self.assertRaises(imp.BadRow) as e:
            imp.build_rows({1: SL1, 3: SL3})
        self.assertIn('missing [2]', str(e.exception))


class Rows(unittest.TestCase):
    def test_day_specific_room(self):
        self.assertEqual(norm(*SL1)['room'], '07A-08C')
        self.assertEqual(norm(*SL2)['room'], '09G-31T')
        self.assertTrue(norm(*SL2)['day_specific_room'])
        # different times on different days (ACT202 section 06)
        r = norm('50', 'ACT202', 'SPC', '06', 'TUESDAY', '11:00 AM', '12:20 PM',
                 'SUN 12:30PM: 09G-31T; TUE 11:00AM: 07A-07C')
        self.assertEqual((r['room'], r['start_time'], r['end_time']), ('07A-07C', time(11, 0), time(12, 20)))

    def test_plain_row(self):
        r = norm('886', 'CSE220', 'AJA', '05', 'SUNDAY', '02:00 PM', '03:20 PM', '09H-34C')
        self.assertEqual({k: r[k] for k in ('course', 'course_norm', 'is_lab', 'base_course', 'section_number',
                                            'section_tag', 'day', 'day_order', 'start_time', 'end_time',
                                            'room', 'faculty', 'day_specific_room')},
                         {'course': 'CSE220', 'course_norm': 'CSE 220', 'is_lab': False, 'base_course': 'CSE 220',
                          'section_number': '05', 'section_tag': None, 'day': 'Sunday', 'day_order': 2,
                          'start_time': time(14, 0), 'end_time': time(15, 20), 'room': '09H-34C',
                          'faculty': 'AJA', 'day_specific_room': False})

    def test_tagged_sections(self):
        for raw, want in (('01 (ALL)', ('01', 'ALL')), ('01 (BBA)', ('01', 'BBA')), ('11-LLB', ('11', 'LLB')),
                          ('05-PG', ('05', 'PG')), ('10- BDM', ('10', 'BDM')), ('10 (BDM)', ('10', 'BDM')),
                          ('01 (online)', ('01', 'online')), ('8', ('08', None)), ('05', ('05', None))):
            self.assertEqual(imp.parse_section(raw), want, raw)
        r = norm(*SL3)
        self.assertEqual((r['section_number'], r['section_tag'], r['faculty']), ('01', 'ALL', None))
        r = norm('2206', 'ECE205L', '', '8', 'TUESDAY', '02:00 PM', '04:50 PM', '11H-45L')
        self.assertEqual(r['section_number'], '08')

    def test_lab_codes(self):
        lab = norm('920', 'CSE220L', '', '05', 'MONDAY', '02:00 PM', '04:50 PM', '09B-10L')
        self.assertEqual((lab['course_norm'], lab['is_lab'], lab['base_course'], lab['end_time']),
                         ('CSE 220L', True, 'CSE 220', time(16, 50)))
        b = norm('2018', 'CSE490BL', '', '01', 'MONDAY', '11:00 AM', '01:50 PM', 'AS2-21L')
        self.assertEqual((b['course_norm'], b['is_lab'], b['base_course']), ('CSE 490BL', True, 'CSE 490B'))
        s = norm('4568', 'STAT101L', '', '01', 'SUNDAY', '11:00 AM', '12:20 PM', '11B-20L')
        self.assertEqual((s['course_norm'], s['is_lab'], s['base_course']), ('STAT 101L', True, 'STAT 101'))
        # TESL ends in L before the number: not a lab
        self.assertEqual(imp.parse_course('TESL501'), {'course': 'TESL501', 'course_norm': 'TESL 501',
                                                       'is_lab': False, 'base_course': 'TESL 501'})
        self.assertFalse(imp.parse_course('CSE490B')['is_lab'])

    def test_friday_and_room_as_is(self):
        r = norm('672', 'BUS702', 'KRM', '01', 'FRIDAY', '06:30 PM', '09:30 PM', '08A-01C')
        self.assertEqual((r['day'], r['day_order'], r['start_time']), ('Friday', 7, time(18, 30)))
        r = norm('4589', 'TSL508', '', '01', 'THURSDAY', '05:00 PM', '07:50 PM', '5F-08')
        self.assertEqual(r['room'], '5F-08')

    def test_lab_without_theory_section(self):
        rows = [norm('1', 'EEE101', '', '08', 'SUNDAY', '08:00 AM', '09:20 AM', '1C'),
                norm('2', 'EEE101L', '', '08', 'MONDAY', '08:00 AM', '10:50 AM', '1L'),
                norm('3', 'EEE101L', '', '09', 'MONDAY', '11:00 AM', '01:50 PM', '1L')]
        self.assertEqual(imp.lab_mismatches(rows), [('EEE101L', '09')])

    def test_bad_cells_stop(self):
        for cells in (('1', 'CSE 22', '', '01', 'SUNDAY', '08:00 AM', '09:20 AM', 'x'),
                      ('1', 'CSE220', '', '01', 'SUNDAY', '8 AM', '09:20 AM', 'x'),
                      ('1', 'CSE220', '', '01', 'FUNDAY', '08:00 AM', '09:20 AM', 'x'),
                      ('1', 'CSE220', '', '01', 'MONDAY', '08:00 AM', '09:20 AM', 'SUN 8:00AM: 1C; TUE 8:00AM: 2C')):
            with self.assertRaises(imp.BadRow, msg=cells):
                norm(*cells)


if __name__ == '__main__':
    unittest.main()
