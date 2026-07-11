"""
There is intentionally NO admin signup endpoint. The admin identity is a
single, manually-seeded row in the existing "user" table with
username="admin" — no schema change, no separate table. Login here just
looks up that one row by username and checks its password the same way
regular login does. All other endpoints in this router require the
resulting JWT (role="admin").
"""
import os
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel
from sqlmodel import Session, func, select
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import auth
import database
import models

load_dotenv()

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
security = HTTPBearer()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
PRO_MONTHLY_PRICE_USD = float(os.getenv("PRO_MONTHLY_PRICE_USD", "5"))

# ---------- schemas ----------
class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- auth ----------
def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    payload = auth.decode_access_token(credentials.credentials)
    if payload is None or payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired admin session")
    return payload.get("sub", "admin")


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(payload: AdminLoginRequest):
    # Only the one designated username is eligible to become an admin session,
    # regardless of what else exists in the user table.
    if payload.username != ADMIN_USERNAME:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    with Session(database.engine) as session:
        user = session.exec(select(models.User).where(models.User.username == ADMIN_USERNAME)).first()

    if not user or not auth.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = auth.create_access_token(
        {"sub": user.username, "role": "admin"}, expires_delta=timedelta(hours=12)
    )
    return AdminLoginResponse(access_token=token)


# ---------- overview ----------
@router.get("/overview")
def get_overview(admin: str = Depends(get_current_admin)):
    with Session(database.engine) as session:
        total_users = session.exec(select(func.count(models.User.id))).one()
        pro_users = session.exec(
            select(func.count(models.User.id)).where(models.User.plan == "pro")
        ).one()
        free_users = total_users - pro_users

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)

        signups_today = session.exec(
            select(func.count(models.User.id)).where(models.User.created_at >= today_start)
        ).one()
        signups_week = session.exec(
            select(func.count(models.User.id)).where(models.User.created_at >= week_start)
        ).one()
        signups_month = session.exec(
            select(func.count(models.User.id)).where(models.User.created_at >= month_start)
        ).one()

        total_documents = session.exec(select(func.count(models.Document.id))).one()
        docs_by_status = dict(
            session.exec(
                select(models.Document.status, func.count(models.Document.id)).group_by(
                    models.Document.status
                )
            ).all()
        )
        docs_today = session.exec(
            select(func.count(models.Document.id)).where(models.Document.created_at >= today_start)
        ).one()
        docs_week = session.exec(
            select(func.count(models.Document.id)).where(models.Document.created_at >= week_start)
        ).one()

        flagged_documents = session.exec(
            select(func.count(models.Document.id)).where(models.Document.flags.is_not(None))
        ).one()

        avg_confidence = session.exec(select(func.avg(models.Extraction.confidence_score))).one()

        active_subscriptions = session.exec(
            select(func.count(models.Subscription.id)).where(models.Subscription.status == "active")
        ).one()

        total_vendors = session.exec(select(func.count(models.Vendor.id))).one()
        total_feedback = session.exec(select(func.count(models.Feedback.id))).one()
        qb_connections = session.exec(select(func.count(models.QuickBooksConnection.id))).one()

        mrr = round((active_subscriptions or 0) * PRO_MONTHLY_PRICE_USD, 2)

    return {
        "users": {
            "total": total_users,
            "pro": pro_users,
            "free": free_users,
            "signups_today": signups_today,
            "signups_last_7d": signups_week,
            "signups_last_30d": signups_month,
        },
        "documents": {
            "total": total_documents,
            "by_status": docs_by_status,
            "created_today": docs_today,
            "created_last_7d": docs_week,
            "flagged": flagged_documents,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
        },
        "billing": {
            "active_subscriptions": active_subscriptions,
            "estimated_mrr_usd": mrr,
            "price_per_seat_usd": PRO_MONTHLY_PRICE_USD,
        },
        "integrations": {
            "quickbooks_connections": qb_connections,
        },
        "vendors": {"total": total_vendors},
        "feedback": {"total": total_feedback},
    }


# ---------- documents time series (for charts) ----------
@router.get("/documents/timeseries")
def documents_timeseries(days: int = Query(default=30, ge=1, le=180), admin: str = Depends(get_current_admin)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with Session(database.engine) as session:
        rows = session.exec(
            select(
                func.date(models.Document.created_at).label("day"),
                func.count(models.Document.id),
            )
            .where(models.Document.created_at >= since)
            .group_by(func.date(models.Document.created_at))
            .order_by(func.date(models.Document.created_at))
        ).all()
    return [{"date": str(day), "count": count} for day, count in rows]


# ---------- users list ----------
@router.get("/users")
def list_users(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    plan: Optional[str] = Query(default=None, description="Filter by 'free' or 'pro'"),
    admin: str = Depends(get_current_admin),
):
    with Session(database.engine) as session:
        query = select(models.User)
        if plan:
            query = query.where(models.User.plan == plan)
        query = query.order_by(models.User.created_at.desc()).offset(offset).limit(limit)
        users = session.exec(query).all()

        total = session.exec(select(func.count(models.User.id))).one()

        results = []
        for u in users:
            doc_count = session.exec(
                select(func.count(models.Document.id)).where(models.Document.owner_id == u.id)
            ).one()
            results.append(
                {
                    "id": u.id,
                    "username": u.username,
                    "plan": u.plan,
                    "created_at": u.created_at,
                    "document_count": doc_count,
                }
            )

    return {"total": total, "limit": limit, "offset": offset, "results": results}


# ---------- feedback list ----------
@router.get("/feedback")
def list_feedback(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: str = Depends(get_current_admin),
):
    with Session(database.engine) as session:
        total = session.exec(select(func.count(models.Feedback.id))).one()
        rows = session.exec(
            select(models.Feedback).order_by(models.Feedback.created_at.desc()).offset(offset).limit(limit)
        ).all()
        results = []
        for f in rows:
            username = None
            if f.user_id:
                u = session.get(models.User, f.user_id)
                username = u.username if u else None
            results.append(
                {
                    "id": f.id,
                    "type": f.type,
                    "message": f.message,
                    "created_at": f.created_at,
                    "username": username,
                }
            )
    return {"total": total, "limit": limit, "offset": offset, "results": results}