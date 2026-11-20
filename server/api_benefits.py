"""
api_benefits.py - מחשבונים וזכויות (docs/10, v1 סעיף 6).

לקוח:
    GET  /api/client/calculators             המחשבונים הרלוונטיים לו, ומה מוגדר
    POST /api/client/calculators/{id}        חישוב (בלי שמירה)
    GET  /api/client/rights                  זכויות מאושרות שמתאימות לו

משרד (קריאה - כל הצוות; כתיבה - מנהל):
    GET  /api/office/calc-parameters
    POST /api/office/calc-parameters                 ערך חדש (לא דריסה)
    POST /api/office/calc-parameters/{id}/review     אישור/דחייה של עדכון שנעצר
    GET  /api/office/rights
    POST /api/office/rights                          יצירה / עריכה (טיוטה)
    POST /api/office/rights/{id}/status              פרסום / הסרה
"""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, calculators, rates_watch, rights
from .auth import require_admin, require_client, require_csrf, require_staff
from .db.pool import cursor

router = APIRouter()


def _client_context(cur, identity):
    cur.execute(
        """select ct.code, d.percent
             from cases c
             join claim_types ct on ct.id = c.claim_type_id
             left join lateral (select percent from case_decisions
                                 where case_id = c.id order by decided_at desc limit 1) d on true
            where c.client_id = %s and c.firm_id = %s
            order by c.opened_at desc""", (identity.subject_id, identity.firm_id))
    rows = cur.fetchall()
    codes = {r["code"] for r in rows}
    percent = next((r["percent"] for r in rows if r["percent"] is not None), None)
    return codes, percent


# ================================================================
#  לקוח
# ================================================================

@router.get("/api/client/calculators")
def client_calculators(request: Request, identity=Depends(require_client)):
    out = []
    with cursor() as cur:
        codes, percent = _client_context(cur, identity)
        for cid, spec in calculators.CALCULATORS.items():
            rows = calculators.active_params(cur, identity.firm_id, cid)
            # מחשבון שאינו קשור לסוג תביעה מוצג רק אם המשרד הגדיר אותו
            if spec["claims"] and not set(spec["claims"]) & codes:
                continue
            if not spec["claims"] and not rows:
                continue
            inputs = []
            for i in spec["inputs"]:
                item = {k: v for k, v in i.items() if k != "prefix"}
                if i["type"] == "options_from":
                    item["options"] = calculators.options(rows, i["prefix"])
                if i.get("prefill") == "decision_percent" and percent is not None:
                    item["value"] = percent
                inputs.append(item)
            out.append({"id": cid, "title": spec["title"], "configured": bool(rows),
                        "inputs": inputs})
    return {"calculators": out, "disclaimer": calculators.DISCLAIMER}


class ComputeBody(BaseModel):
    inputs: dict = {}


@router.post("/api/client/calculators/{calculator}")
def client_compute(calculator: str, body: ComputeBody, request: Request,
                   identity=Depends(require_client)):
    require_csrf(request)
    if calculator not in calculators.CALCULATORS:
        raise HTTPException(status_code=404, detail="המחשבון לא נמצא.")
    with cursor() as cur:
        rows = calculators.active_params(cur, identity.firm_id, calculator)
    try:
        result = calculators.compute(calculator, rows, body.inputs)
    except calculators.NotConfigured:
        raise HTTPException(status_code=409, detail="המחשבון הזה טרם הוגדר במשרד.")
    except calculators.BadInput as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # הערכים שהלקוח הזין אינם נרשמים - שכר הוא מידע אישי, ואין בו צורך ביומן
    audit.record(identity, "client.calculator_used", request=request,
                 metadata={"kind": calculator})
    return {**result, "low": str(result["low"]), "high": str(result["high"])}


@router.get("/api/client/rights")
def client_rights(request: Request, identity=Depends(require_client)):
    with cursor() as cur:
        prof = rights.profile(cur, identity.subject_id, identity.firm_id)
        cur.execute("""select id, title, summary, provider, link_url, conditions
                         from rights_catalog
                        where firm_id = %s and status = 'published'
                        order by position, title""", (identity.firm_id,))
        rows = cur.fetchall()
    return {"rights": [{"id": str(r["id"]), "title": r["title"], "summary": r["summary"],
                        "provider": r["provider"], "link": r["link_url"]}
                       for r in rows if rights.matches(r["conditions"], prof)]}


# ================================================================
#  משרד - פרמטרים
# ================================================================

@router.get("/api/office/calc-parameters")
def office_parameters(request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute("""select id, calculator, key, label, value, effective_from, status,
                              source_url, extract_pattern, origin, last_checked_at, created_at
                         from calc_parameters where firm_id = %s
                        order by calculator, key, effective_from desc, created_at desc""",
                    (identity.firm_id,))
        rows = cur.fetchall()
    by_calc = {}
    for r in rows:
        by_calc.setdefault(r["calculator"], []).append({
            "id": str(r["id"]), "key": r["key"], "label": r["label"], "value": str(r["value"]),
            "from": r["effective_from"].isoformat(), "status": r["status"],
            "source": r["source_url"], "pattern": r["extract_pattern"], "origin": r["origin"],
            "checkedAt": r["last_checked_at"].isoformat() if r["last_checked_at"] else None})
    return {"calculators": [{
        "id": cid, "title": spec["title"], "type": spec["type"],
        "params": [{"key": k, "label": l} for k, l in spec["params"]],
        "values": by_calc.get(cid, []),
    } for cid, spec in calculators.CALCULATORS.items()],
        "allowedHosts": list(rates_watch.ALLOWED_HOSTS)}


class Parameter(BaseModel):
    calculator: str
    key: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=120)
    value: float
    effective_from: str | None = None
    source_url: str | None = Field(default=None, max_length=500)
    extract_pattern: str | None = Field(default=None, max_length=300)


def _valid_key(spec, key):
    for name, _ in spec["params"]:
        if "<" in name:
            prefix = name.split("<", 1)[0]
            if key.startswith(prefix) and re.fullmatch(r"[\w.-]+", key[len(prefix):] or "-"):
                return True
        elif key == name:
            return True
    return key == "uncertainty_percent"


@router.post("/api/office/calc-parameters")
def add_parameter(body: Parameter, request: Request, identity=Depends(require_admin)):
    require_csrf(request)
    spec = calculators.CALCULATORS.get(body.calculator)
    if spec is None:
        raise HTTPException(status_code=400, detail="מחשבון לא מוכר.")
    key = body.key.strip()
    if not _valid_key(spec, key):
        raise HTTPException(status_code=400, detail="מפתח שאינו של המחשבון הזה.")
    if body.source_url and not rates_watch.allowed_url(body.source_url):
        raise HTTPException(status_code=400,
                            detail="מקור מותר: https באתרי ממשלה ו'כל זכות' בלבד.")
    if body.extract_pattern:
        try:
            if re.compile(body.extract_pattern).groups < 1:
                raise ValueError
        except (re.error, ValueError):
            raise HTTPException(status_code=400, detail="הביטוי צריך קבוצה אחת שתופסת את המספר.")
    try:
        effective = date.fromisoformat(body.effective_from) if body.effective_from else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="תאריך התחולה אינו תקין.")
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into calc_parameters (firm_id, calculator, key, label, value, effective_from,
                                            source_url, extract_pattern, created_by_user_id,
                                            approved_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, body.calculator, key, (body.label or "").strip() or None,
             body.value, effective, body.source_url or None, body.extract_pattern or None,
             identity.subject_id, identity.subject_id))
        pid = cur.fetchone()["id"]
    audit.record(identity, "office.calc_parameter_set", request=request,
                 metadata={"kind": body.calculator})
    return {"ok": True, "id": str(pid)}


class Review(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


@router.post("/api/office/calc-parameters/{param_id}/review")
def review_parameter(param_id: str, body: Review, request: Request,
                     identity=Depends(require_admin)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        cur.execute("""update calc_parameters
                          set status = %s, approved_by_user_id = %s
                        where id = %s and firm_id = %s and status = 'pending'
                        returning calculator""",
                    ("active" if body.action == "approve" else "rejected",
                     identity.subject_id, param_id, identity.firm_id))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="אין עדכון ממתין כזה.")
    audit.record(identity, "office.calc_parameter_reviewed", request=request,
                 metadata={"kind": row["calculator"], "decision": body.action})
    return {"ok": True}


# ================================================================
#  משרד - קטלוג הזכויות
# ================================================================

@router.get("/api/office/rights")
def office_rights(request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute("""select * from rights_catalog where firm_id = %s
                        order by status <> 'published', position, title""", (identity.firm_id,))
        rows = cur.fetchall()
    return {"rights": [{"id": str(r["id"]), "title": r["title"], "summary": r["summary"],
                        "provider": r["provider"], "link": r["link_url"],
                        "conditions": r["conditions"], "status": r["status"],
                        "position": r["position"]} for r in rows],
            "knownConditions": sorted(rights.KNOWN)}


class Right(BaseModel):
    id: str | None = None
    title: str = Field(min_length=2, max_length=160)
    summary: str = Field(min_length=10, max_length=2000)
    provider: str | None = Field(default=None, max_length=120)
    link_url: str | None = Field(default=None, max_length=500)
    conditions: dict = {}
    position: int = Field(default=100, ge=0, le=10000)


@router.post("/api/office/rights")
def save_right(body: Right, request: Request, identity=Depends(require_admin)):
    """שמירה מחזירה תמיד לטיוטה: נוסח שהשתנה צריך אישור מחדש."""
    require_csrf(request)
    if set(body.conditions) - rights.KNOWN:
        raise HTTPException(status_code=400, detail="תנאי לא מוכר: %s" %
                            ", ".join(sorted(set(body.conditions) - rights.KNOWN)))
    if body.link_url and not body.link_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="קישור חייב להתחיל ב-https://")
    import json
    values = (body.title.strip(), body.summary.strip(), (body.provider or "").strip() or None,
              body.link_url or None, json.dumps(body.conditions), body.position)
    with cursor(commit=True) as cur:
        if body.id:
            cur.execute("""update rights_catalog set title = %s, summary = %s, provider = %s,
                                  link_url = %s, conditions = %s, position = %s,
                                  status = 'draft', approved_by_user_id = null, approved_at = null,
                                  updated_at = now()
                            where id = %s and firm_id = %s returning id""",
                        values + (body.id, identity.firm_id))
        else:
            cur.execute("""insert into rights_catalog (firm_id, title, summary, provider, link_url,
                                                       conditions, position)
                           values (%s, %s, %s, %s, %s, %s, %s) returning id""",
                        (identity.firm_id,) + values)
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="הזכות לא נמצאה.")
    audit.record(identity, "office.right_saved", request=request)
    return {"ok": True, "id": str(row["id"])}


class RightStatus(BaseModel):
    status: str = Field(pattern="^(published|retired|draft)$")


@router.post("/api/office/rights/{right_id}/status")
def set_right_status(right_id: str, body: RightStatus, request: Request,
                     identity=Depends(require_admin)):
    """פרסום = אישור של עורך דין שהנוסח נכון. נרשם מי ומתי."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        if body.status == "published":
            cur.execute("""update rights_catalog set status = 'published',
                                  approved_by_user_id = %s, approved_at = now(), updated_at = now()
                            where id = %s and firm_id = %s returning id""",
                        (identity.subject_id, right_id, identity.firm_id))
        else:
            cur.execute("""update rights_catalog set status = %s, updated_at = now()
                            where id = %s and firm_id = %s returning id""",
                        (body.status, right_id, identity.firm_id))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="הזכות לא נמצאה.")
    audit.record(identity, "office.right_status_changed", request=request,
                 metadata={"to_status": body.status})
    return {"ok": True}
