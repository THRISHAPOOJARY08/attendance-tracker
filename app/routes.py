import datetime as dt
import io
import uuid

import pytz
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from app.auth import get_current_user, require_manager
from app.config import COMPANY_TIMEZONE
from app.db import get_db

router = APIRouter(prefix="/api")
_tz = pytz.timezone(COMPANY_TIMEZONE)


# ── helpers ──────────────────────────────────────────────────────────────────

def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _company_date() -> str:
    return dt.datetime.now(tz=_tz).strftime("%Y-%m-%d")


def _fmt(utc_iso: str) -> str:
    d = dt.datetime.fromisoformat(utc_iso).astimezone(_tz)
    return d.strftime("%I:%M %p")


# ── auth ─────────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


from app.auth import verify_password, create_access_token


@router.post("/login")
async def login(body: LoginBody):
    db = get_db()
    user = await db["users"].find_one({"username": body.username})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    resp = JSONResponse({"ok": True, "role": user["role"], "name": user["name"]})
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=60 * 60 * 8)
    return resp


@router.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["username"], "name": user["name"], "role": user["role"]}


# ── check-in / check-out ──────────────────────────────────────────────────────

@router.post("/checkin")
async def checkin(user: dict = Depends(get_current_user)):
    db = get_db()
    today = _company_date()
    existing = await db["attendance"].find_one({"user_id": user["username"], "date": today})
    if existing:
        return {"ok": False, "message": f"Already checked in at {_fmt(existing['checkin_utc'])}"}
    now = _utc_now()
    await db["attendance"].insert_one({
        "user_id": user["username"],
        "name": user["name"],
        "date": today,
        "checkin_utc": now.isoformat(),
        "checkout_utc": None,
        "status": "open",
    })
    return {"ok": True, "message": f"Checked in at {_fmt(now.isoformat())}"}


@router.post("/checkout")
async def checkout(user: dict = Depends(get_current_user)):
    db = get_db()
    today = _company_date()
    record = await db["attendance"].find_one({"user_id": user["username"], "date": today})
    if not record:
        return {"ok": False, "message": "You haven't checked in today"}
    if record["status"] == "closed":
        return {"ok": False, "message": f"Already checked out at {_fmt(record['checkout_utc'])}"}
    now = _utc_now()
    ci = dt.datetime.fromisoformat(record["checkin_utc"])
    diff = now - ci
    h, rem = divmod(int(diff.total_seconds()), 3600)
    m = rem // 60
    await db["attendance"].update_one(
        {"user_id": user["username"], "date": today},
        {"$set": {"checkout_utc": now.isoformat(), "status": "closed"}},
    )
    return {"ok": True, "message": f"Checked out at {_fmt(now.isoformat())} — {h}h {m}m today"}


# ── today status ──────────────────────────────────────────────────────────────

@router.get("/today")
async def today_status(user: dict = Depends(get_current_user)):
    db = get_db()
    today = _company_date()
    record = await db["attendance"].find_one({"user_id": user["username"], "date": today})
    leave = await db["leave_requests"].find_one({
        "user_id": user["username"],
        "status": {"$in": ["approved", "pending"]},
        "dates": today,
    })
    if not record:
        checkin = None
        checkout = None
        status = "absent"
    else:
        checkin = _fmt(record["checkin_utc"])
        checkout = _fmt(record["checkout_utc"]) if record.get("checkout_utc") else None
        status = "closed" if record["status"] == "closed" else "open"
    return {
        "date": today,
        "checkin": checkin,
        "checkout": checkout,
        "status": status,
        "on_leave": bool(leave),
    }


# ── leave requests ────────────────────────────────────────────────────────────

LEAVE_TYPES = {
    "sick", "casual", "paid", "maternity", "paternity",
    "bereavement", "unpaid", "compensatory"
}


class LeaveBody(BaseModel):
    leave_type: str
    start_date: str
    days: int
    description: str = ""


@router.post("/leave")
async def request_leave(body: LeaveBody, user: dict = Depends(get_current_user)):
    if body.leave_type not in LEAVE_TYPES:
        raise HTTPException(400, f"Invalid leave type. Choose: {', '.join(sorted(LEAVE_TYPES))}")
    try:
        start = dt.datetime.strptime(body.start_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    if body.days < 1:
        raise HTTPException(400, "Days must be at least 1")

    dates = [(start + dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(body.days)]
    end_date = dates[-1]

    request_id = str(uuid.uuid4())
    db = get_db()
    await db["leave_requests"].insert_one({
        "request_id": request_id,
        "user_id": user["username"],
        "name": user["name"],
        "leave_type": body.leave_type,
        "start_date": body.start_date,
        "end_date": end_date,
        "days": body.days,
        "dates": dates,
        "description": body.description.strip(),
        "status": "pending",
        "submitted_at": _utc_now().isoformat(),
        "decided_by": None,
        "decided_at": None,
    })
    return {"ok": True, "message": f"Leave request submitted for {body.start_date} to {end_date}"}


@router.get("/leave/my")
async def my_leaves(user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db["leave_requests"].find(
        {"user_id": user["username"]},
        sort=[("submitted_at", -1)],
    )
    results = []
    async for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)
    return results


# ── manager: pending leaves ───────────────────────────────────────────────────

@router.get("/manager/leaves/pending")
async def pending_leaves(_u: dict = Depends(require_manager)):
    db = get_db()
    cursor = db["leave_requests"].find({"status": "pending"}, sort=[("submitted_at", 1)])
    results = []
    async for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)
    return results


class DecideBody(BaseModel):
    request_id: str
    decision: str


@router.post("/manager/leaves/decide")
async def decide_leave(body: DecideBody, manager: dict = Depends(require_manager)):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be 'approved' or 'rejected'")
    db = get_db()
    result = await db["leave_requests"].update_one(
        {"request_id": body.request_id, "status": "pending"},
        {"$set": {
            "status": body.decision,
            "decided_by": manager["username"],
            "decided_at": _utc_now().isoformat(),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Leave request not found or already decided")
    return {"ok": True, "message": f"Leave request {body.decision}"}


# ── history ───────────────────────────────────────────────────────────────────

@router.get("/history")
async def my_history(period: str = "week", user: dict = Depends(get_current_user)):
    start, end = _date_range(period)
    db = get_db()
    att_cursor = db["attendance"].find(
        {"user_id": user["username"], "date": {"$gte": start, "$lte": end}},
        sort=[("date", 1)],
    )
    att_by_date = {}
    async for r in att_cursor:
        att_by_date[r["date"]] = r

    leave_cursor = db["leave_requests"].find({
        "user_id": user["username"],
        "status": {"$in": ["approved", "pending"]},
    })
    leave_by_date = {}
    async for lv in leave_cursor:
        for d in lv.get("dates", []):
            if start <= d <= end:
                leave_by_date[d] = lv["leave_type"]

    rows = []
    cur = dt.datetime.strptime(start, "%Y-%m-%d").date()
    end_d = dt.datetime.strptime(end, "%Y-%m-%d").date()
    today = dt.datetime.now(tz=_tz).date()
    while cur <= end_d:
        ds = cur.isoformat()
        att = att_by_date.get(ds)
        leave = leave_by_date.get(ds)
        row = {"date": ds, "weekday": cur.strftime("%a")}
        if leave:
            row["status"] = "leave"
            row["leave_type"] = leave
            row["checkin"] = None
            row["checkout"] = None
            row["duration"] = None
        elif att:
            ci = dt.datetime.fromisoformat(att["checkin_utc"])
            co = dt.datetime.fromisoformat(att["checkout_utc"]) if att.get("checkout_utc") else None
            dur = None
            if co:
                diff = co - ci
                h, rem = divmod(int(diff.total_seconds()), 3600)
                m = rem // 60
                dur = f"{h}h {m}m"
            row["status"] = "present"
            row["checkin"] = _fmt(att["checkin_utc"])
            row["checkout"] = _fmt(att["checkout_utc"]) if att.get("checkout_utc") else None
            row["duration"] = dur
            row["leave_type"] = None
        else:
            row["status"] = "future" if cur > today else "absent"
            row["checkin"] = None
            row["checkout"] = None
            row["duration"] = None
            row["leave_type"] = None
        rows.append(row)
        cur += dt.timedelta(days=1)
    return rows


# ── manager: team report ──────────────────────────────────────────────────────

@router.get("/manager/report")
async def team_report(start: str = "", end: str = "", _u: dict = Depends(require_manager)):
    tz_today = dt.datetime.now(tz=_tz).date().isoformat()
    if not start:
        start = tz_today
    if not end:
        end = start
    try:
        dt.datetime.strptime(start, "%Y-%m-%d")
        dt.datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date format")

    db = get_db()
    att_cursor = db["attendance"].find(
        {"date": {"$gte": start, "$lte": end}},
        sort=[("date", 1), ("user_id", 1)],
    )
    records = []
    async for r in att_cursor:
        ci = dt.datetime.fromisoformat(r["checkin_utc"])
        co = dt.datetime.fromisoformat(r["checkout_utc"]) if r.get("checkout_utc") else None
        dur = None
        if co:
            diff = co - ci
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            dur = f"{h}h {m}m"
        records.append({
            "date": r["date"],
            "user_id": r["user_id"],
            "name": r.get("name", r["user_id"]),
            "checkin": _fmt(r["checkin_utc"]),
            "checkout": _fmt(r["checkout_utc"]) if r.get("checkout_utc") else "—",
            "duration": dur or "—",
            "status": r["status"],
        })

    leave_cursor = db["leave_requests"].find({"status": "approved"})
    leave_records = []
    async for lv in leave_cursor:
        for d in lv.get("dates", []):
            if start <= d <= end:
                leave_records.append({
                    "date": d,
                    "user_id": lv["user_id"],
                    "name": lv.get("name", lv["user_id"]),
                    "leave_type": lv["leave_type"],
                })

    return {"attendance": records, "leaves": leave_records, "start": start, "end": end}


# ── manager: all users ────────────────────────────────────────────────────────

@router.get("/manager/users")
async def list_users(_u: dict = Depends(require_manager)):
    db = get_db()
    cursor = db["users"].find({}, {"password_hash": 0})
    users = []
    async for u in cursor:
        u.pop("_id", None)
        users.append(u)
    return users


class CreateUserBody(BaseModel):
    username: str
    name: str
    password: str
    role: str = "employee"


from app.auth import hash_password


@router.post("/manager/users")
async def create_user(body: CreateUserBody, _u: dict = Depends(require_manager)):
    if body.role not in ("employee", "manager"):
        raise HTTPException(400, "role must be 'employee' or 'manager'")
    db = get_db()
    existing = await db["users"].find_one({"username": body.username})
    if existing:
        raise HTTPException(409, "Username already exists")
    await db["users"].insert_one({
        "username": body.username,
        "name": body.name,
        "password_hash": hash_password(body.password),
        "role": body.role,
    })
    return {"ok": True, "message": f"User '{body.username}' created"}


@router.delete("/manager/users/{username}")
async def delete_user(username: str, _u: dict = Depends(require_manager)):
    db = get_db()
    user = await db["users"].find_one({"username": username})
    if not user:
        raise HTTPException(404, "User not found")
    if user.get("role") == "manager":
        raise HTTPException(400, "Cannot delete a manager account")
    await db["users"].delete_one({"username": username})
    return {"ok": True, "message": f"Employee '{username}' has been removed"}


# ── manager: individual employee report ──────────────────────────────────────

@router.get("/manager/employee-report/{username}")
async def employee_report(username: str, start: str = "", end: str = "", _u: dict = Depends(require_manager)):
    tz_today = dt.datetime.now(tz=_tz).date()
    if not start:
        start = tz_today.replace(day=1).isoformat()
    if not end:
        end = tz_today.isoformat()
    try:
        dt.datetime.strptime(start, "%Y-%m-%d")
        dt.datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date format")

    db = get_db()
    employee = await db["users"].find_one({"username": username}, {"password_hash": 0})
    if not employee:
        raise HTTPException(404, "Employee not found")
    employee.pop("_id", None)

    att_cursor = db["attendance"].find(
        {"user_id": username, "date": {"$gte": start, "$lte": end}},
        sort=[("date", 1)],
    )
    attendance = []
    total_seconds = 0
    present_days = 0
    async for r in att_cursor:
        ci = dt.datetime.fromisoformat(r["checkin_utc"])
        co = dt.datetime.fromisoformat(r["checkout_utc"]) if r.get("checkout_utc") else None
        dur = None
        if co:
            diff = co - ci
            secs = int(diff.total_seconds())
            total_seconds += secs
            h, rem = divmod(secs, 3600)
            m = rem // 60
            dur = f"{h}h {m}m"
        present_days += 1
        attendance.append({
            "date": r["date"],
            "checkin": _fmt(r["checkin_utc"]),
            "checkout": _fmt(r["checkout_utc"]) if r.get("checkout_utc") else "-",
            "duration": dur or "-",
            "status": r["status"],
        })

    total_h, total_rem = divmod(total_seconds, 3600)
    total_m = total_rem // 60
    total_hours = f"{total_h}h {total_m}m"

    leave_cursor = db["leave_requests"].find(
        {"user_id": username, "status": "approved"},
        sort=[("start_date", 1)],
    )
    leaves = []
    total_leave_days = 0
    async for lv in leave_cursor:
        lv.pop("_id", None)
        if lv["end_date"] >= start and lv["start_date"] <= end:
            total_leave_days += lv["days"]
            leaves.append({
                "leave_type": lv["leave_type"],
                "start_date": lv["start_date"],
                "end_date": lv["end_date"],
                "days": lv["days"],
                "description": lv.get("description", ""),
                "submitted_at": lv["submitted_at"][:10],
                "decided_by": lv.get("decided_by", ""),
            })

    return {
        "employee": employee,
        "period": {"start": start, "end": end},
        "summary": {
            "present_days": present_days,
            "total_hours": total_hours,
            "leave_days": total_leave_days,
        },
        "attendance": attendance,
        "leaves": leaves,
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _date_range(period: str):
    today = dt.datetime.now(tz=_tz).date()
    if period == "month":
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(day=31)
        else:
            end = (today.replace(month=today.month + 1, day=1) - dt.timedelta(days=1))
        return start.isoformat(), end.isoformat()
    else:
        monday = today - dt.timedelta(days=today.weekday())
        return monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()


# ── manager: employee PDF report ─────────────────────────────────────────────

_BLUE       = colors.HexColor("#2563b0")
_DARK       = colors.HexColor("#0f172a")
_MUTED      = colors.HexColor("#64748b")
_BORDER     = colors.HexColor("#e2e8f0")
_GREEN_BG   = colors.HexColor("#dcfce7")
_GREEN_TXT  = colors.HexColor("#15803d")
_AMBER_BG   = colors.HexColor("#fef3c7")
_AMBER_TXT  = colors.HexColor("#92400e")
_RED_BG     = colors.HexColor("#fee2e2")
_RED_TXT    = colors.HexColor("#b91c1c")
_HEADER_BG  = colors.HexColor("#1a3a6e")
_ROW_ALT    = colors.HexColor("#f8fafc")


def _build_pdf(emp: dict, period: dict, summary: dict,
               attendance: list, leaves: list) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm,
    )
    W = A4[0] - 40*mm   # usable width

    styles = getSampleStyleSheet()
    def _style(name, **kw):
        s = ParagraphStyle(name, parent=styles["Normal"], **kw)
        return s

    s_title    = _style("Title2",    fontSize=20, textColor=colors.white,
                        fontName="Helvetica-Bold", leading=24)
    s_sub      = _style("Sub",       fontSize=10, textColor=colors.HexColor("#bfdbfe"),
                        fontName="Helvetica", leading=14)
    s_sec      = _style("Sec",       fontSize=10, textColor=_BLUE,
                        fontName="Helvetica-Bold", leading=14, spaceBefore=12)
    s_label    = _style("Lbl",       fontSize=8,  textColor=_MUTED,
                        fontName="Helvetica-Bold")
    s_val      = _style("Val",       fontSize=11, textColor=_DARK,
                        fontName="Helvetica-Bold")
    s_body     = _style("Body",      fontSize=9,  textColor=_DARK,
                        fontName="Helvetica", leading=13)
    s_footer   = _style("Footer",    fontSize=8,  textColor=_MUTED,
                        fontName="Helvetica", alignment=1)

    story = []

    # ── Header banner ─────────────────────────────────────────────────────────
    name     = emp.get("name", emp["username"])
    username = emp["username"]
    role     = emp.get("role", "employee").capitalize()
    gen_date = dt.datetime.now(tz=_tz).strftime("%d %B %Y, %I:%M %p")

    header_data = [[
        Paragraph(f"Employee Attendance Report", s_title),
        ""
    ],[
        Paragraph(f"{name}  ·  @{username}  ·  {role}", s_sub),
        Paragraph(f"Period: {period['start']} – {period['end']}<br/>"
                  f"Generated: {gen_date}", s_sub),
    ]]
    header_tbl = Table(header_data, colWidths=[W*0.6, W*0.4])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), _HEADER_BG),
        ("TOPPADDING",    (0,0), (-1,-1), 12),
        ("BOTTOMPADDING", (0,0), (-1,-1), 12),
        ("LEFTPADDING",   (0,0), (0,-1), 14),
        ("RIGHTPADDING",  (-1,0), (-1,-1), 14),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",  (1,0), (1,-1), "RIGHT"),
        ("SPAN",   (0,0), (0,0)),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6*mm))

    # ── Summary cards ─────────────────────────────────────────────────────────
    cw = W / 3 - 2*mm
    def _stat_cell(val, label, bg, fg):
        return Table(
            [[Paragraph(str(val), _style("sv", fontSize=22, textColor=fg,
                                         fontName="Helvetica-Bold", alignment=1))],
             [Paragraph(label,    _style("sl", fontSize=8,  textColor=fg,
                                         fontName="Helvetica-Bold", alignment=1,
                                         spaceAfter=0))]],
            colWidths=[cw]
        )

    stat_cells = [
        _stat_cell(summary["present_days"], "DAYS PRESENT",   _GREEN_BG, _GREEN_TXT),
        _stat_cell(summary["total_hours"],  "TOTAL HOURS",    _AMBER_BG, _AMBER_TXT),
        _stat_cell(summary["leave_days"],   "LEAVE DAYS",     _RED_BG,   _RED_TXT),
    ]
    for sc in stat_cells:
        sc.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), sc._argH and sc._argH or colors.white),
            ("BOX",        (0,0), (-1,-1), 0.5, _BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 10),
            ("BOTTOMPADDING", (0,0), (-1,-1), 10),
            ("ROUNDEDCORNERS", [4]),
        ]))

    # Rebuild stat cells with styling applied inline
    def _stat_tbl(val, label, bg, fg):
        t = Table(
            [[Paragraph(str(val),  ParagraphStyle("sv2", fontSize=22, textColor=fg,
                                                   fontName="Helvetica-Bold", alignment=1,
                                                   leading=26))],
             [Paragraph(label,     ParagraphStyle("sl2", fontSize=8,  textColor=fg,
                                                   fontName="Helvetica-Bold", alignment=1,
                                                   leading=11))]],
            colWidths=[cw],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), bg),
            ("BOX",           (0,0), (-1,-1), 0.8, _BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 10),
            ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ]))
        return t

    summary_row = Table([[
        _stat_tbl(summary["present_days"], "DAYS PRESENT", _GREEN_BG, _GREEN_TXT),
        _stat_tbl(summary["total_hours"],  "TOTAL HOURS",  _AMBER_BG, _AMBER_TXT),
        _stat_tbl(summary["leave_days"],   "LEAVE DAYS",   _RED_BG,   _RED_TXT),
    ]], colWidths=[cw+2*mm, cw+2*mm, cw+2*mm])
    summary_row.setStyle(TableStyle([
        ("LEFTPADDING",  (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING",   (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",(0,0), (-1,-1), 0),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(summary_row)
    story.append(Spacer(1, 6*mm))

    # ── Attendance table ──────────────────────────────────────────────────────
    story.append(Paragraph("Attendance Records", s_sec))
    story.append(HRFlowable(width=W, thickness=1, color=_BLUE, spaceAfter=4))

    if attendance:
        th = ["Date", "Check-in", "Check-out", "Duration", "Status"]
        rows = [th]
        for i, r in enumerate(attendance):
            status_txt = "Closed" if r["status"] == "closed" else "Open"
            rows.append([
                r["date"], r["checkin"], r["checkout"],
                r["duration"], status_txt
            ])

        att_tbl = Table(rows, colWidths=[35*mm, 30*mm, 30*mm, 30*mm, None])
        ts = TableStyle([
            # header row
            ("BACKGROUND",    (0,0), (-1,0), _BLUE),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,0), 9),
            ("BOTTOMPADDING", (0,0), (-1,0), 7),
            ("TOPPADDING",    (0,0), (-1,0), 7),
            # data rows
            ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
            ("FONTSIZE",      (0,1), (-1,-1), 9),
            ("TOPPADDING",    (0,1), (-1,-1), 6),
            ("BOTTOMPADDING", (0,1), (-1,-1), 6),
            ("GRID",          (0,0), (-1,-1), 0.4, _BORDER),
            ("TEXTCOLOR",     (0,1), (-1,-1), _DARK),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ])
        # alternating rows
        for i in range(1, len(rows)):
            if i % 2 == 0:
                ts.add("BACKGROUND", (0,i), (-1,i), _ROW_ALT)
        # colour status column
        for i, r in enumerate(attendance, start=1):
            if r["status"] == "closed":
                ts.add("TEXTCOLOR",  (4,i), (4,i), _GREEN_TXT)
                ts.add("FONTNAME",   (4,i), (4,i), "Helvetica-Bold")
            else:
                ts.add("TEXTCOLOR",  (4,i), (4,i), _AMBER_TXT)
                ts.add("FONTNAME",   (4,i), (4,i), "Helvetica-Bold")
        att_tbl.setStyle(ts)
        story.append(att_tbl)
    else:
        story.append(Paragraph("No attendance records for this period.", s_body))

    story.append(Spacer(1, 6*mm))

    # ── Leave table ───────────────────────────────────────────────────────────
    story.append(Paragraph("Approved Leave Applications", s_sec))
    story.append(HRFlowable(width=W, thickness=1, color=_BLUE, spaceAfter=4))

    if leaves:
        lh = ["Leave Type", "From", "To", "Days", "Reason", "Approved By"]
        lrows = [lh]
        for lv in leaves:
            ltype = lv["leave_type"].capitalize()
            lrows.append([
                ltype,
                lv["start_date"],
                lv["end_date"],
                str(lv["days"]),
                lv.get("description") or "—",
                "@" + (lv.get("decided_by") or "—"),
            ])

        leave_tbl = Table(lrows, colWidths=[28*mm, 24*mm, 24*mm, 14*mm, None, 28*mm])
        lts = TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), _BLUE),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,0), 9),
            ("BOTTOMPADDING", (0,0), (-1,0), 7),
            ("TOPPADDING",    (0,0), (-1,0), 7),
            ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
            ("FONTSIZE",      (0,1), (-1,-1), 9),
            ("TOPPADDING",    (0,1), (-1,-1), 6),
            ("BOTTOMPADDING", (0,1), (-1,-1), 6),
            ("GRID",          (0,0), (-1,-1), 0.4, _BORDER),
            ("TEXTCOLOR",     (0,1), (-1,-1), _DARK),
            ("ALIGN",         (0,0), (3,-1), "CENTER"),
            ("ALIGN",         (4,1), (4,-1), "LEFT"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ])
        for i in range(1, len(lrows)):
            if i % 2 == 0:
                lts.add("BACKGROUND", (0,i), (-1,i), _ROW_ALT)
        leave_tbl.setStyle(lts)
        story.append(leave_tbl)
    else:
        story.append(Paragraph("No approved leave for this period.", s_body))

    story.append(Spacer(1, 10*mm))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width=W, thickness=0.5, color=_BORDER))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"Attendance Tracker  ·  Confidential  ·  Generated {gen_date}",
        s_footer
    ))

    doc.build(story)
    return buf.getvalue()


@router.get("/manager/employee-report/{username}/pdf")
async def employee_report_pdf(
    username: str,
    start: str = "",
    end: str = "",
    _u: dict = Depends(require_manager),
):
    # Re-use the same data-gathering logic
    tz_today = dt.datetime.now(tz=_tz).date()
    if not start:
        start = tz_today.replace(day=1).isoformat()
    if not end:
        end = tz_today.isoformat()
    try:
        dt.datetime.strptime(start, "%Y-%m-%d")
        dt.datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date format")

    db = get_db()
    employee = await db["users"].find_one({"username": username}, {"password_hash": 0})
    if not employee:
        raise HTTPException(404, "Employee not found")
    employee.pop("_id", None)

    att_cursor = db["attendance"].find(
        {"user_id": username, "date": {"$gte": start, "$lte": end}},
        sort=[("date", 1)],
    )
    attendance = []
    total_seconds = 0
    present_days = 0
    async for r in att_cursor:
        ci = dt.datetime.fromisoformat(r["checkin_utc"])
        co = dt.datetime.fromisoformat(r["checkout_utc"]) if r.get("checkout_utc") else None
        dur = None
        if co:
            diff = co - ci
            secs = int(diff.total_seconds())
            total_seconds += secs
            h, rem = divmod(secs, 3600)
            m = rem // 60
            dur = f"{h}h {m}m"
        present_days += 1
        attendance.append({
            "date": r["date"],
            "checkin": _fmt(r["checkin_utc"]),
            "checkout": _fmt(r["checkout_utc"]) if r.get("checkout_utc") else "—",
            "duration": dur or "—",
            "status": r["status"],
        })

    total_h, total_rem = divmod(total_seconds, 3600)
    total_m = total_rem // 60

    leave_cursor = db["leave_requests"].find(
        {"user_id": username, "status": "approved"},
        sort=[("start_date", 1)],
    )
    leaves = []
    total_leave_days = 0
    async for lv in leave_cursor:
        lv.pop("_id", None)
        if lv["end_date"] >= start and lv["start_date"] <= end:
            total_leave_days += lv["days"]
            leaves.append({
                "leave_type": lv["leave_type"],
                "start_date": lv["start_date"],
                "end_date": lv["end_date"],
                "days": lv["days"],
                "description": lv.get("description", ""),
                "decided_by": lv.get("decided_by", ""),
            })

    summary = {
        "present_days": present_days,
        "total_hours": f"{total_h}h {total_m}m",
        "leave_days": total_leave_days,
    }

    pdf_bytes = _build_pdf(
        emp=employee,
        period={"start": start, "end": end},
        summary=summary,
        attendance=attendance,
        leaves=leaves,
    )

    safe_name = employee.get("name", username).replace(" ", "_")
    filename = f"report_{safe_name}_{start}_{end}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
