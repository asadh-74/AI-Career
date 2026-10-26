"""Validated, user-authored reusable facts. No model-generated answers."""
import re
from pydantic import BaseModel, Field, ConfigDict, field_validator

PROFILE_FIELDS = {
    'name':'Full name', 'email':'Application email', 'phone':'Phone with country code',
    'country':'Country', 'city':'City', 'state_province':'State / province',
    'address_line1':'Address (optional)', 'postal_code':'Postal code',
    'linkedin':'LinkedIn URL', 'github':'GitHub URL', 'portfolio':'Portfolio URL',
    'upwork':'Upwork URL', 'availability':'Availability / start date',
    'university':'University', 'degree_name':'Exact degree name',
    'degree':'Degree level', 'field_of_study':'Field of study',
    'graduation_year':'Expected graduation year',
    'salary_expectation_amount':'Expected compensation amount',
    'salary_expectation_currency':'Compensation currency',
    'salary_expectation_period':'Compensation period (hour/month/year)',
}

def question_key(text):
    return re.sub(r'\s+', ' ', text.lower()).strip(' *?:.\n')

def reusable_question(text):
    return not re.search(r'\b(password|passport|ssn|religion|gender|ethnicity|race|disability|criminal|consent|signature|citizenship|sponsorship|authorization|authorised|authorized|visa)\b|date of birth', text, re.I)

class SavedAnswer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question: str=Field(min_length=3,max_length=500)
    answer: str=Field(min_length=1,max_length=4000)
    @field_validator('question')
    @classmethod
    def safe_question(cls,value):
        if not reusable_question(value):raise ValueError('Use per-application review for sensitive or legal questions')
        return value.strip()
    @field_validator('answer')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('Answer cannot be empty')
        return value.strip()

class VaultInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profile: dict[str,str]=Field(default_factory=dict)
    answers: list[SavedAnswer]=Field(default_factory=list,max_length=200)
    @field_validator('profile')
    @classmethod
    def known_fields(cls,value):
        for key,item in value.items():
            if key not in PROFILE_FIELDS:raise ValueError(f'Unknown field: {key}')
            if len(item)>1000:raise ValueError(f'{key} exceeds 1000 characters')
            if key in ('linkedin','github','portfolio','upwork') and item.strip() and not item.strip().startswith('https://'):
                raise ValueError(f'{key} must start with https://')
        return {k:v.strip() for k,v in value.items()}
    @field_validator('answers')
    @classmethod
    def unique(cls,values):
        keys=[question_key(x.question) for x in values]
        if len(set(keys))!=len(keys):raise ValueError('Duplicate questions: keep one answer per question')
        return values

def saved_answer(profile,label):
    if not reusable_question(label):return ''
    for row in profile.get('saved_answers',[]):
        if question_key(row.get('question',''))==question_key(label):return row.get('answer','')
    return ''
