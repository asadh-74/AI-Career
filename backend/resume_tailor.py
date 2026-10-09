"""Verified-content resume tailoring for Career Atlas."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.units import mm


@dataclass
class TailoredDocument:
    filename:str
    pdf:bytes
    extracted_text:str
    strategy:str


def _tokens(text:str):
    ignored={"the","and","for","with","from","that","this","your","will","have","work","team","role","job","years",
             "using","into","about","our","you","are","was","were","their","they","but","not","all","can","who"}
    return {x for x in re.findall(r"[a-z][a-z0-9+#.]{2,}",(text or "").lower()) if x not in ignored}


def build_tailored_resume(job,source_doc)->TailoredDocument:
    """Build a conservative PDF variant without adding facts.

    It keeps the original extracted resume content and adds only a relevance
    header made from terms that occur in BOTH the job description and resume.
    """
    source=(source_doc.extracted_text or "").strip()
    shared=sorted(_tokens((job.title or "")+" "+(job.description or "")) & _tokens(source))
    focus=shared[:18]
    strategy=("Prioritized verified overlap: "+", ".join(focus)) if focus else "Preserved source resume with no inferred additions."

    # Keep the source resume order intact. ATS tailoring adds a relevance
    # focus line but never shuffles experience, education or chronology.
    lines=[]
    for raw in source.splitlines():
        line=re.sub(r"\s+"," ",raw).strip()
        if line and (not lines or line!=lines[-1]):
            lines.append(line)
    ordered=lines

    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=15*mm,leftMargin=15*mm,topMargin=14*mm,bottomMargin=14*mm)
    styles=getSampleStyleSheet()
    styles["Title"].alignment=TA_CENTER
    story=[]
    # Preserve likely candidate name as title when first line is short.
    if ordered and len(ordered[0])<80:
        story.append(Paragraph(ordered[0],styles["Title"]))
        story.append(Spacer(1,4))
        body=ordered[1:]
    else:
        body=ordered
    if focus:
        story.append(Paragraph("<b>Relevant focus:</b> "+", ".join(focus[:12]),styles["BodyText"]))
        story.append(HRFlowable(width="100%",thickness=.4,spaceBefore=4,spaceAfter=7))
    for line in body[:220]:
        safe=(line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
        style=styles["Heading3"] if len(line)<55 and line.upper()==line and len(line.split())<8 else styles["BodyText"]
        story.append(Paragraph(safe,style))
        story.append(Spacer(1,2))
    doc.build(story)
    name=re.sub(r"[^A-Za-z0-9_-]+","_",job.company or "Employer").strip("_")[:60]
    return TailoredDocument(
        filename=f"Resume_{name}_{job.id}.pdf",
        pdf=buf.getvalue(),
        extracted_text="\n".join(ordered),
        strategy=strategy,
    )
