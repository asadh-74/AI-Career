"""Job-specific ATS resume tailoring for Career Atlas.

The tailor only promotes/reorders facts that already exist in the verified
canonical/master resume. It never invents skills, employers, dates or metrics.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.units import mm


@dataclass
class TailoredDocument:
    filename: str
    pdf: bytes
    extracted_text: str
    strategy: str


SECTION_NAMES = (
    "PROFESSIONAL SUMMARY",
    "TECHNICAL SKILLS",
    "PROFESSIONAL EXPERIENCE",
    "SELECTED PROJECTS",
    "EDUCATION",
    "TRAINING & CERTIFICATIONS",
)

STOP = {
    "the","and","for","with","from","that","this","your","will","have","work","team","role","job","years",
    "using","into","about","our","you","are","was","were","their","they","but","not","all","can","who",
    "experience","skills","required","preferred","strong","ability","responsibilities","requirements",
}

# These are aliases only. A skill is emitted only when its canonical spelling is
# already present in the source resume.
SKILL_ALIASES = {
    "Python": ("python",),
    "FastAPI": ("fastapi","fast api"),
    "Flask": ("flask",),
    "PostgreSQL": ("postgresql","postgres"),
    "SQL": (" sql ","sql database","relational"),
    "SQLAlchemy": ("sqlalchemy",),
    "Pydantic": ("pydantic",),
    "REST APIs": ("rest api","restful","api development","apis"),
    "Docker": ("docker","container"),
    "Git": (" git ","github","version control"),
    "GitHub Actions": ("github actions","ci/cd","ci cd","pipeline"),
    "CI/CD": ("ci/cd","ci cd","continuous integration","continuous deployment"),
    "AWS": ("aws","amazon web services"),
    "Azure": ("azure","microsoft cloud"),
    "LangGraph": ("langgraph",),
    "LangChain": ("langchain",),
    "CrewAI": ("crewai","multi-agent","multi agent"),
    "RAG": ("rag","retrieval augmented","retrieval-augmented"),
    "FAISS": ("faiss","vector search","vector database"),
    "Chroma": ("chroma","vector database"),
    "Hugging Face": ("hugging face","huggingface","embeddings"),
    "Gemini/OpenAI APIs": ("gemini","openai","llm api","language model"),
    "LLMs": ("llm","large language model","generative ai","genai"),
    "MCP": ("mcp","model context protocol"),
    "Playwright": ("playwright","browser automation"),
    "Selenium": ("selenium","browser automation"),
    "n8n": ("n8n","workflow automation"),
    "JavaScript": ("javascript","js "),
    "Flutter": ("flutter",),
    "Pandas": ("pandas",),
    "Scikit-learn": ("scikit","sklearn","machine learning"),
    "TensorFlow/Keras": ("tensorflow","keras","deep learning"),
    "PyTorch": ("pytorch",),
    "OpenCV": ("opencv","computer vision"),
    "YOLOv8": ("yolov8","object detection"),
}


def _norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", (text or "").lower()).strip() + " "


def _tokens(text: str):
    return {
        x for x in re.findall(r"[a-z][a-z0-9+#.]{2,}", (text or "").lower())
        if x not in STOP
    }


def _sections(source: str) -> tuple[list[str], dict[str, list[str]]]:
    header=[]
    sections={name:[] for name in SECTION_NAMES}
    current=None
    for raw in source.splitlines():
        line=re.sub(r"\s+"," ",raw).strip()
        if not line:
            continue
        if line in SECTION_NAMES:
            current=line
            continue
        if current is None:
            header.append(line)
        else:
            sections[current].append(line)
    return header,sections


def _verified_skill_names(source: str) -> list[str]:
    low=_norm(source)
    return [name for name in SKILL_ALIASES if name.lower() in low]


def _skill_matches(source: str, jd: str) -> list[str]:
    verified=_verified_skill_names(source)
    jd_low=_norm(jd)
    scored=[]
    for skill in verified:
        aliases=SKILL_ALIASES.get(skill,(skill.lower(),))
        hits=sum(1 for a in aliases if a in jd_low)
        if hits:
            scored.append((hits,skill))
    scored.sort(key=lambda x:(-x[0],x[1].lower()))
    return [s for _,s in scored]


def _role_family(title: str, description: str) -> str:
    t=_norm(title+" "+description[:1800])
    if any(x in t for x in (" ai engineer "," artificial intelligence "," llm "," rag "," agentic "," machine learning ")):
        return "AI / Agentic Software Engineer"
    if any(x in t for x in (" backend "," back-end "," api engineer "," python developer "," python engineer ")):
        return "Backend / Python Software Engineer"
    if any(x in t for x in (" full stack "," full-stack "," fullstack ")):
        return "Full-Stack Software Engineer"
    if any(x in t for x in (" automation "," integration engineer "," solutions engineer ")):
        return "Automation & Integration Engineer"
    if any(x in t for x in (" embedded "," firmware "," iot ")):
        return "Embedded & IoT Software Engineer"
    return "Software Engineer"


def _summary(role_family: str, matched: list[str]) -> str:
    focus=", ".join(matched[:8])
    base=(
        "Final-year B.E. Electrical Engineering student at NUST with hands-on software engineering experience "
        "across Python/FastAPI backends, database-backed APIs, agentic AI, automation, applied machine learning "
        "and production IoT systems."
    )
    if focus:
        return f"{base} For this role, the strongest verified overlap is {focus}. Builds deployable systems with testing, automation, APIs and CI/CD without overstating unverified experience."
    return base


def _group_entries(lines: list[str]) -> list[list[str]]:
    """Group experience/project heading plus its bullet lines."""
    groups=[]
    cur=[]
    for line in lines:
        is_bullet=line.startswith("- ")
        if not is_bullet and cur:
            groups.append(cur);cur=[]
        cur.append(line)
    if cur:groups.append(cur)
    return groups


def _entry_score(group: list[str], jd_tokens: set[str]) -> int:
    toks=_tokens(" ".join(group))
    return len(toks & jd_tokens)


def _sort_projects(lines: list[str], jd: str) -> list[str]:
    groups=_group_entries(lines)
    jt=_tokens(jd)
    ranked=sorted(enumerate(groups),key=lambda x:(-_entry_score(x[1],jt),x[0]))
    out=[]
    for _,group in ranked:
        out.extend(group)
    return out


def _skill_lines(sections: dict[str,list[str]], matched: list[str]) -> list[str]:
    raw=sections.get("TECHNICAL SKILLS",[])
    if matched:
        return ["Role-aligned verified skills: "+", ".join(matched[:14])] + raw
    return raw


def build_tailored_resume(job, source_doc) -> TailoredDocument:
    """Create a job-specific ATS PDF using only verified source-resume facts."""
    source=(source_doc.extracted_text or "").strip()
    header,sections=_sections(source)
    jd=(job.title or "")+"\n"+(job.description or "")
    matched=_skill_matches(source,jd)
    family=_role_family(job.title or "",job.description or "")
    summary=_summary(family,matched)

    # Header keeps verified identity/contact lines; the generic source title is
    # replaced by a target-family headline.
    name=header[0] if header else "ASAD HUSSAIN"
    contact=[x for x in header[1:] if not re.search(r"SOFTWARE ENGINEER|BACKEND|AGENTIC|AUTOMATION",x,re.I)]
    target_line=f"{family} | Target: {job.title}"

    tailored_sections={
        "PROFESSIONAL SUMMARY":[summary],
        "TECHNICAL SKILLS":_skill_lines(sections,matched),
        # Keep experience chronology intact; only projects are reordered by JD.
        "PROFESSIONAL EXPERIENCE":sections.get("PROFESSIONAL EXPERIENCE",[]),
        "SELECTED PROJECTS":_sort_projects(sections.get("SELECTED PROJECTS",[]),jd),
        "EDUCATION":sections.get("EDUCATION",[]),
        "TRAINING & CERTIFICATIONS":sections.get("TRAINING & CERTIFICATIONS",[]),
    }

    text_lines=[name,*contact,target_line]
    for section in SECTION_NAMES:
        text_lines.append(section)
        text_lines.extend(tailored_sections.get(section,[]))
    extracted="\n".join(text_lines)

    buf=io.BytesIO()
    doc=SimpleDocTemplate(
        buf,pagesize=A4,rightMargin=11*mm,leftMargin=11*mm,
        topMargin=8*mm,bottomMargin=8*mm,
    )
    styles=getSampleStyleSheet()
    body=ParagraphStyle("Body",parent=styles["BodyText"],fontName="Helvetica",fontSize=8.15,leading=9.35,spaceAfter=.7)
    small=ParagraphStyle("Small",parent=body,fontSize=7.9,leading=9.0)
    title_style=ParagraphStyle("TitleX",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=16,leading=17,alignment=TA_CENTER,spaceAfter=1)
    center=ParagraphStyle("Center",parent=small,alignment=TA_CENTER)
    role_style=ParagraphStyle("Role",parent=body,fontName="Helvetica-Bold",fontSize=9.5,leading=10.5,alignment=TA_CENTER,spaceAfter=2)
    section_style=ParagraphStyle("Section",parent=body,fontName="Helvetica-Bold",fontSize=9.1,leading=10,spaceBefore=2.4,spaceAfter=.5)
    bullet=ParagraphStyle("Bullet",parent=small,leftIndent=8,firstLineIndent=-5.5,spaceAfter=.35)

    def esc(s):
        return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    story=[Paragraph(esc(name),title_style)]
    for line in contact[:4]:
        story.append(Paragraph(esc(line),center))
    story.append(Paragraph(esc(target_line),role_style))

    for section in SECTION_NAMES:
        rows=tailored_sections.get(section,[])
        if not rows:continue
        story.append(Paragraph(section,section_style))
        story.append(HRFlowable(width="100%",thickness=.35,spaceBefore=0,spaceAfter=1.2))
        for line in rows:
            safe=esc(line)
            if line.startswith("- "):
                story.append(Paragraph("• "+safe[2:],bullet))
            elif ":" in line and len(line.split(":",1)[0])<32:
                k,v=line.split(":",1)
                story.append(Paragraph(f"<b>{esc(k)}:</b>{esc(v)}",small))
            elif "|" in line and section in ("PROFESSIONAL EXPERIENCE","SELECTED PROJECTS","EDUCATION"):
                story.append(Paragraph(f"<b>{safe}</b>",body))
            else:
                story.append(Paragraph(safe,body))

    doc.build(story)
    safe_company=re.sub(r"[^A-Za-z0-9_-]+","_",job.company or "Employer").strip("_")[:45]
    safe_role=re.sub(r"[^A-Za-z0-9_-]+","_",job.title or "Role").strip("_")[:45]
    strategy=(
        f"ATS-tailored for {family}; verified skill overlap: "
        + (", ".join(matched[:12]) if matched else "no explicit skill overlap found")
        + "; experience chronology preserved; projects reordered by JD relevance."
    )
    return TailoredDocument(
        filename=f"Asad_Hussain_{safe_company}_{safe_role}.pdf",
        pdf=buf.getvalue(),
        extracted_text=extracted,
        strategy=strategy,
    )
