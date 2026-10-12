"""LangGraph orchestration for one Career Atlas application attempt."""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from automation import (
    apply_with_playwright,
    apply_with_selenium,
    email_subject,
    find_application_email,
    naturalize_draft,
    send_email_application,
    generate_verified_application_answers,
)


class ApplicationState(TypedDict, total=False):
    job: Any
    doc: Any
    cfg: Any
    score: int
    draft: str
    result: dict
    route: str
    learned_answers: list
    agent_generated_answers: list


def _score_gate(state: ApplicationState) -> ApplicationState:
    cfg = state["cfg"]
    score = int(state.get("score") or 0)
    if score < cfg.min_match_score:
        return {
            **state,
            "route": "below_threshold",
            "result": {
                "status": "below_threshold",
                "reason": f"Match score {score} is below {cfg.min_match_score}",
            },
        }
    return {**state, "route": "application"}


def _email_attempt(state: ApplicationState) -> ApplicationState:
    job, doc, cfg = state["job"], state["doc"], state["cfg"]
    draft = naturalize_draft(job, state.get("draft", ""))
    address = find_application_email(job.description)
    if not address or not cfg.allow_email:
        return {**state, "route": "browser"}

    try:
        receipt = send_email_application(
            address, email_subject(job), draft, doc.filename, doc.pdf
        )
        return {
            **state,
            "route": "done",
            "result": {"status": "applied", "receipt": receipt},
        }
    except Exception as exc:
        return {
            **state,
            "route": "browser",
            "result": {
                "status": "failed",
                "reason": f"Email failed: {type(exc).__name__}: {str(exc)[:180]}",
            },
        }


def _browser_attempt(state: ApplicationState) -> ApplicationState:
    job, doc, cfg = state["job"], state["doc"], state["cfg"]
    if not cfg.allow_browser:
        previous = state.get("result") or {}
        reason = previous.get("reason") or "Browser applications are disabled"
        return {
            **state,
            "route": "done",
            "result": {"status": "needs_human", "reason": reason},
        }

    draft = naturalize_draft(job, state.get("draft", ""))
    result = apply_with_playwright(
        job.apply_url,
        doc.pdf,
        doc.filename,
        draft,
        state.get("learned_answers") or [],
    )

    if result.get("status") == "failed":
        fallback = apply_with_selenium(job.apply_url, doc.pdf, doc.filename, draft)
        if fallback.get("status") != "failed":
            result = fallback

    return {**state, "route": "agent_repair", "result": result}


def _agent_repair(state: ApplicationState) -> ApplicationState:
    """Run several bounded evidence-grounded repair rounds before human review.

    Each round re-opens the application using all answers learned so far. The
    generator is still forbidden from inventing protected/legal/unknown facts.
    """
    result=state.get("result") or {}
    if result.get("status")!="needs_human":
        return {**state,"route":"review_gate","result":result}

    job,doc,cfg=state["job"],state["doc"],state["cfg"]
    combined=list(state.get("learned_answers") or [])
    all_generated=[]
    seen={(str(x.get("label") or "").strip().lower()) for x in combined}
    repair_rounds=0

    for _ in range(3):
        if result.get("status")!="needs_human":
            break
        fields=result.get("review_fields") or []
        if not fields:
            break
        generated=generate_verified_application_answers(
            job,
            getattr(doc,"extracted_text",""),
            naturalize_draft(job,state.get("draft","")),
            fields,
        )
        fresh=[]
        for item in generated:
            key=str(item.get("label") or "").strip().lower()
            if key and key not in seen:
                combined.append(item)
                fresh.append(item)
                all_generated.append(item)
                seen.add(key)
        if not fresh:
            break
        repair_rounds+=1
        result=apply_with_playwright(
            job.apply_url,
            doc.pdf,
            doc.filename,
            naturalize_draft(job,state.get("draft","")),
            combined,
        )
        if result.get("status")=="failed":
            fallback=apply_with_selenium(
                job.apply_url,doc.pdf,doc.filename,
                naturalize_draft(job,state.get("draft",""))
            )
            if fallback.get("status")!="failed":
                result=fallback

    meta=dict(result.get("meta") or {})
    meta["agentGeneratedFields"]=[str(x.get("label") or "")[:240] for x in all_generated]
    meta["agentGeneratedCount"]=len(all_generated)
    meta["agentRepairRounds"]=repair_rounds
    result["meta"]=meta
    return {
        **state,
        "route":"review_gate",
        "learned_answers":combined,
        "agent_generated_answers":all_generated,
        "result":result,
    }

def _review_gate(state: ApplicationState) -> ApplicationState:
    """Persist an explicit human-review state without losing browser progress."""
    result = state.get("result") or {}
    if result.get("status") == "needs_human":
        result = {
            **result,
            "review_required": True,
            "graph_state": "awaiting_review",
        }
        return {**state, "route": "awaiting_review", "result": result}
    return {**state, "route": "done", "result": result}


def _route_after_score(state: ApplicationState) -> str:
    return "stop" if state.get("route") == "below_threshold" else "email"


def _route_after_email(state: ApplicationState) -> str:
    return "stop" if state.get("route") == "done" else "browser"


_builder = StateGraph(ApplicationState)
_builder.add_node("score_gate", _score_gate)
_builder.add_node("email", _email_attempt)
_builder.add_node("browser", _browser_attempt)
_builder.add_node("agent_repair", _agent_repair)
_builder.add_node("review_gate", _review_gate)
_builder.set_entry_point("score_gate")
_builder.add_conditional_edges(
    "score_gate",
    _route_after_score,
    {"stop": END, "email": "email"},
)
_builder.add_conditional_edges(
    "email",
    _route_after_email,
    {"stop": END, "browser": "browser"},
)
_builder.add_edge("browser", "agent_repair")
_builder.add_edge("agent_repair", "review_gate")
_builder.add_edge("review_gate", END)
APPLICATION_GRAPH = _builder.compile()


def run_application_graph(job, doc, cfg, score: int, draft: str, learned_answers=None) -> dict:
    state = APPLICATION_GRAPH.invoke(
        {
            "job": job,
            "doc": doc,
            "cfg": cfg,
            "score": score,
            "draft": draft,
            "learned_answers": learned_answers or [],
        }
    )
    result=state.get("result") or {
        "status": "failed",
        "reason": "LangGraph finished without a result",
    }
    # Internal hand-off to worker.py. The worker persists these for this
    # application only and removes the key before any user-facing storage.
    generated=state.get("agent_generated_answers") or []
    if generated:
        result["_agent_generated_answers"]=generated
    return result
