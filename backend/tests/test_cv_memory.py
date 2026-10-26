from types import SimpleNamespace as NS
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from cv_memory import extract_documents,retrieve_answer

TEXT='''Alex Example
alex@example.com | +92 300 1234567
https://github.com/alex-example
EDUCATION
Example University
B.E. Electrical Engineering
Expected graduation: 2027
TECHNICAL SKILLS
Python, FastAPI, PostgreSQL
SELECTED PROJECTS
Invoice processor: built a Python API to extract invoice fields.
WORK EXPERIENCE
Example Ltd — Backend Intern
Built and tested REST APIs.
'''

def doc(text=TEXT,ident=1):return NS(id=ident,kind='Resume',filename=f'cv{ident}.pdf',extracted_text=text)

def test_extracts_only_supported_fields_with_sources():
    memory=extract_documents([doc()])
    assert memory['profile']['name']=='Alex Example'
    assert memory['profile']['email']=='alex@example.com'
    assert memory['profile']['university']=='Example University'
    assert memory['profile']['graduation_year']=='2027'
    assert memory['profile']['degree_name']=='B.E. Electrical Engineering'
    assert memory['provenance']['email']['quote'] in TEXT
    assert 'availability' not in memory['profile'] and 'work_authorized' not in memory['profile']

def test_ambiguous_emails_are_not_guessed():
    memory=extract_documents([doc(),doc(TEXT.replace('alex@example.com','other@example.com'),2)])
    assert 'email' not in memory['profile']
    assert memory['conflicts'][0]['field']=='email'

def test_evidence_retrieval_does_not_guess_experience_years():
    profile={'cv_passages':extract_documents([doc()])['passages']}
    assert retrieve_answer(profile,'Describe your technical skills?')=='Python, FastAPI, PostgreSQL'
    assert 'Invoice processor' in retrieve_answer(profile,'Describe your projects')
    for q in ['How many years of Python experience?','Why do you want to work here?','What salary do you expect?','What is your visa status?']:
        assert retrieve_answer(profile,q)==''

def test_conflicting_sections_need_review_and_empty_resume_stays_empty():
    memory=extract_documents([doc(),doc(TEXT.replace('Python, FastAPI, PostgreSQL','Java, Spring'),2)])
    assert retrieve_answer({'cv_passages':memory['passages']},'List your skills')==''
    assert extract_documents([doc('')])['profile']=={}
