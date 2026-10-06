"""Recruiter-email monitoring helpers.

Uses IMAP only when ENABLE_EMAIL_MONITOR=true and credentials are configured.
No message is sent automatically by this module.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timezone
from email.header import decode_header


def _decode(value):
    parts=[]
    for data,enc in decode_header(value or ""):
        if isinstance(data,bytes):
            parts.append(data.decode(enc or "utf-8",errors="replace"))
        else:parts.append(str(data))
    return "".join(parts)


def classify_message(subject:str,body:str)->tuple[str,bool]:
    t=(subject+" "+body).lower()
    if any(x in t for x in ("interview","schedule a call","meet with","technical interview","recruiter call")):
        return "interview",True
    if any(x in t for x in ("assessment","coding test","take-home","hackerrank","codility")):
        return "assessment",True
    if any(x in t for x in ("unfortunately","not moving forward","other candidates","regret to inform")):
        return "rejection",False
    if any(x in t for x in ("offer","offer letter","pleased to offer")):
        return "offer",True
    if any(x in t for x in ("application received","thank you for applying","we received your application")):
        return "receipt",False
    return "recruiter_reply",True


def fetch_recent_messages(limit:int=25)->list[dict]:
    enabled=os.getenv("ENABLE_EMAIL_MONITOR","false").lower() in {"1","true","yes","on"}
    if not enabled:return []
    user=os.getenv("IMAP_USERNAME") or os.getenv("SMTP_USERNAME")
    password=os.getenv("IMAP_PASSWORD") or os.getenv("SMTP_PASSWORD")
    host=os.getenv("IMAP_HOST","imap.gmail.com")
    if not user or not password:return []
    out=[]
    conn=imaplib.IMAP4_SSL(host)
    try:
        conn.login(user,password);conn.select("INBOX")
        status,data=conn.search(None,"UNSEEN")
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
            kind,action=classify_message(subject,body)
            out.append({"sender":sender[:320],"subject":subject[:500],"body":body[:12000],"classification":kind,"action_required":action,"received_at":datetime.now(timezone.utc)})
    finally:
        try:conn.logout()
        except Exception:pass
    return out


def match_application(message,applications_with_jobs):
    text=(message.get("subject","")+" "+message.get("body","")).lower()
    best=None;best_score=0
    for app,job in applications_with_jobs:
        score=0
        for token in re.findall(r"[a-z0-9]{4,}",(job.company+" "+job.title).lower()):
            if token in text:score+=1
        if score>best_score:
            best=(app,job);best_score=score
    return best if best_score>=1 else None
