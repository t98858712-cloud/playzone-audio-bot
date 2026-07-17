from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from web.api import init_web_routes, WEB_DIR

app = FastAPI(title="PlayZone Dashboard")
app.mount("/files", StaticFiles(directory=WEB_DIR), name="files")
init_web_routes(app)
