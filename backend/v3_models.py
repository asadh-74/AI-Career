"""Career Atlas v3 shared models and helpers.

Additive tables only: existing jobs/applications remain compatible.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from main import Base

QUALITY_THRESHOLDS={"conservative":85,"balanced":75,"aggressive":65}
PIPELINE_STAGES=("discovered","matched","prepared","applying","submitted","employer_viewed","interview","rejected","offer")

class JobMetric(Base):
    __tablename__="job_metrics"
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey("jobs.id"),unique=True,index=True)
    technical:Mapped[int]=mapped_column(Integer,default=0)
    experience:Mapped[int]=mapped_column(Integer,default=0)
    location:Mapped[int]=mapped_column(Integer,default=0)
    seniority:Mapped[int]=mapped_column(Integer,default=0)
    education:Mapped[int]=mapped_column(Integer,default=0)
    salary:Mapped[int]=mapped_column(Integer,default=50)
    difficulty:Mapped[int]=mapped_column(Integer,default=50)
    probability:Mapped[int]=mapped_column(Integer,default=0)
    category:Mapped[str]=mapped_column(String(40),default="software")
    fingerprint:Mapped[str]=mapped_column(String(64),index=True)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class ApplicationEvent(Base):
    __tablename__="application_events"
    id:Mapped[int]=mapped_column(primary_key=True)
    application_id:Mapped[int|None]=mapped_column(ForeignKey("applications.id"),nullable=True,index=True)
    job_id:Mapped[int]=mapped_column(ForeignKey("jobs.id"),index=True)
    stage:Mapped[str]=mapped_column(String(40),default="discovered")
    event_type:Mapped[str]=mapped_column(String(60),default="info")
    message:Mapped[str]=mapped_column(Text,default="")
    meta_json:Mapped[str]=mapped_column(Text,default="{}")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class QuestionMemory(Base):
    __tablename__="question_memory"
    id:Mapped[int]=mapped_column(primary_key=True)
    host:Mapped[str]=mapped_column(String(180),index=True)
    label_key:Mapped[str]=mapped_column(String(260))
    answer_key:Mapped[str]=mapped_column(String(120),default="")
    selector_hint:Mapped[str]=mapped_column(String(400),default="")
    sensitive:Mapped[bool]=mapped_column(Boolean,default=False)
    success_count:Mapped[int]=mapped_column(Integer,default=0)
    last_seen_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    __table_args__=(UniqueConstraint("host","label_key",name="uq_question_memory_host_label"),)

class ReviewAnswer(Base):
    __tablename__="review_answers"
    id:Mapped[int]=mapped_column(primary_key=True)
    application_id:Mapped[int]=mapped_column(ForeignKey("applications.id"),index=True)
    label_key:Mapped[str]=mapped_column(String(500))
    answer:Mapped[str]=mapped_column(Text,default="")
    selector_hint:Mapped[str]=mapped_column(String(500),default="")
    sensitive:Mapped[bool]=mapped_column(Boolean,default=False)
    resolved:Mapped[bool]=mapped_column(Boolean,default=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    __table_args__=(UniqueConstraint("application_id","label_key",name="uq_review_answer_application_label"),)

class ResumeVariant(Base):
    __tablename__="resume_variants"
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey("jobs.id"),unique=True,index=True)
    source_document_id:Mapped[int]=mapped_column(ForeignKey("documents.id"))
    filename:Mapped[str]=mapped_column(String(220))
    pdf:Mapped[bytes]=mapped_column(LargeBinary)
    extracted_text:Mapped[str]=mapped_column(Text)
    strategy:Mapped[str]=mapped_column(Text,default="")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class ApplicationArtifact(Base):
    __tablename__="application_artifacts"
    id:Mapped[int]=mapped_column(primary_key=True)
    application_id:Mapped[int]=mapped_column(ForeignKey("applications.id"),index=True)
    kind:Mapped[str]=mapped_column(String(40))
    content_type:Mapped[str]=mapped_column(String(80),default="image/png")
    data:Mapped[bytes]=mapped_column(LargeBinary)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class AutomationSetting(Base):
    __tablename__="automation_settings"
    key:Mapped[str]=mapped_column(String(80),primary_key=True)
    value:Mapped[str]=mapped_column(Text,default="")
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class EmployerMessage(Base):
    __tablename__="employer_messages"
    id:Mapped[int]=mapped_column(primary_key=True)
    application_id:Mapped[int|None]=mapped_column(ForeignKey("applications.id"),nullable=True,index=True)
    sender:Mapped[str]=mapped_column(String(320),default="")
    subject:Mapped[str]=mapped_column(String(500),default="")
    body:Mapped[str]=mapped_column(Text,default="")
    classification:Mapped[str]=mapped_column(String(60),default="other")
    action_required:Mapped[bool]=mapped_column(Boolean,default=False)
    received_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class FollowUpDraft(Base):
    __tablename__="followup_drafts"
    id:Mapped[int]=mapped_column(primary_key=True)
    application_id:Mapped[int]=mapped_column(ForeignKey("applications.id"),unique=True,index=True)
    message:Mapped[str]=mapped_column(Text,default="")
    status:Mapped[str]=mapped_column(String(40),default="draft")
    due_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class InterviewPrep(Base):
    __tablename__="interview_prep"
    id:Mapped[int]=mapped_column(primary_key=True)
    application_id:Mapped[int]=mapped_column(ForeignKey("applications.id"),unique=True,index=True)
    content:Mapped[str]=mapped_column(Text,default="")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class ResearchResult(Base):
    __tablename__="research_results"
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey("jobs.id"),unique=True,index=True)
    company_summary:Mapped[str]=mapped_column(Text,default="")
    eligibility_notes:Mapped[str]=mapped_column(Text,default="")
    quality_score:Mapped[int]=mapped_column(Integer,default=0)
    source:Mapped[str]=mapped_column(String(60),default="heuristic")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

PROTECTED_REVIEW_TERMS=("gender","race","ethnicity","disability","veteran","religion","sexual orientation","marital status","pregnancy")

def review_question_is_sensitive(label:str)->bool:
    low=(label or "").lower()
    return any(x in low for x in PROTECTED_REVIEW_TERMS)

def extract_review_questions(reason:str)->list[dict]:
    """Extract human-answerable questions from an automation blocker string."""
    reason=(reason or "").strip()
    if not reason:return []
    chunks=[]
    if "Invalid fields:" in reason:
        tail=reason.split("Invalid fields:",1)[1]
        chunks.extend(x.strip() for x in tail.split(" | ") if x.strip())
    pattern=re.compile(r"(?:Sensitive/uncertain question:|Unknown required field:)\s*(.*?)(?=(?:\s*\|\s*)?(?:Sensitive/uncertain question:|Unknown required field:)|$)",re.I|re.S)
    chunks.extend(m.group(1).strip(" |") for m in pattern.finditer(reason) if m.group(1).strip())
    seen=set();out=[]
    for raw in chunks:
        raw_low=raw.lower()
        # Browser/ATS validation proxy controls are implementation details,
        # not questions the applicant should ever see.
        if ('aria-hidden="true"' in raw_low or "aria-hidden='true'" in raw_low
            or 'tabindex="-1"' in raw_low or "tabindex='-1'" in raw_low
            or "requiredinput" in raw_low):
            continue
        text=re.sub(r"<[^>]+>"," ",raw)
        text=re.sub(r"\s+"," ",text).strip()
        if not text:continue
        # Strip noisy whole-page prefixes from validation diagnostics.
        for marker in ("LinkedIn Profile","Do you currently","What is your","Have you","Country","Phone","Email","First Name","Last Name"):
            pos=text.find(marker)
            if pos>0 and len(text)>220:
                text=text[pos:]
                break
        text=text[:500]
        key=normalize_text(text)
        if not key or key in seen:continue
        seen.add(key)
        out.append({"label":text,"sensitive":review_question_is_sensitive(text)})
    return out

def normalize_text(v:str)->str:
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9+#. ]+"," ",(v or "").lower())).strip()

def job_fingerprint(company:str,title:str,location:str)->str:
    raw="|".join((normalize_text(company),normalize_text(title),normalize_text(location)))
    return hashlib.sha256(raw.encode()).hexdigest()

def category_for_title(title:str)->str:
    t=normalize_text(title)
    if any(x in t for x in ("machine learning","ml engineer","ai engineer","agentic","automation")): return "ai_automation"
    if any(x in t for x in ("full stack","fullstack","front end","frontend","web developer")): return "fullstack"
    if any(x in t for x in ("embedded","iot","firmware")): return "embedded"
    if any(x in t for x in ("backend","back end","python","api engineer")): return "backend"
    if "data" in t: return "data"
    return "software"

def difficulty_for(job)->int:
    host=(urlparse(job.apply_url or "").hostname or "").lower()
    p=(job.provider or "").lower()
    score=45
    if any(x in host for x in ("lever.co","greenhouse.io","ashbyhq.com","smartrecruiters.com")): score-=22
    if "workday" in host: score+=25
    if any(x in p for x in ("remoteok","jobicy","remotive","wwr","himalayas","arbeitnow")): score+=30
    if len(job.description or "")>7000: score+=5
    return max(5,min(95,score))

def dimensional_scores(job,doc,profile:dict,base_score:int)->dict:
    jd=normalize_text((job.title or "")+" "+(job.description or ""))
    cv=normalize_text(doc.extracted_text or "")
    tokens=lambda s:set(re.findall(r"[a-z][a-z0-9+#.]{2,}",s))
    ignored={"the","and","for","with","from","that","this","your","will","have","work","team","role","job","years"}
    jt=tokens(jd)-ignored; ct=tokens(cv)-ignored
    overlap=len(jt & ct)/max(1,len(jt))
    technical=max(0,min(100,round(35+65*overlap)))

    req=0
    m=re.search(r"(\d+)\+?\s*(?:years|yrs)",jd)
    if m:req=int(m.group(1))
    own=0
    m2=re.search(r"(\d+)",str(profile.get("professional_experience","")))
    if m2:own=int(m2.group(1))
    experience=90 if req==0 else max(10,min(100,round(100*own/max(req,1))))

    loc=(job.location or "").lower()
    location=95 if any(x in loc for x in ("worldwide","anywhere","global","pakistan","remote")) else 60
    seniority=30 if any(x in jd[:250] for x in ("senior","staff","principal","lead ")) and own<4 else 90
    education=85
    if "degree" in jd and profile.get("degree"):education=90
    salary=70
    difficulty=difficulty_for(job)
    weighted=round(.34*technical+.2*experience+.14*location+.1*seniority+.08*education+.06*salary+.08*base_score)
    probability=max(1,min(99,round(weighted*(1-(difficulty/220)))))
    return dict(
        technical=technical,experience=experience,location=location,seniority=seniority,
        education=education,salary=salary,difficulty=difficulty,probability=probability,
        category=category_for_title(job.title),fingerprint=job_fingerprint(job.company,job.title,job.location),
    )

def get_setting(session,key:str,default:str="")->str:
    row=session.get(AutomationSetting,key)
    return row.value if row else default

def set_setting(session,key:str,value:str):
    row=session.get(AutomationSetting,key)
    if not row:
        row=AutomationSetting(key=key,value=value)
        session.add(row)
    row.value=value
    row.updated_at=datetime.now(timezone.utc)
    return row

def quality_threshold(session,env_default:int=75)->int:
    mode=get_setting(session,"quality_mode","balanced").lower()
    return QUALITY_THRESHOLDS.get(mode,env_default)

def record_event(session,job_id:int,stage:str,event_type:str,message:str="",application_id:int|None=None,meta:dict|None=None):
    row=ApplicationEvent(job_id=job_id,application_id=application_id,stage=stage,event_type=event_type,message=(message or "")[:3000],meta_json=json.dumps(meta or {})[:8000])
    session.add(row)
    return row

def classify_failure(reason:str)->str:
    r=(reason or "").lower()
    if "captcha" in r or "anti-bot" in r:return "captcha"
    if "assessment" in r:return "assessment"
    if "timeout" in r:return "timeout"
    if "unknown required" in r:return "unknown_field"
    if "sensitive" in r:return "sensitive_field"
    if "submit" in r and "confirmation" in r:return "confirmation"
    if "selector" in r or "locator" in r:return "selector"
    if "http 5" in r or "server" in r:return "temporary_server"
    if "profile" in r or "configured" in r:return "missing_profile"
    return "unsupported_form"

def strategy_select(jobs,limit:int=120):
    quotas={"backend":4,"ai_automation":2,"fullstack":2,"software":1,"embedded":1,"data":1}
    groups={k:[] for k in quotas}
    other=[]
    for j in jobs:
        c=category_for_title(j.title)
        (groups[c] if c in groups else other).append(j)
    chosen=[]
    # Repeat quota pattern so larger candidate pools remain diversified.
    while len(chosen)<limit and any(groups.values()):
        moved=False
        for cat,n in quotas.items():
            for _ in range(n):
                if groups[cat] and len(chosen)<limit:
                    chosen.append(groups[cat].pop(0));moved=True
        if not moved:break
    chosen.extend(other[:max(0,limit-len(chosen))])
    return chosen[:limit]
