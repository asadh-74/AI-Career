"""Scheduled Career Atlas automation worker."""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import select
from automation import (
    AutomationConfig, apply_with_playwright, apply_with_selenium,
    email_subject, find_application_email, host_allowed, naturalize_draft,
    provider_allowed, send_email_application,
)
from main import Application, Document, Job, JobStatus, SessionLocal, ai_prepare

def mark_status(session,job_id,status,note):
    row=session.scalar(select(JobStatus).where(JobStatus.job_id==job_id))
    if not row:
        row=JobStatus(job_id=job_id,status=status,note=note); session.add(row)
    row.status=status; row.note=note[:1000]; row.updated_at=datetime.now(timezone.utc)

def run():
    cfg=AutomationConfig.from_env()
    if not cfg.enabled:return {"status":"disabled","processed":0}
    processed=applied=needs_attention=failed=0
    with SessionLocal() as session:
        jobs=session.scalars(select(Job).order_by(Job.found_at.desc()).limit(200)).all()
        for job in jobs:
            if processed>=cfg.daily_limit:break
            if cfg.remote_only and job.location and "remote" not in job.location.lower():continue
            if not provider_allowed(job.provider,cfg) or not host_allowed(job.apply_url):continue
            existing=session.scalar(select(Application).where(Application.job_id==job.id))
            if existing and existing.status=="applied":continue
            sent=session.scalar(select(JobStatus).where(JobStatus.job_id==job.id,JobStatus.status=="submitted"))
            if sent:continue
            doc=session.scalar(select(Document).where(Document.kind=="Resume"))
            if not doc:doc=session.scalar(select(Document).where(Document.kind=="CV"))
            if not doc:return {"status":"needs_setup","reason":"Upload a Resume or CV first","processed":processed}
            if not existing:
                score,rationale,draft=ai_prepare(job,doc)
                existing=Application(job_id=job.id,document_id=doc.id,score=score,rationale=rationale,draft=draft)
                session.add(existing);session.flush()
            if existing.score<cfg.min_match_score:continue
            processed+=1
            draft=naturalize_draft(job,existing.draft)
            address=find_application_email(job.description)
            result=None
            if address and cfg.allow_email:
                try:
                    receipt=send_email_application(address,email_subject(job),draft,doc.filename,doc.pdf)
                    result={"status":"applied","receipt":receipt}
                except Exception as exc:
                    result={"status":"failed","reason":f"Email failed: {type(exc).__name__}: {str(exc)[:180]}"}
            if result is None and cfg.allow_browser:
                result=apply_with_playwright(job.apply_url,doc.pdf,doc.filename,draft)
                if result.get("status")=="failed":
                    fallback=apply_with_selenium(job.apply_url,doc.pdf,doc.filename,draft)
                    if fallback.get("status")!="failed":result=fallback
            if result is None:result={"status":"needs_human","reason":"No permitted application route"}
            state=result.get("status","failed")
            if state=="applied":
                existing.status="applied";existing.receipt=result.get("receipt","browser-confirmed")
                mark_status(session,job.id,"submitted",existing.receipt);applied+=1
            elif state in {"needs_human","ready"}:
                existing.status="needs_human";existing.receipt=result.get("reason","Review required")
                mark_status(session,job.id,"not_submitted",existing.receipt);needs_attention+=1
            else:
                existing.status="failed";existing.receipt=result.get("reason","Automation failed");failed+=1
            session.commit()
    return {"status":"ok","processed":processed,"applied":applied,"needsAttention":needs_attention,"failed":failed}

if __name__=="__main__":
    print(run())
