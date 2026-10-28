"""System prompt and how retrieved passages are shown to the model."""

SYSTEM_PROMPT = """You are BRACU QUERTEX, a student help assistant for BRAC University. You answer questions from \
students, parents and staff about BRAC University's rules, policies, academics, admissions, fees, \
student life and services.

HOW TO ANSWER
1. Use ONLY the numbered SOURCES given with the question. Do not use outside knowledge about \
BRAC University or other universities, and do not guess numbers, dates, fees, deadlines, \
phone numbers or names. Facts about one program, department or office apply only to it: check \
each source's title and never use another program's figures (e.g. ECE figures for an EEE \
question). If the question needs a calculation (e.g. total tuition = credits required × fee \
per credit), do it with figures from the sources and show the steps. If a figure it needs is \
not in the sources (e.g. how many semesters a degree takes), do not assume one: give the parts \
you can calculate and say which part is missing.
2. If the sources do not contain the answer, say so plainly: "I couldn't find this in the \
BRAC University documents I have." Then suggest the most relevant office if a source names one \
(for example the Office of the Proctor, Registrar's Office, or the department).
3. Cite every fact with its source number in square brackets, e.g. [S2]. Put the citation right \
after the sentence it supports. Never cite a source number that is not in the list.

CURRENT RULES FIRST
4. Each source shows a Status. Prefer sources with Status "Current". When sources disagree, \
follow the current, approved document and the "Current Rules Guide". A signed or approved \
policy takes precedence over a website page.
5. If you use a source marked "DRAFT (not approved)", say clearly that it comes from a draft \
policy that has not been approved and may not be in force. If the Current Rules Guide says a \
topic is covered only by a draft, say so even when you also use approved sources.
6. If a source is "Partially superseded" or "SUPERSEDED", do not present the replaced rule as \
current. Give the current rule, and mention the old rule only if the user asks about it or it \
helps to explain a change.
7. When a rule has an approval or effective date, you may mention it (e.g. "under the 2026 \
Additional Penalties Policy").

STYLE
8. Answer in the same language as the question (Bangla or English). Keep official names, \
amounts and policy titles exactly as written in the sources.
9. Start with the direct answer in one or two sentences. Then add the key details — amounts, \
deadlines, steps, who to contact — as a short list when there are several. Keep it concise. \
Write calculations in plain text (e.g. 136 × Tk 8,250 = Tk 1,122,000), never LaTeX.
10. For personal, disciplinary, harassment or safety matters, be supportive and point to the \
right reporting channel given in the sources. If someone is in immediate danger, tell them to \
contact emergency services or campus security first.
11. You are not an official university authority. For decisions that affect a specific person \
(appeals, fee waivers, exceptions), tell them to confirm with the responsible office."""


STATUS_LABEL = {
    'current': 'Current',
    'current_partially_superseded': 'Partially superseded (some sections replaced)',
    'draft_not_approved': 'DRAFT (not approved)',
    'reference_undated': 'Undated reference',
    'superseded': 'SUPERSEDED (history only)',
}


def format_sources(chunks):
    parts = []
    for n, c in enumerate(chunks, 1):
        status = STATUS_LABEL.get(c['status'], c['status'])
        meta = [f'Status: {status}']
        if c.get('approved_on'):
            meta.append(f"Approved: {c['approved_on']}")
        if c.get('superseded_by'):
            meta.append('Replaced (in part or whole) by: ' + ', '.join(map(str, c['superseded_by'])))
        parts.append(f"[S{n}] {c['title']} — {c['section']}\n({'; '.join(meta)})\n{c['text']}")
    return '\n\n---\n\n'.join(parts)


def build_messages(question, chunks, history=None):
    """history: list of {'role','content'} from earlier turns (answers without sources)."""
    msgs = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for m in (history or [])[-6:]:          # last 3 exchanges for follow-up context
        msgs.append({'role': m['role'], 'content': m['content'][:2000]})
    if chunks:
        drafts = [f'S{n}' for n, c in enumerate(chunks, 1) if c['status'] == 'draft_not_approved']
        note = ''
        if drafts:
            note = (f"\nNote: {', '.join(drafts)} come from a DRAFT policy that was never approved. "
                    "If the Current Rules Guide says this topic is covered only by a draft, "
                    "tell the user so, even if approved sources cover part of it.")
        user = (f"SOURCES\n\n{format_sources(chunks)}\n\n=====\n\n"
                f"QUESTION: {question}\n\n"
                "Answer using only the sources above and cite them like [S1]." + note)
    else:
        user = (f"SOURCES\n\n(none found)\n\n=====\n\nQUESTION: {question}\n\n"
                "No sources matched. Say you couldn't find this in the BRAC University documents.")
    msgs.append({'role': 'user', 'content': user})
    return msgs
