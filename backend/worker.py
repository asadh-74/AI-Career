"""Scheduled Career Atlas automation worker."""
from __future__ import annotations

from datetime import datetime, timezone
import re

import httpx
from sqlalchemy import select

from automation import (
    AutomationConfig, apply_with_playwright, apply_with_selenium,
    email_subject, find_application_email, host_allowed, naturalize_draft,
    provider_allowed, send_email_application,
)
from main import (
    Application, Document, Job, JobStatus, SessionLocal,
    ai_prepare, scan,
)
from public_sources import scan_public_sources

TARGET_TITLE_TERMS = (
    # Core software roles
    "software engineer", "software developer", "application developer",
    "python engineer", "python developer", "python",
    "backend engineer", "backend developer", "back-end engineer", "back-end developer",
    "backend", "back-end", "fastapi", "api engineer",
    "frontend engineer", "frontend developer", "front-end engineer", "front-end developer",
    "frontend", "front-end", "web developer",
    "full stack", "full-stack", "fullstack",

    # AI / automation / data roles
    "ai engineer", "machine learning", "ml engineer", "automation", "agentic",
    "artificial intelligence", "platform engineer", "data engineer",

    # Embedded / IoT roles
    "embedded", "iot", "computer vision",
)

def mark_status(session, job_id, status, note):
    row=session.scalar(select(JobStatus).where(JobStatus.job_id==job_id))
    if not row:
        row=JobStatus(job_id=job_id,status=status,note=note)
        session.add(row)
    row.status=status
    row.note=note[:1000]
    row.updated_at=datetime.now(timezone.utc)

def remote_eligible(location: str) -> bool:
    """Accept remote roles realistically available from Pakistan.

    We intentionally do not treat EMEA-only or Americas-only roles as eligible.
    APAC, Asia, Pakistan, Worldwide, Anywhere and explicit Remote are accepted.
    """
    loc=(location or "").strip().lower()
    if not loc:
        return False
    if any(x in loc for x in (
        "remote",
        "home based - worldwide",
        "home-based - worldwide",
        "worldwide",
        "anywhere",
        "global",
    )):
        return True
    if any(x in loc for x in ("pakistan", "apac", "asia")) and any(
        x in loc for x in ("home based", "home-based", "distributed", "virtual")
    ):
        return True
    return False

def title_relevant(title: str) -> bool:
    t=(title or "").lower()
    excluded=(
        "director", "vice president", "vp ", "head of ", "principal",
        "staff engineer", "engineering manager", "sales manager",
        "sales director", "account manager", "solutions architecture manager",
        "senior manager",
    )
    if any(term in t for term in excluded):
        return False
    return any(term in t for term in TARGET_TITLE_TERMS)

def run():
    cfg=AutomationConfig.from_env()
    if not cfg.enabled:
        return {"status":"disabled","processed":0}

    stats={
        "status":"ok",
        "sourcesScanned":0,
        "newJobs":0,
        "scanErrors":[],
        "publicSources":{},
        "jobsConsidered":0,
        "remoteEligible":0,
        "titleRelevant":0,
        "scored":0,
        "belowThreshold":0,
        "processed":0,
        "applied":0,
        "needsAttention":0,
        "failed":0,
        "alreadyApplied":0,
    }

    with SessionLocal() as session:
        # Always refresh configured Greenhouse/Lever boards before applying.
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                scan_result=scan(session, client)
                public_result=scan_public_sources(session, client)
            stats["newJobs"]=scan_result.get("added",0)+public_result.get("added",0)
            stats["scanErrors"]=scan_result.get("errors",[])+public_result.get("errors",[])
            stats["publicSources"]=public_result.get("details",{})
            # Count active company sources plus public feeds.
            from main import Source
            company_sources=len(session.scalars(select(Source).where(Source.active==True)).all())
            stats["sourcesScanned"]=company_sources+len(stats["publicSources"])
        except Exception as exc:
            stats["scanErrors"]=[f"scan failed: {type(exc).__name__}: {str(exc)[:180]}"]

        doc=session.scalar(select(Document).where(Document.kind=="Resume"))
        if not doc:
            doc=session.scalar(select(Document).where(Document.kind=="CV"))
        if not doc:
            return {**stats,"status":"needs_setup","reason":"Upload a Resume or CV first"}

        # Recent jobs first. Cap expensive Gemini scoring so a scheduled run stays bounded.
        jobs=session.scalars(select(Job).order_by(Job.found_at.desc()).limit(300)).all()
        max_to_score=max(30, min(60, cfg.daily_limit*2))

        for job in jobs:
            stats["jobsConsidered"]+=1
            if stats["processed"]>=cfg.daily_limit or stats["scored"]>=max_to_score:
                break

            if cfg.remote_only and not remote_eligible(job.location):
                continue
            stats["remoteEligible"]+=1

            if not title_relevant(job.title):
                continue
            stats["titleRelevant"]+=1

            if not provider_allowed(job.provider,cfg) or not host_allowed(job.apply_url):
                continue

            existing=session.scalar(select(Application).where(Application.job_id==job.id))
            if existing and existing.status=="applied":
                stats["alreadyApplied"]+=1
                continue
            sent=session.scalar(
                select(JobStatus).where(
                    JobStatus.job_id==job.id,
                    JobStatus.status=="submitted"
                )
            )
            if sent:
                stats["alreadyApplied"]+=1
                continue

            needs_rescore = (
                existing is not None
                and (existing.rationale or "").startswith("Local keyword estimate")
            )
            if not existing or needs_rescore:
                score,rationale,draft=ai_prepare(job,doc)
                if existing:
                    existing.document_id=doc.id
                    existing.score=score
                    existing.rationale=rationale
                    existing.draft=draft
                else:
                    existing=Application(
                        job_id=job.id,
                        document_id=doc.id,
                        score=score,
                        rationale=rationale,
                        draft=draft,
                    )
                    session.add(existing)
                session.flush()
                stats["scored"]+=1

            if existing.score<cfg.min_match_score:
                stats["belowThreshold"]+=1
                session.commit()
                continue

            stats["processed"]+=1
            draft=naturalize_draft(job,existing.draft)
            address=find_application_email(job.description)
            result=None

            if address and cfg.allow_email:
                try:
                    receipt=send_email_application(
                        address,email_subject(job),draft,doc.filename,doc.pdf
                    )
                    result={"status":"applied","receipt":receipt}
                except Exception as exc:
                    result={
                        "status":"failed",
                        "reason":f"Email failed: {type(exc).__name__}: {str(exc)[:180]}"
                    }

            if result is None and cfg.allow_browser:
                result=apply_with_playwright(
                    job.apply_url,doc.pdf,doc.filename,draft
                )
                if result.get("status")=="failed":
                    fallback=apply_with_selenium(
                        job.apply_url,doc.pdf,doc.filename,draft
                    )
                    if fallback.get("status")!="failed":
                        result=fallback

            if result is None:
                result={"status":"needs_human","reason":"No permitted application route"}

            state=result.get("status","failed")
            if state=="applied":
                existing.status="applied"
                existing.receipt=result.get("receipt","browser-confirmed")
                mark_status(session,job.id,"submitted",existing.receipt)
                stats["applied"]+=1
            elif state in {"needs_human","ready"}:
                existing.status="needs_human"
                existing.receipt=result.get("reason","Review required")
                mark_status(session,job.id,"not_submitted",existing.receipt)
                stats["needsAttention"]+=1
            else:
                existing.status="failed"
                existing.receipt=result.get("reason","Automation failed")
                stats["failed"]+=1

            session.commit()

    return stats

if __name__=="__main__":
    print(run())
