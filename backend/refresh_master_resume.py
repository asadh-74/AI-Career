"""One-time refresh of the Career Atlas master software/AI resume."""
from __future__ import annotations

import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from sqlalchemy import select

from main import Document, SessionLocal

FILENAME="Asad_Hussain_Software_AI_Resume_2026.pdf"

HEADER=[
    "ASAD HUSSAIN",
    "Software Engineer | Backend, Agentic AI & Automation",
    "Islamabad, Pakistan | +92 320 4141092 | asadh1521@gmail.com",
    "linkedin.com/in/asad-hussain92 | github.com/asadh-74 | automation-portfolio-steel.vercel.app",
]
SUMMARY=(
    "Final-year B.E. Electrical Engineering student at NUST with hands-on software engineering experience across "
    "Python/FastAPI backends, database-backed APIs, agentic AI, browser automation and production IoT systems. "
    "Builds end-to-end workflows with PostgreSQL/SQL Server, LangGraph, CrewAI, LangChain/RAG, Playwright, n8n, Docker and CI/CD, "
    "with deployment experience across Render, Vercel and cloud project environments."
)
SKILLS=[
    ("Programming","Python, JavaScript, SQL, C/C++, PHP, HTML/CSS"),
    ("Backend & APIs","FastAPI, Flask, Laravel, REST APIs, PostgreSQL, SQL Server, SQLite, SQLAlchemy, authentication/RBAC"),
    ("AI & Agentic","LangGraph, LangChain, CrewAI, RAG, FAISS, Hugging Face embeddings, Gemini/OpenAI APIs, prompt engineering"),
    ("Automation & DevOps","Playwright, Selenium, n8n, Docker, GitHub Actions, Linux, Git, CI/CD, Render, Vercel, Azure/AWS project exposure"),
    ("Data & ML","Pandas, Scikit-learn, TensorFlow/Keras, PyTorch, OpenCV, YOLOv8, Streamlit, Flutter"),
]
EXPERIENCE=[
    ("Full Stack Development Intern | Lantrotech, Islamabad | Jul-Aug 2026",[
        "Built and extended a construction-bid monitoring system using Python/FastAPI and browser automation; normalized multi-source procurement data, deduplicated records and exposed searchable results for estimating workflows.",
        "Integrated web interfaces, backend logic and persistent data while automating repetitive collection and verification steps.",
    ]),
    ("Backend AI Engineering Intern | FlyRank AI, Remote | Jun-Aug 2026",[
        "Built FastAPI CRUD services with persistent storage, validation and Docker-based reproducible deployment; progressed from in-memory APIs to SQLite-backed workflows.",
        "Applied LLM-assisted scoring and automation to search-intelligence and review-ranking tasks.",
    ]),
    ("Technical Intern | NEPRA, Islamabad | Jul-Aug 2026",[
        "Worked across IT and technical functions, including website/search-indexing tasks and document-intelligence workflows; analyzed utility and transformer data for regulatory reporting.",
    ]),
    ("Engineering Intern | RDC / Heavy Industries Taxila | Jun-Aug 2025",[
        "Delivered an operational fleet-management platform integrating ESP32/LTE, GPS, RFID, MQTT, Flask and SQL Server with live tracking, driver authentication and geofencing.",
    ]),
]
PROJECTS=[
    "Career Atlas - FastAPI, PostgreSQL, LangGraph, CrewAI, Playwright/Selenium, GitHub Actions, Gemini. Built a job-discovery and application-automation platform with multi-source ingestion, AI matching, ATS adapters, retry logic, evidence capture and confirmed-submission tracking.",
    "US Construction Bid Monitor - Python, FastAPI, Playwright, GitHub Actions. Aggregates public procurement sources, normalizes project data, deduplicates records and tracks contractor/contact evidence.",
    "AI Support Triage - n8n, Gmail API, Slack API, LLMs. Automates classification, urgency routing and contextual response drafting for support requests.",
]
EDUCATION="B.E. Electrical Engineering | National University of Sciences and Technology (NUST) | 2023-2027 (expected)"
TRAINING="AtomCamp AI Bootcamp: Deep Learning, NLP/LLMs, Generative AI, RAG, LangChain/LangGraph, n8n, MLOps/Deployment | IBM Data Analysis with Python | Microsoft/edX AI Apps & Agents on Azure"

def build_pdf():
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=12*mm,leftMargin=12*mm,topMargin=9*mm,bottomMargin=9*mm)
    styles=getSampleStyleSheet()
    body=ParagraphStyle("Body",parent=styles["BodyText"],fontName="Helvetica",fontSize=8.5,leading=10.1,spaceAfter=1.2)
    small=ParagraphStyle("Small",parent=body,fontSize=8.1,leading=9.4)
    title=ParagraphStyle("Title",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=17,leading=18,alignment=TA_CENTER,spaceAfter=1)
    subtitle=ParagraphStyle("Subtitle",parent=body,fontName="Helvetica-Bold",fontSize=10.3,leading=11.5,alignment=TA_CENTER,spaceAfter=1)
    center=ParagraphStyle("Center",parent=small,alignment=TA_CENTER)
    section=ParagraphStyle("Section",parent=body,fontName="Helvetica-Bold",fontSize=9.5,leading=10.3,spaceBefore=3,spaceAfter=1)
    role=ParagraphStyle("Role",parent=body,fontName="Helvetica-Bold",fontSize=8.7,leading=9.8,spaceBefore=1.2,spaceAfter=.6)
    bullet=ParagraphStyle("Bullet",parent=small,leftIndent=9,firstLineIndent=-6,spaceAfter=.5)

    story=[Paragraph(HEADER[0],title),Paragraph(HEADER[1],subtitle),Paragraph(HEADER[2],center),Paragraph(HEADER[3],center)]
    def sec(t):
        story.extend([Paragraph(t,section),HRFlowable(width="100%",thickness=.45,spaceBefore=0,spaceAfter=2)])
    sec("PROFESSIONAL SUMMARY"); story.append(Paragraph(SUMMARY,body))
    sec("TECHNICAL SKILLS")
    for k,v in SKILLS:story.append(Paragraph(f"<b>{k}:</b> {v}",small))
    sec("EXPERIENCE")
    for heading,bullets in EXPERIENCE:
        story.append(Paragraph(heading,role))
        for b in bullets:story.append(Paragraph("• "+b,bullet))
    sec("SELECTED PROJECTS")
    for p in PROJECTS:story.append(Paragraph("• "+p,bullet))
    sec("EDUCATION & TRAINING")
    story.append(Paragraph(f"<b>{EDUCATION}</b>",body));story.append(Paragraph(TRAINING,small))
    doc.build(story)
    return buf.getvalue()

def extracted_text():
    lines=HEADER+["PROFESSIONAL SUMMARY",SUMMARY,"TECHNICAL SKILLS"]
    lines += [f"{k}: {v}" for k,v in SKILLS]
    lines += ["EXPERIENCE"]
    for h,bs in EXPERIENCE:lines += [h]+bs
    lines += ["SELECTED PROJECTS"]+PROJECTS+["EDUCATION & TRAINING",EDUCATION,TRAINING]
    return "\n".join(lines)

def main():
    pdf=build_pdf()
    text=extracted_text()
    with SessionLocal() as session:
        row=session.scalar(select(Document).where(Document.kind=="Resume"))
        if not row:
            row=Document(kind="Resume",filename=FILENAME,pdf=pdf,extracted_text=text)
            session.add(row)
        else:
            row.filename=FILENAME
            row.pdf=pdf
            row.extracted_text=text
        session.commit()
        print({"status":"updated","filename":FILENAME,"pdf_bytes":len(pdf),"text_chars":len(text)})

if __name__=="__main__":
    main()
