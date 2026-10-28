"""Tokenizer + light stemmer for BM25.

Used twice: by scripts/ingest.py to build the BM25 postings stored in
Postgres, and by the retriever to tokenize questions. Both sides MUST use
this same function, so never change it without running ingest.py again.
(Copied unchanged from the original retriever.py.)
"""
import re

TOKEN = re.compile(r'[0-9a-zঀ-৿]+')
STOP = set('''a an the of to in on for and or is are was were be been by with at from as
that this these those it its what which who whom how do does did can could i my me we
our you your any there their than then so if not no will would should shall may must
about into over under per via also has have had'''.split())

SAME_WORD = {'complaint': 'complain', 'used': 'use', 'using': 'use'}  # the stemmer can't join these
VOWEL = re.compile(r'[aeiouy]')
EMAIL = re.compile(r'\S+@\S+\.\w+')


def _stem(t):
    """fines/fined/fining -> fin, payments/paying -> pay, suspended -> suspend.
    Bangla words and numbers are kept as they are."""
    if not t.isascii() or not t.isalpha() or len(t) <= 3:
        return SAME_WORD.get(t, t)
    # 1. plurals
    if t.endswith('ies') and len(t) > 4:
        t = t[:-3] + 'y'
    elif t.endswith(('sses', 'shes', 'ches', 'xes', 'zes')):
        t = t[:-2]
    elif t.endswith('s') and not t.endswith(('ss', 'us', 'is')):
        t = t[:-1]
    # 2. -ing / -ed, only if a real stem (3+ letters with a vowel) is left
    if t.endswith('ied') and len(t) > 4:
        t = t[:-3] + 'y'                        # applied -> apply
    for suf in ('ing', 'ed'):
        if t.endswith(suf) and len(t) - len(suf) >= 3 and VOWEL.search(t[:-len(suf)]):
            t = t[:-len(suf)]
            if len(t) > 3 and t[-1] == t[-2] and t[-1] not in 'lsz':
                t = t[:-1]                      # submitted -> submitt -> submit
            break
    # 3. -ment (payment -> pay, punishment -> punish)
    if t.endswith('ment') and len(t) - 4 >= 3:
        t = t[:-4]
    # 4. final silent e (fine -> fin, so it matches fined/fining); keep "ee"
    if t.endswith('e') and not t.endswith('ee') and len(t) > 3:
        t = t[:-1]
    return SAME_WORD.get(t, t)


def tokenize(text):
    toks = [_stem(t) for t in TOKEN.findall(text.lower()) if t not in STOP]
    # passages give addresses, not the word "email": let "what email ..." match them
    return toks + ['email'] * len(EMAIL.findall(text))
