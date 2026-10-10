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

    return {**state, "route": "review_gate", "result": result}


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
_builder.add_edge("browser", "review_gate")
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
    return state.get("result") or {
        "status": "failed",
        "reason": "LangGraph finished without a result",
    }
