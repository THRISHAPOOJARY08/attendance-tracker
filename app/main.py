from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db import init_db, close_db
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Attendance Tracker", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/login.html")


@app.get("/dashboard")
async def dashboard():
    return FileResponse("static/dashboard.html")


@app.get("/manager")
async def manager_page():
    return FileResponse("static/manager.html")
