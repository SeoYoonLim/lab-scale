from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.research import router as research_router

app = FastAPI(title="AI Investment Research Agent")

# 로컬 프론트 개발 서버(Vite 5173, CRA/Next 3000) 허용. 실제 프론트 포트가 확정되면 조정한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research_router)
