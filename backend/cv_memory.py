"""Local CV extraction and evidence retrieval; no API requests or invented facts."""
import hashlib
import json
import re
from collections import defaultdict
from profile_vault import PROFILE_FIELDS, question_key, reusable_question

VERSION='cv-memory-v1'
HEADINGS={
    'education':'education','academic qualifications':'education',
    'experience':'experience','work experience':'experience','professional experience':'experience',
    'internships':'experience','internship experience':'experience',
    'projects':'projects','selected projects':'projects','personal projects':'projects',
    'skills':'skills','technical skills':'skills','core skills':'skills',
    'summary':'summary','professional summary':'summary','profile':'summary','objective':'summary',
    'certifications':'certifications','professional development':'certifications','training':'certifications',
    'languages':'languages','interests':'interests','achievements':'achievements',
}

def extract_document(doc):
    text=doc.extracted_text or ''
    lines=[re.sub(r'\s+',' ',x).strip(' \t•') for x in text.splitlines() if x.strip()]
    facts=[]; sections=defaultdict(list); section='header'
    def add(key,value,evidence):
        value=value.strip(' |;,')
        if value and len(value)<=1000:
            facts.append({'field':key,'value':value,'quote':evidence[:1200],'document':doc.filename,'documentId':doc.id})
    for line in lines:
        heading=line.lower().strip(' :—-')
        if heading in HEADINGS:section=HEADINGS[heading];continue
        sections[section].append(line)
    for line in sections['header'][:12]:
        for value in re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',line):add('email',value,line)
        for value in re.findall(r'(?<!\w)\+\d[\d ()-]{8,20}\d',line):
            if 9<=len(re.sub(r'\D','',value))<=15:add('phone',value,line)
        for key,domain in [('linkedin','linkedin.com/in/'),('github','github.com/'),('upwork','upwork.com/freelancers/')]:
            for value in re.findall(r'(?:https?://)?(?:www\.)?'+re.escape(domain)+r'[A-Za-z0-9_~%./-]+',line,re.I):
                add(key, value if value.startswith('https://') else 'https://'+re.sub(r'^http://','',value),line)
        for key,label in [('name','(?:full name|name)'),('city','(?:current city|city)'),('country','(?:current country|country)'),('portfolio','(?:portfolio|personal website)')]:
            m=re.match(label+r'\s*:\s*(.+)',line,re.I)
            if m and (key!='portfolio' or m[1].startswith('https://')):add(key,m[1],line)
    if lines and not any(f['field']=='name' for f in facts):
        first=lines[0]
        excluded=r'\b(resume|curriculum|vitae|engineer|engineering|developer|student|profile|contact|software|portfolio|cv)\b'
        if re.fullmatch(r"[A-Za-z][A-Za-z'.-]*(?: [A-Za-z][A-Za-z'.-]*){1,4}",first) and not re.search(excluded,first,re.I):add('name',first,first)
    for line in sections['education']:
        if re.search(r'\b(university|institute|college|NUST)\b',line,re.I):
            value=re.split(r'\s[|–—]\s|\s{2,}',line)[0]
            add('university',value,line)
        m=re.search(r"\b(?:B\.?\s?E\.?|B\.?\s?Sc\.?|BS|Bachelor(?:'s)?(?: of (?:Science|Engineering))?)\s+(?:in\s+)?[A-Za-z][A-Za-z &/-]+",line,re.I)
        if m:
            add('degree_name',m[0].strip(' -/'),line)
            major=re.sub(r"^(?:B\.?\s?E\.?|B\.?\s?Sc\.?|BS|Bachelor(?:'s)?(?: of (?:Science|Engineering))?)\s+(?:in\s+)?",'',m[0],flags=re.I).strip(' -/')
            if major:add('field_of_study',major,line)
        m=re.search(r'(?:expected(?: graduation)?|graduating|graduation(?: year)?)\s*[:\-]?\s*(?:[A-Za-z]+\s+)?(20\d{2})',line,re.I)
        if m:add('graduation_year',m[1],line)
    passages=[]
    for kind in ('summary','skills','education','experience','projects','certifications'):
        if sections[kind]:passages.append({'section':kind,'text':'\n'.join(sections[kind])[:10000],'document':doc.filename,'documentId':doc.id})
    return facts,passages

def extract_documents(documents):
    candidates=defaultdict(list);passages=[]
    for doc in documents:
        facts,parts=extract_document(doc);passages.extend(parts)
        for fact in facts:
            if not any(x['value'].casefold()==fact['value'].casefold() for x in candidates[fact['field']]):candidates[fact['field']].append(fact)
    profile={};provenance={};conflicts=[]
    for key,items in candidates.items():
        if len(items)==1:profile[key]=items[0]['value'];provenance[key]=items[0]
        else:conflicts.append({'field':key,'label':PROFILE_FIELDS.get(key,key),'candidates':items})
    return {'version':VERSION,'profile':profile,'provenance':provenance,'conflicts':conflicts,'passages':passages,
            'documents':[{'id':d.id,'filename':d.filename} for d in documents]}

def document_memory(session):
    from sqlalchemy import select
    from main import Document
    from v3_models import get_setting,set_setting
    docs=session.scalars(select(Document).order_by(Document.id)).all()
    docs.sort(key=lambda d: (d.kind!='Resume',d.id))
    digest=hashlib.sha256((VERSION+'\n'+json.dumps([(d.id,d.filename,d.extracted_text) for d in docs])).encode()).hexdigest()
    try:cache=json.loads(get_setting(session,'cv_memory','{}'))
    except (TypeError,ValueError):cache={}
    if cache.get('digest')==digest:return cache
    result=extract_documents(docs);result['digest']=digest
    set_setting(session,'cv_memory',json.dumps(result,ensure_ascii=False));session.flush()
    return result

def effective_profile(session):
    import os
    from v3_models import get_setting
    memory=document_memory(session)
    try:base=json.loads(os.getenv('APPLICANT_PROFILE_JSON','{}') or '{}')
    except ValueError:base={}
    if not isinstance(base,dict):base={}
    try:manual=json.loads(get_setting(session,'applicant_profile_extra','{}') or '{}')
    except ValueError:manual={}
    if not isinstance(manual,dict):manual={}
    # Explicitly stored values override extracted facts. Environment values fill
    # gaps only; they cannot silently resolve disagreement between uploaded CVs.
    base.update(memory['profile'])
    for conflict in memory['conflicts']:base[conflict['field']]=''
    base.update(manual)
    base['cv_passages']=memory['passages']
    return base,memory,manual

def retrieve_answer(profile,label):
    """Only general experience prompts; return source excerpts, never infer years."""
    low=question_key(label)
    if not reusable_question(label) or re.search(r'\b(years|salary|pay|notice|available|availability|why|willing|agree|certify|test|solve|calculate|write code|gpa|grade)\b',low):return ''
    section=None
    if re.fullmatch(r'(?:please )?(?:list|describe|what are|tell us about) (?:your )?(?:technical )?skills',low):section='skills'
    elif re.fullmatch(r'(?:please )?(?:describe|summarize|tell us about) (?:your )?(?:work |professional )?experience',low):section='experience'
    elif re.fullmatch(r'(?:please )?(?:describe|list|tell us about) (?:your )?(?:relevant |selected )?projects',low):section='projects'
    elif low in ('tell us about yourself','tell me about yourself','professional summary','brief introduction'):section='summary'
    parts=[p for p in profile.get('cv_passages',[]) if p['section']==section] if section else []
    if not parts:return ''
    if len({p['text'].casefold() for p in parts})>1:return ''
    # A Resume is chosen upstream by document order; avoid combining overlapping
    # versions into inflated claims. Excerpts retain the applicant's original text.
    return parts[0]['text'][:3500]
