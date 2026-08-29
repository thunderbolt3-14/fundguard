from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import get_db

app = FastAPI(title="FundGuard API")


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}