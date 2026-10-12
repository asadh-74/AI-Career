"""Generic form intelligence for Career Atlas browser automation.

This module centralizes browser-field discovery/filling so ATS-specific quirks do
not require one-off patches in worker.py. It never bypasses CAPTCHA, assessments,
authentication, or employer restrictions.
"""
from __future__ import annotations
import re

UUID_RE=re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",re.I)
TRUTHY={"1","true","yes","y","on","selected","check"}
FALSEY={"0","false","no","n","off","none",""}

def truthy(value)->bool:
    return value is True or str(value).strip().lower() in TRUTHY

def clean_question(text:str)->str:
    text=re.sub(r"<[^>]+>"," ",text or "")
    text=re.sub(r"\s+"," ",text).strip()
    # Remove duplicated UUID/name/id noise emitted by Ashby validation.
    parts=text.split()
    compact=[]
    seen_uuid=set()
    for p in parts:
        bare=p.strip("[](),:;")
        if UUID_RE.match(bare):
            if bare.lower() in seen_uuid:continue
            seen_uuid.add(bare.lower())
            continue
        if bare.lower() in {"type","here...","typehere..."} and not compact:
            continue
        compact.append(p)
    text=" ".join(compact).strip()
    text=re.sub(r"\s+question_[A-Za-z0-9_\[\]-]+.*$","",text,flags=re.I).strip()
    return text[:500]

def visible_option_label(review_label:str)->str:
    label=clean_question(review_label)
    label=re.split(r"\s+question_",label,flags=re.I)[0].strip(" *:")
    return label[:240]

def _candidate_values(value):
    if isinstance(value,(list,tuple,set)):
        vals=[str(v).strip() for v in value if str(v).strip()]
    else:
        raw=str(value).strip()
        vals=[x.strip() for x in re.split(r"[,;|]",raw) if x.strip()]
    return vals or [str(value).strip()]

def field_label(scope,field)->str:
    """Best-effort visible question text for a field, avoiding UUID-only labels."""
    candidates=[]
    try:
        aria=(field.get_attribute("aria-label") or "").strip()
        if aria:candidates.append(aria)
    except Exception:pass
    try:
        fid=(field.get_attribute("id") or "").strip()
        if fid:
            lab=scope.locator(f'label[for="{fid}"]')
            if lab.count():
                txt=(lab.first.inner_text(timeout=500) or "").strip()
                if txt:candidates.append(txt)
    except Exception:pass
    try:
        labelledby=(field.get_attribute("aria-labelledby") or "").strip()
        for token in labelledby.split():
            node=scope.locator(f'#{token}')
            if node.count():
                txt=(node.first.inner_text(timeout=500) or "").strip()
                if txt:candidates.append(txt)
    except Exception:pass
    try:
        fs=field.locator("xpath=ancestor::fieldset[1]")
        if fs.count():
            legend=fs.locator("legend")
            if legend.count():
                txt=(legend.first.inner_text(timeout=500) or "").strip()
                if txt:candidates.append(txt)
    except Exception:pass
    try:
        # Ashby commonly places the question in the nearest container above a
        # UUID-named input.
        node=field
        for _ in range(4):
            node=node.locator("xpath=..")
            txt=(node.inner_text(timeout=400) or "").strip()
            txt=re.sub(r"\s+"," ",txt)
            if txt and len(txt)<=600:candidates.append(txt)
    except Exception:pass
    try:
        placeholder=(field.get_attribute("placeholder") or "").strip()
        if placeholder and placeholder.lower() not in {"type here...","type here","select..."}:
            candidates.append(placeholder)
    except Exception:pass

    cleaned=[]
    for raw in candidates:
        q=clean_question(raw)
        if not q:continue
        if UUID_RE.match(q):continue
        if q.lower() in {"type here...","type here","select..."}:continue
        if q not in cleaned:cleaned.append(q)
    # Prefer concise question-like labels over whole container text.
    cleaned.sort(key=lambda x:(0 if ("?" in x or "*" in x) else 1,len(x)))
    return cleaned[0] if cleaned else ""

def selector_hint(field)->str:
    try:
        fid=(field.get_attribute("id") or "").strip()
        if fid:return f'#{fid}'
    except Exception:pass
    try:
        name=(field.get_attribute("name") or "").strip()
        if name:return f'[name="{name}"]'
    except Exception:pass
    return ""

def fill_answer(scope,label,value,selector="")->bool:
    """Fill one saved/generated answer across text/select/radio/checkbox/custom UI."""
    if value in (None,""):return False
    vals=_candidate_values(value)
    visible=visible_option_label(label)

    def fill_element(el)->bool:
        try:
            if not el.is_visible():return False
        except Exception:
            pass
        try:tag=(el.evaluate("(x)=>x.tagName.toLowerCase()") or "").lower()
        except Exception:tag=""
        try:typ=(el.get_attribute("type") or "").lower()
        except Exception:typ=""
        try:role=(el.get_attribute("role") or "").lower()
        except Exception:role=""
        if typ=="checkbox":
            if truthy(value):
                try:
                    if not el.is_checked():el.check()
                    return True
                except Exception:return False
            return False
        if typ=="radio":
            if truthy(value):
                try:el.check();return True
                except Exception:return False
            return False
        if tag=="select":
            for candidate in vals:
                try:el.select_option(label=re.compile(rf"^{re.escape(candidate)}$",re.I));return True
                except Exception:
                    try:el.select_option(value=candidate);return True
                    except Exception:pass
            return False
        if role=="combobox":
            for candidate in vals:
                try:
                    el.click()
                    try:el.fill(candidate)
                    except Exception:pass
                    try:scope.wait_for_timeout(250)
                    except Exception:pass
                    opt=scope.get_by_role("option",name=re.compile(rf"^{re.escape(candidate)}",re.I))
                    if opt.count():opt.first.click();return True
                except Exception:pass
            return False
        if typ not in {"file","submit","button","hidden"}:
            try:el.fill(str(value));return True
            except Exception:return False
        return False

    if selector:
        try:
            loc=scope.locator(selector)
            for i in range(min(loc.count(),4)):
                if fill_element(loc.nth(i)):return True
        except Exception:pass

    # Existing review labels sometimes contain a UUID name/id from Ashby.
    for token in re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27,}",label or "",re.I):
        try:
            loc=scope.locator(f'#{token}, [name="{token}"]')
            for i in range(min(loc.count(),4)):
                if fill_element(loc.nth(i)):return True
        except Exception:pass

    if label:
        try:
            loc=scope.get_by_label(re.compile(re.escape(clean_question(label)),re.I))
            for i in range(min(loc.count(),8)):
                if fill_element(loc.nth(i)):return True
        except Exception:pass

    # Checkbox/radio options are often labelled by the option text, not the
    # parent question.
    if visible and truthy(value):
        for role in ("checkbox","radio"):
            try:
                loc=scope.get_by_role(role,name=re.compile(rf"^{re.escape(visible)}$",re.I))
                if loc.count():
                    if role=="checkbox":
                        if not loc.first.is_checked():loc.first.check()
                    else:loc.first.check()
                    return True
            except Exception:pass

    # Text/value option fallback for custom listboxes.
    for candidate in vals:
        try:
            opt=scope.get_by_role("option",name=re.compile(rf"^{re.escape(candidate)}",re.I))
            if opt.count():opt.first.click();return True
        except Exception:pass
    return False

def unresolved_required(scope,sensitive_checker=None):
    """Return structured unresolved required fields with useful labels/selectors."""
    out=[]
    try:required=scope.locator("input[required], textarea[required], select[required], [aria-required='true']")
    except Exception:return out
    for i in range(min(required.count(),160)):
        field=required.nth(i)
        try:
            typ=(field.get_attribute("type") or "").lower()
            if typ in {"hidden","submit","button","file"}:continue
            if (field.get_attribute("aria-hidden") or "").lower()=="true":continue
            if (field.get_attribute("tabindex") or "").strip()=="-1":continue
            if "requiredinput" in (field.get_attribute("class") or "").lower():continue
            try:
                if not field.is_visible():continue
            except Exception:pass
            # Radio/checkbox groups are handled through browser validation after
            # submit; individual unchecked choices are not automatically blockers.
            if typ in {"checkbox","radio"}:continue
            value=""
            try:value=(field.input_value() or "").strip()
            except Exception:pass
            if value:continue
            q=field_label(scope,field)
            if not q:
                q=clean_question(" ".join(filter(None,[
                    field.get_attribute("aria-label"),field.get_attribute("placeholder"),
                    field.get_attribute("name"),field.get_attribute("id")
                ])))
            if not q:continue
            sensitive=bool(sensitive_checker(q)) if sensitive_checker else False
            out.append({"label":q[:500],"selector_hint":selector_hint(field),"sensitive":sensitive})
        except Exception:pass
    uniq=[]
    seen=set()
    for item in out:
        key=(item["label"].lower(),item["selector_hint"])
        if key in seen:continue
        seen.add(key);uniq.append(item)
    return uniq[:20]

def fill_phone_country(scope,country:str)->bool:
    """Best effort for phone-country comboboxes without touching phone number data."""
    if not country:return False
    try:
        phones=scope.locator('input[type="tel"], input[name*="phone" i]')
        if not phones.count():return False
        phone=phones.first
        node=phone
        for _ in range(4):
            node=node.locator("xpath=..")
            combos=node.get_by_role("combobox")
            for i in range(min(combos.count(),3)):
                combo=combos.nth(i)
                try:
                    combo.click()
                    try:combo.fill(country)
                    except Exception:pass
                    try:scope.wait_for_timeout(250)
                    except Exception:pass
                    option=scope.get_by_role("option",name=re.compile(rf"^{re.escape(country)}",re.I))
                    if option.count():
                        option.first.click();return True
                except Exception:pass
    except Exception:pass
    return False
