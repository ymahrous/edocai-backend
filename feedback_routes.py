import database, models
from sqlmodel import Session
from pydantic import BaseModel
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

class FeedbackCreate(BaseModel):
    type: str
    message: str

@router.post("/")
def submit_feedback(
    req: FeedbackCreate, 
    session: Session = Depends(database.get_session),
    # user: models.User = Depends(get_current_user) # Uncomment if you only want logged-in users
):
    feedback = models.Feedback(type=req.type, message=req.message)
    # if user: feedback.user_id = user.id
    session.add(feedback)
    session.commit()
    return {"status": "success"}