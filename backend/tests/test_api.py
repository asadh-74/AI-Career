import os
import sys
import tempfile
from pathlib import Path

os.environ['DATABASE_URL']='sqlite:///'+tempfile.mktemp(suffix='.db')
os.environ['SESSION_SECRET']='unit-test-secret'
sys.path.insert(0,str(Path(__file__).parents[1]))
from passlib.hash import pbkdf2_sha256
os.environ['ADMIN_PASSWORD_HASH']=pbkdf2_sha256.hash('correct horse battery')
from fastapi.testclient import TestClient
import main

client=TestClient(main.app)

def headers():
    token=client.post('/api/auth/login',json={'password':'correct horse battery'}).json()['token']
    return {'Authorization':'Bearer '+token}

def test_private_api_and_login():
    assert client.get('/api/jobs').status_code==401
    assert client.post('/api/auth/login',json={'password':'wrong'}).status_code==401
    assert client.get('/api/jobs',headers=headers()).status_code==200

def test_source_scan_and_dedupe(monkeypatch):
    h=headers()
    assert client.post('/api/sources',headers=h,json={'company':'Acme','provider':'Other','slug':'acme'}).status_code==400
    r=client.post('/api/sources',headers=h,json={'company':'Acme','provider':'Greenhouse','slug':'acme'})
    assert r.status_code==200
    unsupported=client.post('/api/sources',headers=h,json={'company':'Unknown','website':'https://example.com/careers'})
    assert unsupported.status_code==400
    eu=client.post('/api/sources',headers=h,json={'company':'Example EU','website':'https://jobs.eu.lever.co/example'})
    assert eu.json()['provider']=='LeverEU'
    def fake_fetch(c,source):
        return [dict(company='Acme',title='Junior Python Engineer',location='Remote',description='Build APIs',url='https://boards.greenhouse.io/acme/jobs/42',apply_url='https://boards.greenhouse.io/acme/jobs/42',provider='Greenhouse')]
    monkeypatch.setattr(main,'fetch_board',fake_fetch)
    assert client.post('/api/scan',headers=h).json()['added']==1
    assert client.post('/api/scan',headers=h).json()['added']==0
    jobs=client.get('/api/jobs',headers=h).json()
    assert len(jobs)==1
    assert jobs[0]['company']=='Acme'

def test_board_adapters_parse_public_responses():
    class Reply:
        def __init__(self, data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class FakeClient:
        def get(self,url,**kwargs):
            if 'greenhouse' in url:return Reply({'jobs':[{'title':'Backend Developer','absolute_url':'https://boards.greenhouse.io/acme/jobs/42','location':{'name':'Remote'},'content':'<p>Python</p>'}]})
            assert 'api.eu.lever.co' in url
            return Reply([{'text':'AI Engineer','hostedUrl':'https://jobs.eu.lever.co/example/123','applyUrl':'https://jobs.eu.lever.co/example/123/apply','categories':{'location':'Remote'},'descriptionPlain':'Build AI agents'}])
    gh=main.Source(company='Acme',provider='Greenhouse',slug='acme')
    eu=main.Source(company='Example',provider='LeverEU',slug='example')
    assert main.fetch_board(FakeClient(),gh)[0]['description']==' Python '
    assert main.fetch_board(FakeClient(),eu)[0]['apply_url'].endswith('/apply')

def test_pdf_validation_and_application_queue(monkeypatch):
    h=headers()
    bad=client.post('/api/documents',headers=h,data={'kind':'Resume'},files={'file':('x.pdf',b'not a PDF','application/pdf')})
    assert bad.status_code==400
    with main.SessionLocal() as db:
        doc=main.Document(kind='Resume',filename='resume.pdf',pdf=b'%PDF-test',extracted_text='Python FastAPI projects and experience')
        db.add(doc);db.commit()
    monkeypatch.setattr(main,'ai_prepare',lambda job,doc:(82,'Relevant Python experience. Check eligibility.','Dear team, I built Python APIs.'))
    job_id=client.get('/api/jobs',headers=h).json()[0]['id']
    prepared=client.post('/api/applications/prepare',headers=h,json={'job_id':job_id,'document_kind':'Resume'})
    assert prepared.status_code==200
    a=prepared.json()
    assert a['status']=='ready_for_review'
    assert client.post(f"/api/applications/{a['id']}/submit",headers=h).json()['status']=='needs_human'
    assert client.post(f"/api/applications/{a['id']}/confirm",headers=h,json={'receipt':'Employer confirmation #42'}).json()['status']=='applied'
    assert client.get('/api/job-statuses',headers=h).json()[0]['status']=='submitted'

def test_manual_application_tracking_without_ai_or_document():
    h=headers()
    job_id=client.get('/api/jobs',headers=h).json()[0]['id']
    assert client.post('/api/job-statuses',headers=h,json={'job_id':job_id,'status':'submitted'}).json()['status']=='submitted'
    assert client.post('/api/job-statuses',headers=h,json={'job_id':job_id,'status':'not_submitted'}).json()['status']=='not_submitted'
    assert client.post('/api/job-statuses',headers=h,json={'job_id':job_id,'status':'fake'}).status_code==400
    assert client.post('/api/job-statuses',headers=h,json={'job_id':999999,'status':'submitted'}).status_code==404
    assert len(client.get('/api/job-statuses',headers=h).json())==1

def test_gemini_rejection_has_honest_local_fallback(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-placeholder')
    class Rejected:
        status_code=429
    monkeypatch.setattr(main.httpx,'post',lambda *args,**kwargs:Rejected())
    job=main.Job(title='Python Backend Engineer',company='Acme',description='Build Python FastAPI APIs',location='Remote')
    doc=main.Document(extracted_text='Python and FastAPI experience')
    score,rationale,draft=main.ai_prepare(job,doc)
    assert 0 <= score <= 85
    assert 'Local keyword estimate' in rationale and 'HTTP 429' in rationale
    assert 'not an AI assessment' in rationale
    assert 'Acme' in draft


def test_job_feed_exposes_large_corpus_and_role_contract_facets():
    from datetime import datetime, timezone
    h=headers()
    with main.SessionLocal() as db:
        db.add(main.Job(
            url='https://example.com/jobs/contract-python',
            apply_url='https://example.com/jobs/contract-python',
            company='Example',title='Python Backend Engineer - Contract',
            location='Remote',description='Build FastAPI APIs on a fixed price project.',
            provider='Example',found_at=datetime.now(timezone.utc),
        ))
        db.commit()
    response=client.get('/api/jobs?limit=5000',headers=h)
    assert response.status_code==200
    job=next(x for x in response.json() if x['company']=='Example')
    assert job['careerRelevant'] is True
    assert job['contractProject'] is True

def test_email_status_never_exposes_credentials(monkeypatch):
    h=headers()
    monkeypatch.setenv('ENABLE_EMAIL_MONITOR','true')
    monkeypatch.setenv('IMAP_USERNAME','candidate@example.com')
    monkeypatch.setenv('IMAP_PASSWORD','private-test-secret')
    response=client.get('/api/email/status',headers=h)
    assert response.status_code==200
    data=response.json()
    assert data['monitorEnabled'] is True
    assert data['imapConfigured'] is True
    assert 'private-test-secret' not in response.text
    assert data['mode']=='inbound_replies_only'


def test_prepare_returns_visible_tailored_resume(monkeypatch):
    h=headers()
    with main.SessionLocal() as db:
        job=main.Job(
            url='https://example.com/jobs/resume-preview',
            apply_url='https://example.com/jobs/resume-preview',
            company='Example',title='Python Backend Engineer',
            location='Remote',description='Build APIs with Python and FastAPI.',
            provider='Example',
        )
        doc=main.Document(
            kind='Resume',filename='resume.pdf',pdf=b'%PDF-test',
            extracted_text='''ASAD HUSSAIN
Islamabad, Pakistan | asadh1521@gmail.com
PROFESSIONAL SUMMARY
Software engineer with Python and FastAPI experience.
TECHNICAL SKILLS
Backend: Python, FastAPI, PostgreSQL
WORK EXPERIENCE
Backend Engineer | Example
• Built APIs with Python.
PROJECTS
Career Atlas | FastAPI
• Built a job platform.
EDUCATION
B.E. Electrical Engineering | NUST''',
        )
        db.add_all([job,doc]);db.commit();job_id=job.id
    monkeypatch.setattr(main,'ai_prepare',lambda job,doc:(82,'Relevant Python experience.','Dear Hiring Team.'))
    prepared=client.post('/api/applications/prepare',headers=h,json={'job_id':job_id,'document_kind':'Resume'})
    assert prepared.status_code==200
    variant_id=prepared.json()['resumeVariantId']
    preview=client.get(f'/api/v3/resume-variants/{variant_id}',headers=h)
    assert preview.status_code==200
    assert 'FastAPI' in preview.json()['extractedText']
    assert 'Built a job platform.' in preview.json()['extractedText']
    pdf=client.get(f'/api/v3/resume-variants/file/{variant_id}',headers=h)
    assert pdf.status_code==200 and pdf.headers['content-type']=='application/pdf'
