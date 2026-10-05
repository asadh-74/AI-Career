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
from fastapi import FastAPI, Depends, File, Form, Header, HTTPException, UploadFile
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
class ConfirmIn(BaseModel): receipt: str = Field(min_length=1, max_length=1000)
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
def app_out(a): return dict(id=a.id, jobId=a.job_id, documentId=a.document_id, score=a.score, rationale=a.rationale, draft=a.draft, status=a.status, receipt=a.receipt)
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
        else: raise HTTPException(400,'This website has no supported public board feed yet; supported: Greenhouse and Lever')
        slug=parsed.path.strip('/').split('/')[0]
    if provider not in ('Greenhouse','Lever','LeverEU'): raise HTTPException(400, 'Supported company feeds: Greenhouse and Lever')
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
    host='api.eu.lever.co' if source.provider=='LeverEU' else 'api.lever.co'
    r=client.get(f'https://{host}/v0/postings/{source.slug}',params={'mode':'json'});r.raise_for_status()
    return [dict(company=source.company,title=x.get('text',''),location=(x.get('categories') or {}).get('location',''),description=(x.get('descriptionPlain') or '')[:8000],url=x.get('hostedUrl',''),apply_url=x.get('applyUrl') or x.get('hostedUrl',''),provider=source.provider) for x in r.json()]

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
    with httpx.Client(timeout=20,follow_redirects=False) as client:return scan(session,client)
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
    if not session.get(Job,body.job_id):raise HTTPException(404,'Job not found')
    item=session.scalar(select(JobStatus).where(JobStatus.job_id==body.job_id))
    if not item:item=JobStatus(job_id=body.job_id,status=body.status);session.add(item)
    item.status=body.status;item.note=body.note.strip();item.updated_at=datetime.now(timezone.utc)
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
        terms=lambda s:set(re.findall(r'[a-z][a-z+#.]{2,}',s.lower()))
        ignored={'the','and','for','with','you','are','our','that','this','from','your','will','have','team','work','years','remote','job','role','experience'}
        required=(terms(job.title+' '+job.description)-ignored)
        present=(terms(doc.extracted_text)-ignored)
        shared=sorted(required & present)[:12]
        score=min(85,round(100*len(required & present)/max(len(required),1)))
        rationale=f'Local keyword estimate ({reason}). Shared terms: {", ".join(shared) if shared else "none found"}. Review location and requirements yourself; this is not an AI assessment.'
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
    items=session.scalars(select(Application).order_by(Application.created_at.desc())).all()
    if os.getenv('GEMINI_API_KEY'):
        changed=False
        for item in items:
            if (item.rationale or '').startswith('Local keyword estimate'):
                job=session.get(Job,item.job_id)
                doc=session.get(Document,item.document_id)
                if job and doc:
                    score,rationale,draft=ai_prepare(job,doc)
                    item.score=score
                    item.rationale=rationale
                    item.draft=draft
                    changed=True
        if changed:
            session.commit()
    return [app_out(x) for x in items]

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
    item.status='applied';item.receipt=body.receipt
    status=session.scalar(select(JobStatus).where(JobStatus.job_id==item.job_id))
    if not status:status=JobStatus(job_id=item.job_id,status='submitted');session.add(status)
    status.status='submitted';status.note=body.receipt;status.updated_at=datetime.now(timezone.utc)
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


WEB=Path(__file__).parent/'web'
if WEB.exists():
    app.mount('/assets',StaticFiles(directory=WEB/'assets'),name='assets')
    @app.get('/{path:path}',include_in_schema=False)
    def flutter_page(path:str):
        file=WEB/path
        if file.is_file() and WEB.resolve() in file.resolve().parents:return FileResponse(file)
        return FileResponse(WEB/'index.html')
