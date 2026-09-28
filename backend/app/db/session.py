import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# 호스트 포트는 15432 (5432는 Windows 예약 포트 범위에 걸려 docker가 바인딩하지 못함)
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment",
)

# DB가 꺼져 있으면 기본 설정은 요청이 끝없이 매달린다. 5초 안에 실패시켜 API가 503을 돌려줄 수 있게 한다.
engine = create_engine(DB_URL, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
