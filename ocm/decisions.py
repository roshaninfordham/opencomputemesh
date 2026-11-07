"""Exportable routing decisions: the labelled dataset a house model trains on.

One row per terminal job: what was asked for (model), what the router said
(route reason), where it went (node plus that node's runtime tier), and what
happened (status, failure code, tokens, timings). No prompt or output text is
included — the jobs table holds none, and this module adds none. That property is
what makes the export safe to hand to a training job anywhere.
"""

from .db import rows

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "EXPIRED")


def export_decisions(db, limit: int = 1000, since: str | None = None) -> list[dict]:
    """Newest terminal jobs first. `since` is an ISO timestamp floor on created_at."""
    where = "AND j.created_at >= ?" if since else ""
    out = []
    for j in rows(db, f"""SELECT j.id, j.model_id, j.model_digest, j.route_reason, j.status,
                                 j.failure_code, j.input_tokens, j.output_tokens,
                                 j.worker_elapsed_ms, j.time_to_first_token_ms,
                                 j.settlement_capped, j.created_at, j.selected_node_id,
                                 n.runtime_name, n.runtime_scope
                          FROM jobs j LEFT JOIN nodes n ON n.id = j.selected_node_id
                          WHERE j.status IN ('COMPLETED','FAILED','CANCELLED','EXPIRED')
                          {where} ORDER BY j.created_at DESC LIMIT ?""",
                   *(([since] if since else []) + [limit])):
        out.append({
            "job_id": j["id"],
            "model_id": j["model_id"],
            "model_digest": j["model_digest"],
            "route_reason": j["route_reason"],
            "node_id": j["selected_node_id"],
            "node_runtime": j["runtime_name"],
            "node_runtime_scope": j["runtime_scope"],
            "status": j["status"],
            "failure_code": j["failure_code"],
            "input_tokens": j["input_tokens"],
            "output_tokens": j["output_tokens"],
            "worker_elapsed_ms": j["worker_elapsed_ms"],
            "time_to_first_token_ms": j["time_to_first_token_ms"],
            "settlement_capped": bool(j["settlement_capped"]),
            "created_at": j["created_at"],
        })
    return out
