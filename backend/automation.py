"""Career Atlas automation helpers.

Fail-safe browser automation: unknown/sensitive questions, CAPTCHA, assessments,
or missing verified profile data stop submission and return needs_human.
"""
from __future__ import annotations
import json, os, re, smtplib, tempfile
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

SENSITIVE_PATTERNS=("authorized to work","work authorization","visa sponsorship","sponsorship",
"security clearance","criminal","conviction","disability","veteran","gender","race","ethnicity",
"salary expectation","expected salary","desired salary","assessment","test","captcha")

@dataclass
class AutomationConfig:
    enabled: bool=False
    min_match_score: int=72
    daily_limit: int=15
    remote_only: bool=True
    allow_email: bool=True
    allow_browser: bool=True
    allow_indeed: bool=True
    allow_glassdoor: bool=True
    auto_submit_browser: bool=False

    @classmethod
    def from_env(cls):
        yes=lambda key,default=False: os.getenv(key,str(default)).strip().lower() in {"1","true","yes","on"}
        return cls(
            enabled=yes("AUTO_APPLY",False),
            min_match_score=int(os.getenv("MIN_MATCH_SCORE","72")),
            daily_limit=int(os.getenv("MAX_APPLICATIONS_PER_DAY","15")),
            remote_only=yes("REMOTE_ONLY",True),
            allow_email=yes("ALLOW_EMAIL_APPLICATIONS",True),
            allow_browser=yes("ALLOW_BROWSER_APPLICATIONS",True),
            allow_indeed=yes("ALLOW_INDEED",True),
            allow_glassdoor=yes("ALLOW_GLASSDOOR",True),
            auto_submit_browser=yes("AUTO_SUBMIT_BROWSER",False),
        )

def load_profile():
    raw=os.getenv("APPLICANT_PROFILE_JSON","").strip()
    if not raw:return {}
    try:
        data=json.loads(raw); return data if isinstance(data,dict) else {}
    except json.JSONDecodeError:return {}

def find_application_email(text):
    candidates=re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",text or "")
    for address in candidates:
        low=address.lower()
        if not any(x in low for x in ("example.com","example.org","noreply","no-reply")):return address
    return None

def needs_human(question):
    q=(question or "").lower()
    return any(x in q for x in SENSITIVE_PATTERNS)

def email_subject(job):
    name=load_profile().get("name","Applicant")
    return f"Application — {job.title} — {name}"

def naturalize_draft(job,draft):
    draft=(draft or "").strip()
    for old,new in {
        "I am thrilled to apply":"I am applying",
        "I am excited to apply":"I am applying",
        "I am passionate about":"I have focused my work on",
        "diverse skillset":"technical background",
    }.items(): draft=draft.replace(old,new)
    if job.company and job.company.lower() not in draft.lower():
        draft=f"Dear Hiring Team at {job.company},\n\n"+draft
    return draft[:5000]

def smtp_ready():
    return all(os.getenv(k) for k in ("SMTP_HOST","SMTP_PORT","SMTP_USERNAME","SMTP_PASSWORD","SMTP_FROM"))

def send_email_application(to_address,subject,body,filename,pdf):
    if not smtp_ready():raise RuntimeError("SMTP is not configured")
    msg=EmailMessage(); msg["From"]=os.environ["SMTP_FROM"]; msg["To"]=to_address; msg["Subject"]=subject
    msg.set_content(body)
    msg.add_attachment(pdf,maintype="application",subtype="pdf",filename=filename or "resume.pdf")
    with smtplib.SMTP(os.environ["SMTP_HOST"],int(os.environ["SMTP_PORT"]),timeout=30) as server:
        server.starttls(); server.login(os.environ["SMTP_USERNAME"],os.environ["SMTP_PASSWORD"]); server.send_message(msg)
    return f"email:{to_address}"

def _fill(page,label,value):
    if not value:return False
    try:
        loc=page.get_by_label(re.compile(label,re.I))
        if loc.count(): loc.first.fill(str(value)); return True
    except Exception:pass
    return False

def _select_known_answer(page,label_pattern,value):
    if not value:return False
    try:
        field=page.get_by_label(re.compile(label_pattern,re.I))
        if field.count():
            first=field.first
            tag=first.evaluate("(el)=>el.tagName.toLowerCase()")
            if tag=="select":
                try:first.select_option(label=re.compile(rf"^{re.escape(str(value))}$",re.I));return True
                except Exception:
                    try:first.select_option(value=str(value));return True
                    except Exception:pass
    except Exception:pass
    try:
        radio=page.get_by_role("radio",name=re.compile(rf"^{re.escape(str(value))}$",re.I))
        if radio.count():radio.first.check();return True
    except Exception:pass
    try:
        option=page.get_by_text(re.compile(rf"^{re.escape(str(value))}$",re.I),exact=True)
        if option.count():option.first.click();return True
    except Exception:pass
    return False

def _stop_signal(page):
    text=(page.locator("body").inner_text(timeout=5000) or "")[:30000].lower()
    if "captcha" in text or "verify you are human" in text or "security check" in text:
        return "CAPTCHA or anti-bot verification detected"
    if "assessment" in text and ("start assessment" in text or "take assessment" in text):
        return "Employer assessment detected"
    return None

def apply_with_playwright(url,pdf,filename,draft):
    profile=load_profile()
    if not profile:return {"status":"needs_human","reason":"APPLICANT_PROFILE_JSON is not configured"}
    try:from playwright.sync_api import sync_playwright
    except Exception:return {"status":"failed","reason":"Playwright is not installed"}
    with tempfile.TemporaryDirectory() as td:
        resume_path=Path(td)/(filename or "resume.pdf"); resume_path.write_bytes(pdf)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True); page=browser.new_page()
            try:
                page.goto(url,wait_until="domcontentloaded",timeout=45000)
                signal=_stop_signal(page)
                if signal:return {"status":"needs_human","reason":signal}
                _fill(page,r"full.?name|name",profile.get("name",""))
                _fill(page,r"email",profile.get("email","")); _fill(page,r"phone|mobile",profile.get("phone",""))
                _fill(page,r"location|city",profile.get("location","")); _fill(page,r"linkedin",profile.get("linkedin",""))
                _fill(page,r"github",profile.get("github","")); _fill(page,r"portfolio|website",profile.get("portfolio",""))
                _fill(page,r"cover.?letter|message|additional information",draft)
                uploads=page.locator('input[type="file"]')
                if uploads.count():uploads.first.set_input_files(str(resume_path))
                # Use explicit user-provided demographic answers only when present.
                gender=os.getenv("APPLICANT_GENDER","").strip() or str(profile.get("gender","")).strip()
                if gender:
                    _select_known_answer(page,r"gender",gender)

                ethnicity_raw=os.getenv("APPLICANT_ETHNICITY_OPTIONS","").strip()
                ethnicity_options=[x.strip() for x in ethnicity_raw.split("|") if x.strip()]
                ethnicity_answer=""
                for option in ethnicity_options:
                    if _select_known_answer(page,r"race|ethnicity",option):
                        ethnicity_answer=option
                        break

                labels=page.locator("label")
                for i in range(min(labels.count(),80)):
                    text=(labels.nth(i).inner_text() or "").strip()
                    if not text:
                        continue
                    low=text.lower()
                    if "gender" in low and gender:
                        continue
                    if ("race" in low or "ethnicity" in low) and ethnicity_answer:
                        continue
                    if needs_human(text):
                        return {"status":"needs_human","reason":f"Sensitive/uncertain question: {text[:180]}"}
                if not AutomationConfig.from_env().auto_submit_browser:
                    return {"status":"ready","reason":"Form filled; AUTO_SUBMIT_BROWSER is disabled"}
                buttons=page.get_by_role("button",name=re.compile(r"submit|apply|send application",re.I))
                if not buttons.count():return {"status":"needs_human","reason":"No unambiguous submit button found"}
                buttons.last.click(); page.wait_for_timeout(2500)
                final=(page.locator("body").inner_text(timeout=5000) or "").lower()
                if any(x in final for x in ("application submitted","thank you for applying","application received")):
                    return {"status":"applied","receipt":page.url}
                return {"status":"needs_human","reason":"Submission confirmation was not detected"}
            except Exception as exc:return {"status":"failed","reason":f"{type(exc).__name__}: {str(exc)[:220]}"}
            finally:browser.close()

def apply_with_selenium(url,pdf,filename,draft):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except Exception:return {"status":"failed","reason":"Selenium is not installed"}
    options=Options(); options.add_argument("--headless=new"); options.add_argument("--no-sandbox"); options.add_argument("--disable-dev-shm-usage")
    driver=webdriver.Chrome(options=options)
    try:
        driver.get(url); body=(driver.page_source or "").lower()
        if "captcha" in body or "verify you are human" in body:return {"status":"needs_human","reason":"CAPTCHA or anti-bot verification detected"}
        return {"status":"ready","reason":"Selenium fallback opened the application; Playwright remains the primary form filler"}
    except Exception as exc:return {"status":"failed","reason":f"{type(exc).__name__}: {str(exc)[:220]}"}
    finally:driver.quit()

def provider_allowed(provider,config):
    p=(provider or "").lower()
    if "indeed" in p:return config.allow_indeed
    if "glassdoor" in p:return config.allow_glassdoor
    return True

def host_allowed(url):
    host=(urlparse(url).hostname or "").lower()
    return bool(host and not host.endswith(".invalid"))
