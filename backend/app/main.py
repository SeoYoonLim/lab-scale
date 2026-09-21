from fastapi import FastAPI

from app.api.research import router as research_router

app = FastAPI(title="AI Investment Research Agent")

app.include_router(research_router)
