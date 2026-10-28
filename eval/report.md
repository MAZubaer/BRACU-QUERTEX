# Evaluation report

<!-- mode: synonyms -->
## Queries: synonyms

Search mode: **hybrid**, top-k = 5

- Retrieval hit@5: **49/49 (100%)**
- MRR: 0.88
  - bangla: 3/3 (100%)
  - conflict: 3/3 (100%)
  - draft: 2/2 (100%)
  - fact: 38/38 (100%)
  - history: 1/1 (100%)
  - multi_fact: 2/2 (100%)

| id | type | retrieval | answer | question | also searched |
|---|---|---|---|---|---|
| q01 | fact | hit (rank 1) |  | What is the fine for smoking on campus? | What is the fine for smoking on campus? penalty |
| q02 | fact | hit (rank 1) |  | What happens if I come to campus without my ID card? |  |
| q03 | fact | hit (rank 1) |  | What is the penalty for lending my ID card to someone? | What is the penalty for lending my ID card to someone? fine |
| q04 | fact | hit (rank 1) |  | Within how many days must a disciplinary fine be paid? | Within how many days must a disciplinary fine be paid? penalty |
| q05 | fact | hit (rank 1) |  | How long do I have to appeal a non-academic disciplinary decision, and to whom? |  |
| q06 | fact | hit (rank 1) |  | How long are disciplinary records kept on a student's file? |  |
| q07 | fact | hit (rank 3) |  | What are the penalty levels for non-academic misconduct? | What are the penalty levels for non-academic misconduct? fine |
| q08 | fact | hit (rank 1) |  | How do I report sexual harassment and within what time? |  |
| q09 | fact | hit (rank 1) |  | How many members does SHEBREC have and who chairs it? |  |
| q10 | fact | hit (rank 1) |  | How long should a SHEBREC harassment case take to complete? |  |
| q11 | fact | hit (rank 1) |  | Who handles a Level 1 academic misconduct case? |  |
| q12 | fact | hit (rank 1) |  | How many days do I have to respond to an academic misconduct allegation? |  |
| q13 | fact | hit (rank 1) |  | Can AI detection software alone prove I cheated? |  |
| q14 | fact | hit (rank 1) |  | If my course syllabus says nothing about AI, am I allowed to use ChatGPT? |  |
| q15 | fact | hit (rank 1) |  | Do I have to declare when I use AI tools in my assignment? |  |
| q16 | conflict | hit (rank 1) |  | Can I leave the exam hall in the first hour of the exam? | Can I leave the exam hall in the first hour of the exam? examination |
| q17 | fact | hit (rank 1) |  | How late can I arrive to an exam and still be allowed in? | How late can I arrive to an exam and still be allowed in? examination attendance |
| q18 | fact | hit (rank 1) |  | I missed a final exam because I was sick. How do I apply for a make-up exam? | I missed a final exam because I was sick. How do I apply for a make-up exam? examination medical center |
| q19 | fact | hit (rank 4) |  | What is the make-up examination fee? |  |
| q20 | fact | hit (rank 1) |  | How do I apply for re-checking of my exam script? | How do I apply for re-checking of my exam script? examination |
| q21 | conflict | hit (rank 1) |  | Who gives permission to use the BRAC University logo for a student event? |  |
| q22 | fact | hit (rank 1) |  | Can we start a political student club at BRAC University? |  |
| q23 | fact | hit (rank 1) |  | Can I talk to journalists on behalf of the university? |  |
| q24 | fact | hit (rank 1) |  | What clothes are not allowed under the dress code? |  |
| q25 | fact | hit (rank 1) |  | What is the tuition fee per credit for CSE? |  |
| q26 | fact | hit (rank 1) |  | How much is the admission fee for undergraduate students? | How much is the admission fee for undergraduate students? tuition |
| q27 | fact | hit (rank 2) |  | How much is the Residential Semester fee? | How much is the Residential Semester fee? tuition |
| q28 | fact | hit (rank 1) |  | How can I pay my tuition fees? |  |
| q29 | fact | hit (rank 2) |  | Can I pay my semester fees by cheque? |  |
| q30 | fact | hit (rank 1) |  | What is the university bus fare for one trip? | What is the university bus fare for one trip? transport |
| q31 | fact | hit (rank 1) |  | How do I pay the bus fare? | How do I pay the bus fare? transport |
| q32 | fact | hit (rank 1) |  | What attendance is required in the Residential Semester to sit the final exam? | What attendance is required in the Residential Semester to sit the final exam? examination |
| q33 | fact | hit (rank 1) |  | What time does the RS dormitory gate close at night? |  |
| q34 | fact | hit (rank 1) |  | When do Fall 2026 classes begin? |  |
| q35 | fact | hit (rank 1) |  | When are the Fall 2026 mid-term exams? | When are the Fall 2026 mid-term exams? examination |
| q36 | fact | hit (rank 2) |  | How many credits are needed for the B.Sc. in CSE? |  |
| q37 | fact | hit (rank 1) |  | What minimum GPA in SSC and HSC do I need for undergraduate admission? | What minimum GPA in SSC and HSC do I need for undergraduate admission? grading cgpa |
| q38 | draft | hit (rank 1) |  | What support does the university provide for students with disabilities? |  |
| q39 | draft | hit (rank 1) |  | Does BRAC University have a gender policy? |  |
| q40 | history | hit (rank 1) |  | What did the old 2010 sexual harassment code say about the complaint committee? |  |
| q41 | conflict | hit (rank 1) |  | Is smoking allowed in designated smoking areas on campus? |  |
| q42 | fact | hit (rank 6) |  | What email should I use to make a complaint to the Proctor's office? |  |
| q43 | fact | hit (rank 1) |  | What is the contact number of the medical center? |  |
| q44 | not_found | n/a |  | What is the WiFi password for the library? |  |
| q45 | not_found | n/a |  | Who won the inter-university football tournament in 2025? |  |
| q46 | bangla | hit (rank 1) |  | ক্যাম্পাসে ধূমপান করলে জরিমানা কত? |  |
| q47 | bangla | hit (rank 1) |  | পরীক্ষা শুরুর কত মিনিট পরে পরীক্ষার হলে ঢোকা যাবে না? |  |
| q48 | bangla | hit (rank 4) |  | সিএসই প্রোগ্রামে প্রতি ক্রেডিটের টিউশন ফি কত? |  |
| q49 | multi_fact | hit (rank 5) |  | What is the total expense I have to pay for graduation in 4 years in CSE? | What is the total expense I have to pay for graduation in 4 years in CSE? fee tuition degree credits |
| q50 | multi_fact | hit (rank 5) |  | How much money do I need for the whole EEE degree? | How much money do I need for the whole EEE degree? fee tuition |
| q51 | fact | hit (rank 1) |  | When is the next mid term exam for CSE? | When is the next mid term exam for CSE? examination |
<!-- /mode: synonyms -->

<!-- mode: synonyms + answers -->
## Queries: synonyms + answers

Search mode: **hybrid**, top-k = 5

- Retrieval hit@5: **1/1 (100%)**
- MRR: 1.00
  - fact: 1/1 (100%)
- Answers containing the expected facts: **1/1 (100%)**

| id | type | retrieval | answer | question | also searched |
|---|---|---|---|---|---|
| q51 | fact | hit (rank 1) | ok | When is the next mid term exam for CSE? | When is the next mid term exam for CSE? examination |
<!-- /mode: synonyms + answers -->

<!-- mode: planner -->
## Queries: planner

Search mode: **hybrid**, top-k = 5/7, planner used for 50/50 questions

- Retrieval hit@5/7: **48/48 (100%)**
- MRR: 0.91
  - bangla: 3/3 (100%)
  - conflict: 3/3 (100%)
  - draft: 2/2 (100%)
  - fact: 37/37 (100%)
  - history: 1/1 (100%)
  - multi_fact: 2/2 (100%)

| id | type | retrieval | answer | question | also searched |
|---|---|---|---|---|---|
| q01 | fact | hit (rank 1) |  | What is the fine for smoking on campus? | disciplinary penalty for smoking<br>campus smoking policy fine<br>What is the fine for smoking on campus? penalty |
| q02 | fact | hit (rank 1) |  | What happens if I come to campus without my ID card? | campus ID card policy<br>penalty for missing ID card<br>campus access rules |
| q03 | fact | hit (rank 1) |  | What is the penalty for lending my ID card to someone? | What is the disciplinary penalty for lending my ID card to someone?<br>disciplinary penalty for lending ID card<br>What is the disciplinary penalty for lending my ID card to someone? fine |
| q04 | fact | hit (rank 1) |  | Within how many days must a disciplinary fine be paid? | disciplinary penalty<br>Within how many days must a disciplinary fine be paid? penalty |
| q05 | fact | hit (rank 1) |  | How long do I have to appeal a non-academic disciplinary decision, and to whom? | non-academic disciplinary appeal period<br>non-academic disciplinary appeal authority |
| q06 | fact | hit (rank 1) |  | How long are disciplinary records kept on a student's file? | disciplinary record retention period<br>student file retention policy |
| q07 | fact | hit (rank 2) |  | What are the penalty levels for non-academic misconduct? | disciplinary penalty for non-academic misconduct<br>What are the penalty levels for non-academic misconduct? fine |
| q08 | fact | hit (rank 1) |  | How do I report sexual harassment and within what time? | sexual harassment reporting procedure<br>time limit for reporting sexual harassment |
| q09 | fact | hit (rank 1) |  | How many members does SHEBREC have and who chairs it? | SHEBREC membership<br>SHEBREC chairperson |
| q10 | fact | hit (rank 1) |  | How long should a SHEBREC harassment case take to complete? | SHEBREC harassment case duration<br>SHEBREC disciplinary procedure timeline<br>SHEBREC case processing time |
| q11 | fact | hit (rank 1) |  | Who handles a Level 1 academic misconduct case? | Level 1 academic misconduct<br>academic misconduct procedure |
| q12 | fact | hit (rank 1) |  | How many days do I have to respond to an academic misconduct allegation? | academic misconduct response deadline<br>academic misconduct allegation response period |
| q13 | fact | hit (rank 1) |  | Can AI detection software alone prove I cheated? | AI detection software policy<br>academic integrity policy<br>disciplinary penalty for plagiarism<br>evidence required for cheating |
| q14 | fact | hit (rank 1) |  | If my course syllabus says nothing about AI, am I allowed to use ChatGPT? | AI usage policy<br>academic integrity policy<br>disallowed AI tools |
| q15 | fact | hit (rank 1) |  | Do I have to declare when I use AI tools in my assignment? | AI usage policy<br>academic integrity policy<br>assignment submission guidelines |
| q16 | conflict | hit (rank 1) |  | Can I leave the exam hall in the first hour of the exam? | disciplinary penalty for leaving exam hall<br>make-up examination policy<br>Can I leave the exam hall in the first hour of the exam? examination |
| q17 | fact | hit (rank 1) |  | How late can I arrive to an exam and still be allowed in? | exam attendance policy<br>late arrival policy<br>make-up examination<br>How late can I arrive to an exam and still be allowed in? examination attendance |
| q18 | fact | hit (rank 1) |  | I missed a final exam because I was sick. How do I apply for a make-up exam? | make-up examination policy<br>exam deferral due to illness<br>medical certificate requirement<br>I missed a final exam because I was sick. How do I apply for a make-up exam? examination medical center |
| q19 | fact | hit (rank 4) |  | What is the make-up examination fee? | make-up examination fee |
| q20 | fact | hit (rank 1) |  | How do I apply for re-checking of my exam script? | script re-checking procedure<br>exam script re-checking policy<br>How do I apply for re-checking of my exam script? examination |
| q21 | conflict | hit (rank 1) |  | Who gives permission to use the BRAC University logo for a student event? | logo usage policy<br>BRAC University logo usage permission<br>student event logo permission<br>brand guidelines |
| q22 | fact | hit (rank 1) |  | Can we start a political student club at BRAC University? | student club policy<br>political club policy<br>student organization guidelines |
| q23 | fact | hit (rank 1) |  | Can I talk to journalists on behalf of the university? | public relations policy<br>media relations guidelines<br>official spokesperson policy<br>communication officer responsibilities |
| q24 | fact | hit (rank 1) |  | What clothes are not allowed under the dress code? | dress code policy |
| q25 | fact | hit (rank 1) |  | What is the tuition fee per credit for CSE? | CSE tuition fee per credit |
| q26 | fact | hit (rank 1) |  | How much is the admission fee for undergraduate students? | What is the admission fee for undergraduate students?<br>undergraduate admission fee |
| q27 | fact | hit (rank 1) |  | How much is the Residential Semester fee? | Residential Semester fee<br>How much is the Residential Semester fee? tuition |
| q28 | fact | hit (rank 1) |  | How can I pay my tuition fees? | tuition fee payment procedure<br>payment methods for tuition fee<br>tuition fee payment schedule |
| q29 | fact | hit (rank 2) |  | Can I pay my semester fees by cheque? | semester fee payment methods<br>cheque payment policy |
| q30 | fact | hit (rank 1) |  | What is the university bus fare for one trip? | university bus fare<br>What is the university bus fare for one trip? transport |
| q31 | fact | hit (rank 1) |  | How do I pay the bus fare? | bus fare payment procedure<br>bus fare payment options<br>How do I pay the bus fare? transport |
| q32 | fact | hit (rank 1) |  | What attendance is required in the Residential Semester to sit the final exam? | Residential Semester attendance requirement<br>final exam eligibility Residential Semester<br>What attendance is required in the Residential Semester to sit the final exam? examination |
| q33 | fact | hit (rank 1) |  | What time does the RS dormitory gate close at night? | RS dormitory gate closing time |
| q34 | fact | hit (rank 1) |  | When do Fall 2026 classes begin? | Fall 2026 semester start date<br>Fall 2026 academic calendar |
| q35 | fact | hit (rank 1) |  | When are the Fall 2026 mid-term exams? | Fall 2026 mid-term exam schedule<br>When are the Fall 2026 mid-term exams? examination |
| q36 | fact | hit (rank 2) |  | How many credits are needed for the B.Sc. in CSE? | total credits required for BSc CSE |
| q37 | fact | hit (rank 1) |  | What minimum GPA in SSC and HSC do I need for undergraduate admission? | minimum SSC GPA requirement for undergraduate admission<br>minimum HSC GPA requirement for undergraduate admission<br>What minimum GPA in SSC and HSC do I need for undergraduate admission? grading cgpa |
| q38 | draft | hit (rank 1) |  | What support does the university provide for students with disabilities? | disability support services<br>accommodation policy for students with disabilities<br>assistive technology services<br>reasonable accommodation procedures |
| q39 | draft | hit (rank 1) |  | Does BRAC University have a gender policy? | gender policy<br>gender equality policy<br>gender policy statement |
| q40 | history | hit (rank 1) |  | What did the old 2010 sexual harassment code say about the complaint committee? | 2010 sexual harassment code<br>complaint committee |
| q41 | conflict | hit (rank 1) |  | Is smoking allowed in designated smoking areas on campus? | campus smoking policy<br>designated smoking areas |
| q42 | fact | hit (rank 3) |  | What email should I use to make a complaint to the Proctor's office? | Proctor's office email address |
| q43 | fact | hit (rank 1) |  | What is the contact number of the medical center? | medical center contact number |
| q44 | not_found | n/a |  | What is the WiFi password for the library? | library WiFi password |
| q45 | not_found | n/a |  | Who won the inter-university football tournament in 2025? | inter-university football tournament 2025 winner |
| q46 | bangla | hit (rank 1) |  | ক্যাম্পাসে ধূমপান করলে জরিমানা কত? | What is the fine for smoking on campus?<br>disciplinary penalty for smoking on campus<br>What is the fine for smoking on campus? penalty |
| q47 | bangla | hit (rank 1) |  | পরীক্ষা শুরুর কত মিনিট পরে পরীক্ষার হলে ঢোকা যাবে না? | At what minute after the exam starts is it prohibited to enter the exam hall?<br>exam hall entry policy<br>exam hall entry time<br>At what minute after the exam starts is it prohibited to enter the exam hall? examination |
| q48 | bangla | hit (rank 1) |  | সিএসই প্রোগ্রামে প্রতি ক্রেডিটের টিউশন ফি কত? | What is the tuition fee per credit in the CSE program?<br>CSE tuition fee per credit |
| q49 | multi_fact | hit (rank 3) |  | What is the total expense I have to pay for graduation in 4 years in CSE? | CSE tuition fee per credit<br>total credits required for BSc CSE<br>CSE semester fee<br>residential semester fee<br>What is the total expense I have to pay for graduation in 4 years in CSE? fee tuition degree credits |
| q50 | multi_fact | hit (rank 4) |  | How much money do I need for the whole EEE degree? | EEE tuition fee per credit<br>total credits required for BSc EEE<br>EEE semester fee<br>residential semester fee<br>How much money do I need for the whole EEE degree? fee tuition |
<!-- /mode: planner -->

