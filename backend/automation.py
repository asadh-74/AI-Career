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

def _fill_css(page,selectors,value):
    if value in (None,""):return False
    for selector in selectors:
        try:
            loc=page.locator(selector)
            if loc.count() and loc.first.is_visible():
                loc.first.fill(str(value));return True
        except Exception:pass
    return False

def _answer_boolean(page,label_pattern,value):
    if value is None:return False
    wanted="yes" if bool(value) else "no"
    try:
        group=page.get_by_label(re.compile(label_pattern,re.I))
        if group.count():
            field=group.first
            tag=field.evaluate("(el)=>el.tagName.toLowerCase()")
            if tag=="select":
                for candidate in (wanted,wanted.title(),str(bool(value)).lower()):
                    try:field.select_option(label=re.compile(rf"^{candidate}$",re.I));return True
                    except Exception:pass
    except Exception:pass
    try:
        labels=page.locator("label")
        for i in range(min(labels.count(),100)):
            txt=(labels.nth(i).inner_text() or "").strip()
            if re.search(label_pattern,txt,re.I):
                parent=labels.nth(i).locator("xpath=..")
                radio=parent.get_by_role("radio",name=re.compile(rf"^{wanted}$",re.I))
                if radio.count():radio.first.check();return True
    except Exception:pass
    return False

def _fill_standard_fields(page,profile,draft,resume_path):
    name=str(profile.get("name","")).strip()
    parts=name.split()
    first=parts[0] if parts else ""
    last=" ".join(parts[1:]) if len(parts)>1 else ""

    _fill(page,r"full.?name|^name$",name)
    _fill(page,r"first.?name|given.?name",first)
    _fill(page,r"last.?name|family.?name|surname",last)
    _fill(page,r"email",profile.get("email",""))
    _fill(page,r"phone|mobile",profile.get("phone",""))
    _fill(page,r"location|city",profile.get("location",""))
    _fill(page,r"linkedin",profile.get("linkedin",""))
    _fill(page,r"github",profile.get("github",""))
    _fill(page,r"portfolio|website|personal.?site",profile.get("portfolio",""))
    _fill(page,r"cover.?letter|message|additional information",draft)

    _fill_css(page,['input[data-testid="apply-name"]','input[name="name"]','input[name*="full_name" i]'],name)
    _fill_css(page,['input[name*="first" i]'],first)
    _fill_css(page,['input[name*="last" i]','input[name*="surname" i]'],last)
    _fill_css(page,['input[type="email"]','input[data-testid="apply-email"]','input[name*="email" i]'],profile.get("email",""))
    _fill_css(page,['input[type="tel"]','input[data-testid="apply-phone"]','input[name*="phone" i]','input[name*="mobile" i]'],profile.get("phone",""))
    _fill_css(page,['input[name*="linkedin" i]'],profile.get("linkedin",""))
    _fill_css(page,['input[name*="github" i]'],profile.get("github",""))
    _fill_css(page,['input[name*="portfolio" i]','input[name*="website" i]'],profile.get("portfolio",""))

    country="Pakistan" if "pakistan" in str(profile.get("location","")).lower() else ""
    if country:
        _select_known_answer(page,r"country|current country|country of residence",country)

    _answer_boolean(page,r"authorized to work|work authorization|legally authorized",profile.get("work_authorized"))
    sponsorship=profile.get("requires_sponsorship")
    if sponsorship is not None:
        _answer_boolean(page,r"require.*sponsorship|need.*sponsorship|visa sponsorship",sponsorship)

    availability=profile.get("availability","")
    if availability:_fill(page,r"availability|start date|when can you start",availability)

    salary=profile.get("salary_expectation_amount")
    if salary:
        currency=profile.get("salary_expectation_currency","")
        period=profile.get("salary_expectation_period","")
        _fill(page,r"salary expectation|expected salary|desired salary|compensation",f"{salary} {currency} {period}".strip())

    uploads=page.locator('input[type="file"]')
    for i in range(min(uploads.count(),3)):
        try:
            if uploads.nth(i).is_visible() or uploads.nth(i).get_attribute("type")=="file":
                uploads.nth(i).set_input_files(str(resume_path))
                break
        except Exception:pass

    # Standard application privacy/terms consent can be accepted as part of
    # the user's explicit request to submit the application.
    checks=page.locator('input[type="checkbox"]')
    for i in range(min(checks.count(),20)):
        try:
            cb=checks.nth(i)
            if cb.is_checked():continue
            parent_text=(cb.locator("xpath=..").inner_text(timeout=1000) or "").lower()
            grand_text=(cb.locator("xpath=../..").inner_text(timeout=1000) or "").lower()
            text=(parent_text+" "+grand_text)[:1200]
            if any(x in text for x in ("privacy policy","privacy","agree to","consent","terms")):
                cb.check()
        except Exception:pass

def _sensitive_or_unknown_required(page):
    root=page
    try:
        submit=_find_submit(page)
        if submit is not None:
            form=submit.locator("xpath=ancestor::form[1]")
            if form.count():
                root=form.first
    except Exception:
        pass

    labels=root.locator("label")
    for i in range(min(labels.count(),120)):
        text=(labels.nth(i).inner_text() or "").strip()
        if text and needs_human(text):
            return f"Sensitive/uncertain question: {text[:180]}"

    required=root.locator("input[required], textarea[required], select[required]")
    safe_tokens=("name","email","phone","mobile","location","city","country","linkedin","github","portfolio","website",
                 "resume","cv","cover","message","additional","authorization","authorized","sponsorship","salary",
                 "availability","start date")
    for i in range(min(required.count(),120)):
        field=required.nth(i)
        try:
            typ=(field.get_attribute("type") or "").lower()
            if typ in ("hidden","submit","button","file","checkbox","radio"):continue
            value=(field.input_value() or "").strip()
            if value:continue
            ident=" ".join(filter(None,[
                field.get_attribute("name"),
                field.get_attribute("id"),
                field.get_attribute("placeholder"),
                field.get_attribute("aria-label"),
                field.get_attribute("data-testid"),
            ])).lower()
            if "newsletter" in ident:
                continue
            if not any(tok in ident for tok in safe_tokens):
                try:
                    html=(field.evaluate("(el)=>el.outerHTML") or "")[:240]
                except Exception:
                    html=""
                return f"Unknown required field: {ident[:180] or 'unlabelled required field'} {html}"
        except Exception:pass
    return None

def _find_submit(page):
    candidates=[
        page.get_by_role("button",name=re.compile(r"submit|apply now|send application|send my application|finish|complete application",re.I)),
        page.locator('button[type="submit"]'),
        page.locator('input[type="submit"]'),
    ]
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible() and loc.first.is_enabled():return loc.first
        except Exception:pass
    return None

def _find_next(page):
    candidates=[
        page.get_by_role("button",name=re.compile(r"^next$|continue|save and continue|proceed",re.I)),
        page.get_by_role("link",name=re.compile(r"^next$|continue|proceed",re.I)),
    ]
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible() and loc.first.is_enabled():return loc.first
        except Exception:pass
    return None

def apply_with_playwright(url,pdf,filename,draft):
    profile=load_profile()
    if not profile:return {"status":"needs_human","reason":"APPLICANT_PROFILE_JSON is not configured"}
    try:from playwright.sync_api import sync_playwright
    except Exception:return {"status":"failed","reason":"Playwright is not installed"}
    with tempfile.TemporaryDirectory() as td:
        resume_path=Path(td)/(filename or "resume.pdf"); resume_path.write_bytes(pdf)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page()
            try:
                page.goto(url,wait_until="domcontentloaded",timeout=45000)

                host=(urlparse(page.url).hostname or "").lower()
                if any(x in host for x in ("weworkremotely.com","remoteok.com","jobicy.com","himalayas.app","arbeitnow.com","remotive.com")):
                    try:
                        apply_link=page.get_by_role("link",name=re.compile(r"apply",re.I))
                        for i in range(min(apply_link.count(),10)):
                            href=apply_link.nth(i).get_attribute("href")
                            if href and not href.startswith("#"):
                                page.goto(href,wait_until="domcontentloaded",timeout=45000)
                                break
                    except Exception:pass

                for _ in range(5):
                    signal=_stop_signal(page)
                    if signal:return {"status":"needs_human","reason":signal}

                    _fill_standard_fields(page,profile,draft,resume_path)
                    blocker=_sensitive_or_unknown_required(page)
                    if blocker:return {"status":"needs_human","reason":blocker}

                    if not AutomationConfig.from_env().auto_submit_browser:
                        return {"status":"ready","reason":"Form filled; AUTO_SUBMIT_BROWSER is disabled"}

                    submit=_find_submit(page)
                    if submit is not None:
                        before=page.url
                        submit.click()
                        page.wait_for_timeout(4500)
                        final=(page.locator("body").inner_text(timeout=7000) or "").lower()
                        confirmations=(
                            "application submitted","thank you for applying","application received",
                            "thanks for applying","successfully submitted","we have received your application",
                            "your application has been submitted","thank you for your application",
                        )
                        if any(x in final for x in confirmations):
                            return {"status":"applied","receipt":page.url}
                        # Some ATS pages navigate to a confirmation URL with little text.
                        if page.url!=before and any(x in page.url.lower() for x in ("thank","success","confirmation","submitted")):
                            return {"status":"applied","receipt":page.url}
                        return {"status":"needs_human","reason":"Submit was clicked but reliable confirmation was not detected"}

                    nxt=_find_next(page)
                    if nxt is not None:
                        nxt.click();page.wait_for_timeout(2500)
                        continue
                    return {"status":"needs_human","reason":"No actionable Next or Submit control found"}

                return {"status":"needs_human","reason":"Application has more than five automated form steps"}
            except Exception as exc:
                return {"status":"failed","reason":f"{type(exc).__name__}: {str(exc)[:220]}"}
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
