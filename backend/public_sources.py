"""Public job-board discovery adapters for Career Atlas.

Only documented/public feeds are used. Each adapter returns normalized Job fields.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from main import Job

def _text(value):
    if value is None:
        return ""
    value=str(value)
    value=re.sub(r"<[^>]+>"," ",value)
    value=html.unescape(value)
    return re.sub(r"\s+"," ",value).strip()

def _https(url):
    try:
        p=urlparse(url or "")
        return url if p.scheme=="https" and p.hostname else ""
    except Exception:
        return ""

def _store(session, rows):
    added=0
    for row in rows:
        url=_https(row.get("url",""))
        apply_url=_https(row.get("apply_url","")) or url
        if not url or not row.get("title"):
            continue
        if session.scalar(select(Job.id).where(Job.url==url)):
            continue
        session.add(Job(
            url=url,
            apply_url=apply_url,
            company=_text(row.get("company") or "Unknown")[:160],
            title=_text(row.get("title"))[:300],
            location=_text(row.get("location") or "Remote")[:200],
            description=_text(row.get("description"))[:8000],
            provider=_text(row.get("provider"))[:30],
        ))
        added+=1
    session.commit()
    return added

def remoteok(client):
    r=client.get("https://remoteok.com/api",headers={"User-Agent":"CareerAtlas/2.0 (+job-search assistant)"})
    r.raise_for_status()
    data=r.json()
    rows=[]
    for x in data if isinstance(data,list) else []:
        if not isinstance(x,dict) or not x.get("position"):
            continue
        rows.append(dict(
            company=x.get("company",""),
            title=x.get("position",""),
            location=x.get("location") or "Remote / Worldwide",
            description=x.get("description",""),
            url=x.get("url") or x.get("apply_url") or "",
            apply_url=x.get("apply_url") or x.get("url") or "",
            provider="RemoteOK",
        ))
    return rows

def remotive(client):
    r=client.get("https://remotive.com/api/remote-jobs")
    r.raise_for_status()
    data=r.json()
    rows=[]
    for x in data.get("jobs",[]) if isinstance(data,dict) else []:
        rows.append(dict(
            company=x.get("company_name",""),
            title=x.get("title",""),
            location=x.get("candidate_required_location") or "Remote",
            description=x.get("description",""),
            url=x.get("url",""),
            apply_url=x.get("url",""),
            provider="Remotive",
        ))
    return rows

def jobicy(client):
    r=client.get("https://jobicy.com/api/v2/remote-jobs",params={"count":200})
    r.raise_for_status()
    data=r.json()
    rows=[]
    for x in data.get("jobs",[]) if isinstance(data,dict) else []:
        rows.append(dict(
            company=x.get("companyName") or x.get("company") or "",
            title=x.get("jobTitle") or x.get("title") or "",
            location=x.get("jobGeo") or x.get("geo") or "Remote",
            description=x.get("jobDescription") or x.get("description") or "",
            url=x.get("url",""),
            apply_url=x.get("url",""),
            provider="Jobicy",
        ))
    return rows

def himalayas(client):
    r=client.get("https://himalayas.app/jobs/api",params={"limit":20})
    r.raise_for_status()
    data=r.json()
    rows=[]
    items=(data.get("jobs") or data.get("data") or []) if isinstance(data,dict) else []
    for x in items:
        company=x.get("companyName") or (x.get("company") or {}).get("name","") if isinstance(x.get("company"),dict) else x.get("company","")
        location=x.get("locationRestriction") or x.get("location") or "Remote"
        if isinstance(location,list): location=", ".join(str(v) for v in location)
        rows.append(dict(
            company=company,
            title=x.get("title",""),
            location=location,
            description=x.get("description") or x.get("content") or "",
            url=x.get("url") or x.get("applicationUrl") or "",
            apply_url=x.get("applicationUrl") or x.get("url") or "",
            provider="Himalayas",
        ))
    return rows

def weworkremotely(client):
    r=client.get("https://weworkremotely.com/remote-jobs.rss")
    r.raise_for_status()
    root=ET.fromstring(r.text)
    rows=[]
    for item in root.findall(".//item"):
        def val(tag):
            el=item.find(tag)
            return (el.text or "") if el is not None else ""
        title=val("title")
        company=""
        role=title
        if ":" in title:
            company,role=title.split(":",1)
        rows.append(dict(
            company=company.strip() or "We Work Remotely employer",
            title=role.strip(),
            location="Remote",
            description=val("description"),
            url=val("link"),
            apply_url=val("link"),
            provider="WWR",
        ))
    return rows

def arbeitnow(client):
    r=client.get("https://www.arbeitnow.com/api/job-board-api")
    r.raise_for_status()
    data=r.json()
    rows=[]
    for x in data.get("data",[]) if isinstance(data,dict) else []:
        if not x.get("remote"):
            continue
        rows.append(dict(
            company=x.get("company_name",""),
            title=x.get("title",""),
            location=x.get("location") or "Remote",
            description=x.get("description",""),
            url=x.get("url",""),
            apply_url=x.get("url",""),
            provider="Arbeitnow",
        ))
    return rows

PUBLIC_ADAPTERS=(
    ("RemoteOK",remoteok),
    ("Remotive",remotive),
    ("Jobicy",jobicy),
    ("Himalayas",himalayas),
    ("We Work Remotely",weworkremotely),
    ("Arbeitnow",arbeitnow),
)

def scan_public_sources(session, client: httpx.Client):
    added=0
    details={}
    errors=[]
    for name,adapter in PUBLIC_ADAPTERS:
        try:
            rows=adapter(client)
            count=_store(session,rows)
            details[name]={"fetched":len(rows),"added":count}
            added+=count
        except Exception as exc:
            session.rollback()
            errors.append(f"{name}: {type(exc).__name__}")
            details[name]={"fetched":0,"added":0,"error":type(exc).__name__}
    return {"added":added,"details":details,"errors":errors}
