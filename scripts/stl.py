#!/usr/bin/env python3
"""Speed-to-Lead Test: log demo requests, collect every reply, score the response.

Part of the speed-to-lead plugin. Standard library only.

A run is a folder (default ./runs/<name>) holding:
  run.json        run settings: name, response window in days, created time
  companies.json  the companies tested and how each form behaved at submission
  touches.jsonl   every reply: email, call, voicemail, sms, linkedin
  numbers.json    caller phone number -> company, used to attribute calls and texts
  unmatched.jsonl calls and texts pulled from Twilio that are not yet attributed
  impact.json     funnel inputs and pipeline model, written by `impact`
  scores.json     written by `score`
  report.md       written by `report`

Commands:
  init RUN [--window-days 7]
  add-company RUN --name NAME --domain DOMAIN [--form-url URL]
  submit RUN --company ID [--at ISO] [--fields N] [--scheduler none|instant] [--phone-required]
  touch RUN --company ID --channel CH --at ISO --kind human|auto
        [--personalization 0|1|2] [--meeting none|questions|link|times] [--summary TEXT] [--from ADDR]
  import RUN FILE.json            add a list of touch objects (used after reading the inbox)
  map-number RUN --number E164 --company ID   attribute a caller and re-check unmatched events
  twilio-pull RUN [--since ISO]   read calls and texts to TWILIO_NUMBER (read-only)
  status RUN
  score RUN
  report RUN
  impact RUN --company ID --monthly-requests N --qualify-rate R --opp-rate R --pipeline-per-opp USD
        [--target-minutes 60] [--current-minutes M] [--win-rate R] [--qualify-rate-at-target R] [--cap R]
                                  model the pipeline gained by answering faster; rates accept 0.2, 20 or 20%
  sample OUTDIR                  build a synthetic run, score it and write the report

Twilio credentials come from the environment and are never printed:
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_NUMBER (E.164, for example +14155550100)
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

CHANNELS = ("email", "call", "voicemail", "sms", "linkedin")
KINDS = ("human", "auto")
MEETING_LEVELS = ("none", "questions", "link", "times")


# ---------- storage ----------

def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


class Run:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def settings(self) -> dict:
        s = _read_json(self.root / "run.json", None)
        if s is None:
            raise SystemExit(f"error: {self.root} is not a run folder (no run.json). Run `init` first.")
        return s

    def companies(self) -> list[dict]:
        return _read_json(self.root / "companies.json", [])

    def save_companies(self, rows: list[dict]) -> None:
        _write_json(self.root / "companies.json", rows)

    def company(self, cid: str) -> dict:
        for c in self.companies():
            if c["id"] == cid:
                return c
        raise SystemExit(f"error: unknown company id {cid!r}. Known: {', '.join(c['id'] for c in self.companies()) or 'none'}")

    def touches(self) -> list[dict]:
        return _read_jsonl(self.root / "touches.jsonl")

    def numbers(self) -> dict:
        return _read_json(self.root / "numbers.json", {})


# ---------- helpers ----------

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_time(value: str) -> datetime:
    """Parse ISO 8601. A value with no offset is rejected so times are never ambiguous."""
    v = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        raise ValueError(f"time {value!r} has no UTC offset; use e.g. 2026-11-18T09:30:00-05:00")
    return dt


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_number(n: str) -> str:
    digits = re.sub(r"[^\d+]", "", n)
    if digits and not digits.startswith("+"):
        digits = "+1" + digits if len(digits) == 10 else "+" + digits
    return digits


def validate_touch(t: dict, company_ids: set[str]) -> dict:
    required = ("company", "channel", "at", "kind")
    missing = [k for k in required if not t.get(k)]
    if missing:
        raise ValueError(f"touch missing {', '.join(missing)}: {t}")
    if t["company"] not in company_ids:
        raise ValueError(f"touch for unknown company {t['company']!r}")
    if t["channel"] not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {t['channel']!r}")
    if t["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {t['kind']!r}")
    p = int(t.get("personalization", 0))
    if p not in (0, 1, 2):
        raise ValueError("personalization must be 0, 1 or 2")
    m = t.get("meeting", "none")
    if m not in MEETING_LEVELS:
        raise ValueError(f"meeting must be one of {MEETING_LEVELS}")
    return {
        "company": t["company"],
        "channel": t["channel"],
        "at": iso(parse_time(t["at"])),
        "kind": t["kind"],
        "personalization": p,
        "meeting": m,
        "from": t.get("from", ""),
        "summary": t.get("summary", ""),
        "source": t.get("source", "manual"),
        "ref": t.get("ref", ""),
    }


# ---------- scoring ----------
# The rubric is documented in method/rubric.md. Keep the two in step.

SPEED_BANDS = [  # (max minutes, points)
    (5, 40), (15, 32), (60, 24), (240, 16), (1440, 8), (4320, 3),
]


def speed_points(minutes: float | None) -> int:
    if minutes is None:
        return 0
    for limit, pts in SPEED_BANDS:
        if minutes <= limit:
            return pts
    return 0


def persistence_points(n_human: int) -> int:
    if n_human >= 5:
        return 20
    return {0: 0, 1: 5, 2: 10, 3: 15, 4: 15}[n_human]


def channel_points(n_channels: int) -> int:
    return 0 if n_channels == 0 else 4 if n_channels == 1 else 7 if n_channels == 2 else 10


def personalization_points(best: int) -> int:
    return {0: 0, 1: 8, 2: 15}[best]


def meeting_points(scheduler: str, best_meeting: str) -> int:
    if scheduler == "instant":
        return 15
    return {"times": 12, "link": 10, "questions": 4, "none": 0}[best_meeting]


def grade(total: int) -> str:
    for cut, g in ((85, "A"), (70, "B"), (55, "C"), (40, "D")):
        if total >= cut:
            return g
    return "F"


def _channel_group(ch: str) -> str:
    return "phone" if ch in ("call", "voicemail") else ch


def score_company(company: dict, touches: list[dict], window_days: int, now: datetime) -> dict:
    sub = company.get("submitted_at")
    base = {"id": company["id"], "name": company["name"], "domain": company["domain"]}
    if not sub:
        return {**base, "status": "not submitted"}
    t0 = parse_time(sub)
    end = t0 + timedelta(days=window_days)
    inside = sorted(
        (t for t in touches if t["company"] == company["id"] and t0 <= parse_time(t["at"]) <= end),
        key=lambda t: t["at"],
    )
    human = [t for t in inside if t["kind"] == "human"]
    auto = [t for t in inside if t["kind"] == "auto"]
    first_human = human[0] if human else None
    minutes = (parse_time(first_human["at"]) - t0).total_seconds() / 60 if first_human else None
    first_auto_min = (parse_time(auto[0]["at"]) - t0).total_seconds() / 60 if auto else None
    channels = sorted({_channel_group(t["channel"]) for t in human})
    best_pers = max((t["personalization"] for t in human), default=0)
    order = {m: i for i, m in enumerate(MEETING_LEVELS)}
    best_meet = max((t["meeting"] for t in human), key=lambda m: order[m], default="none")
    form = company.get("form", {})
    scheduler = form.get("scheduler", "none")
    parts = {
        "speed": speed_points(minutes),
        "persistence": persistence_points(len(human)),
        "channels": channel_points(len(channels)),
        "personalization": personalization_points(best_pers),
        "meeting": meeting_points(scheduler, best_meet),
    }
    total = sum(parts.values())
    window_open = now < end
    by_channel: dict[str, int] = {}
    for t in human:
        by_channel[t["channel"]] = by_channel.get(t["channel"], 0) + 1
    return {
        **base,
        "status": "window open" if window_open else "complete",
        "submitted_at": iso(t0),
        "window_ends": iso(end),
        "minutes_to_first_human": round(minutes, 1) if minutes is not None else None,
        "first_human_channel": first_human["channel"] if first_human else None,
        "minutes_to_first_auto": round(first_auto_min, 1) if first_auto_min is not None else None,
        "human_touches": len(human),
        "auto_touches": len(auto),
        "touches_by_channel": by_channel,
        "channels": channels,
        "best_personalization": best_pers,
        "best_meeting": "instant scheduler" if scheduler == "instant" else best_meet,
        "form_fields": form.get("fields"),
        "phone_required": form.get("phone_required", False),
        "points": parts,
        "total": total,
        "grade": grade(total),
    }


def score_run(run: Run, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = run.settings
    touches = run.touches()
    rows = [score_company(c, touches, s["window_days"], now) for c in run.companies()]
    impacts = _read_json(run.root / "impact.json", {})
    for r in rows:
        if r["id"] in impacts:
            m = impact_for(r, impacts[r["id"]], s["window_days"])
            if m:
                r["impact"] = m
    scored = [r for r in rows if r["status"] == "complete"]
    responded = [r for r in scored if r["minutes_to_first_human"] is not None]
    bench = {
        "companies_scored": len(scored),
        "companies_pending": sum(1 for r in rows if r["status"] == "window open"),
        "no_human_response": sum(1 for r in scored if r["minutes_to_first_human"] is None),
        "median_minutes_to_first_human": round(median(r["minutes_to_first_human"] for r in responded), 1) if responded else None,
        "within_5_minutes": sum(1 for r in responded if r["minutes_to_first_human"] <= 5),
        "within_1_hour": sum(1 for r in responded if r["minutes_to_first_human"] <= 60),
        "median_human_touches": median(r["human_touches"] for r in scored) if scored else None,
        "instant_scheduler": sum(1 for r in scored if r["best_meeting"] == "instant scheduler"),
        "median_score": median(r["total"] for r in scored) if scored else None,
    }
    out = {"run": s["name"], "scored_at": iso(now), "window_days": s["window_days"], "benchmark": bench, "companies": rows}
    _write_json(run.root / "scores.json", out)
    return out


# ---------- pipeline impact ----------
#
# Translates a measured response time into pipeline. The client's own funnel rates are the baseline.
# The research effect (HBR 2011: ~7x the odds of qualifying within an hour vs. later, 60x+ vs. 24 hours)
# is observational and expressed as odds ratios, so it is never applied directly. Instead the model uses
# either the client's own qualification rate for fast-contacted leads, or three deliberately small
# scenarios, scaled by how large the response-time gap is and capped. See method/impact.md.

SCENARIOS = (("Conservative", 0.25), ("Base", 0.50), ("Aggressive", 1.00))
FULL_EFFECT_RATIO = 24  # the full scenario uplift applies once current time is 24x the target or more
DEFAULT_CAP = 0.90


def parse_rate(value) -> float:
    """Accepts 0.2, 20, or '20%' and returns 0.2."""
    s = str(value).strip().rstrip("%")
    r = float(s)
    if r > 1 or str(value).strip().endswith("%"):
        r = r / 100
    if not 0 <= r <= 1:
        raise SystemExit(f"error: rate {value!r} must be between 0 and 100%")
    return r


def gap_share(current_minutes: float, target_minutes: float) -> float:
    """Share of the scenario uplift that applies, on a log scale of current/target, 0..1."""
    if current_minutes <= target_minutes:
        return 0.0
    return min(1.0, math.log(current_minutes / target_minutes) / math.log(FULL_EFFECT_RATIO))


def impact_model(*, current_minutes: float, target_minutes: float, monthly_requests: float, qualify_rate: float,
                 opp_rate: float, pipeline_per_opp: float, win_rate: float | None = None,
                 qualify_rate_at_target: float | None = None, cap: float = DEFAULT_CAP) -> dict:
    if target_minutes <= 0:
        raise SystemExit("error: target minutes must be above 0")
    base_qualified = monthly_requests * qualify_rate
    if qualify_rate_at_target is not None:
        source = "own data"
        plan = [("Your data", None, max(qualify_rate, qualify_rate_at_target))]
        share = None
    else:
        source = "scenarios"
        share = gap_share(current_minutes, target_minutes)
        plan = [(name, u, min(max(qualify_rate, cap), qualify_rate * (1 + u * share))) for name, u in SCENARIOS]
    scenarios = []
    for name, u, new_rate in plan:
        extra_q = monthly_requests * (new_rate - qualify_rate)
        extra_opps = extra_q * opp_rate
        extra_pipe = extra_opps * pipeline_per_opp
        row = {
            "scenario": name,
            "uplift": u,
            "applied_uplift": round(new_rate / qualify_rate - 1, 4) if qualify_rate else None,
            "capped": u is not None and new_rate < qualify_rate * (1 + u * share) - 1e-12,
            "qualify_rate": round(new_rate, 4),
            "extra_qualified_per_month": round(extra_q, 2),
            "extra_opps_per_month": round(extra_opps, 2),
            "extra_pipeline_per_month": round(extra_pipe, 2),
            "extra_pipeline_per_quarter": round(extra_pipe * 3, 2),
            "extra_pipeline_per_year": round(extra_pipe * 12, 2),
        }
        if win_rate is not None:
            row["extra_bookings_per_year"] = round(extra_pipe * 12 * win_rate, 2)
        scenarios.append(row)
    return {
        "source": source,
        "inputs": {
            "current_minutes": round(current_minutes, 1), "target_minutes": target_minutes,
            "monthly_requests": monthly_requests, "qualify_rate": qualify_rate, "opp_rate": opp_rate,
            "pipeline_per_opp": pipeline_per_opp, "win_rate": win_rate,
            "qualify_rate_at_target": qualify_rate_at_target, "cap": cap,
        },
        "gap_share": None if share is None else round(share, 4),
        "baseline": {
            "qualified_per_month": round(base_qualified, 2),
            "opps_per_month": round(base_qualified * opp_rate, 2),
            "pipeline_per_month": round(base_qualified * opp_rate * pipeline_per_opp, 2),
        },
        "scenarios": scenarios,
    }


IMPACT_KEYS = ("monthly_requests", "qualify_rate", "opp_rate", "pipeline_per_opp", "target_minutes",
               "current_minutes", "win_rate", "qualify_rate_at_target", "cap")


def impact_for(row: dict, inputs: dict, window_days: int) -> dict | None:
    """Runs the model for one scored company using the saved funnel inputs and the measured response time."""
    if row.get("status") != "complete":
        return None
    measured = row.get("minutes_to_first_human")
    current = inputs.get("current_minutes")
    no_response = current is None and measured is None
    if current is None:
        current = measured if measured is not None else window_days * 1440
    m = impact_model(
        current_minutes=current, target_minutes=inputs.get("target_minutes", 60),
        monthly_requests=inputs["monthly_requests"], qualify_rate=inputs["qualify_rate"],
        opp_rate=inputs["opp_rate"], pipeline_per_opp=inputs["pipeline_per_opp"],
        win_rate=inputs.get("win_rate"), qualify_rate_at_target=inputs.get("qualify_rate_at_target"),
        cap=inputs.get("cap", DEFAULT_CAP),
    )
    m["no_response"] = no_response
    return m


def fmt_money(x: float) -> str:
    a = abs(x)
    if a >= 1_000_000:
        s = f"${a / 1_000_000:.1f}M"
    elif a >= 10_000:
        s = f"${a / 1000:.0f}K"
    elif a >= 1000:
        s = f"${a / 1000:.1f}K"
    else:
        s = f"${a:,.0f}"
    return ("-" if x < 0 else "") + s


def fmt_target(m: float) -> str:
    if m >= 60 and m % 60 == 0:
        h = int(m // 60)
        return f"{h} hour" if h == 1 else f"{h} hours"
    return fmt_minutes(m)


def fmt_pct(r: float) -> str:
    v = r * 100
    return f"{v:.0f}%" if abs(v - round(v)) < 0.05 else f"{v:.1f}%"


def render_impact(name: str, m: dict) -> list[str]:
    i, b = m["inputs"], m["baseline"]
    today = (f"no human touch in {fmt_minutes(i['current_minutes'])}, modeled as that long"
             if m.get("no_response") else f"first human touch in {fmt_minutes(i['current_minutes'])}")
    lines = [
        f"### {name}",
        "",
        f"Today: {today}. Target: {fmt_target(i['target_minutes'])}. "
        f"Funnel inputs from the company: {_num(i['monthly_requests'])} demo requests a month, "
        f"{fmt_pct(i['qualify_rate'])} become qualified meetings, {fmt_pct(i['opp_rate'])} of those become opportunities, "
        f"{fmt_money(i['pipeline_per_opp'])} of pipeline per opportunity. "
        f"Baseline: {_num(b['opps_per_month'])} opportunities and {fmt_money(b['pipeline_per_month'])} of pipeline a month.",
        "",
    ]
    win = i["win_rate"] is not None
    head = "| Scenario | Qualify rate | Extra meetings / month | Extra opps / month | Extra pipeline / quarter | Extra pipeline / year |"
    sep = "|---|---|---|---|---|---|"
    if win:
        head += " Extra bookings / year |"
        sep += "---|"
    lines += [head, sep]
    for s in m["scenarios"]:
        row = (f"| {s['scenario']} | {fmt_pct(i['qualify_rate'])} → {fmt_pct(s['qualify_rate'])}{' (capped)' if s['capped'] else ''} | "
               f"{_num(s['extra_qualified_per_month'])} | {_num(s['extra_opps_per_month'])} | "
               f"{fmt_money(s['extra_pipeline_per_quarter'])} | {fmt_money(s['extra_pipeline_per_year'])} |")
        if win:
            row += f" {fmt_money(s['extra_bookings_per_year'])} |"
        lines.append(row)
    lines.append("")
    if m["source"] == "own data":
        lines.append(f"Uplift measured from the company's own data: leads contacted within the target time qualify at "
                     f"{fmt_pct(i['qualify_rate_at_target'])}. This is a correlation in the company's history, so treat it as an upper bound.")
    else:
        pct = round((m["gap_share"] or 0) * 100)
        lines.append(
            f"Scenarios raise the qualification rate by 25%, 50% or 100% at full effect. {pct}% of that effect applies here, "
            f"based on how far the current response time is from the target (full effect at {FULL_EFFECT_RATIO}x slower or more). "
            f"Rates are capped at {fmt_pct(i['cap'])}. The scenarios are directional. They sit far below the effect in the published "
            "research, which is observational. Replace them with the company's own qualification rate by response time when it is available. "
            "Method: method/impact.md.")
    lines.append("")
    return lines


# ---------- report ----------

def fmt_minutes(m: float | None) -> str:
    if m is None:
        return "no response"
    if m < 1:
        return "under 1 min"
    if m < 60:
        return f"{m:.0f} min"
    if m < 1440:
        return f"{m / 60:.1f} hours"
    return f"{m / 1440:.1f} days"


MEETING_TEXT = {
    "instant scheduler": "calendar offered on submit",
    "times": "proposed specific times",
    "link": "sent a booking link",
    "questions": "asked qualifying questions only",
    "none": "no meeting offered",
}
PERS_TEXT = {0: "template", 1: "named the company or role", 2: "answered the question asked"}


def _num(x) -> str:
    if x is None:
        return "n/a"
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def render_report(scores: dict) -> str:
    b = scores["benchmark"]
    rows = sorted(
        (r for r in scores["companies"] if r["status"] == "complete"),
        key=lambda r: (-r["total"], r["name"]),
    )
    lines = [
        f"# Speed-to-Lead Test: {scores['run']}",
        "",
        f"Scored {scores['scored_at'][:10]}. Each company received one demo request from a named buyer with a genuine question. "
        f"Every reply was logged for {scores['window_days']} days. Method and rubric: method/rubric.md.",
        "",
    ]
    modeled = [r for r in rows if r.get("impact")]
    if modeled:
        lines += ["## What faster response is worth", ""]
        for r in modeled:
            lines += render_impact(r["name"], r["impact"])
    lines += [
        "## Benchmark",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Companies scored | {b['companies_scored']} |",
        f"| Median time to first human touch | {fmt_minutes(b['median_minutes_to_first_human'])} |",
        f"| First human touch within 5 minutes | {b['within_5_minutes']} of {b['companies_scored']} |",
        f"| First human touch within 1 hour | {b['within_1_hour']} of {b['companies_scored']} |",
        f"| No human response in {scores['window_days']} days | {b['no_human_response']} of {b['companies_scored']} |",
        f"| Calendar offered on submit | {b['instant_scheduler']} of {b['companies_scored']} |",
        f"| Median human touches | {_num(b['median_human_touches'])} |",
        f"| Median score | {_num(b['median_score'])} of 100 |",
    ]
    if b["companies_pending"]:
        lines += ["", f"{b['companies_pending']} more companies are still inside the response window and are not scored yet."]
    lines += [
        "",
        "## Ranking",
        "",
        "| Company | Score | Grade | First human touch | Human touches | Channels |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['total']} | {r['grade']} | {fmt_minutes(r['minutes_to_first_human'])} | "
            f"{r['human_touches']} | {', '.join(r['channels']) or 'none'} |"
        )
    lines += ["", "## Scorecards", ""]
    for r in rows:
        p = r["points"]
        auto = (f"Automated confirmation sent {fmt_minutes(r['minutes_to_first_auto'])} after the request."
                if r["minutes_to_first_auto"] is not None else "No automated confirmation.")
        form = f"{r['form_fields']} form fields" if r["form_fields"] else "form fields not recorded"
        if r["phone_required"]:
            form += ", phone required"
        first = (f"First human touch by {r['first_human_channel']}, {fmt_minutes(r['minutes_to_first_human'])} after the request."
                 if r["minutes_to_first_human"] is not None else f"No human touch in {scores['window_days']} days.")
        lines += [
            f"### {r['name']}: {r['total']} ({r['grade']})",
            "",
            f"Requested {r['submitted_at'][:16].replace('T', ' ')} UTC. "
            f"{first} {auto} {r['human_touches']} human touches. Best message: {PERS_TEXT[r['best_personalization']]}. "
            f"Meeting: {MEETING_TEXT[r['best_meeting']]}. Form: {form}.",
            "",
            f"Speed {p['speed']}/40 · Persistence {p['persistence']}/20 · Channels {p['channels']}/10 · "
            f"Personalization {p['personalization']}/15 · Meeting {p['meeting']}/15",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


# ---------- twilio (read-only) ----------

def _twilio_get(url: str, sid: str, token: str) -> dict:
    req = urllib.request.Request(url)
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def twilio_events(number: str, since: datetime, fetch, sid: str) -> list[dict]:
    """Return inbound calls and texts to `number` since `since`. `fetch(url)` returns parsed JSON."""
    base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    events: list[dict] = []
    day = since.strftime("%Y-%m-%d")
    q_calls = urllib.parse.urlencode({"To": number, "StartTime>": day, "PageSize": 1000})
    url = f"{base}/Calls.json?{q_calls}"
    while url:
        page = fetch(url)
        for c in page.get("calls", []):
            started = c.get("start_time") or c.get("date_created")
            if not started:
                continue
            dt = datetime.strptime(started, "%a, %d %b %Y %H:%M:%S %z")
            if dt < since:
                continue
            events.append({"sid": c["sid"], "type": "call", "from": c.get("from", ""), "at": iso(dt),
                           "duration": int(c.get("duration") or 0), "status": c.get("status", "")})
        nxt = page.get("next_page_uri")
        url = f"https://api.twilio.com{nxt}" if nxt else None
    q_msgs = urllib.parse.urlencode({"To": number, "DateSent>": day, "PageSize": 1000})
    url = f"{base}/Messages.json?{q_msgs}"
    while url:
        page = fetch(url)
        for m in page.get("messages", []):
            sent = m.get("date_sent") or m.get("date_created")
            if not sent:
                continue
            dt = datetime.strptime(sent, "%a, %d %b %Y %H:%M:%S %z")
            if dt < since:
                continue
            events.append({"sid": m["sid"], "type": "sms", "from": m.get("from", ""), "at": iso(dt),
                           "body": m.get("body", "")})
        nxt = page.get("next_page_uri")
        url = f"https://api.twilio.com{nxt}" if nxt else None
    return sorted(events, key=lambda e: e["at"])


def attribute_events(run: Run, events: list[dict]) -> tuple[int, int]:
    """Turn Twilio events into touches when the caller is mapped; queue the rest. Skips duplicates."""
    numbers = run.numbers()
    seen = {t.get("ref") for t in run.touches()} | {e["sid"] for e in _read_jsonl(run.root / "unmatched.jsonl")}
    new_touches, new_unmatched = [], []
    for e in events:
        if e["sid"] in seen:
            continue
        cid = numbers.get(normalize_number(e["from"]))
        if not cid:
            new_unmatched.append(e)
            continue
        new_touches.append(_event_to_touch(e, cid))
    if new_touches:
        _append_jsonl(run.root / "touches.jsonl", new_touches)
    if new_unmatched:
        _append_jsonl(run.root / "unmatched.jsonl", new_unmatched)
    return len(new_touches), len(new_unmatched)


def _event_to_touch(e: dict, cid: str) -> dict:
    channel = "sms" if e["type"] == "sms" else ("voicemail" if e.get("duration", 0) > 0 else "call")
    summary = e.get("body", "") if e["type"] == "sms" else f"{e.get('status', '')}, {e.get('duration', 0)}s"
    return {"company": cid, "channel": channel, "at": e["at"], "kind": "human", "personalization": 0,
            "meeting": "none", "from": e["from"], "summary": summary, "source": "twilio", "ref": e["sid"]}


# ---------- sample data ----------

SAMPLE = [
    # name, fields, scheduler, phone_required, touches as (minutes, channel, kind, personalization, meeting)
    ("Ledgerline", 5, "instant", False, [(0.5, "email", "auto", 0, "none"), (3, "call", "human", 1, "times"),
                                          (4, "email", "human", 2, "times"), (1500, "email", "human", 2, "link"),
                                          (2900, "linkedin", "human", 1, "none")]),
    ("Quorum CRM", 7, "none", True, [(1, "email", "auto", 0, "none"), (11, "email", "human", 2, "times"),
                                      (95, "call", "human", 1, "none"), (1460, "email", "human", 1, "link"),
                                      (4300, "email", "human", 1, "link")]),
    ("Brightpath Security", 9, "none", True, [(2, "email", "auto", 0, "none"), (52, "voicemail", "human", 1, "none"),
                                               (58, "email", "human", 1, "link"), (1500, "sms", "human", 0, "link"),
                                               (2950, "email", "human", 0, "link"), (5800, "email", "human", 0, "none")]),
    ("Cobalt HR", 6, "none", False, [(1, "email", "auto", 0, "none"), (190, "email", "human", 1, "questions"),
                                      (2880, "email", "human", 0, "link")]),
    ("Fieldnote", 11, "none", True, [(0.3, "email", "auto", 0, "none"), (1300, "email", "human", 0, "link"),
                                      (4400, "email", "human", 0, "link"), (7200, "email", "human", 0, "none")]),
    ("Tallyhawk", 4, "none", False, [(2, "email", "auto", 0, "none"), (3100, "email", "human", 0, "questions")]),
    ("Meridian Ops", 8, "none", True, [(1, "email", "auto", 0, "none")]),
    ("Parcelwise", 5, "none", False, [(6, "email", "human", 2, "times"), (1450, "call", "human", 2, "times"),
                                      (2890, "email", "human", 1, "link")]),
]


SAMPLE_IMPACT_COMPANY = "tallyhawk"
SAMPLE_IMPACT = {"monthly_requests": 200, "qualify_rate": 0.20, "opp_rate": 0.50, "pipeline_per_opp": 40000,
                 "target_minutes": 60, "current_minutes": None, "win_rate": 0.25, "qualify_rate_at_target": None,
                 "cap": DEFAULT_CAP}


def build_sample(outdir: Path) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    _write_json(outdir / "run.json", {"name": "Synthetic sample (fictional companies)", "window_days": 7,
                                      "created_at": "2026-11-02T13:00:00Z"})
    t0 = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
    companies, touches = [], []
    for i, (name, fields, sched, phone, tlist) in enumerate(SAMPLE):
        cid = slugify(name)
        sub = t0 + timedelta(minutes=37 * i)
        domain = f"{cid}.example"
        companies.append({"id": cid, "name": name, "domain": domain, "form_url": f"https://{domain}/demo",
                          "submitted_at": iso(sub),
                          "form": {"fields": fields, "scheduler": sched, "phone_required": phone}})
        for (mins, ch, kind, pers, meet) in tlist:
            touches.append({"company": cid, "channel": ch, "at": iso(sub + timedelta(minutes=mins)), "kind": kind,
                            "personalization": pers, "meeting": meet, "from": f"rep@{domain}" if ch == "email" else "",
                            "summary": "", "source": "sample", "ref": ""})
    _write_json(outdir / "companies.json", companies)
    _write_jsonl(outdir / "touches.jsonl", touches)
    _write_json(outdir / "impact.json", {SAMPLE_IMPACT_COMPANY: SAMPLE_IMPACT})
    run = Run(outdir)
    scores = score_run(run, now=datetime(2026, 11, 16, tzinfo=timezone.utc))
    (outdir / "report.md").write_text(render_report(scores))
    return outdir


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stl", description="Speed-to-Lead Test")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("run"); p.add_argument("--window-days", type=int, default=7)
    p.add_argument("--name")
    p = sub.add_parser("add-company"); p.add_argument("run"); p.add_argument("--name", required=True)
    p.add_argument("--domain", required=True); p.add_argument("--form-url", default="")
    p = sub.add_parser("submit"); p.add_argument("run"); p.add_argument("--company", required=True)
    p.add_argument("--at"); p.add_argument("--fields", type=int)
    p.add_argument("--scheduler", choices=("none", "instant"), default="none")
    p.add_argument("--phone-required", action="store_true")
    p = sub.add_parser("touch"); p.add_argument("run"); p.add_argument("--company", required=True)
    p.add_argument("--channel", required=True, choices=CHANNELS); p.add_argument("--at", required=True)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--personalization", type=int, default=0, choices=(0, 1, 2))
    p.add_argument("--meeting", default="none", choices=MEETING_LEVELS)
    p.add_argument("--summary", default=""); p.add_argument("--from", dest="from_", default="")
    p = sub.add_parser("import"); p.add_argument("run"); p.add_argument("file")
    p = sub.add_parser("map-number"); p.add_argument("run"); p.add_argument("--number", required=True)
    p.add_argument("--company", required=True)
    p = sub.add_parser("twilio-pull"); p.add_argument("run"); p.add_argument("--since")
    for name in ("status", "score", "report"):
        p = sub.add_parser(name); p.add_argument("run")
    p = sub.add_parser("impact"); p.add_argument("run"); p.add_argument("--company", required=True)
    p.add_argument("--monthly-requests", type=float, required=True)
    p.add_argument("--qualify-rate", required=True); p.add_argument("--opp-rate", required=True)
    p.add_argument("--pipeline-per-opp", type=float, required=True)
    p.add_argument("--target-minutes", type=float, default=60)
    p.add_argument("--current-minutes", type=float)
    p.add_argument("--win-rate"); p.add_argument("--qualify-rate-at-target")
    p.add_argument("--cap", default=str(DEFAULT_CAP))
    p = sub.add_parser("sample"); p.add_argument("outdir")

    a = ap.parse_args(argv)

    if a.cmd == "sample":
        out = build_sample(Path(a.outdir))
        print(f"sample run written to {out}")
        return 0

    run = Run(Path(a.run))

    if a.cmd == "init":
        run.root.mkdir(parents=True, exist_ok=True)
        if (run.root / "run.json").exists():
            print(f"error: {run.root} already has a run.json", file=sys.stderr)
            return 1
        _write_json(run.root / "run.json", {"name": a.name or run.root.name, "window_days": a.window_days,
                                            "created_at": iso(datetime.now(timezone.utc))})
        print(f"run created at {run.root}")
        return 0

    run.settings  # validates the folder

    if a.cmd == "add-company":
        rows = run.companies()
        cid = slugify(a.name)
        if any(c["id"] == cid for c in rows):
            print(f"error: company {cid} already exists", file=sys.stderr)
            return 1
        rows.append({"id": cid, "name": a.name, "domain": a.domain.lower(), "form_url": a.form_url,
                     "submitted_at": None, "form": {}})
        run.save_companies(rows)
        print(f"added {cid}")
    elif a.cmd == "submit":
        rows = run.companies()
        run.company(a.company)
        at = parse_time(a.at) if a.at else datetime.now(timezone.utc)
        for c in rows:
            if c["id"] == a.company:
                c["submitted_at"] = iso(at)
                c["form"] = {"fields": a.fields, "scheduler": a.scheduler, "phone_required": a.phone_required}
        run.save_companies(rows)
        print(f"{a.company} submitted at {iso(at)}")
    elif a.cmd == "touch":
        ids = {c["id"] for c in run.companies()}
        t = validate_touch({"company": a.company, "channel": a.channel, "at": a.at, "kind": a.kind,
                            "personalization": a.personalization, "meeting": a.meeting,
                            "summary": a.summary, "from": a.from_}, ids)
        _append_jsonl(run.root / "touches.jsonl", [t])
        print(f"logged {t['channel']} touch for {t['company']}")
    elif a.cmd == "import":
        ids = {c["id"] for c in run.companies()}
        incoming = json.loads(Path(a.file).read_text())
        existing = {t.get("ref") for t in run.touches() if t.get("ref")}
        valid = [validate_touch(t, ids) for t in incoming]
        new = [t for t in valid if not t["ref"] or t["ref"] not in existing]
        _append_jsonl(run.root / "touches.jsonl", new)
        print(f"imported {len(new)} touches, skipped {len(valid) - len(new)} already logged")
    elif a.cmd == "map-number":
        run.company(a.company)
        numbers = run.numbers()
        numbers[normalize_number(a.number)] = a.company
        _write_json(run.root / "numbers.json", numbers)
        pending = _read_jsonl(run.root / "unmatched.jsonl")
        keep, moved = [], []
        for e in pending:
            cid = numbers.get(normalize_number(e["from"]))
            (moved.append(_event_to_touch(e, cid)) if cid else keep.append(e))
        _append_jsonl(run.root / "touches.jsonl", moved)
        _write_jsonl(run.root / "unmatched.jsonl", keep)
        print(f"mapped {normalize_number(a.number)} to {a.company}; attributed {len(moved)} queued events")
    elif a.cmd == "twilio-pull":
        sid, token, number = (os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER"))
        missing = [k for k, v in (("TWILIO_ACCOUNT_SID", sid), ("TWILIO_AUTH_TOKEN", token), ("TWILIO_NUMBER", number)) if not v]
        if missing:
            print(f"error: missing environment variables: {', '.join(missing)}", file=sys.stderr)
            return 2
        subs = [parse_time(c["submitted_at"]) for c in run.companies() if c.get("submitted_at")]
        since = parse_time(a.since) if a.since else (min(subs) if subs else datetime.now(timezone.utc) - timedelta(days=7))
        events = twilio_events(normalize_number(number), since, lambda u: _twilio_get(u, sid, token), sid)
        added, queued = attribute_events(run, events)
        print(f"twilio: {len(events)} events, {added} new touches, {queued} queued as unmatched")
        if queued:
            print("map callers with: stl.py map-number RUN --number +1... --company ID")
    elif a.cmd == "status":
        touches = run.touches()
        now = datetime.now(timezone.utc)
        w = run.settings["window_days"]
        for c in run.companies():
            n = sum(1 for t in touches if t["company"] == c["id"])
            if not c.get("submitted_at"):
                state = "not submitted"
            else:
                end = parse_time(c["submitted_at"]) + timedelta(days=w)
                state = f"window ends {iso(end)[:16]}Z" if now < end else "window closed"
            print(f"{c['id']:<28} {state:<32} {n} touches")
        um = len(_read_jsonl(run.root / "unmatched.jsonl"))
        if um:
            print(f"{um} unmatched calls or texts")
    elif a.cmd == "score":
        s = score_run(run)
        b = s["benchmark"]
        print(f"scored {b['companies_scored']} companies, {b['companies_pending']} pending; wrote scores.json")
    elif a.cmd == "impact":
        run.company(a.company)
        inputs = {
            "monthly_requests": a.monthly_requests, "qualify_rate": parse_rate(a.qualify_rate),
            "opp_rate": parse_rate(a.opp_rate), "pipeline_per_opp": a.pipeline_per_opp,
            "target_minutes": a.target_minutes, "current_minutes": a.current_minutes,
            "win_rate": parse_rate(a.win_rate) if a.win_rate is not None else None,
            "qualify_rate_at_target": parse_rate(a.qualify_rate_at_target) if a.qualify_rate_at_target is not None else None,
            "cap": parse_rate(a.cap),
        }
        impacts = _read_json(run.root / "impact.json", {})
        impacts[a.company] = inputs
        _write_json(run.root / "impact.json", impacts)
        s = score_run(run)
        row = next(r for r in s["companies"] if r["id"] == a.company)
        if row.get("impact"):
            print("\n".join(render_impact(row["name"], row["impact"])))
        elif a.current_minutes is not None:
            m = impact_model(current_minutes=a.current_minutes, **{k: v for k, v in inputs.items() if k != "current_minutes"})
            print("\n".join(render_impact(row["name"], m)))
        else:
            print(f"saved funnel inputs for {a.company}; the model runs once its window closes, "
                  "or pass --current-minutes to model now")
    elif a.cmd == "report":
        s = score_run(run)
        (run.root / "report.md").write_text(render_report(s))
        print(f"wrote {run.root / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
