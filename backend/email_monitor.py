"""Recruiter-email monitoring helpers.

Uses IMAP only when ENABLE_EMAIL_MONITOR=true and credentials are configured.
No message is sent automatically by this module.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime


def _decode(value):
    parts=[]
    for data,enc in decode_header(value or ""):
        if isinstance(data,bytes):
            parts.append(data.decode(enc or "utf-8",errors="replace"))
        else:parts.append(str(data))
    return "".join(parts)


def is_bulk_message(message):
    subject = message.get("subject", "").lower()
    return bool(message.get("bulk")) or any(term in subject for term in (
        "might interest you", "recommended jobs", "recommended projects", "job alert",
        "jobs for you", "projects for you", "new jobs", "newsletter", "weekly digest",
        "daily digest", "job recommendations", "matched jobs",
    ))


def classify_message(subject:str,body:str)->tuple[str,bool]:
    if is_bulk_message({"subject":subject}):return "job_recommendation",False
    t=(subject+" "+body).lower()
    if any(x in t for x in ("interview","schedule a call","meet with","technical interview","recruiter call")):
        return "interview",True
    if any(x in t for x in ("assessment","coding test","take-home","hackerrank","codility")):
        return "assessment",True
    if any(x in t for x in ("unfortunately","not moving forward","other candidates","regret to inform")):
        return "rejection",False
    if any(x in t for x in ("offer letter","pleased to offer","offer of employment")):
        return "offer",True
    if any(x in t for x in ("application received","thank you for applying","we received your application")):
        return "receipt",False
    return "recruiter_reply",True


def fetch_recent_messages(limit:int=200)->list[dict]:
    enabled=os.getenv("ENABLE_EMAIL_MONITOR","false").lower() in {"1","true","yes","on"}
    if not enabled:return []
    user=os.getenv("IMAP_USERNAME") or os.getenv("SMTP_USERNAME")
    password=os.getenv("IMAP_PASSWORD") or os.getenv("SMTP_PASSWORD")
    host=os.getenv("IMAP_HOST","imap.gmail.com")
    if not user or not password:return []
    out=[]
    conn=imaplib.IMAP4_SSL(host)
    try:
        conn.login(user,password);conn.select("INBOX", readonly=True)
        status,data=conn.search(None,"SINCE",(datetime.now(timezone.utc)-timedelta(days=14)).strftime("%d-%b-%Y"))
        if status!="OK":return []
        ids=(data[0].split() if data and data[0] else [])[-limit:]
        for mid in ids:
            status,msgdata=conn.fetch(mid,"(BODY.PEEK[])")
            if status!="OK" or not msgdata:continue
            raw=next((x[1] for x in msgdata if isinstance(x,tuple)),b"")
            msg=email.message_from_bytes(raw)
            subject=_decode(msg.get("Subject",""))
            sender=_decode(msg.get("From",""))
            body=""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type()=="text/plain" and "attachment" not in (part.get("Content-Disposition","").lower()):
                        try:body+=part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8",errors="replace")
                        except Exception:pass
            else:
                try:body=msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8",errors="replace")
                except Exception:body=""
            try:
                received_at=parsedate_to_datetime(msg.get("Date", ""))
                if received_at.tzinfo is None:received_at=received_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):received_at=datetime.now(timezone.utc)
            kind,action=classify_message(subject,body)
            out.append({"bulk":bool(msg.get("List-Id")) or msg.get("Precedence", "").lower() in {"bulk", "list"},"sender":sender[:320],"subject":subject[:500],"body":body[:12000],"classification":kind,"action_required":action,"received_at":received_at})
    finally:
        try:conn.logout()
        except Exception:pass
    return out


def match_application(message,applications_with_jobs):
    # Never associate marketing digests with applications, even if their body
    # happens to mention a company or assessment keyword.
    if is_bulk_message(message):return None
    words=set(re.findall(r"[a-z0-9]+", (message.get("sender","")+" "+message.get("subject","")+" "+message.get("body","")).lower()))
    candidates=[]
    for app,job in applications_with_jobs:
        company=set(re.findall(r"[a-z0-9]+",(job.company or "").lower()))-{
            "technologies","technology","solutions","company","labs","group","inc","ltd","the","and"}
        # Require every company word as a whole word. Role overlap alone is
        # not evidence that an employer contacted this applicant.
        if not company or not company.issubset(words):continue
        title=set(re.findall(r"[a-z0-9]+",(job.title or "").lower()))-{
            "software","engineer","developer","remote","job","role","full","stack","backend","frontend"}
        candidates.append((len(title & words),app,job))
    candidates.sort(key=lambda x:x[0],reverse=True)
    if not candidates:return None
    if len(candidates)>1 and candidates[0][0]==candidates[1][0]:return None
    return candidates[0][1],candidates[0][2]
