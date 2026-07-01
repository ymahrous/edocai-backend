from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlmodel import Session, select
from datetime import date
import stripe
import os
import database, models
from dependencies import get_current_user

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PRO_MONTHLY_PRICE_ID = os.getenv("STRIPE_PRICE_ID_PRO_MONTHLY")
PRO_ANNUALLY_PRICE_ID = os.getenv("STRIPE_PRICE_ID_PRO_ANNUAL")

# Endpoint called by the frontend "Upgrade" button
@router.post("/create-checkout-session")
def create_checkout_session(current_user: models.User = Depends(get_current_user)):
    if current_user.plan == "pro":
        raise HTTPException(status_code=400, detail="Already on Pro plan")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": PRO_MONTHLY_PRICE_ID, "quantity": 1}],
            client_reference_id=str(current_user.id),
            success_url=f"{os.getenv('FRONTEND_URL')}/billing/success",
            cancel_url=f"{os.getenv('FRONTEND_URL')}/pricing",
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint called by Stripe's servers when a payment happens
@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    with next(database.get_session()) as session:
        # Handle initial successful checkout
        if event["type"] == "checkout.session.completed":
            checkout_session = event["data"]["object"]
            user_id = checkout_session["client_reference_id"]
            stripe_customer_id = checkout_session["customer"]
            stripe_subscription_id = checkout_session["subscription"]

            # Update or create Subscription record
            sub = session.exec(select(models.Subscription).where(models.Subscription.user_id == user_id)).first()
            if not sub:
                sub = models.Subscription(user_id=user_id)
            
            sub.plan = "pro"
            sub.stripe_customer_id = stripe_customer_id
            sub.stripe_subscription_id = stripe_subscription_id
            sub.status = "active"
            session.add(sub)

            # Update User base table for faster JWT lookups
            user = session.exec(select(models.User).where(models.User.id == user_id)).first()
            if user:
                user.plan = "pro"
                session.add(user)
            
            session.commit()

        # Handle renewals, failures, or cancellations
        elif event["type"] in ["customer.subscription.updated", "customer.subscription.deleted"]:
            subscription = event["data"]["object"]
            stripe_customer_id = subscription["customer"]
            new_status = subscription["status"]
            
            new_plan = "pro" if new_status == "active" else "free"
            
            sub = session.exec(select(models.Subscription).where(models.Subscription.stripe_customer_id == stripe_customer_id)).first()
            if sub:
                sub.status = new_status
                sub.plan = new_plan
                session.add(sub)

                user = session.exec(select(models.User).where(models.User.id == sub.user_id)).first()
                if user:
                    user.plan = new_plan
                    session.add(user)
                
                session.commit()
                session.refresh(user)
                print(user.plan)

    return {"status": "success"}

# Get current usage for the logged-in user
@router.get("/usage")
def get_usage(current_user: models.User = Depends(get_current_user)):
    current_month = date.today().replace(day=1)
    
    with next(database.get_session()) as session:
        usage = session.exec(
            select(models.UsageRecord)
            .where(models.UsageRecord.user_id == current_user.id)
            .where(models.UsageRecord.month == current_month)
        ).first()
        
        docs_processed = usage.documents_processed if usage else 0
        
        return {
            "plan": current_user.plan,
            "documents_processed": docs_processed,
            "limit": 10 if current_user.plan == "free" else -1 # -1 means unlimited
        }