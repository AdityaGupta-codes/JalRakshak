"""Decision records for review meetings, audits and RTI replies, and pilot indicators (KPIs).

run_report_csv / run_report_html: one applied (or shadow) plan with everything needed to explain it: who
applied it, under which policy version and government reference, the alternatives that were compared, any
officer override with its reason, and the litres each organisation received. The HTML version is laid out
for printing ("Save as PDF" in the browser), so no PDF library is needed on the server.
"""
import csv
import html
import io
import json
from datetime import datetime, timedelta, timezone

from . import service
from .policy import plain

ALG = service.ALGORITHM_NAMES
METRIC_LABELS = [("avg_wait_time", "Average wait (min)"), ("jain_fairness", "Fairness (Jain index)"),
                 ("priority_weighted_satisfaction", "Importance-weighted delivery"),
                 ("satisfaction_ratio", "Share of requested water delivered"),
                 ("unserved_count", "Requests left with nothing"), ("deadline_miss_count", "Missed deadlines")]


def _json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return plain(v)


def _run(conn, run_id):
    run = service.get_run(conn, run_id)
    for k in ("metrics", "overrides", "comparison"):
        run[k] = _json(run.get(k))
    return run


def run_report_csv(conn, run_id):
    run = _run(conn, run_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["JalRakshak decision record", f"plan #{run['run_id']}"])
    w.writerow(["Rule", ALG.get(run["algorithm"], run["algorithm"])])
    w.writerow(["Applied", "yes" if run["committed"] else "no (shadow / pilot record)"])
    w.writerow(["Recorded at", str(run["created_at"])])
    w.writerow(["Recorded by", run.get("created_by_email") or "system"])
    w.writerow(["Policy", f"v{run.get('policy_version')} {run.get('policy_title') or ''}".strip()])
    w.writerow(["Policy reference", run.get("policy_reference") or ""])
    w.writerow(["Water before (L)", run["available_before"]])
    w.writerow(["Water after (L)", run["available_after"]])
    w.writerow(["Officer override reason", run.get("override_reason") or ""])
    w.writerow([])
    w.writerow(["request_id", "organisation", "type", "priority", "requested_litres", "allocated_litres",
                "share", "wait_minutes", "score", "changed_by_officer"])
    changed = {c["request_id"]: c for c in (run.get("overrides") or [])}
    for a in run["allocations"]:
        req, got = float(a["requested_litres"]), float(a["allocated_litres"])
        w.writerow([a["request_id"], a["user_name"], a["user_type"], a["priority"], req, got,
                    f"{got / req:.3f}" if req else "", a["wait_minutes"], a["score"],
                    f"plan {changed[a['request_id']]['plan_litres']} L" if a["request_id"] in changed else ""])
    comparison = run.get("comparison") or {}
    if comparison:
        w.writerow([])
        w.writerow(["Alternatives compared"] + [ALG.get(k, k) for k in comparison])
        for key, label in METRIC_LABELS:
            w.writerow([label] + [(m or {}).get(key, "") for m in comparison.values()])
    return buf.getvalue()


def run_report_html(conn, run_id):
    run = _run(conn, run_id)
    e = lambda v: html.escape("" if v is None else str(v))  # noqa: E731
    changed = {c["request_id"]: c for c in (run.get("overrides") or [])}
    rows = []
    for a in run["allocations"]:
        req, got = float(a["requested_litres"]), float(a["allocated_litres"])
        wait = "" if a["wait_minutes"] is None else f"{float(a['wait_minutes']):.0f}"
        note = f"changed by officer (plan: {changed[a['request_id']]['plan_litres']:,.0f} L)" \
            if a["request_id"] in changed else ""
        rows.append(f"<tr><td>{a['request_id']}</td><td>{e(a['user_name'])}</td><td>{e(a['user_type'])}</td>"
                    f"<td>{a['priority']}</td><td class=n>{req:,.0f}</td><td class=n>{got:,.2f}</td>"
                    f"<td class=n>{(got / req if req else 0):.0%}</td>"
                    f"<td class=n>{wait}</td>"
                    f"<td>{e(note)}</td></tr>")
    comparison = run.get("comparison") or {}
    comp = ""
    if comparison:
        head = "".join(f"<th>{e(ALG.get(k, k))}{' (chosen)' if k == run['algorithm'] else ''}</th>" for k in comparison)
        body = "".join(
            f"<tr><td>{e(label)}</td>" + "".join(
                f"<td class=n>{e((m or {}).get(key, '-'))}</td>" for m in comparison.values()) + "</tr>"
            for key, label in METRIC_LABELS)
        comp = f"<h2>Alternatives that were compared</h2><table><tr><th>Measure</th>{head}</tr>{body}</table>"
    override = ""
    if run.get("override_reason"):
        override = (f"<h2>Officer override</h2><p><b>Reason recorded:</b> {e(run['override_reason'])}</p>"
                    f"<p>{len(changed)} request(s) changed from the computed plan.</p>")
    status = "Applied" if run["committed"] else "Not applied (shadow / pilot record)"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Decision record - plan {run['run_id']}</title>
<style>
body{{font:14px/1.5 Arial,sans-serif;color:#111;margin:32px;max-width:960px}}
h1{{font-size:20px;margin:0}} h2{{font-size:16px;margin:24px 0 8px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #999;padding:4px 6px;text-align:left}}
th{{background:#eee}} td.n{{text-align:right}} .meta td{{border:none;padding:2px 12px 2px 0}}
.sign{{margin-top:48px;display:flex;gap:80px}} .sign div{{border-top:1px solid #333;padding-top:4px;width:240px}}
@media print{{button{{display:none}} body{{margin:12mm}}}}
</style></head><body>
<button onclick="window.print()">Print / Save as PDF</button>
<h1>Water allocation - decision record</h1>
<p>Plan #{run['run_id']} &middot; {e(status)}</p>
<table class=meta>
<tr><td>Rule used</td><td><b>{e(ALG.get(run['algorithm'], run['algorithm']))}</b></td></tr>
<tr><td>Recorded at</td><td>{e(run['created_at'])}</td></tr>
<tr><td>Recorded by</td><td>{e(run.get('created_by_email') or 'system')}</td></tr>
<tr><td>Water policy</td><td>v{e(run.get('policy_version'))} {e(run.get('policy_title'))}</td></tr>
<tr><td>Policy reference</td><td>{e(run.get('policy_reference'))}</td></tr>
<tr><td>Water available before / after</td><td>{float(run['available_before']):,.2f} L / {float(run['available_after']):,.2f} L</td></tr>
</table>
{override}
<h2>Allocation to each organisation</h2>
<table><tr><th>#</th><th>Organisation</th><th>Type</th><th>Priority</th><th>Asked (L)</th><th>Allocated (L)</th>
<th>Share</th><th>Wait (min)</th><th>Note</th></tr>{''.join(rows)}</table>
{comp}
<p style="margin-top:24px;font-size:12px;color:#555">Generated by JalRakshak from the database record of this plan.
The audit log keeps every action and cannot be edited.</p>
<div class=sign><div>Prepared by</div><div>Approved by</div></div>
</body></html>"""


def kpis(conn, days=30):
    """Indicators for a pilot: how plans were made and what they achieved, over the last `days` days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FILTER (WHERE committed) AS applied, "
                    "COUNT(*) FILTER (WHERE committed AND overrides IS NOT NULL) AS overridden, "
                    "COUNT(*) FILTER (WHERE scenario = 'shadow') AS shadow "
                    "FROM scheduler_runs WHERE created_at >= %s", (since,))
        runs = cur.fetchone()
        cur.execute("SELECT essential_classes FROM policies WHERE status = 'approved' LIMIT 1")
        row = cur.fetchone()
        essential = _json(row["essential_classes"]) if row else ["hospital", "domestic"]
        cur.execute("SELECT COALESCE(SUM(a.allocated_litres), 0) AS got, COALESCE(SUM(r.requested_litres), 0) AS asked, "
                    "COUNT(*) FILTER (WHERE a.allocated_litres = 0) AS empty, COUNT(*) AS n "
                    "FROM allocations a JOIN scheduler_runs s ON s.run_id = a.run_id AND s.committed "
                    "JOIN water_requests r ON r.request_id = a.request_id JOIN users u ON u.user_id = r.user_id "
                    "WHERE s.created_at >= %s AND u.user_type = ANY(%s)", (since, list(essential)))
        ess = cur.fetchone()
        cur.execute("SELECT COUNT(*) FILTER (WHERE a.allocated_litres = 0) AS empty, COUNT(*) AS n "
                    "FROM allocations a JOIN scheduler_runs s ON s.run_id = a.run_id AND s.committed "
                    "WHERE s.created_at >= %s", (since,))
        allreq = cur.fetchone()
        cur.execute("SELECT category, COUNT(*) AS n FROM issues WHERE created_at >= %s GROUP BY category "
                    "ORDER BY category", (since,))
        issues_by = {r["category"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) FILTER (WHERE status IN ('open', 'in_progress')) AS open, "
                    "AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600) "
                    "FILTER (WHERE resolved_at IS NOT NULL) AS hours FROM issues WHERE created_at >= %s", (since,))
        iss = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM alerts WHERE alert_type IN ('anomaly', 'complaints') AND created_at >= %s",
                    (since,))
        leak_alerts = cur.fetchone()["n"]
        cur.execute("SELECT for_date, result FROM supply_timetables WHERE status = 'published' "
                    "ORDER BY for_date DESC LIMIT 1")
        tt = cur.fetchone()
        cur.execute("SELECT COALESCE(SUM(loads) FILTER (WHERE status = 'delivered'), 0) AS delivered, "
                    "COALESCE(SUM(litres) FILTER (WHERE status = 'delivered'), 0) AS litres "
                    "FROM tanker_trips WHERE planned_for >= %s::date", (since,))
        tank = cur.fetchone()
        cur.execute("SELECT version, title FROM policies WHERE status = 'approved' LIMIT 1")
        pol = cur.fetchone()
    applied = int(runs["applied"] or 0)
    tt_metrics = (_json(tt["result"]) or {}).get("metrics") if tt else None
    return {
        "days": days,
        "plans_applied": applied,
        "plans_overridden": int(runs["overridden"] or 0),
        "override_rate": (int(runs["overridden"] or 0) / applied) if applied else None,
        "shadow_plans": int(runs["shadow"] or 0),
        "essential_classes": essential,
        "essential_share_delivered": (float(ess["got"]) / float(ess["asked"])) if float(ess["asked"] or 0) else None,
        "essential_requests_left_empty": int(ess["empty"] or 0),
        "requests_decided": int(allreq["n"] or 0),
        "requests_left_empty": int(allreq["empty"] or 0),
        "issues_by_category": issues_by,
        "issues_open": int(iss["open"] or 0),
        "issue_resolution_hours": None if iss["hours"] is None else round(float(iss["hours"]), 1),
        "leak_alerts": int(leak_alerts),
        "latest_timetable": {"for_date": str(tt["for_date"]), **(tt_metrics or {})} if tt else None,
        "tanker_trips_delivered": int(tank["delivered"] or 0),
        "tanker_litres_delivered": float(tank["litres"] or 0),
        "active_policy": dict(pol) if pol else None,
    }
