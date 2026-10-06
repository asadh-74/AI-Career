"""ATS-specific browser helpers for Career Atlas.

These adapters only improve deterministic field targeting. They do not bypass
CAPTCHAs, assessments, authentication, or employer restrictions.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


def ats_name(url:str)->str:
    host=(urlparse(url or "").hostname or "").lower()
    if "greenhouse.io" in host:return "greenhouse"
    if "lever.co" in host:return "lever"
    if "ashbyhq.com" in host:return "ashby"
    if "smartrecruiters.com" in host:return "smartrecruiters"
    if "myworkdayjobs.com" in host or "workday" in host:return "workday"
    return "generic"


def _fill_first(page,selectors,value)->bool:
    if value in (None,""):return False
    for selector in selectors:
        try:
            loc=page.locator(selector)
            if loc.count() and loc.first.is_visible():
                loc.first.fill(str(value))
                return True
        except Exception:
            pass
    return False


def _upload_first(page,selectors,path)->bool:
    for selector in selectors:
        try:
            loc=page.locator(selector)
            if loc.count():
                loc.first.set_input_files(str(path))
                return True
        except Exception:
            pass
    return False


def fill_ats_fields(page,url,profile,draft,resume_path)->dict:
    name=str(profile.get("name","")).strip()
    parts=name.split()
    first=parts[0] if parts else ""
    last=" ".join(parts[1:]) if len(parts)>1 else ""
    email=profile.get("email","")
    phone=profile.get("phone","")
    linkedin=profile.get("linkedin","")
    portfolio=profile.get("portfolio","")
    kind=ats_name(url)
    touched=[]

    if kind=="greenhouse":
        pairs=[
            (['input[name="job_application[first_name]"]','#first_name'],first,"first_name"),
            (['input[name="job_application[last_name]"]','#last_name'],last,"last_name"),
            (['input[name="job_application[email]"]','#email'],email,"email"),
            (['input[name="job_application[phone]"]','#phone'],phone,"phone"),
            (['input[name*="linkedin" i]'],linkedin,"linkedin"),
            (['input[name*="website" i]','input[name*="portfolio" i]'],portfolio,"portfolio"),
        ]
        for selectors,value,label in pairs:
            if _fill_first(page,selectors,value):touched.append(label)
        if _upload_first(page,['input[type="file"][name*="resume" i]','input[type="file"]'],resume_path):touched.append("resume")

    elif kind=="lever":
        pairs=[
            (['input[name="name"]'],name,"name"),
            (['input[name="email"]'],email,"email"),
            (['input[name="phone"]'],phone,"phone"),
            (['input[name="urls[LinkedIn]"]','input[name*="linkedin" i]'],linkedin,"linkedin"),
            (['input[name="urls[Portfolio]"]','input[name*="portfolio" i]'],portfolio,"portfolio"),
        ]
        for selectors,value,label in pairs:
            if _fill_first(page,selectors,value):touched.append(label)
        if _upload_first(page,['input[type="file"][name="resume"]','input[type="file"]'],resume_path):touched.append("resume")

    elif kind=="ashby":
        pairs=[
            (['input[name*="name" i]','input[autocomplete="name"]'],name,"name"),
            (['input[type="email"]','input[autocomplete="email"]'],email,"email"),
            (['input[type="tel"]','input[autocomplete="tel"]'],phone,"phone"),
            (['input[name*="linkedin" i]'],linkedin,"linkedin"),
            (['input[name*="website" i]','input[name*="portfolio" i]'],portfolio,"portfolio"),
        ]
        for selectors,value,label in pairs:
            if _fill_first(page,selectors,value):touched.append(label)
        if _upload_first(page,['input[type="file"]'],resume_path):touched.append("resume")

    elif kind=="smartrecruiters":
        pairs=[
            (['input[name="firstName"]','input[id*="first-name" i]'],first,"first_name"),
            (['input[name="lastName"]','input[id*="last-name" i]'],last,"last_name"),
            (['input[name="email"]','input[type="email"]'],email,"email"),
            (['input[name="phoneNumber"]','input[type="tel"]'],phone,"phone"),
        ]
        for selectors,value,label in pairs:
            if _fill_first(page,selectors,value):touched.append(label)
        if _upload_first(page,['input[type="file"]'],resume_path):touched.append("resume")

    elif kind=="workday":
        pairs=[
            (['input[data-automation-id="legalNameSection_firstName"]','input[data-automation-id*="firstName"]'],first,"first_name"),
            (['input[data-automation-id="legalNameSection_lastName"]','input[data-automation-id*="lastName"]'],last,"last_name"),
            (['input[data-automation-id="email"]','input[type="email"]'],email,"email"),
            (['input[data-automation-id*="phone"]','input[type="tel"]'],phone,"phone"),
        ]
        for selectors,value,label in pairs:
            if _fill_first(page,selectors,value):touched.append(label)
        if _upload_first(page,['input[type="file"]'],resume_path):touched.append("resume")

    # Common cover-letter textareas used by several ATS products.
    if draft:
        try:
            loc=page.locator('textarea[name*="cover" i], textarea[id*="cover" i], textarea[name*="message" i]')
            if loc.count() and loc.first.is_visible() and not (loc.first.input_value() or "").strip():
                loc.first.fill(draft)
                touched.append("cover")
        except Exception:
            pass

    return {"ats":kind,"touched":touched}


def application_root(page):
    """Prefer the form containing the best submit button; fall back to page."""
    buttons=[
        page.get_by_role("button",name=re.compile(r"submit|apply|send application|finish|complete",re.I)),
        page.locator('button[type="submit"]'),
        page.locator('input[type="submit"]'),
    ]
    for candidate in buttons:
        try:
            for i in range(min(candidate.count(),8)):
                btn=candidate.nth(i)
                if not btn.is_visible():continue
                form=btn.locator("xpath=ancestor::form[1]")
                if form.count():return form.first
        except Exception:
            pass
    return page
