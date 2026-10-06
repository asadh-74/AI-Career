"""CrewAI-assisted job research with a deterministic fallback."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse


def heuristic_research(job,profile:dict)->dict:
    desc=(job.description or "").lower()
    location=(job.location or "").lower()
    quality=70
    if any(x in location for x in ("worldwide","remote","pakistan","anywhere")):quality+=10
    if any(x in desc for x in ("senior","staff","principal","5+ years","7+ years")):quality-=25
    if any(x in desc for x in ("python","fastapi","automation","ai","machine learning","react","api")):quality+=8
    host=(urlparse(job.apply_url or "").hostname or "").lower()
    direct=not any(x in host for x in ("indeed.","glassdoor.","remoteok.","jobicy.","remotive."))
    if direct:quality+=5
    eligibility="Remote/location appears compatible." if any(x in location for x in ("worldwide","remote","pakistan","anywhere","global")) else "Location eligibility needs verification."
    return {
        "company_summary":f"{job.company} — {job.title}. Public job text analyzed by Career Atlas.",
        "eligibility_notes":eligibility,
        "quality_score":max(0,min(100,quality)),
        "source":"heuristic",
    }


def crew_research(job,profile:dict)->dict:
    """Use CrewAI when explicitly configured; otherwise deterministic fallback."""
    model=os.getenv("CREWAI_LLM","").strip()
    if not model:
        return heuristic_research(job,profile)
    try:
        from crewai import Agent, Crew, LLM, Process, Task
        llm=LLM(model=model)
        sourcing=Agent(
            role="Job Source Validator",
            goal="Assess source quality and whether the role is worth an application attempt.",
            backstory="You validate job opportunities conservatively and never invent facts.",
            llm=llm,verbose=False,
        )
        eligibility=Agent(
            role="Eligibility Analyst",
            goal="Check location, seniority and explicit eligibility requirements against supplied profile facts.",
            backstory="You distinguish confirmed facts from unknown requirements.",
            llm=llm,verbose=False,
        )
        ranking=Agent(
            role="Opportunity Ranker",
            goal="Return a concise quality score and rationale using only supplied text.",
            backstory="You prioritize relevant junior and early-career software/AI roles.",
            llm=llm,verbose=False,
        )
        payload={
            "company":job.company,"title":job.title,"location":job.location,
            "description":(job.description or "")[:9000],
            "profile":{k:profile.get(k) for k in ("location","professional_experience","degree","availability","available_remote") if k in profile},
        }
        t1=Task(description="Validate this opportunity source and summarize the company/job text. Input: "+json.dumps(payload),expected_output="A concise factual source/company summary.",agent=sourcing)
        t2=Task(description="Assess eligibility from the same supplied input. Do not infer protected traits or legal authorization.",expected_output="Concise eligibility notes with unknowns stated.",agent=eligibility)
        t3=Task(description="Using prior task outputs, return exactly: QUALITY_SCORE: <0-100> then one-sentence reason.",expected_output="QUALITY_SCORE: integer plus one sentence.",agent=ranking)
        out=Crew(agents=[sourcing,eligibility,ranking],tasks=[t1,t2,t3],process=Process.sequential,verbose=False).kickoff()
        text=str(out)
        m=re.search(r"QUALITY_SCORE:\s*(\d+)",text,re.I)
        score=int(m.group(1)) if m else heuristic_research(job,profile)["quality_score"]
        return {
            "company_summary":str(t1.output.raw if getattr(t1,"output",None) else "")[:3000],
            "eligibility_notes":str(t2.output.raw if getattr(t2,"output",None) else "")[:3000],
            "quality_score":max(0,min(100,score)),
            "source":"crewai",
        }
    except Exception as exc:
        out=heuristic_research(job,profile)
        out["crew_error"]=f"{type(exc).__name__}: {str(exc)[:240]}"
        return out
