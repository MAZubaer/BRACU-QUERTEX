"""Offline tests for the "Is CST mandatory for CSE?" fixes (no database, no LLM):

- citation ranges and lists ([S1-S4], [S1, S2]) count every source they name
  (bracu/rag.py; public/index.html renders them with the same rules);
- the CST / GenEd synonym query (bracu/query.py);
- long passages are embedded as windows that each fit the model (bracu/embedder.py;
  only the tokenizer is used here, not the ONNX model);
- the prompt keeps the citation-format and requirement rules.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bracu import config                                                   # noqa: E402
from bracu.embedder import Embedder                                        # noqa: E402
from bracu.prompts import SYSTEM_PROMPT                                    # noqa: E402
from bracu.query import PLANNER_PROMPT, synonym_query                      # noqa: E402
from bracu.rag import citation_numbers, cited_numbers                      # noqa: E402


class Citations(unittest.TestCase):
    def test_single(self):
        self.assertEqual(cited_numbers('Fine is Tk 5,000 [S2]. Paid in 15 days [S5].'), [2, 5])

    def test_spaces_and_fullwidth_brackets(self):
        self.assertEqual(cited_numbers('a [ S1 ] b 【S3】 c'), [1, 3])

    def test_range(self):
        # the bug: "[S1-S4]" was not counted, so the panel showed "Sources (1)"
        answer = ('Students must take one CST [S5]. The CSE documents do not list CST as a '
                  'required component [S1-S4].')
        self.assertEqual(cited_numbers(answer), [1, 2, 3, 4, 5])

    def test_range_forms(self):
        self.assertEqual(cited_numbers('x [S1–S3]'), [1, 2, 3])      # en dash
        self.assertEqual(cited_numbers('x [S2-4]'), [2, 3, 4])        # second S left out
        self.assertEqual(cited_numbers('x 【S1-S2】'), [1, 2])

    def test_lists(self):
        self.assertEqual(cited_numbers('x [S1, S2]'), [1, 2])
        self.assertEqual(cited_numbers('x [S1,S3, S6-S7]'), [1, 3, 6, 7])
        self.assertEqual(cited_numbers('x [S2 and S4]'), [2, 4])

    def test_order_and_repeats(self):
        self.assertEqual(citation_numbers('S3, S1-S3'), [3, 1, 2])

    def test_absurd_range_keeps_its_ends(self):
        self.assertEqual(cited_numbers('x [S1-S99]'), [1, 99])
        self.assertEqual(cited_numbers('x [S5-S2]'), [2, 5])

    def test_not_citations(self):
        self.assertEqual(cited_numbers('[Section 2] [S] [5] SS1 [link](https://x.y/S1)'), [])


class CstSynonyms(unittest.TestCase):
    def test_cst_question_gets_gened_words(self):
        q = synonym_query('Is CST mandatory for CSE?')
        self.assertIsNotNone(q)
        for word in ('Communities', 'Seeking', 'Transformation', 'general', 'education', 'core'):
            self.assertIn(word, q)

    def test_gened_question(self):
        q = synonym_query('Which GenEd courses are mandatory for CSE?')
        self.assertIn('CST', q)
        self.assertIn('university', q)

    def test_unrelated_question_unchanged(self):
        self.assertIsNone(synonym_query('Who chairs SHEBREC?'))

    def test_planner_asks_for_degree_plan_and_gened(self):
        self.assertIn('degree plan', PLANNER_PROMPT)
        self.assertIn('General Education', PLANNER_PROMPT)


class PromptRules(unittest.TestCase):
    def test_one_number_per_bracket(self):
        self.assertIn('never ranges or lists such as [S1-S3]', SYSTEM_PROMPT)

    def test_requirement_rules(self):
        self.assertIn('Absence from the sources proves nothing', SYSTEM_PROMPT)
        self.assertIn('part of every undergraduate program', SYSTEM_PROMPT)
        self.assertIn('not its opposite', SYSTEM_PROMPT)


class PassageWindows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tokenizers import Tokenizer
        tok_file = config.MODEL_DIR / 'tokenizer.json'
        if not tok_file.exists():
            raise unittest.SkipTest('model/tokenizer.json missing')
        cls.emb = object.__new__(Embedder)             # windows() needs only the tokenizer
        cls.emb._counter = Tokenizer.from_file(str(tok_file))
        cls.emb.max_length = 512

    def test_short_passage_is_one_piece(self):
        text = 'Document: Fees | Section: Tuition | Status: Current\n\nTk 8,250 per credit.'
        self.assertEqual(self.emb.windows(text), [text])

    def test_long_table_is_split_with_header_in_every_window(self):
        header = 'Document: Degree Plan CSE | Section: University Core | Status: Current'
        rows = [f'| | CST{300 + i} | Course name number {i} | 3.0 | | | |' for i in range(120)]
        text = header + '\n\n' + '\n'.join(rows)
        wins = self.emb.windows(text)
        self.assertGreater(len(wins), 1)
        for w in wins:
            self.assertTrue(w.startswith(header + '\n\n'))
            self.assertLessEqual(self.emb._tokens('passage: ' + w), 512 - 16)
        # every row is embedded somewhere, the last one included (the CST row in the bug)
        body = '\n'.join(w[len(header) + 2:] for w in wins)
        self.assertEqual(body.split('\n'), rows)

    def test_very_long_line_is_split_between_words(self):
        text = 'word ' * 2000
        wins = self.emb.windows(text.strip())
        self.assertGreater(len(wins), 1)
        self.assertEqual(' '.join(wins).split(), text.split())


if __name__ == '__main__':
    unittest.main()
