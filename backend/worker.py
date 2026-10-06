"""Scheduled Career Atlas v3 automation worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import os
import re

import httpx
from sqlalchemy import select

from automation import AutomationConfig, host_allowed, load_profile, provider_allowed
from main import Application, Document, Job, JobStatus, SessionLocal, Source, ai_prepare, scan
from public_sources import scan_public_sources
from job_graph import run_application_graph
from research_agents import crew_research
from resume_tailor import build_tailored_resume
from email_monitor import fetch_recent_messages, match_application
from v3_models import (
    ApplicationArtifact, ApplicationEvent, EmployerMessage, FollowUpDraft,
    InterviewPrep, JobMetric, QuestionMemory, ResearchResult, ResumeVariant,
    classify_failure, dimensional_scores, job_fingerprint, quality_threshold,
    record_event, strategy_select,
)

TARGET_TITLE_TERMS=(
    "software engineer","software developer","application developer","python engineer","python developer","python",
    "backend engineer","backend developer","back-end engineer","back-end developer","backend","back-end","fastapi","api engineer",
    "frontend engineer","frontend developer","front-end engineer","front-end developer","frontend","front-end","web developer",
    "full stack","full-stack","fullstack","ai engineer","machine learning","ml engineer","automation","agentic",
    "artificial intelligence","platform engineer","data engineer","embedded","iot","computer vision",
)

def mark_status(session,job_id,status,note):
    row=session.scalar(select(JobStatus).where(JobStatus.job_id==job_id))
    if not row:
        row=JobStatus(job_id=job_id,status=status,note=note);session.add(row)
    row.status=status;row.note=(note or "")[:1000];row.updated_at=datetime.now(timezone.utc)
    return row

def remote_eligible(location:str)->bool:
    loc=(location or "").strip().lower()
    if not loc:return False
    if any(x in loc for x in ("remote","home based - worldwide","home-based - worldwide","worldwide","anywhere","global","pakistan")):
        return True
    if any(x in loc for x in ("apac","asia")) and any(x in loc for x in ("home based","home-based","distributed","virtual","remote")):
        return True
    return False

def title_relevant(title:str)->bool:
    t=(title or "").lower()
    excluded=("director","vice president","vp ","head of ","principal","staff engineer","engineering manager",
              "sales manager","sales director","account manager","solutions architecture manager","senior manager")
    return not any(x in t for x in excluded) and any(x in t for x in TARGET_TITLE_TERMS)

def _profile_value(profile,key):
    aliases={
        "full_name":"name","name":"name","email":"email","phone":"phone","mobile":"phone",
        "location":"location","city":"location","linkedin":"linkedin","github":"github","portfolio":"portfolio",
        "website":"portfolio","availability":"availability","salary":"salary_expectation_amount",
        "work_authorized":"work_authorized","requires_sponsorship":"requires_sponsorship",
    }
    return profile.get(aliases.get(key,key),"")

def learned_answers_for(session,url,profile):
    host=(urlparse(url or "").hostname or "").lower()
    rows=session.scalars(select(QuestionMemory).where(QuestionMemory.host==host,QuestionMemory.sensitive==False)).all()
    out=[]
    for row in rows:
        value=_profile_value(profile,row.answer_key)
        if value not in (None,""):
            out.append({"label":row.label_key,"selector_hint":row.selector_hint,"value":value})
    return out

def remember_blocker(session,job,reason):
    host=(urlparse(job.apply_url or "").hostname or "").lower()
    low=(reason or "").lower()
    sensitive="sensitive/uncertain question:" in low
    unknown="unknown required field:" in low
    if not (sensitive or unknown):return
    label=(reason.split(":",1)[1].strip() if ":" in reason else reason)[:260]
    existing=session.scalar(select(QuestionMemory).where(QuestionMemory.host==host,QuestionMemory.label_key==label))
    if not existing:
        answer_key=""
        l=label.lower()
        for token,key in (
            ("full name","name"),("first name","name"),("email","email"),("phone","phone"),
            ("linkedin","linkedin"),("github","github"),("portfolio","portfolio"),("website","portfolio"),
            ("availability","availability"),("salary","salary"),("location","location"),("city","location"),
        ):
            if token in l:answer_key=key;break
        selector=""
        m=re.search(r'data-testid="([^"]+)"',reason)
        if m:selector=f'[data-testid="{m.group(1)}"]'
        existing=QuestionMemory(host=host,label_key=label,answer_key=answer_key,selector_hint=selector,sensitive=sensitive)
        session.add(existing)
    existing.last_seen_at=datetime.now(timezone.utc)

def upsert_metric(session,job,doc,profile,base_score):
    values=dimensional_scores(job,doc,profile,base_score)
    row=session.scalar(select(JobMetric).where(JobMetric.job_id==job.id))
    if not row:
        row=JobMetric(job_id=job.id,**values);session.add(row)
    else:
        for k,v in values.items():setattr(row,k,v)
        row.updated_at=datetime.now(timezone.utc)
    return row

def research_job(session,job,profile):
    row=session.scalar(select(ResearchResult).where(ResearchResult.job_id==job.id))
    if row:return row
    data=crew_research(job,profile)
    row=ResearchResult(job_id=job.id,company_summary=data["company_summary"],eligibility_notes=data["eligibility_notes"],
                       quality_score=int(data["quality_score"]),source=data["source"])
    session.add(row)
    return row

def tailored_doc_for(session,job,doc):
    enabled=os.getenv("TAILOR_RESUME","true").lower() in {"1","true","yes","on"}
    if not enabled:return doc
    row=session.scalar(select(ResumeVariant).where(ResumeVariant.job_id==job.id))
    if not row:
        tailored=build_tailored_resume(job,doc)
        row=ResumeVariant(job_id=job.id,source_document_id=doc.id,filename=tailored.filename,pdf=tailored.pdf,
                          extracted_text=tailored.extracted_text,strategy=tailored.strategy)
        session.add(row);session.flush()
    return SimpleNamespace(id=doc.id,filename=row.filename,pdf=row.pdf,extracted_text=row.extracted_text)

def store_artifacts(session,application_id,result):
    for key,kind in (("pre_screenshot","before_submit"),("post_screenshot","after_submit")):
        data=result.get(key)
        if isinstance(data,(bytes,bytearray)) and data:
            session.add(ApplicationArtifact(application_id=application_id,kind=kind,content_type="image/png",data=bytes(data)))

def duplicate_already_submitted(session,metric,job_id):
    if not metric or not metric.fingerprint:return False
    twins=session.scalars(select(JobMetric).where(JobMetric.fingerprint==metric.fingerprint,JobMetric.job_id!=job_id)).all()
    for twin in twins:
        sent=session.scalar(select(JobStatus).where(JobStatus.job_id==twin.job_id,JobStatus.status=="submitted"))
        if sent:return True
    return False

def ensure_followup_drafts(session):
    cutoff=datetime.now(timezone.utc)-timedelta(days=5)
    rows=session.execute(
        select(Application,Job,JobStatus).join(Job,Job.id==Application.job_id).join(JobStatus,JobStatus.job_id==Job.id)
        .where(Application.status=="applied",JobStatus.status=="submitted",JobStatus.updated_at<=cutoff)
    ).all()
    for app,job,status in rows:
        if session.scalar(select(FollowUpDraft).where(FollowUpDraft.application_id==app.id)):continue
        if session.scalar(select(EmployerMessage).where(EmployerMessage.application_id==app.id)):continue
        msg=(f"Hello {job.company} Hiring Team,\n\nI am following up on my application for the {job.title} role. "
             "I remain interested in the opportunity and would be glad to provide any additional information.\n\nBest regards")
        session.add(FollowUpDraft(application_id=app.id,message=msg,status="draft",due_at=datetime.now(timezone.utc)))
        record_event(session,job.id,"submitted","followup_ready","Follow-up draft prepared; not sent automatically.",application_id=app.id)

def process_recruiter_mail(session,doc,stats):
    try:messages=fetch_recent_messages()
    except Exception as exc:
        stats["emailMonitorError"]=f"{type(exc).__name__}: {str(exc)[:120]}";return
    if not messages:return
    pairs=session.execute(select(Application,Job).join(Job,Job.id==Application.job_id)).all()
    for msg in messages:
        duplicate=session.scalar(select(EmployerMessage).where(EmployerMessage.sender==msg["sender"],EmployerMessage.subject==msg["subject"]))
        if duplicate:continue
        matched=match_application(msg,pairs)
        app=matched[0] if matched else None
        job=matched[1] if matched else None
        row=EmployerMessage(application_id=app.id if app else None,sender=msg["sender"],subject=msg["subject"],body=msg["body"],
                            classification=msg["classification"],action_required=msg["action_required"],received_at=msg["received_at"])
        session.add(row)
        if app and job:
            kind=msg["classification"]
            stage="employer_viewed"
            if kind=="interview":stage="interview"
            elif kind=="rejection":stage="rejected"
            elif kind=="offer":stage="offer"
            record_event(session,job.id,stage,"employer_message",msg["subject"],application_id=app.id)
            if kind=="interview" and not session.scalar(select(InterviewPrep).where(InterviewPrep.application_id==app.id)):
                focus=sorted(set(re.findall(r"[A-Za-z][A-Za-z+#.]{2,}",job.description or "")) & set(re.findall(r"[A-Za-z][A-Za-z+#.]{2,}",doc.extracted_text or "")))[:20]
                prep=(f"Interview preparation for {job.company} — {job.title}\n\n"
                      f"Role focus from verified overlap: {', '.join(focus) if focus else 'review the job description and resume together'}.\n\n"
                      "Prepare concise examples for: project architecture, debugging decisions, API/backend design, teamwork, deployment, and why this role. "
                      "Review every requirement in the job description and identify which resume project demonstrates it.")
                session.add(InterviewPrep(application_id=app.id,content=prep))
    stats["recruiterMessages"]=len(messages)

def run():
    cfg=AutomationConfig.from_env()
    if not cfg.enabled:return {"status":"disabled","processed":0}
    stats={"status":"ok","sourcesScanned":0,"newJobs":0,"scanErrors":[],"publicSources":{},"jobsConsidered":0,
           "remoteEligible":0,"titleRelevant":0,"scored":0,"belowThreshold":0,"processed":0,"applied":0,
           "needsAttention":0,"failed":0,"alreadyApplied":0,"duplicatesSkipped":0,"retried":0}

    with SessionLocal() as session:
        profile=load_profile()
        cfg.min_match_score=quality_threshold(session,cfg.min_match_score)
        stats["qualityThreshold"]=cfg.min_match_score

        target_job_id=(os.getenv("TARGET_JOB_ID") or "").strip()
        target_job_url=(os.getenv("TARGET_JOB_URL") or "").strip()
        if target_job_url and not target_job_id.isdigit():
            target=session.scalar(select(Job).where(Job.apply_url==target_job_url))
            if not target:
                target=Job(url=target_job_url,apply_url=target_job_url,
                           company=(os.getenv("TARGET_JOB_COMPANY") or "Unknown company").strip(),
                           title=(os.getenv("TARGET_JOB_TITLE") or "Target role").strip(),
                           location=(os.getenv("TARGET_JOB_LOCATION") or "Remote").strip(),
                           description=(os.getenv("TARGET_JOB_DESCRIPTION") or "").strip(),
                           provider=(os.getenv("TARGET_JOB_PROVIDER") or "Indeed").strip())
                session.add(target);session.flush()
            target_job_id=str(target.id)

        if not target_job_id.isdigit():
            try:
                with httpx.Client(timeout=30,follow_redirects=True) as client:
                    core=scan(session,client);public=scan_public_sources(session,client)
                stats["newJobs"]=core.get("added",0)+public.get("added",0)
                stats["scanErrors"]=core.get("errors",[])+public.get("errors",[])
                stats["publicSources"]=public.get("details",{})
                stats["sourcesScanned"]=len(session.scalars(select(Source).where(Source.active==True)).all())+len(stats["publicSources"])
            except Exception as exc:
                stats["scanErrors"]=[f"scan failed: {type(exc).__name__}: {str(exc)[:180]}"]

        doc=session.scalar(select(Document).where(Document.kind=="Resume")) or session.scalar(select(Document).where(Document.kind=="CV"))
        if not doc:return {**stats,"status":"needs_setup","reason":"Upload a Resume or CV first"}

        if target_job_id.isdigit():
            target=session.get(Job,int(target_job_id));jobs=[target] if target else []
        else:
            recent=session.scalars(select(Job).order_by(Job.found_at.desc()).limit(400)).all()
            jobs=strategy_select(recent,160)
        max_to_score=max(50,min(120,cfg.daily_limit*10))

        pkt=ZoneInfo("Asia/Karachi");now_pkt=datetime.now(pkt)
        day_start_utc=now_pkt.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
        submitted_today=len(session.scalars(select(JobStatus).where(JobStatus.status=="submitted",JobStatus.updated_at>=day_start_utc)).all())
        stats["submittedToday"]=submitted_today
        remaining_today=max(0,cfg.daily_limit-submitted_today);stats["remainingToday"]=remaining_today
        max_attempts=max(20,min(70,remaining_today*7 if remaining_today else 20))

        for job in jobs:
            if not job:continue
            stats["jobsConsidered"]+=1
            if stats["applied"]>=remaining_today or stats["processed"]>=max_attempts or stats["scored"]>=max_to_score:break
            if cfg.remote_only and not remote_eligible(job.location):continue
            stats["remoteEligible"]+=1
            if not title_relevant(job.title):continue
            stats["titleRelevant"]+=1
            if not provider_allowed(job.provider,cfg) or not host_allowed(job.apply_url):continue

            existing=session.scalar(select(Application).where(Application.job_id==job.id))
            sent=session.scalar(select(JobStatus).where(JobStatus.job_id==job.id,JobStatus.status=="submitted"))
            if (existing and existing.status=="applied") or sent:
                stats["alreadyApplied"]+=1;continue

            research=research_job(session,job,profile)
            if research.quality_score<45:
                record_event(session,job.id,"discovered","research_skip",research.eligibility_notes);session.commit();continue

            needs_rescore=existing is not None and (existing.rationale or "").startswith("Local keyword estimate")
            if not existing or needs_rescore:
                score,rationale,draft=ai_prepare(job,doc)
                if existing:
                    existing.document_id=doc.id;existing.score=score;existing.rationale=rationale;existing.draft=draft
                else:
                    existing=Application(job_id=job.id,document_id=doc.id,score=score,rationale=rationale,draft=draft)
                    session.add(existing)
                session.flush();stats["scored"]+=1
                record_event(session,job.id,"matched","scored",f"Match score {score}",application_id=existing.id)

            metric=upsert_metric(session,job,doc,profile,existing.score)
            if duplicate_already_submitted(session,metric,job.id):
                stats["duplicatesSkipped"]+=1
                record_event(session,job.id,"discovered","duplicate_skip","Equivalent role already submitted.",application_id=existing.id)
                session.commit();continue

            apply_doc=tailored_doc_for(session,job,doc)
            learned=learned_answers_for(session,job.apply_url,profile)
            record_event(session,job.id,"prepared","prepared",f"Probability {metric.probability}%; difficulty {metric.difficulty}%.",application_id=existing.id)
            record_event(session,job.id,"applying","attempt_started","LangGraph application attempt started.",application_id=existing.id)

            result=run_application_graph(job=job,doc=apply_doc,cfg=cfg,score=existing.score,draft=existing.draft,learned_answers=learned)
            state=result.get("status","failed")
            if state=="below_threshold":
                stats["belowThreshold"]+=1;session.commit();continue

            stats["processed"]+=1
            if state=="failed":
                kind=classify_failure(result.get("reason",""))
                if kind in {"timeout","temporary_server","selector","confirmation"}:
                    stats["retried"]+=1
                    record_event(session,job.id,"applying","retry",f"Retrying recoverable failure: {kind}",application_id=existing.id)
                    result=run_application_graph(job=job,doc=apply_doc,cfg=cfg,score=existing.score,draft=existing.draft,learned_answers=learned)
                    state=result.get("status","failed")

            store_artifacts(session,existing.id,result)
            if state=="applied":
                existing.status="applied";existing.receipt=result.get("receipt","browser-confirmed")
                mark_status(session,job.id,"submitted",existing.receipt);stats["applied"]+=1
                record_event(session,job.id,"submitted","confirmed",existing.receipt,application_id=existing.id,meta=result.get("meta") or {})
            elif state in {"needs_human","ready"}:
                reason=result.get("reason","Review required")
                existing.status="needs_human";existing.receipt=reason
                mark_status(session,job.id,"not_submitted",reason);stats["needsAttention"]+=1
                remember_blocker(session,job,reason)
                record_event(session,job.id,"applying",classify_failure(reason),reason,application_id=existing.id)
            else:
                reason=result.get("reason","Automation failed")
                existing.status="failed";existing.receipt=reason;stats["failed"]+=1
                record_event(session,job.id,"applying",classify_failure(reason),reason,application_id=existing.id)
            session.commit()

        ensure_followup_drafts(session)
        process_recruiter_mail(session,doc,stats)
        session.commit()

    return stats

if __name__=="__main__":
    print(run())
