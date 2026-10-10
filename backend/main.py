"""Career Atlas API: one-user job discovery, document matching and application queue."""
import io
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import FastAPI, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.hash import pbkdf2_sha256

DB_URL = os.getenv('DATABASE_URL', 'sqlite:///./career.db').replace('postgres://', 'postgresql://', 1)
if DB_URL.startswith('postgresql://'): DB_URL = DB_URL.replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_engine(DB_URL, connect_args={'check_same_thread': False} if DB_URL.startswith('sqlite') else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
SECRET = os.getenv('SESSION_SECRET', '')
serializer = URLSafeTimedSerializer(SECRET or 'development-only')

class Base(DeclarativeBase): pass
class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(20))
    slug: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    apply_url: Mapped[str] = mapped_column(String(1000))
    company: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str] = mapped_column(String(200), default='')
    description: Mapped[str] = mapped_column(Text, default='')
    provider: Mapped[str] = mapped_column(String(30))
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    extracted_text: Mapped[str] = mapped_column(Text)
class Application(Base):
    __tablename__ = 'applications'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), unique=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'))
    score: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(Text)
    draft: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default='ready_for_review')
    receipt: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class JobStatus(Base):
    __tablename__ = 'job_statuses'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), unique=True)
    status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# Import additive v3 models after Base/core models exist, then create all tables.
from v3_models import (
    ApplicationArtifact, ApplicationEvent, AutomationSetting, EmployerMessage,
    FollowUpDraft, InterviewPrep, JobMetric, QuestionMemory, ResearchResult,
    ResumeVariant, ReviewAnswer, extract_review_questions, get_setting,
    quality_threshold, record_event, set_setting,
)
Base.metadata.create_all(engine)

app = FastAPI(title='Career Atlas API')
origins = [x.strip() for x in os.getenv('CORS_ORIGINS', '').split(',') if x.strip()]
if origins: app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=['GET','POST','DELETE'], allow_headers=['Authorization','Content-Type'])

class Login(BaseModel): password: str
class SourceIn(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    provider: str = ''
    slug: str = ''
    website: str = ''
class Profile(BaseModel):
    name: str = ''
    email: str = ''
    phone: str = ''
class PrepareIn(BaseModel): job_id: int; document_kind: str = 'Resume'
class ConfirmIn(BaseModel): receipt: str = Field(default='', max_length=1000)
class LocateApplicationIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
class TrackIn(BaseModel):
    job_id: int
    status: str
    note: str = Field(default='', max_length=1000)

def auth(authorization: str | None = Header(default=None)):
    if not SECRET or not authorization or not authorization.startswith('Bearer '): raise HTTPException(401, 'Sign in required')
    try:
        if serializer.loads(authorization[7:], max_age=7*86400) != 'owner': raise ValueError()
    except (BadSignature, SignatureExpired, ValueError): raise HTTPException(401, 'Session expired')

def db():
    with SessionLocal() as session: yield session

def source_out(s): return dict(id=s.id, company=s.company, provider=s.provider, slug=s.slug, active=s.active)
def job_out(j): return dict(id=j.id, company=j.company, title=j.title, location=j.location, url=j.url, applyUrl=j.apply_url, provider=j.provider, foundAt=j.found_at.isoformat())
def app_out(a,job=None):
    out=dict(id=a.id, jobId=a.job_id, documentId=a.document_id, score=a.score, rationale=a.rationale, draft=a.draft, status=a.status, receipt=a.receipt)
    if job is not None:
        out.update(company=job.company,title=job.title,location=job.location,provider=job.provider,url=job.url,applyUrl=job.apply_url)
    return out
def status_out(s): return dict(jobId=s.job_id, status=s.status, note=s.note, updatedAt=s.updated_at.isoformat())

@app.post('/api/auth/login')
def login(body: Login):
    password_hash = os.getenv('ADMIN_PASSWORD_HASH', '')
    if not SECRET or not password_hash: raise HTTPException(503, 'Set SESSION_SECRET and ADMIN_PASSWORD_HASH')
    if not pbkdf2_sha256.verify(body.password, password_hash): raise HTTPException(401, 'Wrong password')
    return {'token': serializer.dumps('owner')}
@app.get('/api/health')
def health(): return {'status':'ok'}
@app.get('/api/sources', dependencies=[Depends(auth)])
def sources(session: Session=Depends(db)): return [source_out(x) for x in session.scalars(select(Source).order_by(Source.company)).all()]
@app.post('/api/sources', dependencies=[Depends(auth)])
def add_source(body: SourceIn, session: Session=Depends(db)):
    provider, slug = body.provider, body.slug
    if body.website:
        parsed=urlparse(body.website)
        host=(parsed.hostname or '').lower()
        if parsed.scheme!='https': raise HTTPException(400,'Use an HTTPS company careers URL')
        if host in ('boards.greenhouse.io','job-boards.greenhouse.io'): provider='Greenhouse'
        elif host == 'jobs.lever.co': provider='Lever'
        elif host == 'jobs.eu.lever.co': provider='LeverEU'
        elif host == 'jobs.ashbyhq.com': provider='Ashby'
        elif host == 'careers.smartrecruiters.com': provider='SmartRecruiters'
        else: raise HTTPException(400,'Supported public board feeds: Greenhouse, Lever, Ashby, SmartRecruiters')
        slug=parsed.path.strip('/').split('/')[0]
    if provider not in ('Greenhouse','Lever','LeverEU','Ashby','SmartRecruiters'): raise HTTPException(400, 'Supported company feeds: Greenhouse, Lever, Ashby, SmartRecruiters')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{2,100}', slug): raise HTTPException(400, 'Invalid board slug')
    existing=session.scalar(select(Source).where(Source.provider==provider,Source.slug==slug))
    if existing: return source_out(existing)
    item=Source(company=body.company.strip(), provider=provider, slug=slug)
    session.add(item);session.commit();session.refresh(item);return source_out(item)
@app.delete('/api/sources/{source_id}', dependencies=[Depends(auth)])
def remove_source(source_id:int,session:Session=Depends(db)):
    item=session.get(Source,source_id)
    if not item: raise HTTPException(404,'Source not found')
    session.delete(item);session.commit();return {'ok':True}


def fetch_board(client, source):
    if source.provider=='Greenhouse':
        r=client.get(f'https://boards-api.greenhouse.io/v1/boards/{source.slug}/jobs',params={'content':'true'});r.raise_for_status()
        return [dict(company=source.company,title=x.get('title',''),location=(x.get('location') or {}).get('name',''),description=re.sub('<[^>]+>',' ',x.get('content') or '')[:8000],url=x.get('absolute_url',''),apply_url=x.get('absolute_url',''),provider='Greenhouse') for x in r.json().get('jobs',[])]
    if source.provider in ('Lever','LeverEU'):
        host='api.eu.lever.co' if source.provider=='LeverEU' else 'api.lever.co'
        r=client.get(f'https://{host}/v0/postings/{source.slug}',params={'mode':'json'});r.raise_for_status()
        return [dict(company=source.company,title=x.get('text',''),location=(x.get('categories') or {}).get('location',''),description=(x.get('descriptionPlain') or '')[:8000],url=x.get('hostedUrl',''),apply_url=x.get('applyUrl') or x.get('hostedUrl',''),provider=source.provider) for x in r.json()]
    if source.provider=='Ashby':
        r=client.get(f'https://api.ashbyhq.com/posting-api/job-board/{source.slug}',params={'includeCompensation':'true'});r.raise_for_status()
        rows=[]
        for x in r.json().get('jobs',[]):
            url=x.get('jobUrl') or x.get('url') or x.get('applyUrl') or ''
            rows.append(dict(company=source.company,title=x.get('title',''),location=x.get('location') or '',description=re.sub('<[^>]+>',' ',x.get('descriptionHtml') or x.get('descriptionPlain') or x.get('description') or '')[:8000],url=url,apply_url=x.get('applyUrl') or url,provider='Ashby'))
        return rows
    if source.provider=='SmartRecruiters':
        r=client.get(f'https://api.smartrecruiters.com/v1/companies/{source.slug}/postings',params={'limit':100,'locationType':'ANY'});r.raise_for_status()
        rows=[]
        for x in r.json().get('content',[]):
            loc=x.get('location') or {}
            location=', '.join(str(v) for v in (loc.get('city'),loc.get('region'),loc.get('country')) if v) if isinstance(loc,dict) else str(loc)
            url=x.get('applyUrl') or x.get('jobAdUrl') or x.get('ref') or ''
            desc=x.get('jobAd',{}).get('sections',{}).get('jobDescription',{}).get('text','') if isinstance(x.get('jobAd'),dict) else ''
            rows.append(dict(company=source.company,title=x.get('name') or x.get('title') or '',location=location,description=re.sub('<[^>]+>',' ',desc)[:8000],url=url,apply_url=x.get('applyUrl') or url,provider='SmartRecruiters'))
        return rows
    return []

def scan(session, client):
    added=0; errors=[]
    for source in session.scalars(select(Source).where(Source.active==True)).all():
        try:
            for row in fetch_board(client,source):
                url=urlparse(row['url'])
                if url.scheme!='https' or not url.hostname: continue
                if session.scalar(select(Job.id).where(Job.url==row['url'])): continue
                session.add(Job(**row));added+=1
            session.commit()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            session.rollback();errors.append(f'{source.company}: {type(exc).__name__}')
    return {'added':added,'errors':errors}
@app.post('/api/scan', dependencies=[Depends(auth)])
def scan_now(session:Session=Depends(db)):
    from public_sources import scan_public_sources
    with httpx.Client(timeout=30,follow_redirects=True) as client:
        core=scan(session,client)
        public=scan_public_sources(session,client)
    return {
        'added':core.get('added',0)+public.get('added',0),
        'errors':core.get('errors',[])+public.get('errors',[]),
        'publicSources':public.get('details',{}),
    }
@app.post('/api/scan/scheduled')
def scan_scheduled(x_scan_secret: str | None=Header(default=None),session:Session=Depends(db)):
    if not os.getenv('SCAN_SECRET') or not secrets.compare_digest(x_scan_secret or '',os.getenv('SCAN_SECRET','')):raise HTTPException(401,'Invalid scan secret')
    with httpx.Client(timeout=20) as client:return scan(session,client)
@app.get('/api/jobs', dependencies=[Depends(auth)])
def jobs(session:Session=Depends(db),limit:int=100):
    return [job_out(x) for x in session.scalars(select(Job).order_by(Job.found_at.desc()).limit(min(max(limit,1),300))).all()]

@app.get('/api/job-statuses', dependencies=[Depends(auth)])
def job_statuses(session:Session=Depends(db)):
    return [status_out(x) for x in session.scalars(select(JobStatus).order_by(JobStatus.updated_at.desc())).all()]

@app.post('/api/job-statuses', dependencies=[Depends(auth)])
def track_job(body:TrackIn,session:Session=Depends(db)):
    if body.status not in ('submitted','not_submitted'):raise HTTPException(400,'Choose submitted or not_submitted')
    job=session.get(Job,body.job_id)
    if not job:raise HTTPException(404,'Job not found')
    item=session.scalar(select(JobStatus).where(JobStatus.job_id==body.job_id))
    if not item:item=JobStatus(job_id=body.job_id,status=body.status);session.add(item)
    note=(body.note or '').strip()
    if body.status=='submitted' and not note:
        note='Manual submission confirmed by user'
    item.status=body.status;item.note=note;item.updated_at=datetime.now(timezone.utc)

    application=session.scalar(select(Application).where(Application.job_id==body.job_id))
    if body.status=='submitted':
        if not application:
            doc=session.scalar(select(Document).where(Document.kind=='Resume')) or session.scalar(select(Document).where(Document.kind=='CV'))
            if not doc: raise HTTPException(400,'Upload a Resume or CV first')
            application=Application(
                job_id=job.id,document_id=doc.id,score=0,
                rationale='Manually submitted outside Career Atlas.',
                draft='',status='applied',receipt=note,
            )
            session.add(application);session.flush()
        else:
            application.status='applied'
            application.receipt=note
        record_event(session,job.id,'submitted','manual_confirmation',note,application_id=application.id)
    elif application and application.status=='applied':
        application.status='ready_for_review'
        application.receipt=note or 'Manual submitted status was undone by user.'
        record_event(session,job.id,'prepared','manual_submission_undone',application.receipt,application_id=application.id)

    session.commit();session.refresh(item);return status_out(item)

@app.post('/api/documents', dependencies=[Depends(auth)])
async def upload_document(kind:str=Form(...),file:UploadFile=File(...),session:Session=Depends(db)):
    if kind not in ('CV','Resume'):raise HTTPException(400,'Use CV or Resume')
    raw=await file.read(3_000_001)
    if len(raw)>3_000_000 or not raw.startswith(b'%PDF-'):raise HTTPException(400,'PDF only, up to 3 MB')
    try:text='\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(raw)).pages)[:25000]
    except Exception:raise HTTPException(400,'Unreadable PDF')
    if len(text.strip())<80:raise HTTPException(400,'Scanned PDF has no selectable text; use an OCR PDF')
    doc=session.scalar(select(Document).where(Document.kind==kind))
    if not doc:doc=Document(kind=kind,filename='',pdf=b'',extracted_text='');session.add(doc)
    doc.filename=Path(file.filename or f'{kind}.pdf').name[:200];doc.pdf=raw;doc.extracted_text=text
    session.commit();return {'kind':kind,'filename':doc.filename,'characters':len(text)}
@app.get('/api/documents', dependencies=[Depends(auth)])
def documents(session:Session=Depends(db)):return [{'kind':x.kind,'filename':x.filename,'characters':len(x.extracted_text)} for x in session.scalars(select(Document)).all()]

def ai_prepare(job,doc):
    import json
    key=os.getenv('GEMINI_API_KEY','')
    def local_match(reason):
        hay=(job.title+' '+job.description).lower()
        cv=doc.extracted_text.lower()
        role_groups={
            'backend':('backend','python','fastapi','api engineer','software engineer'),
            'agentic_ai':('ai engineer','agentic','langgraph','langchain','rag','llm','automation'),
            'fullstack':('full stack','full-stack','web developer','software developer'),
            'ml':('machine learning','ml engineer','applied ai','computer vision'),
            'automation':('automation','integrations','workflow','n8n','zapier'),
        }
        role_hits=sum(1 for terms_ in role_groups.values() if any(x in job.title.lower() for x in terms_))
        core=('python','fastapi','flask','postgresql','sql','sqlalchemy','pydantic','docker','linux','git','ci/cd',
              'langgraph','langchain','crewai','rag','faiss','chroma','mcp','n8n','playwright','selenium',
              'javascript','flutter','pandas','scikit-learn','tensorflow','pytorch','rest api','websocket')
        requested=[x for x in core if x in hay]
        matched=[x for x in requested if x in cv]
        score=35
        score+=min(20,role_hits*10)
        score+=min(35,len(matched)*5)
        # Reward broadly relevant engineering roles even when the posting is verbose.
        if any(x in job.title.lower() for x in ('junior','entry','graduate','associate')): score+=5
        # Avoid inflating obviously senior roles during API fallback.
        if any(x in job.title.lower() for x in ('senior','staff','principal','director','lead ')): score-=25
        score=max(0,min(85,score))
        rationale=(f'Local weighted estimate ({reason}). Role groups matched: {role_hits}; '
                   f'core stack overlap: {", ".join(matched[:12]) if matched else "none"}. '
                   'This fallback uses verified resume skills and does not infer missing experience.')
        draft=f'Dear Hiring Team,\n\nI am interested in the {job.title} position at {job.company}. My attached resume describes my experience and projects. I would appreciate the opportunity to discuss how my background fits this role.\n\nSincerely'
        return score,rationale,draft
    if not key:return local_match('Gemini API key is not configured')
    prompt=f'''You are a careful job matching assistant. Use only the CV/resume and job text. Return ONLY JSON with score (0-100 integer), rationale (two sentences), draft (short cover note), and eligible (boolean). Do not invent achievements. If requirements or location cannot be confirmed, state this in rationale.\nDOCUMENT:\n{doc.extracted_text[:16000]}\nJOB TITLE: {job.title}\nLOCATION: {job.location}\nJOB:\n{job.description[:10000]}'''
    configured=os.getenv("GEMINI_MODEL","gemini-3.8-flash").strip()
    models=[]
    for model in (configured,"gemini-3.8-flash","gemini-3.5-flash","gemini-flash-latest"):
        if model and model not in models:
            models.append(model)
    last_error="Gemini request rejected"
    for model in models:
        try:
            r=httpx.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                headers={'x-goog-api-key':key},
                json={
                    'contents':[{'parts':[{'text':prompt}]}],
                    'generationConfig':{'responseMimeType':'application/json','temperature':0.2},
                },
                timeout=45,
            )
        except httpx.RequestError:
            last_error='Gemini connection failed'
            continue
        if r.status_code==404:
            last_error=f'Gemini model {model} unavailable'
            continue
        if r.status_code in (429,503):
            last_error=f'Gemini HTTP {r.status_code}: temporary service/quota issue'
            # Retry the same model once after a short backoff, then try fallbacks.
            time.sleep(2)
            try:
                retry=httpx.post(
                    f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                    headers={'x-goog-api-key':key},
                    json={
                        'contents':[{'parts':[{'text':prompt}]}],
                        'generationConfig':{'responseMimeType':'application/json','temperature':0.2},
                    },
                    timeout=45,
                )
                if retry.status_code<400:
                    r=retry
                else:
                    continue
            except httpx.RequestError:
                continue
        elif r.status_code>=400:
            hint={400:'check the request',401:'check the API key',403:'check key permissions or billing'}.get(r.status_code,'request rejected')
            return local_match(f'Gemini HTTP {r.status_code}: {hint}')
        try:
            out=json.loads(r.json()['candidates'][0]['content']['parts'][0]['text'])
            return max(0,min(100,int(out['score']))),str(out['rationale'])[:1500],str(out['draft'])[:4000]
        except (KeyError,ValueError,TypeError,IndexError,json.JSONDecodeError):
            last_error=f'Gemini {model} returned an unreadable response'
            continue
    return local_match(last_error)
@app.post('/api/applications/prepare', dependencies=[Depends(auth)])
def prepare(body:PrepareIn,session:Session=Depends(db)):
    job=session.get(Job,body.job_id);doc=session.scalar(select(Document).where(Document.kind==body.document_kind))
    if not job or not doc:raise HTTPException(404,'Job or uploaded document not found')
    existing=session.scalar(select(Application).where(Application.job_id==job.id))
    if existing and not (existing.rationale.startswith('Local keyword estimate') and os.getenv('GEMINI_API_KEY')):return app_out(existing)
    score,rationale,draft=ai_prepare(job,doc)
    if existing:
        existing.score=score;existing.rationale=rationale;existing.draft=draft
        session.commit();session.refresh(existing);return app_out(existing)
    item=Application(job_id=job.id,document_id=doc.id,score=score,rationale=rationale,draft=draft)
    session.add(item);session.commit();session.refresh(item);return app_out(item)
@app.get('/api/applications', dependencies=[Depends(auth)])
def applications(session:Session=Depends(db)):
    rows=session.execute(
        select(Application,Job)
        .join(Job,Job.id==Application.job_id)
        .order_by(Application.created_at.desc())
    ).all()
    if os.getenv('GEMINI_API_KEY'):
        changed=False
        for item,job in rows:
            if (item.rationale or '').startswith('Local keyword estimate'):
                doc=session.get(Document,item.document_id)
                if job and doc:
                    score,rationale,draft=ai_prepare(job,doc)
                    item.score=score
                    item.rationale=rationale
                    item.draft=draft
                    changed=True
        if changed:
            session.commit()
    output=[]
    for item,job in rows:
        row=app_out(item,job)
        if item.status in ("needs_human","ready_for_retry"):
            review_rows=_ensure_review_fields(session,item)
            row["reviewQuestions"]=[{
                "id":x.id,
                "label":x.label_key,
                "sensitive":x.sensitive,
                "answered":bool((x.answer or "").strip()),
            } for x in review_rows]
            row["reviewQueued"]=item.status=="ready_for_retry"
        else:
            row["reviewQuestions"]=[]
            row["reviewQueued"]=False
        output.append(row)
    return output

@app.post('/api/applications/locate', dependencies=[Depends(auth)])
def locate_application(body:LocateApplicationIn,session:Session=Depends(db)):
    raw=(body.url or '').strip()
    parsed=urlparse(raw)
    if parsed.scheme not in ('http','https') or not parsed.hostname:
        raise HTTPException(400,'Paste a valid employer application or confirmation URL')

    clean_path=re.sub(r'/confirmation/?$','',parsed.path.rstrip('/'),flags=re.I)
    clean=f'{parsed.scheme}://{parsed.netloc}{clean_path}'
    matched=None
    stable_tokens=re.findall(r'[A-Za-z0-9_-]{8,}',clean_path)
    for job in session.scalars(select(Job)).all():
        for candidate in (job.apply_url,job.url):
            if not candidate:
                continue
            cp=urlparse(candidate)
            canonical=f'{cp.scheme}://{cp.netloc}{cp.path.rstrip("/")}'
            if canonical==clean:
                matched=job
                break
            if stable_tokens and (cp.hostname or '').lower()==(parsed.hostname or '').lower():
                if any(token in cp.path for token in stable_tokens[-3:]):
                    matched=job
                    break
        if matched:
            break

    if not matched:
        raise HTTPException(404,'No Career Atlas job matches that confirmation URL')

    application=session.scalar(select(Application).where(Application.job_id==matched.id))
    return {
        'job':job_out(matched),
        'application':app_out(application) if application else None,
        'cleanUrl':clean,
        'matchedBy':'confirmation_url',
    }

@app.get('/api/applications/submitted', dependencies=[Depends(auth)])
def submitted_applications(session:Session=Depends(db)):
    rows=session.execute(
        select(Application,Job,JobStatus)
        .join(Job,Job.id==Application.job_id)
        .join(JobStatus,JobStatus.job_id==Application.job_id)
        .where(Application.status=="applied",JobStatus.status=="submitted")
        .order_by(JobStatus.updated_at.desc())
    ).all()
    return [{
        "applicationId":a.id,
        "jobId":j.id,
        "company":j.company,
        "title":j.title,
        "location":j.location,
        "provider":j.provider,
        "applyUrl":j.apply_url,
        "score":a.score,
        "receipt":a.receipt or s.note,
        "submittedAt":s.updated_at.isoformat(),
    } for a,j,s in rows]

@app.post('/api/applications/{application_id}/confirm', dependencies=[Depends(auth)])
def confirm(application_id:int,body:ConfirmIn,session:Session=Depends(db)):
    item=session.get(Application,application_id)
    if not item:raise HTTPException(404,'Application not found')
    receipt=(body.receipt or '').strip() or 'Manual submission confirmed by user'
    item.status='applied';item.receipt=receipt
    status=session.scalar(select(JobStatus).where(JobStatus.job_id==item.job_id))
    if not status:status=JobStatus(job_id=item.job_id,status='submitted');session.add(status)
    status.status='submitted';status.note=receipt;status.updated_at=datetime.now(timezone.utc)
    record_event(session,item.job_id,'submitted','manual_confirmation',receipt,application_id=item.id)
    session.commit();return app_out(item)
@app.post('/api/applications/{application_id}/submit', dependencies=[Depends(auth)])
def submit(application_id:int,session:Session=Depends(db)):
    item=session.get(Application,application_id)
    if not item:raise HTTPException(404,'Application not found')
    job=session.get(Job,item.job_id)
    return {'status':'needs_human','reason':'This employer does not provide applicant-side API access. Open the official form, review its questions, and submit there. Record the confirmation afterward.','applyUrl':job.apply_url}


@app.get('/api/automation/config', dependencies=[Depends(auth)])
def automation_config():
    from automation import AutomationConfig
    cfg=AutomationConfig.from_env()
    return {
        'enabled':cfg.enabled,
        'minMatchScore':cfg.min_match_score,
        'dailyLimit':cfg.daily_limit,
        'remoteOnly':cfg.remote_only,
        'allowEmail':cfg.allow_email,
        'allowBrowser':cfg.allow_browser,
        'allowIndeed':cfg.allow_indeed,
        'allowGlassdoor':cfg.allow_glassdoor,
        'autoSubmitBrowser':cfg.auto_submit_browser,
    }

@app.post('/api/automation/run', dependencies=[Depends(auth)])
def automation_run():
    from worker import run
    return run()

class QualityModeIn(BaseModel):
    mode: str

class StageIn(BaseModel):
    stage: str
    note: str = Field(default='', max_length=1500)

class EmployerMessageIn(BaseModel):
    application_id: int | None = None
    sender: str = ''
    subject: str = ''
    body: str = ''
    classification: str = 'other'
    action_required: bool = False

class QuestionMemoryIn(BaseModel):
    answer_key: str = Field(default='', max_length=120)
    selector_hint: str = Field(default='', max_length=400)

class ReviewFieldAnswerIn(BaseModel):
    field_id: int
    answer: str = Field(default='', max_length=2000)

class ReviewSubmitIn(BaseModel):
    answers: list[ReviewFieldAnswerIn] = Field(default_factory=list)

class ReviewAnswerItemIn(BaseModel):
    label: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=3000)

class ReviewAnswersIn(BaseModel):
    answers: list[ReviewAnswerItemIn]

@app.get('/api/v3/dashboard', dependencies=[Depends(auth)])
def v3_dashboard(session:Session=Depends(db)):
    statuses=session.scalars(select(JobStatus)).all()
    apps=session.scalars(select(Application)).all()
    metrics=session.scalars(select(JobMetric)).all()
    events=session.scalars(select(ApplicationEvent).order_by(ApplicationEvent.created_at.desc()).limit(80)).all()
    submitted=sum(1 for s in statuses if s.status=='submitted')
    needs=sum(1 for a in apps if a.status=='needs_human')
    failed=sum(1 for a in apps if a.status=='failed')
    avg=round(sum(m.probability for m in metrics)/max(1,len(metrics))) if metrics else 0
    stages={}
    latest={}
    for e in events:
        key=e.application_id if e.application_id is not None else f"job:{e.job_id}"
        if key not in latest:
            latest[key]=e.stage
    for stage in latest.values():
        stages[stage]=stages.get(stage,0)+1
    return {
        'jobs':session.query(Job).count(),
        'applications':len(apps),
        'submitted':submitted,
        'needsAttention':needs,
        'failed':failed,
        'averageProbability':avg,
        'qualityMode':get_setting(session,'quality_mode','balanced'),
        'threshold':quality_threshold(session,75),
        'stageCounts':stages,
        'recentEvents':[{
            'id':e.id,'jobId':e.job_id,'applicationId':e.application_id,
            'stage':e.stage,'type':e.event_type,'message':e.message,
            'createdAt':e.created_at.isoformat()
        } for e in events[:30]],
    }

@app.get('/api/v3/metrics', dependencies=[Depends(auth)])
def v3_metrics(session:Session=Depends(db)):
    rows=session.execute(select(JobMetric,Job).join(Job,Job.id==JobMetric.job_id).order_by(JobMetric.probability.desc()).limit(200)).all()
    return [{
        'jobId':j.id,'company':j.company,'title':j.title,'category':m.category,
        'technical':m.technical,'experience':m.experience,'location':m.location,
        'seniority':m.seniority,'education':m.education,'salary':m.salary,
        'difficulty':m.difficulty,'probability':m.probability,'fingerprint':m.fingerprint,
        'updatedAt':m.updated_at.isoformat()
    } for m,j in rows]

@app.get('/api/v3/events', dependencies=[Depends(auth)])
def v3_events(session:Session=Depends(db),limit:int=100):
    rows=session.scalars(select(ApplicationEvent).order_by(ApplicationEvent.created_at.desc()).limit(min(max(limit,1),300))).all()
    return [{
        'id':e.id,'applicationId':e.application_id,'jobId':e.job_id,'stage':e.stage,
        'type':e.event_type,'message':e.message,'meta':e.meta_json,'createdAt':e.created_at.isoformat()
    } for e in rows]

@app.post('/api/v3/applications/{application_id}/stage', dependencies=[Depends(auth)])
def v3_set_stage(application_id:int,body:StageIn,session:Session=Depends(db)):
    item=session.get(Application,application_id)
    if not item: raise HTTPException(404,'Application not found')
    allowed={'discovered','matched','prepared','applying','submitted','employer_viewed','interview','rejected','offer'}
    if body.stage not in allowed: raise HTTPException(400,'Invalid stage')
    record_event(session,item.job_id,body.stage,'manual_stage',body.note,application_id=item.id)
    session.commit()
    return {'ok':True,'stage':body.stage}

def _review_questions_for(session:Session,item:Application):
    extracted=extract_review_questions(item.receipt or "")
    saved=session.scalars(select(ReviewAnswer).where(ReviewAnswer.application_id==item.id)).all()
    by_label={x.label_key:x for x in saved}
    out=[]
    for q in extracted:
        row=by_label.get(q["label"])
        out.append({
            "label":q["label"],
            "sensitive":bool(q["sensitive"]),
            "answered":bool(row and (row.answer or "").strip()),
            "answer":"" if q["sensitive"] else ((row.answer or "") if row else ""),
        })
    return out

@app.get('/api/v3/applications/{application_id}/review-questions', dependencies=[Depends(auth)])
def v3_review_questions(application_id:int,session:Session=Depends(db)):
    item=session.get(Application,application_id)
    if not item: raise HTTPException(404,'Application not found')
    return {
        "applicationId":item.id,
        "status":item.status,
        "questions":_review_questions_for(session,item),
        "queued":item.status=="ready_for_retry",
    }

@app.post('/api/v3/applications/{application_id}/review-answers', dependencies=[Depends(auth)])
def v3_review_answers(application_id:int,body:ReviewAnswersIn,session:Session=Depends(db)):
    item=session.get(Application,application_id)
    if not item: raise HTTPException(404,'Application not found')
    job=session.get(Job,item.job_id)
    questions={q["label"]:q for q in extract_review_questions(item.receipt or "")}
    if not questions:
        raise HTTPException(400,'No structured review questions were found for this application')

    saved=0
    manual=[]
    now=datetime.now(timezone.utc)
    for entry in body.answers:
        label=entry.label.strip()
        answer=entry.answer.strip()
        q=questions.get(label)
        if not q:
            raise HTTPException(400,f'Unknown review question: {label[:100]}')
        if q["sensitive"]:
            manual.append(label)
            continue
        row=session.scalar(select(ReviewAnswer).where(
            ReviewAnswer.application_id==item.id,
            ReviewAnswer.label_key==label,
        ))
        if not row:
            row=ReviewAnswer(application_id=item.id,label_key=label)
            session.add(row)
        row.answer=answer
        row.sensitive=False
        row.resolved=False
        row.updated_at=now
        saved+=1

    remaining_non_sensitive=[
        q for q in questions.values()
        if not q["sensitive"] and not session.scalar(select(ReviewAnswer).where(
            ReviewAnswer.application_id==item.id,
            ReviewAnswer.label_key==q["label"],
            ReviewAnswer.answer!="",
        ))
    ]
    if saved:
        item.status="ready_for_retry"
        record_event(session,item.job_id,'applying','review_answered',
                     f'{saved} review answer(s) supplied; queued for LangGraph retry.',
                     application_id=item.id)
    session.commit()
    return {
        "ok":True,
        "saved":saved,
        "manualOnly":manual,
        "queued":bool(saved),
        "remaining":len(remaining_non_sensitive),
        "job":job_out(job) if job else None,
    }

@app.get('/api/v3/questions', dependencies=[Depends(auth)])
def v3_questions(session:Session=Depends(db)):
    rows=session.scalars(select(QuestionMemory).order_by(QuestionMemory.last_seen_at.desc()).limit(300)).all()
    return [{
        'id':x.id,'host':x.host,'label':x.label_key,'answerKey':x.answer_key,
        'selectorHint':x.selector_hint,'sensitive':x.sensitive,'successCount':x.success_count,
        'lastSeenAt':x.last_seen_at.isoformat()
    } for x in rows]

@app.post('/api/v3/questions/{question_id}', dependencies=[Depends(auth)])
def v3_update_question(question_id:int,body:QuestionMemoryIn,session:Session=Depends(db)):
    item=session.get(QuestionMemory,question_id)
    if not item: raise HTTPException(404,'Question memory not found')
    if item.sensitive and body.answer_key:
        raise HTTPException(400,'Sensitive questions cannot use normal profile memory')
    allowed={'','name','email','phone','location','linkedin','github','portfolio','availability','salary','work_authorized','requires_sponsorship'}
    if body.answer_key not in allowed: raise HTTPException(400,'Unsupported profile key')
    item.answer_key=body.answer_key
    if body.selector_hint.strip(): item.selector_hint=body.selector_hint.strip()
    item.last_seen_at=datetime.now(timezone.utc)
    session.commit()
    return {'id':item.id,'answerKey':item.answer_key,'sensitive':item.sensitive}

def _ensure_review_fields(session:Session,application:Application):
    rows=session.scalars(select(ReviewAnswer).where(
        ReviewAnswer.application_id==application.id,
        ReviewAnswer.resolved==False,
    )).all()
    if rows:
        return rows
    if application.status!='needs_human':
        return []
    from automation import needs_human
    for item in extract_review_questions(application.receipt or ''):
        label=(item.get('label') or '').strip()[:500]
        if not label: continue
        sensitive=bool(item.get('sensitive') or needs_human(label))
        existing=session.scalar(select(ReviewAnswer).where(
            ReviewAnswer.application_id==application.id,
            ReviewAnswer.label_key==label,
        ))
        if not existing:
            existing=ReviewAnswer(
                application_id=application.id,
                label_key=label,
                answer='',
                selector_hint=(item.get('selector_hint') or '')[:500],
                sensitive=sensitive,
                resolved=False,
            )
            session.add(existing)
        else:
            existing.sensitive=sensitive
            existing.resolved=False
    session.commit()
    return session.scalars(select(ReviewAnswer).where(
        ReviewAnswer.application_id==application.id,
        ReviewAnswer.resolved==False,
    )).all()

@app.get('/api/v3/applications/{application_id}/review-fields', dependencies=[Depends(auth)])
def v3_review_fields(application_id:int,session:Session=Depends(db)):
    application=session.get(Application,application_id)
    if not application: raise HTTPException(404,'Application not found')
    job=session.get(Job,application.job_id)
    rows=_ensure_review_fields(session,application)
    return {
        'applicationId':application.id,
        'jobId':application.job_id,
        'company':job.company if job else '',
        'title':job.title if job else '',
        'officialForm':job.apply_url if job else '',
        'status':application.status,
        'fields':[{
            'id':row.id,'label':row.label_key,'answer':'' if row.sensitive else row.answer,
            'selectorHint':row.selector_hint,'sensitive':row.sensitive,'resolved':row.resolved,
            'memoryPolicy':'one_application_only' if row.sensitive else 'application_review'
        } for row in rows]
    }

@app.post('/api/v3/applications/{application_id}/review-submit', dependencies=[Depends(auth)])
def v3_review_submit(application_id:int,body:ReviewSubmitIn,background_tasks:BackgroundTasks,session:Session=Depends(db)):
    application=session.get(Application,application_id)
    if not application: raise HTTPException(404,'Application not found')
    job=session.get(Job,application.job_id)
    rows=_ensure_review_fields(session,application)
    by_id={row.id:row for row in rows}
    for entry in body.answers:
        row=by_id.get(entry.field_id)
        if not row: continue
        # Protected answers are accepted only because the user explicitly
        # entered them for this one application. They are never inferred.
        row.answer=entry.answer.strip()[:2000]
        row.updated_at=datetime.now(timezone.utc)
    session.flush()
    missing=[row.label_key for row in rows if not (row.answer or '').strip()]
    if missing:
        session.commit()
        return {'status':'needs_answers','missing':missing[:12]}
    application.status='ready_for_retry'
    application.receipt='Review answers saved. Career Atlas will refill all known fields and retry automatically.'
    record_event(session,application.job_id,'applying','review_answers_saved',
                 'User supplied only the unresolved application answers; LangGraph retry queued.',
                 application_id=application.id)
    session.commit()
    from worker import retry_application
    background_tasks.add_task(retry_application,application.id)
    return {
        'status':'retry_started',
        'message':'Answers saved. The agent will refill the complete employer form and retry submission.',
        'officialForm':job.apply_url if job else ''
    }

@app.get('/api/v3/resume-variants', dependencies=[Depends(auth)])
def v3_resume_variants(session:Session=Depends(db)):
    rows=session.execute(select(ResumeVariant,Job).join(Job,Job.id==ResumeVariant.job_id).order_by(ResumeVariant.created_at.desc()).limit(100)).all()
    return [{
        'id':v.id,'jobId':j.id,'company':j.company,'title':j.title,'filename':v.filename,
        'strategy':v.strategy,'createdAt':v.created_at.isoformat()
    } for v,j in rows]

@app.get('/api/v3/artifacts/{application_id}', dependencies=[Depends(auth)])
def v3_artifacts(application_id:int,session:Session=Depends(db)):
    rows=session.scalars(select(ApplicationArtifact).where(ApplicationArtifact.application_id==application_id).order_by(ApplicationArtifact.created_at)).all()
    return [{'id':x.id,'kind':x.kind,'contentType':x.content_type,'createdAt':x.created_at.isoformat()} for x in rows]

@app.get('/api/v3/artifacts/file/{artifact_id}', dependencies=[Depends(auth)])
def v3_artifact_file(artifact_id:int,session:Session=Depends(db)):
    from fastapi.responses import Response
    item=session.get(ApplicationArtifact,artifact_id)
    if not item: raise HTTPException(404,'Artifact not found')
    return Response(content=item.data,media_type=item.content_type)

@app.get('/api/v3/settings', dependencies=[Depends(auth)])
def v3_settings(session:Session=Depends(db)):
    mode=get_setting(session,'quality_mode','balanced')
    return {'qualityMode':mode,'threshold':quality_threshold(session,75)}

@app.post('/api/v3/settings/quality', dependencies=[Depends(auth)])
def v3_quality(body:QualityModeIn,session:Session=Depends(db)):
    mode=body.mode.lower().strip()
    if mode not in ('conservative','balanced','aggressive'): raise HTTPException(400,'Use conservative, balanced, or aggressive')
    set_setting(session,'quality_mode',mode);session.commit()
    return {'qualityMode':mode,'threshold':quality_threshold(session,75)}

@app.get('/api/v3/messages', dependencies=[Depends(auth)])
def v3_messages(session:Session=Depends(db)):
    rows=session.scalars(select(EmployerMessage).order_by(EmployerMessage.received_at.desc()).limit(200)).all()
    return [{
        'id':x.id,'applicationId':x.application_id,'sender':x.sender,'subject':x.subject,
        'body':x.body,'classification':x.classification,'actionRequired':x.action_required,
        'receivedAt':x.received_at.isoformat()
    } for x in rows]

@app.post('/api/v3/messages', dependencies=[Depends(auth)])
def v3_add_message(body:EmployerMessageIn,session:Session=Depends(db)):
    item=EmployerMessage(application_id=body.application_id,sender=body.sender[:320],subject=body.subject[:500],body=body.body[:12000],classification=body.classification[:60],action_required=body.action_required)
    session.add(item)
    if body.application_id:
        app_item=session.get(Application,body.application_id)
        if app_item:
            stage='interview' if body.classification=='interview' else ('rejected' if body.classification=='rejection' else 'employer_viewed')
            record_event(session,app_item.job_id,stage,'employer_message',body.subject,application_id=app_item.id)
    session.commit();session.refresh(item)
    return {'id':item.id}

@app.get('/api/v3/followups', dependencies=[Depends(auth)])
def v3_followups(session:Session=Depends(db)):
    rows=session.execute(select(FollowUpDraft,Application,Job).join(Application,Application.id==FollowUpDraft.application_id).join(Job,Job.id==Application.job_id).order_by(FollowUpDraft.due_at)).all()
    return [{
        'id':d.id,'applicationId':a.id,'company':j.company,'title':j.title,'message':d.message,
        'status':d.status,'dueAt':d.due_at.isoformat() if d.due_at else None
    } for d,a,j in rows]

@app.get('/api/v3/interview-prep/{application_id}', dependencies=[Depends(auth)])
def v3_interview_prep(application_id:int,session:Session=Depends(db)):
    row=session.scalar(select(InterviewPrep).where(InterviewPrep.application_id==application_id))
    return {'applicationId':application_id,'content':row.content if row else ''}

@app.get('/api/v3/research', dependencies=[Depends(auth)])
def v3_research(session:Session=Depends(db)):
    rows=session.execute(select(ResearchResult,Job).join(Job,Job.id==ResearchResult.job_id).order_by(ResearchResult.quality_score.desc()).limit(150)).all()
    return [{
        'jobId':j.id,'company':j.company,'title':j.title,'companySummary':r.company_summary,
        'eligibilityNotes':r.eligibility_notes,'qualityScore':r.quality_score,'source':r.source
    } for r,j in rows]


WEB=Path(__file__).parent/'web'
if WEB.exists():
    app.mount('/assets',StaticFiles(directory=WEB/'assets'),name='assets')
    @app.get('/{path:path}',include_in_schema=False)
    def flutter_page(path:str):
        file=WEB/path
        if file.is_file() and WEB.resolve() in file.resolve().parents:return FileResponse(file)
        return FileResponse(WEB/'index.html')
