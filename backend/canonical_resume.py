"""Canonical verified software/AI resume profile used for automatic applications.

Keep this file limited to facts already supported by the user's projects,
experience, uploaded records, or explicit profile information.
"""

RESUME_PROFILE_VERSION = "2026-10-10-v1"

CANONICAL_RESUME_TEXT = """ASAD HUSSAIN
Islamabad, Pakistan | +92 320 4141092 | asadh1521@gmail.com
LinkedIn: linkedin.com/in/asad-hussain92 | GitHub: github.com/asadh-74
Portfolio: automation-portfolio-steel.vercel.app

SOFTWARE ENGINEER | BACKEND, AGENTIC AI & AUTOMATION

PROFESSIONAL SUMMARY
Final-year B.E. Electrical Engineering student at the National University of Sciences and Technology (NUST) with hands-on software engineering experience across Python/FastAPI backends, database-backed APIs, agentic AI, browser automation, applied machine learning and production IoT systems. Builds end-to-end workflows using PostgreSQL/SQL Server, SQLAlchemy, Pydantic, LangGraph, LangChain, CrewAI, RAG, Playwright, Selenium, n8n, Docker and CI/CD, with deployments across Render, Vercel and cloud project environments.

TECHNICAL SKILLS
Programming: Python, JavaScript, SQL, C/C++, PHP, HTML/CSS
Backend & APIs: FastAPI, Flask, Laravel, REST APIs, PostgreSQL, SQL Server, SQLite, SQLAlchemy, Pydantic, authentication, RBAC, API integrations, WebSockets
Agentic AI & LLMs: LangGraph, LangChain, CrewAI, RAG, FAISS, Chroma, Hugging Face embeddings, Gemini/OpenAI APIs, MCP, prompt engineering, tool calling, multi-agent workflows
Automation & DevOps: Playwright, Selenium, n8n, Zapier, Docker, GitHub Actions, Linux, Git, CI/CD, Render, Vercel, Azure/AWS project exposure
Data & ML: Pandas, Scikit-learn, TensorFlow/Keras, PyTorch, OpenCV, YOLOv8, Streamlit
Frontend & Mobile: JavaScript, HTML/CSS, Flutter

PROFESSIONAL EXPERIENCE
Full Stack Development Intern | Lantrotech, Islamabad | Jul-Aug 2026
- Built and extended a construction-bid monitoring system using Python/FastAPI and browser automation; normalized multi-source procurement data, deduplicated records and exposed searchable results for estimating workflows.
- Integrated web interfaces, backend logic and persistent data while automating repetitive collection and verification steps.

Backend AI Engineering Intern | FlyRank AI, Remote | Jun-Aug 2026
- Built FastAPI CRUD services with persistent storage, validation and Docker-based reproducible deployment; progressed from in-memory APIs to SQLite-backed workflows.
- Applied LLM-assisted scoring and automation to search-intelligence and review-ranking tasks.

Technical Intern | NEPRA, Islamabad | Jul-Aug 2026
- Worked across IT and technical functions, including website/search-indexing tasks and document-intelligence workflows; analyzed utility and transformer data for regulatory reporting.

Engineering Intern | RDC / Heavy Industries Taxila | Jun-Aug 2025
- Delivered an operational fleet-management platform integrating ESP32/LTE, GPS, RFID, MQTT, Flask and SQL Server with live tracking, driver authentication and geofencing.

SELECTED PROJECTS
Career Atlas | FastAPI, PostgreSQL, SQLAlchemy, LangGraph, CrewAI, LangChain, Playwright, Selenium, GitHub Actions, Gemini
- Built a job-discovery and application-automation platform with multi-source ingestion, AI matching, ATS adapters, retry logic, evidence capture, resume tailoring and confirmed-submission tracking.

US Construction Bid Monitor | Python, FastAPI, Playwright, GitHub Actions
- Built a multi-source procurement pipeline that normalizes project data, deduplicates opportunities and tracks contractor/contact evidence.

AI Support Triage | n8n, Gmail API, Slack API, LLMs
- Automated support-email classification, urgency routing and contextual response drafting.

Agentic Knowledge Assistant | Python, RAG, LangChain, FAISS/Chroma
- Built document retrieval and grounded question-answering workflows using embeddings and vector search.

EDUCATION
B.E. Electrical Engineering | National University of Sciences and Technology (NUST) | 2023-2027 (expected)

TRAINING & CERTIFICATIONS
AtomCamp AI Bootcamp: Deep Learning, NLP/LLMs, Generative AI, RAG, LangChain/LangGraph, n8n, MLOps/Deployment
IBM Data Analysis with Python | Microsoft/edX AI Apps & Agents on Azure
"""


def build_canonical_resume_pdf():
    """Generate a compact ATS-friendly PDF from the canonical verified text."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=12*mm,leftMargin=12*mm,topMargin=9*mm,bottomMargin=9*mm)
    styles=getSampleStyleSheet()
    body=ParagraphStyle("Body",parent=styles["BodyText"],fontName="Helvetica",fontSize=8.3,leading=9.7,spaceAfter=1)
    small=ParagraphStyle("Small",parent=body,fontSize=8.0,leading=9.2)
    title=ParagraphStyle("Title",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=16.5,leading=17,alignment=TA_CENTER,spaceAfter=1)
    subtitle=ParagraphStyle("Subtitle",parent=body,fontName="Helvetica-Bold",fontSize=10,leading=11,alignment=TA_CENTER,spaceAfter=1)
    section=ParagraphStyle("Section",parent=body,fontName="Helvetica-Bold",fontSize=9.3,leading=10,spaceBefore=3,spaceAfter=1)

    lines=[x.rstrip() for x in CANONICAL_RESUME_TEXT.splitlines()]
    story=[]
    section_names={"PROFESSIONAL SUMMARY","TECHNICAL SKILLS","PROFESSIONAL EXPERIENCE","SELECTED PROJECTS","EDUCATION","TRAINING & CERTIFICATIONS"}
    for idx,line in enumerate(lines):
        if not line:
            continue
        safe=line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if idx==0:
            story.append(Paragraph(safe,title))
        elif idx==1:
            story.append(Paragraph(safe,subtitle))
        elif idx in (2,3,4):
            center=ParagraphStyle(f"Center{idx}",parent=small,alignment=TA_CENTER)
            story.append(Paragraph(safe,center))
        elif line in section_names:
            story.append(Paragraph(safe,section))
            story.append(HRFlowable(width="100%",thickness=.45,spaceBefore=0,spaceAfter=2))
        elif line.startswith("- "):
            story.append(Paragraph("• "+safe[2:],ParagraphStyle("Bullet",parent=small,leftIndent=9,firstLineIndent=-6,spaceAfter=.4)))
        elif "|" in line and any(x in line for x in ("Intern","Engineering Intern","B.E. Electrical Engineering")):
            story.append(Paragraph(f"<b>{safe}</b>",body))
        elif ":" in line and line.split(":",1)[0] in {"Programming","Backend & APIs","Agentic AI & LLMs","Automation & DevOps","Data & ML","Frontend & Mobile"}:
            k,v=line.split(":",1)
            story.append(Paragraph(f"<b>{k}:</b>{v}",small))
        else:
            story.append(Paragraph(safe,body))
    doc.build(story)
    return buf.getvalue()
