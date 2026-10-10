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
from canonical_resume import CANONICAL_RESUME_TEXT, RESUME_PROFILE_VERSION, build_canonical_resume_pdf
from v3_models import (
    ApplicationArtifact, ApplicationEvent, EmployerMessage, FollowUpDraft,
    InterviewPrep, JobMetric, QuestionMemory, ResearchResult, ResumeVariant, ReviewAnswer,
    classify_failure, dimensional_scores, extract_review_questions, job_fingerprint, quality_threshold,
    record_event, strategy_select,
)

TARGET_TITLE_TERMS=(
    "software engineer","software developer","application developer","python engineer","python developer","python",
    "backend engineer","backend developer","back-end engineer","back-end developer","backend","back-end","fastapi","api engineer",
    "frontend engineer","frontend developer","front-end engineer","front-end developer","frontend","front-end","web developer",
    "full stack","full-stack","fullstack","ai engineer","machine learning engineer","ml engineer","automation engineer",
    "agentic ai","agentic engineer","artificial intelligence engineer","platform engineer","data engineer",
    "ai integrator","ai integration","workflow automation","integration engineer","mlops engineer",
)

SOFTWARE_ONLY_EXCLUDED=(
    "data scientist","data analyst","business analyst","research scientist","embedded","firmware","iot","hardware",
    "electrical","electronics","support engineer","technical support","solutions engineer","sales","account executive",
    "customer success","product manager","project manager","qa analyst","manual tester",
)

def mark_status(session,job_id,status,note):
    row=session.scalar(select(JobStatus).where(JobStatus.job_id==job_id))
    if not row:
        row=JobStatus(job_id=job_id,status=status,note=note);session.add(row)
    row.status=status;row.note=(note or "")[:1000];row.updated_at=datetime.now(timezone.utc)
    return row

def remote_eligible(job)->bool:
    """Respect REMOTE_ONLY without treating every Pakistan role as remote."""
    loc=(getattr(job,"location","") or "").strip().lower()
    title=(getattr(job,"title","") or "").lower()
    desc=(getattr(job,"description","") or "").lower()[:1800]
    text=" ".join((loc,title,desc))
    if not text.strip():return False
    if any(x in text for x in ("remote","home based","home-based","work from home","work-from-home","distributed","virtual","worldwide","anywhere","global")):
        return True
    if any(x in loc for x in ("apac","asia")) and any(x in text for x in ("remote","home based","distributed","virtual")):
        return True
    return False

def title_relevant(title:str)->bool:
    t=(title or "").lower()
    excluded=("director","vice president","vp ","head of ","principal","staff engineer","engineering manager",
              "sales manager","sales director","account manager","solutions architecture manager","senior ","sr. ","sr ","lead ")
    if any(x in t for x in excluded):
        return False
    if os.getenv("SOFTWARE_ONLY","false").lower() in {"1","true","yes","on"}:
        if any(x in t for x in SOFTWARE_ONLY_EXCLUDED):
            return False
    return any(x in t for x in TARGET_TITLE_TERMS)
AGGREGATOR_PROVIDERS={"wwr","arbeitnow","remotive","jobicy","remoteok","himalayas"}

def direct_apply_priority(job)->int:
    """Prefer direct forms that Career Atlas can complete reliably."""
    host=(urlparse(job.apply_url or "").hostname or "").lower()
    provider=(job.provider or "").lower()
    company=(job.company or "").lower()
    # Canonical's current Greenhouse forms repeatedly require protected
    # demographic answers; keep the jobs visible but spend auto-submit time elsewhere.
    if company=="canonical":
        return 1
    if provider in {"ashby","lever","levereu","smartrecruiters"}:
        return 6
    if any(x in host for x in ("ashbyhq.com","lever.co","smartrecruiters.com")):
        return 6
    if provider=="greenhouse" or "greenhouse.io" in host:
        return 5
    if any(x in host for x in ("myworkdayjobs.com","workdayjobs.com")):
        return 4
    if provider=="indeed":
        return 3
    if provider not in AGGREGATOR_PROVIDERS:
        return 2
    return 0


def _profile_value(profile,key):
    aliases={
        "full_name":"name","name":"name","email":"email","phone":"phone","mobile":"phone",
        "location":"location","city":"city","country":"country","linkedin":"linkedin","github":"github","portfolio":"portfolio",
        "website":"portfolio","availability":"availability","salary":"salary_expectation_amount",
        "work_authorized":"work_authorized","requires_sponsorship":"requires_sponsorship",
        "university":"university","degree":"degree","degree_name":"degree_name",
        "field_of_study":"field_of_study","graduation_year":"graduation_year",
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

def verified_answer_for_review(profile,label):
    """Resolve a review question only from explicit verified profile facts."""
    low=(label or "").lower()
    mapping=(
        (("linkedin",),"linkedin"),
        (("github",),"github"),
        (("portfolio","personal website","website"),"portfolio"),
        (("country",),"country"),
        (("city",),"city"),
        (("university","college","school"),"university"),
        (("field of study","main field","major","discipline"),"field_of_study"),
        (("degree name","qualification"),"degree_name"),
        (("degree level","degree type","highest degree","education level"),"degree"),
        (("graduation year","expected graduation","year of graduation"),"graduation_year"),
        (("availability","when can you start","start date"),"availability"),
        (("salary expectation","expected salary","desired salary"),"salary_expectation_amount"),
    )
    for tokens,key in mapping:
        if any(token in low for token in tokens):
            value=_profile_value(profile,key)
            if value not in (None,""):return str(value)
    # Explicit booleans are safe to reuse only when present in the profile.
    if any(x in low for x in ("authorized to work","work authorization","legally authorized")):
        value=profile.get("work_authorized")
        if value is not None:return "Yes" if bool(value) else "No"
    if any(x in low for x in ("require sponsorship","need sponsorship","visa sponsorship")):
        value=profile.get("requires_sponsorship")
        if value is not None:return "Yes" if bool(value) else "No"
    return ""

def remember_blocker(session,job,reason):
    host=(urlparse(job.apply_url or "").hostname or "").lower()
    for part in [x.strip() for x in (reason or "").split(" | ") if x.strip()]:
        low=part.lower()
        sensitive="sensitive/uncertain question:" in low
        unknown="unknown required field:" in low
        if not (sensitive or unknown):continue
        label=(part.split(":",1)[1].strip() if ":" in part else part)[:260]
        existing=session.scalar(select(QuestionMemory).where(QuestionMemory.host==host,QuestionMemory.label_key==label))
        if not existing:
            answer_key=""
            l=label.lower()
            for token,key in (
                ("full name","name"),("first name","name"),("email","email"),("phone","phone"),
                ("linkedin","linkedin"),("github","github"),("portfolio","portfolio"),("website","portfolio"),
                ("availability","availability"),("salary","salary"),("location","location"),("city","city"),
                ("country","country"),("university","university"),("college","university"),("school","university"),
                ("degree","degree"),("major","field_of_study"),("field of study","field_of_study"),
                ("graduation year","graduation_year"),("expected graduation","graduation_year"),
            ):
                if token in l:answer_key=key;break
            selector=""
            m=re.search(r'data-testid="([^"]+)"',part)
            if m:selector=f'[data-testid="{m.group(1)}"]'
            existing=QuestionMemory(host=host,label_key=label,answer_key=answer_key,selector_hint=selector,sensitive=sensitive)
            session.add(existing)
        existing.last_seen_at=datetime.now(timezone.utc)

def review_answers_for(session,application_id):
    rows=session.scalars(
        select(ReviewAnswer).where(
            ReviewAnswer.application_id==application_id,
            ReviewAnswer.resolved==False,
            ReviewAnswer.sensitive==False,
        )
    ).all()
    return [
        {"label":row.label_key,"selector_hint":row.selector_hint,"value":row.answer,"sensitive":row.sensitive}
        for row in rows if (row.answer or "").strip()
    ]

def store_review_fields(session,application_id,job,result):
    # Mark old blockers resolved first; anything still blocking will be
    # re-opened below. This keeps the dashboard focused on the current form.
    old=session.scalars(select(ReviewAnswer).where(ReviewAnswer.application_id==application_id)).all()
    for row in old: row.resolved=True

    questions=result.get("review_fields") or extract_review_questions(result.get("reason",""))
    created=0
    for item in questions:
        label=(item.get("label") or "").strip()[:500]
        if not label:continue
        sensitive=bool(item.get("sensitive"))
        selector=(item.get("selector_hint") or "").strip()[:500]
        row=session.scalar(select(ReviewAnswer).where(
            ReviewAnswer.application_id==application_id,
            ReviewAnswer.label_key==label,
        ))
        if not row:
            row=ReviewAnswer(
                application_id=application_id,label_key=label,selector_hint=selector,
                sensitive=sensitive,resolved=False
            )
            session.add(row);created+=1
        else:
            row.selector_hint=selector or row.selector_hint
            row.sensitive=sensitive
            row.resolved=False
        row.updated_at=datetime.now(timezone.utc)
    session.flush()
    return created

def review_is_waiting_for_user(session,application_id):
    rows=session.scalars(select(ReviewAnswer).where(
        ReviewAnswer.application_id==application_id,
        ReviewAnswer.resolved==False,
    )).all()
    return bool(rows) and any(not (r.answer or "").strip() for r in rows)

def resolve_review_fields(session,application_id):
    rows=session.scalars(select(ReviewAnswer).where(ReviewAnswer.application_id==application_id)).all()
    for row in rows:
        row.resolved=True
        # Protected/demographic answers are one-application values. Do not
        # retain them after a successful submission.
        if row.sensitive:
            row.answer=""
        row.updated_at=datetime.now(timezone.utc)

def count_learned_successes(session,job,result):
    used=((result.get("meta") or {}).get("learnedFields") or [])
    if not used:return
    host=(urlparse(job.apply_url or "").hostname or "").lower()
    for label in used:
        row=session.scalar(select(QuestionMemory).where(QuestionMemory.host==host,QuestionMemory.label_key==label))
        if row:row.success_count+=1;row.last_seen_at=datetime.now(timezone.utc)

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
    tailored=build_tailored_resume(job,doc)
    if not row:
        row=ResumeVariant(job_id=job.id,source_document_id=doc.id,filename=tailored.filename,pdf=tailored.pdf,
                          extracted_text=tailored.extracted_text,strategy=f"{RESUME_PROFILE_VERSION} | {tailored.strategy}")
        session.add(row)
    else:
        row.source_document_id=doc.id
        row.filename=tailored.filename
        row.pdf=tailored.pdf
        row.extracted_text=tailored.extracted_text
        row.strategy=f"{RESUME_PROFILE_VERSION} | {tailored.strategy}"
    session.flush()
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
    pairs=session.execute(
        select(Application,Job)
        .join(Job,Job.id==Application.job_id)
        .where(Application.status=="applied")
    ).all()

    # Revalidate records imported by earlier matcher versions. Keep only
    # messages that still have strong evidence for their linked application.
    for old in session.scalars(select(EmployerMessage)).all():
        if old.application_id is None:
            session.delete(old)
            continue
        app_job=next(((a,j) for a,j in pairs if a.id==old.application_id),None)
        if not app_job or not match_application(
            {"sender":old.sender,"subject":old.subject,"body":old.body},
            [app_job],
        ):
            session.delete(old)
    session.flush()
    for prep in session.scalars(select(InterviewPrep)).all():
        has_interview=session.scalar(select(EmployerMessage).where(
            EmployerMessage.application_id==prep.application_id,
            EmployerMessage.classification=="interview"
        ))
        if not has_interview:
            session.delete(prep)

    matched_count=0
    for msg in messages:
        matched=match_application(msg,pairs)
        if not matched:
            continue
        app,job=matched
        duplicate=session.scalar(select(EmployerMessage).where(
            EmployerMessage.application_id==app.id,
            EmployerMessage.sender==msg["sender"],
            EmployerMessage.subject==msg["subject"]
        ))
        if duplicate:continue
        row=EmployerMessage(application_id=app.id,sender=msg["sender"],subject=msg["subject"],body=msg["body"],
                            classification=msg["classification"],action_required=msg["action_required"],received_at=msg["received_at"])
        session.add(row);matched_count+=1
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
    stats["recruiterMessages"]=matched_count

def cleanup_invalid_email_submissions(session):
    """Undo legacy false positives caused by non-recruiting contact emails."""
    bad=("accommodation","accessibility","reasonable","privacy","legal","support","help","security","press","media","billing","compliance")
    fixed=0
    rows=session.execute(
        select(Application,Job,JobStatus)
        .join(Job,Job.id==Application.job_id)
        .join(JobStatus,JobStatus.job_id==Job.id)
        .where(Application.status=="applied",JobStatus.status=="submitted")
    ).all()
    for app,job,status in rows:
        receipt=(app.receipt or "").lower()
        if not receipt.startswith("email:"):continue
        address=receipt.split(":",1)[1]
        local=address.split("@",1)[0]
        if any(x in local for x in bad):
            reason="Legacy email route used a non-recruiting contact; application requires official-form submission."
            app.status="needs_human"
            app.receipt=reason
            status.status="not_submitted"
            status.note=reason
            status.updated_at=datetime.now(timezone.utc)
            record_event(session,job.id,"applying","invalid_email_receipt",reason,application_id=app.id)
            fixed+=1
    if fixed:session.flush()
    return fixed

def ensure_priority_sources(session):
    """Maintain a small curated set of public direct employer boards."""
    specs=(
        ("Hamster Garage","Ashby","hamstergarage"),
        ("Stellic","Ashby","stellic"),
        ("CareerSwift","Ashby","careerswift.ai"),
        ("CrewBloom","Ashby","crewbloom"),
        ("TensorOps","Greenhouse","tensorops"),
        ("Motive","Greenhouse","gomotive"),
        ("Whippy","Ashby","whippy"),
        ("Enveritas","Greenhouse","enveritas"),
    )
    added=0
    for company,provider,slug in specs:
        row=session.scalar(select(Source).where(Source.provider==provider,Source.slug==slug))
        if not row:
            session.add(Source(company=company,provider=provider,slug=slug,active=True))
            added+=1
        elif not row.active:
            row.active=True
    if added:session.commit()
    return added

def run(target_job_id_override=None):
    cfg=AutomationConfig.from_env()
    if not cfg.enabled:return {"status":"disabled","processed":0}
    stats={"status":"ok","sourcesScanned":0,"newJobs":0,"scanErrors":[],"publicSources":{},"jobsConsidered":0,
           "remoteEligible":0,"titleRelevant":0,"scored":0,"belowThreshold":0,"processed":0,"applied":0,
           "needsAttention":0,"failed":0,"alreadyApplied":0,"duplicatesSkipped":0,"retried":0}

    with SessionLocal() as session:
        stats["invalidEmailSubmissionsCorrected"]=cleanup_invalid_email_submissions(session)
        stats["prioritySourcesAdded"]=ensure_priority_sources(session)
        profile=load_profile()
        cfg.min_match_score=quality_threshold(session,cfg.min_match_score)
        stats["qualityThreshold"]=cfg.min_match_score

        target_job_id=(str(target_job_id_override) if target_job_id_override is not None else (os.getenv("TARGET_JOB_ID") or "")).strip()
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

        stored_doc=session.scalar(select(Document).where(Document.kind=="Resume")) or session.scalar(select(Document).where(Document.kind=="CV"))
        if not stored_doc:return {**stats,"status":"needs_setup","reason":"Upload a Resume or CV first"}
        # Keep the live Career Atlas master resume synchronized with the
        # corrected verified software/AI profile.
        canonical_filename="Asad_Hussain_Software_AI_Resume_2026.pdf"
        if stored_doc.filename!=canonical_filename or stored_doc.extracted_text!=CANONICAL_RESUME_TEXT:
            stored_doc.filename=canonical_filename
            stored_doc.extracted_text=CANONICAL_RESUME_TEXT
            stored_doc.pdf=build_canonical_resume_pdf()
            session.commit()
        doc=SimpleNamespace(
            id=stored_doc.id,
            kind=getattr(stored_doc,"kind","Resume"),
            filename=canonical_filename,
            pdf=stored_doc.pdf,
            extracted_text=CANONICAL_RESUME_TEXT,
        )
        stats["resumeProfileVersion"]=RESUME_PROFILE_VERSION

        if target_job_id.isdigit():
            target=session.get(Job,int(target_job_id));jobs=[target] if target else []
        else:
            recent=session.scalars(select(Job).order_by(Job.found_at.desc()).limit(400)).all()
            direct=session.scalars(
                select(Job).where(Job.provider.in_(("Greenhouse","Lever","LeverEU","Ashby","SmartRecruiters")))
                .order_by(Job.found_at.desc()).limit(350)
            ).all()
            by_id={j.id:j for j in recent}
            for j in direct:by_id.setdefault(j.id,j)
            recent=list(by_id.values())
            # Build a cheap first-pass probability for every recent/direct role so the
            # daily strategy spends browser time on the strongest opportunities.
            probability={}
            for candidate in recent:
                if not candidate or not title_relevant(candidate.title):
                    continue
                existing_app=session.scalar(select(Application).where(Application.job_id==candidate.id))
                base=int(existing_app.score) if existing_app else 50
                metric=upsert_metric(session,candidate,doc,profile,base)
                probability[candidate.id]=metric.probability
            recent.sort(key=lambda j:(direct_apply_priority(j),probability.get(j.id,0),j.found_at),reverse=True)
            jobs=strategy_select(recent,160)
            session.flush()

        # Applications for which the user answered only the missing review
        # questions jump to the front of the next LangGraph run.
        retry_jobs=session.scalars(
            select(Job).join(Application,Application.job_id==Job.id)
            .where(Application.status=="ready_for_retry")
            .order_by(Application.created_at.asc())
        ).all()
        if retry_jobs:
            seen=set();ordered=[]
            for candidate in [*retry_jobs,*jobs]:
                if candidate and candidate.id not in seen:
                    ordered.append(candidate);seen.add(candidate.id)
            jobs=ordered
        max_to_score=max(50,min(120,cfg.daily_limit*10))

        pkt=ZoneInfo("Asia/Karachi");now_pkt=datetime.now(pkt)
        day_start_utc=now_pkt.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
        submitted_today=len(session.scalars(select(JobStatus).where(JobStatus.status=="submitted",JobStatus.updated_at>=day_start_utc)).all())
        stats["submittedToday"]=submitted_today
        remaining_today=max(0,cfg.daily_limit-submitted_today);stats["remainingToday"]=remaining_today
        max_attempts=max(30,min(150,remaining_today*8 if remaining_today else 30))

        for job in jobs:
            if not job:continue
            stats["jobsConsidered"]+=1
            # Canonical's current Greenhouse forms require protected
            # demographic answers. Keep them discoverable in the dashboard,
            # but do not spend automatic-application attempts on them.
            if (job.company or "").strip().lower()=="canonical":
                record_event(session,job.id,"discovered","protected_fields_skip","Automatic submission skipped because the employer form requires protected demographic answers.")
                session.commit()
                continue
            if stats["applied"]>=remaining_today or stats["processed"]>=max_attempts or stats["scored"]>=max_to_score:break
            if cfg.remote_only and not remote_eligible(job):continue
            stats["remoteEligible"]+=1
            if not title_relevant(job.title):continue
            stats["titleRelevant"]+=1
            if not provider_allowed(job.provider,cfg) or not host_allowed(job.apply_url):continue

            existing=session.scalar(select(Application).where(Application.job_id==job.id))
            sent=session.scalar(select(JobStatus).where(JobStatus.job_id==job.id,JobStatus.status=="submitted"))
            if (existing and existing.status=="applied") or sent:
                stats["alreadyApplied"]+=1;continue
            if existing and existing.status=="needs_human" and not target_job_id.isdigit() and review_is_waiting_for_user(session,existing.id):
                record_event(session,job.id,"applying","awaiting_review","Waiting for the user to answer only the unresolved review fields.",application_id=existing.id)
                session.commit();continue

            research=research_job(session,job,profile)
            if research.quality_score<45:
                record_event(session,job.id,"discovered","research_skip",research.eligibility_notes);session.commit();continue

            force_rescore=os.getenv("FORCE_RESCORE","false").lower() in {"1","true","yes","on"}
            needs_rescore=existing is not None and (force_rescore or (existing.rationale or "").startswith("Local keyword estimate"))
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

            # Run the more expensive CrewAI research only for roles that
            # already clear the selected application threshold.
            if existing.score>=cfg.min_match_score and os.getenv("CREWAI_LLM","").strip() and research.source!="crewai":
                deep=crew_research(job,profile)
                research.company_summary=deep["company_summary"]
                research.eligibility_notes=deep["eligibility_notes"]
                research.quality_score=int(deep["quality_score"])
                research.source=deep["source"]
                detail=f"CrewAI research: {research.quality_score}/100 ({research.source})."
                if deep.get("crew_error"):
                    detail+=f" Fallback reason: {deep['crew_error']}"
                record_event(session,job.id,"matched","deep_research",detail,application_id=existing.id)

            if duplicate_already_submitted(session,metric,job.id):
                stats["duplicatesSkipped"]+=1
                record_event(session,job.id,"discovered","duplicate_skip","Equivalent role already submitted.",application_id=existing.id)
                session.commit();continue

            # Do not spend time generating tailored PDFs or opening browsers
            # for roles that already fail the selected quality threshold.
            if existing.score<cfg.min_match_score:
                stats["belowThreshold"]+=1
                record_event(session,job.id,"matched","below_threshold",f"Match score {existing.score} is below {cfg.min_match_score}.",application_id=existing.id)
                session.commit();continue

            apply_doc=tailored_doc_for(session,job,doc)
            learned=learned_answers_for(session,job.apply_url,profile)
            learned.extend(review_answers_for(session,existing.id))
            record_event(session,job.id,"prepared","prepared",f"Probability {metric.probability}%; difficulty {metric.difficulty}%.",application_id=existing.id)
            record_event(session,job.id,"applying","attempt_started","LangGraph application attempt started.",application_id=existing.id)

            result=run_application_graph(job=job,doc=apply_doc,cfg=cfg,score=existing.score,draft=existing.draft,learned_answers=learned)
            state=result.get("status","failed")
            if state=="below_threshold":
                stats["belowThreshold"]+=1;session.commit();continue

            # LangGraph human-in-the-loop self-repair: before asking the user,
            # resolve custom ATS fields from verified profile facts and retry once.
            if state in {"needs_human","ready"}:
                questions=result.get("review_fields") or extract_review_questions(result.get("reason",""))
                if questions:
                    store_review_fields(session,existing.id,job,result)
                    rows=session.scalars(select(ReviewAnswer).where(
                        ReviewAnswer.application_id==existing.id,
                        ReviewAnswer.resolved==False,
                    )).all()
                    auto_count=0
                    for row in rows:
                        if row.sensitive or (row.answer or "").strip():
                            continue
                        value=verified_answer_for_review(profile,row.label_key)
                        if value:
                            row.answer=value
                            row.updated_at=datetime.now(timezone.utc)
                            auto_count+=1
                    if auto_count:
                        session.flush()
                        smarter=list(learned)
                        smarter.extend(review_answers_for(session,existing.id))
                        record_event(session,job.id,"applying","review_autofill",
                                     f"Resolved {auto_count} ATS review field(s) from verified profile; LangGraph retrying automatically.",
                                     application_id=existing.id)
                        result=run_application_graph(job=job,doc=apply_doc,cfg=cfg,score=existing.score,draft=existing.draft,learned_answers=smarter)
                        state=result.get("status","failed")

            stats["processed"]+=1
            if state=="failed":
                kind=classify_failure(result.get("reason",""))
                if kind in {"timeout","temporary_server","selector","confirmation"}:
                    stats["retried"]+=1
                    record_event(session,job.id,"applying","retry",f"Retrying recoverable failure: {kind}",application_id=existing.id)
                    result=run_application_graph(job=job,doc=apply_doc,cfg=cfg,score=existing.score,draft=existing.draft,learned_answers=learned)
                    state=result.get("status","failed")

            store_artifacts(session,existing.id,result)
            count_learned_successes(session,job,result)
            if state=="applied":
                existing.status="applied";existing.receipt=result.get("receipt","browser-confirmed")
                mark_status(session,job.id,"submitted",existing.receipt);stats["applied"]+=1
                resolve_review_fields(session,existing.id)
                record_event(session,job.id,"submitted","confirmed",existing.receipt,application_id=existing.id,meta=result.get("meta") or {})
            elif state in {"needs_human","ready"}:
                reason=result.get("reason","Review required")
                existing.status="needs_human";existing.receipt=reason
                mark_status(session,job.id,"not_submitted",reason);stats["needsAttention"]+=1
                remember_blocker(session,job,reason)
                store_review_fields(session,existing.id,job,result)
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

def retry_application(application_id:int):
    """Retry one reviewed application through the same LangGraph pipeline."""
    with SessionLocal() as session:
        app=session.get(Application,application_id)
        if not app:
            return {"status":"not_found"}
        sensitive=session.scalars(select(ReviewAnswer).where(
            ReviewAnswer.application_id==application_id,
            ReviewAnswer.resolved==False,
            ReviewAnswer.sensitive==True,
        )).all()
        if sensitive:
            return {"status":"manual_required","missing":[r.label_key for r in sensitive[:12]]}
        pending=session.scalars(select(ReviewAnswer).where(
            ReviewAnswer.application_id==application_id,
            ReviewAnswer.resolved==False,
            ReviewAnswer.sensitive==False,
        )).all()
        missing=[r.label_key for r in pending if not (r.answer or "").strip()]
        if missing:
            return {"status":"needs_answers","missing":missing[:12]}
        job_id=app.job_id
        record_event(session,job_id,"applying","review_retry_queued","User supplied review answers; LangGraph retry starting.",application_id=application_id)
        session.commit()
    return run(target_job_id_override=job_id)

if __name__=="__main__":
    print(run())
