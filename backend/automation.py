"""Career Atlas automation helpers.

Fail-safe browser automation: unknown/sensitive questions, CAPTCHA, assessments,
or missing verified profile data stop submission and return needs_human.
"""
from __future__ import annotations
import json, os, re, smtplib, tempfile
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse, urljoin
from ats_adapters import ats_name, fill_ats_fields, application_root

SENSITIVE_PATTERNS=("security clearance","criminal","conviction","disability","veteran","gender","race","ethnicity",
"assessment","test","captcha")
LEGAL_JURISDICTION_TERMS=("united states","u.s.","usa","united kingdom","uk","european union","eu citizen","canada","australia")

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
        data=json.loads(raw)
        if not isinstance(data,dict):return {}
        # Verified non-sensitive defaults from the canonical Career Atlas resume.
        # Runtime profile values always win when explicitly configured.
        data.setdefault("country","Pakistan")
        data.setdefault("city","Islamabad")
        data.setdefault("university","National University of Sciences and Technology (NUST)")
        data.setdefault("degree","Bachelor's degree")
        data.setdefault("degree_name","B.E. Electrical Engineering")
        data.setdefault("field_of_study","Electrical Engineering")
        data.setdefault("graduation_year","2027")
        return data
    except json.JSONDecodeError:return {}

def load_sensitive_profile():
    """Explicit protected/demographic answers live separately from normal profile memory."""
    raw=os.getenv("SENSITIVE_PROFILE_JSON","").strip()
    if not raw:return {}
    try:
        data=json.loads(raw);return data if isinstance(data,dict) else {}
    except json.JSONDecodeError:return {}


def find_application_email(text):
    """Return only an email that clearly looks like a recruiting inbox.

    Job descriptions often contain accessibility, privacy, legal or support
    addresses. Those must never receive an application automatically.
    """
    candidates=re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",text or "")
    positive=("jobs","job","careers","career","recruit","recruiting","recruitment","talent","hiring","hr","people","apply","applications")
    negative=("accommodation","accessibility","reasonable","privacy","legal","support","help","security","press","media","billing","compliance","noreply","no-reply")
    for address in candidates:
        low=address.lower()
        local=low.split("@",1)[0]
        if any(x in low for x in ("example.com","example.org")):continue
        if any(x in local for x in negative):continue
        if any(x in local for x in positive):return address
    return None

def needs_human(question):
    q=(question or "").lower()
    if any(x in q for x in SENSITIVE_PATTERNS):
        return True
    # Generic authorization/sponsorship can use an explicit profile answer.
    # Jurisdiction-specific legal eligibility must never be inferred.
    if any(x in q for x in ("authorized to work","work authorization","visa sponsorship","sponsorship")):
        return any(x in q for x in LEGAL_JURISDICTION_TERMS)
    return False

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

def _fill_explicit_sensitive_answers(page):
    profile=load_sensitive_profile()
    if not profile:return []
    filled=[]
    # Never infer these values. They are used only when explicitly provided in
    # the separate SENSITIVE_PROFILE_JSON runtime secret.
    mappings=(
        ("gender",r"gender"),
        ("race_ethnicity",r"race|ethnicity"),
        ("veteran_status",r"veteran"),
        ("disability_status",r"disability"),
    )
    for key,pattern in mappings:
        value=profile.get(key)
        if value and _select_known_answer(page,pattern,value):
            filled.append(key)
    return filled

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

def _select_or_fill_labeled(page,label_pattern,value,aliases=()):
    if value in (None,""):return False
    candidates=[str(value),*[str(x) for x in aliases if x]]
    try:
        field=page.get_by_label(re.compile(label_pattern,re.I))
        for i in range(min(field.count(),8)):
            item=field.nth(i)
            try:
                if not item.is_visible():continue
                tag=item.evaluate("(el)=>el.tagName.toLowerCase()")
                role=(item.get_attribute("role") or "").lower()
                if tag=="select":
                    for candidate in candidates:
                        try:item.select_option(label=re.compile(rf"^{re.escape(candidate)}$",re.I));return True
                        except Exception:pass
                    continue
                if role=="combobox":
                    item.click()
                    for candidate in candidates:
                        try:
                            option=page.get_by_role("option",name=re.compile(rf"^{re.escape(candidate)}$",re.I))
                            if option.count():option.first.click();return True
                        except Exception:pass
                typ=(item.get_attribute("type") or "").lower()
                if typ not in ("radio","checkbox","file"):
                    item.fill(str(value));return True
            except Exception:pass
    except Exception:pass
    # Custom ATS dropdowns often expose only a visible label and listbox.
    try:
        labels=page.locator("label")
        for i in range(min(labels.count(),120)):
            txt=(labels.nth(i).inner_text() or "").strip()
            if not re.search(label_pattern,txt,re.I):continue
            parent=labels.nth(i).locator("xpath=..")
            combo=parent.get_by_role("combobox")
            if combo.count():
                combo.first.click()
                for candidate in candidates:
                    option=page.get_by_role("option",name=re.compile(rf"^{re.escape(candidate)}$",re.I))
                    if option.count():option.first.click();return True
    except Exception:pass
    return False

def _fill_verified_profile_questions(page,profile):
    touched=[]
    mappings=(
        ("country",r"country(?: of residence)?|current country",profile.get("country") or "Pakistan",()),
        ("city",r"current city|city of residence|^city$",profile.get("city") or "Islamabad",()),
        ("university",r"university|college|school",profile.get("university"),("NUST",)),
        ("degree",r"degree level|highest degree|degree type|education level",profile.get("degree"),("Bachelor","Bachelors","Bachelor's","Undergraduate")),
        ("degree_name",r"degree name|qualification",profile.get("degree_name"),()),
        ("field_of_study",r"field of study|major|discipline",profile.get("field_of_study"),("Electrical Engineering","Engineering")),
        ("graduation_year",r"graduation year|year of graduation|expected graduation",profile.get("graduation_year"),("2027",)),
        ("linkedin",r"linkedin",profile.get("linkedin"),()),
        ("github",r"github",profile.get("github"),()),
        ("portfolio",r"portfolio|personal website|website",profile.get("portfolio"),()),
    )
    for key,pattern,value,aliases in mappings:
        if value and _select_or_fill_labeled(page,pattern,value,aliases):
            touched.append(key)
    return touched

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

    country=profile.get("country") or ("Pakistan" if "pakistan" in str(profile.get("location","")).lower() else "")
    if country:
        _select_known_answer(page,r"country|current country|country of residence",country)
    _fill_verified_profile_questions(page,profile)

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

def _fill_learned_answers(page,learned_answers):
    filled=[]
    for item in learned_answers or []:
        value=item.get("value")
        if value in (None,""):continue
        selector=item.get("selector_hint") or ""
        label=item.get("label") or ""
        try:
            if selector:
                loc=page.locator(selector)
                if loc.count() and loc.first.is_visible():
                    tag=loc.first.evaluate("(el)=>el.tagName.toLowerCase()")
                    typ=(loc.first.get_attribute("type") or "").lower()
                    if tag=="select":
                        try:loc.first.select_option(label=re.compile(rf"^{re.escape(str(value))}$",re.I));filled.append(label);continue
                        except Exception:pass
                    if typ in ("checkbox","radio"):
                        if bool(value):loc.first.check();filled.append(label);continue
                    loc.first.fill(str(value));filled.append(label);continue
            if label and _fill(page,re.escape(label),value):
                filled.append(label)
        except Exception:
            pass
    return filled

def _sensitive_or_unknown_required(page,sensitive_filled=None):
    root=application_root(page)
    blockers=[]

    labels=root.locator("label")
    for i in range(min(labels.count(),120)):
        label=labels.nth(i)
        text=(label.inner_text() or "").strip()
        if text and needs_human(text):
            low=text.lower()
            explicit=sensitive_filled or []
            if ("gender" in low and "gender" in explicit) or (("race" in low or "ethnicity" in low) and "race_ethnicity" in explicit) or ("veteran" in low and "veteran_status" in explicit) or ("disability" in low and "disability_status" in explicit):
                continue
            # Optional demographic/sensitive questions are left blank instead
            # of blocking the application or inventing an answer.
            required=False
            try:
                parent=label.locator("xpath=..")
                required=parent.locator("input[required],select[required],textarea[required],[aria-required='true']").count()>0
            except Exception:
                required=False
            if not required and "*" not in text and "required" not in low:
                continue
            blockers.append(f"Sensitive/uncertain question: {text[:180]}")

    required=root.locator("input[required], textarea[required], select[required]")
    safe_tokens=("name","email","phone","mobile","location","city","country","linkedin","github","portfolio","website",
                 "resume","cv","cover","message","additional","authorization","authorized","sponsorship","salary",
                 "availability","start date","university","college","school","degree","education","major","field of study",
                 "graduation","graduate year","expected graduation")
    for i in range(min(required.count(),120)):
        field=required.nth(i)
        try:
            typ=(field.get_attribute("type") or "").lower()
            aria_hidden=(field.get_attribute("aria-hidden") or "").lower()
            tabindex=(field.get_attribute("tabindex") or "").strip()
            classes=(field.get_attribute("class") or "").lower()
            if typ in ("hidden","submit","button","file","checkbox","radio"):continue
            # Greenhouse/Remix and other ATS products use invisible required
            # proxy inputs to drive custom dropdown validation. They are not
            # applicant questions and must not block an otherwise completed form.
            if aria_hidden=="true" or tabindex=="-1" or "requiredinput" in classes:
                continue
            try:
                if not field.is_visible():
                    continue
            except Exception:
                pass
            value=(field.input_value() or "").strip()
            if value:continue
            ident=" ".join(filter(None,[
                field.get_attribute("name"),field.get_attribute("id"),field.get_attribute("placeholder"),
                field.get_attribute("aria-label"),field.get_attribute("data-testid"),
            ])).lower()
            if "newsletter" in ident:continue
            if not any(tok in ident for tok in safe_tokens):
                try:html=(field.evaluate("(el)=>el.outerHTML") or "")[:220]
                except Exception:html=""
                blockers.append(f"Unknown required field: {ident[:160] or 'unlabelled required field'} {html}")
        except Exception:pass

    if blockers:
        # Return all unique blockers so the next adapter/memory update can fix
        # the entire form in one iteration rather than one field at a time.
        unique=[]
        for b in blockers:
            if b not in unique:unique.append(b)
        return " | ".join(unique[:12])
    return None

AGGREGATOR_HOSTS=("weworkremotely.com","remoteok.com","jobicy.com","himalayas.app","arbeitnow.com","arbeitnow.ch","remotive.com")
ATS_HOST_HINTS=("greenhouse.io","lever.co","ashbyhq.com","smartrecruiters.com","myworkdayjobs.com","workdayjobs.com")

def _follow_external_apply(page):
    """Resolve an aggregator listing to the employer/ATS application page."""
    try:
        current=(urlparse(page.url).hostname or "").lower()
        if not any(x in current for x in AGGREGATOR_HOSTS):
            return False
        candidates=[]
        links=page.locator("a[href]")
        for i in range(min(links.count(),120)):
            try:
                link=links.nth(i)
                href=(link.get_attribute("href") or "").strip()
                if not href or href.startswith(("#","javascript:","mailto:")):
                    continue
                absolute=urljoin(page.url,href)
                parsed=urlparse(absolute)
                host=(parsed.hostname or "").lower()
                if parsed.scheme not in ("http","https") or not host:
                    continue
                text=(link.inner_text(timeout=500) or "").strip().lower()
                score=0
                if re.search(r"apply|application|company website|view (job|role)|original (job|posting)",text,re.I):score+=70
                if any(x in host for x in ATS_HOST_HINTS):score+=60
                if host!=current:score+=25
                if re.search(r"/(jobs?|careers?|apply|positions?|openings?)(/|$)",parsed.path,re.I):score+=20
                if any(x in host for x in AGGREGATOR_HOSTS):score-=45
                if any(x in host for x in ("linkedin.com","facebook.com","twitter.com","x.com","instagram.com")):score-=80
                candidates.append((score,absolute))
            except Exception:
                pass
        if not candidates:return False
        candidates.sort(key=lambda x:x[0],reverse=True)
        score,target=candidates[0]
        if score<45:return False
        page.goto(target,wait_until="domcontentloaded",timeout=45000)
        return True
    except Exception:
        return False

def _scopes(page):
    scopes=[page]
    try:
        for frame in page.frames:
            if frame != page.main_frame:
                scopes.append(frame)
    except Exception:
        pass
    return scopes

def _form_field_count(scope):
    try:
        return scope.locator('input:not([type="hidden"]), textarea, select').count()
    except Exception:
        return 0

def _find_open_application(scope):
    candidates=[
        scope.get_by_role("button",name=re.compile(r"^apply$|^apply now$|start application|begin application|apply for this job",re.I)),
        scope.get_by_role("link",name=re.compile(r"^apply$|^apply now$|start application|begin application|apply for this job",re.I)),
    ]
    for loc in candidates:
        try:
            for i in range(min(loc.count(),8)):
                item=loc.nth(i)
                if item.is_visible() and item.is_enabled():
                    return item
        except Exception:
            pass
    return None

def _validation_diagnostics(scope,profile=None):
    """Return labels for browser-invalid fields without exposing answers."""
    out=[]
    profile=profile or {}
    redact=[str(profile.get(k,"")).strip() for k in ("name","email","phone") if str(profile.get(k,"")).strip()]
    try:
        invalid=scope.locator(':invalid, [aria-invalid="true"]')
        for i in range(min(invalid.count(),24)):
            field=invalid.nth(i)
            try:
                fid=(field.get_attribute("id") or "").strip()
                name=(field.get_attribute("name") or "").strip()
                aria=(field.get_attribute("aria-label") or "").strip()
                placeholder=(field.get_attribute("placeholder") or "").strip()
                label_text=""
                if fid:
                    lab=scope.locator(f'label[for="{fid}"]')
                    if lab.count():
                        label_text=(lab.first.inner_text(timeout=500) or "").strip()
                if not label_text:
                    try:
                        wrapper=field.locator("xpath=ancestor::*[self::fieldset or self::div][1]")
                        label_text=(wrapper.inner_text(timeout=500) or "").strip()
                    except Exception:
                        pass
                text=" ".join(x for x in (label_text,aria,placeholder,name,fid) if x)
                text=re.sub(r"\s+"," ",text).strip()
                for secret in redact:
                    text=text.replace(secret,"[redacted]")
                if text and text not in out:
                    out.append(text[:220])
            except Exception:
                pass
    except Exception:
        pass
    return out[:10]

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

def apply_with_playwright(url,pdf,filename,draft,learned_answers=None):
    profile=load_profile()
    if not profile:return {"status":"needs_human","reason":"APPLICANT_PROFILE_JSON is not configured"}
    try:from playwright.sync_api import sync_playwright
    except Exception:return {"status":"failed","reason":"Playwright is not installed"}
    with tempfile.TemporaryDirectory() as td:
        resume_path=Path(td)/(filename or "resume.pdf"); resume_path.write_bytes(pdf)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page()
            network_posts=[]
            def _capture_submit_response(response):
                try:
                    request=response.request
                    if (request.method or "").upper()!="POST":
                        return
                    parsed=urlparse(response.url)
                    host=(parsed.hostname or "").lower()
                    path=parsed.path or "/"
                    # Store only host/path/status. Never store query strings,
                    # request bodies or applicant data.
                    atsish=(
                        any(x in host for x in ("greenhouse.io","ashbyhq.com","lever.co","smartrecruiters.com","workday"))
                        or any(x in path.lower() for x in ("application","apply","submit","candidate"))
                    )
                    if atsish:
                        op=""
                        try:
                            body=request.post_data or ""
                            if body and len(body)<200000:
                                parsed_body=json.loads(body)
                                if isinstance(parsed_body,dict):
                                    op=str(parsed_body.get("operationName") or "").strip()
                                    if not op:
                                        query=str(parsed_body.get("query") or "")
                                        m=re.search(r"\b(?:mutation|query)\s+([A-Za-z0-9_]+)",query)
                                        if m:op=m.group(1)
                        except Exception:
                            op=""
                        network_posts.append({"status":int(response.status),"host":host,"path":path[:240],"op":op[:100]})
                except Exception:
                    pass
            page.on("response",_capture_submit_response)
            try:
                page.goto(url,wait_until="domcontentloaded",timeout=45000)
                # JS-heavy ATS pages (especially Ashby/Workday) often render
                # the application UI after DOMContentLoaded.
                try:
                    page.wait_for_timeout(2500)
                except Exception:
                    pass

                _follow_external_apply(page)
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

                for _ in range(5):
                    signal=_stop_signal(page)
                    if signal:return {"status":"needs_human","reason":signal}

                    active_scope=page
                    ats_info={"ats":"generic","touched":[]}
                    learned_filled=[]
                    blocker=None
                    submit=None
                    nxt=None
                    for scope in _scopes(page):
                        try:
                            _fill_standard_fields(scope,profile,draft,resume_path)
                            ats_info=fill_ats_fields(scope,page.url,profile,draft,resume_path)
                            learned_filled.extend(_fill_learned_answers(scope,learned_answers))
                            sensitive_filled=_fill_explicit_sensitive_answers(scope)
                            possible=_sensitive_or_unknown_required(scope,sensitive_filled)
                            if possible and blocker is None:blocker=possible
                            found_submit=_find_submit(scope)
                            found_next=_find_next(scope)
                            if found_submit is not None or found_next is not None:
                                active_scope=scope;submit=found_submit;nxt=found_next;blocker=possible
                                break
                        except Exception:
                            pass
                    if blocker:return {"status":"needs_human","reason":blocker}

                    if not AutomationConfig.from_env().auto_submit_browser:
                        return {"status":"ready","reason":"Form filled; AUTO_SUBMIT_BROWSER is disabled"}

                    if submit is None:submit=_find_submit(active_scope)

                    # Some ATS pages show an "Apply now" launcher before the
                    # actual form. Do not mistake that for the final submit.
                    opener=_find_open_application(active_scope)
                    if opener is not None and _form_field_count(active_scope)<2:
                        try:
                            opener.click()
                            page.wait_for_timeout(2500)
                            continue
                        except Exception:
                            pass

                    if submit is not None:
                        try:
                            submit_text=(submit.inner_text(timeout=800) or submit.get_attribute("value") or "").strip().lower()
                        except Exception:
                            submit_text=""
                        if re.fullmatch(r"apply|apply now|start application|begin application|apply for this job",submit_text,re.I) and _form_field_count(active_scope)<2:
                            try:
                                submit.click()
                                page.wait_for_timeout(2500)
                                continue
                            except Exception:
                                pass
                        before=page.url
                        try:pre_shot=page.screenshot(full_page=True)
                        except Exception:pre_shot=b""
                        submit.click()
                        page.wait_for_timeout(4500)
                        try:post_shot=page.screenshot(full_page=True)
                        except Exception:post_shot=b""
                        texts=[]
                        for scope in _scopes(page):
                            try:texts.append(scope.locator("body").inner_text(timeout=4000) or "")
                            except Exception:pass
                            try:
                                alerts=scope.locator('[role="alert"], [aria-live="polite"], [aria-live="assertive"], .toast, .notification, .success')
                                for ai in range(min(alerts.count(),10)):
                                    texts.append(alerts.nth(ai).inner_text(timeout=500) or "")
                            except Exception:pass
                        final="\n".join(texts).lower()
                        confirmations=(
                            "application submitted","thank you for applying","application received",
                            "thanks for applying","successfully submitted","we have received your application",
                            "your application has been submitted","thank you for your application",
                        )
                        meta={"ats":ats_info.get("ats","generic"),"learnedFields":learned_filled,"networkPosts":network_posts[-8:]}
                        # A successful response from a documented ATS application
                        # submission endpoint is strong confirmation even when the
                        # employer customizes or omits the thank-you text.
                        network_confirmed=None
                        for hit in network_posts:
                            host=hit.get("host","");path=hit.get("path","");status=int(hit.get("status",0))
                            ok=200 <= status < 300
                            if not ok:continue
                            op=(hit.get("op") or "").lower()
                            if host=="api.ashbyhq.com" and "applicationform.submit" in path.lower():
                                network_confirmed=hit;break
                            if host=="jobs.ashbyhq.com" and path=="/api/non-user-graphql" and "submit" in op and "application" in op:
                                network_confirmed=hit;break
                            if "greenhouse.io" in host and re.search(r"/jobs/[^/]+(?:/applications?)?$|/applications?/",path,re.I):
                                network_confirmed=hit;break
                            if "lever.co" in host and re.search(r"/postings/|/apply",path,re.I):
                                network_confirmed=hit;break
                        if network_confirmed:
                            receipt=f"ats-network-confirmed:{network_confirmed['host']}{network_confirmed['path']}"
                            return {"status":"applied","receipt":receipt,"pre_screenshot":pre_shot,"post_screenshot":post_shot,"meta":meta}
                        if any(x in final for x in confirmations):
                            return {"status":"applied","receipt":page.url,"pre_screenshot":pre_shot,"post_screenshot":post_shot,"meta":meta}
                        # Some ATS pages navigate to a confirmation URL with little text.
                        if page.url!=before and any(x in page.url.lower() for x in ("thank","success","confirmation","submitted")):
                            return {"status":"applied","receipt":page.url,"pre_screenshot":pre_shot,"post_screenshot":post_shot,"meta":meta}
                        diag=""
                        if network_posts:
                            brief=", ".join(f"{x['status']} {x['host']}{x['path']}"+(f" op={x.get('op')}" if x.get("op") else "") for x in network_posts[-8:])
                            diag+=f" ATS POST responses: {brief}"
                        invalid=[]
                        for scope in _scopes(page):
                            invalid.extend(_validation_diagnostics(scope,profile))
                        invalid=list(dict.fromkeys(invalid))[:10]
                        if invalid:
                            diag+=" Invalid fields: "+" | ".join(invalid)
                        return {"status":"needs_human","reason":"Submit was clicked but reliable confirmation was not detected."+diag,"pre_screenshot":pre_shot,"post_screenshot":post_shot,"meta":meta}

                    if nxt is None:nxt=_find_next(active_scope)
                    if nxt is not None:
                        nxt.click();page.wait_for_timeout(2500)
                        continue

                    # Last attempt: an ATS may render its form launcher only
                    # after scripts settle, or use a generic Apply control.
                    opener=_find_open_application(active_scope) or _find_open_application(page)
                    if opener is not None:
                        try:
                            opener.click();page.wait_for_timeout(3000);continue
                        except Exception:
                            pass
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
