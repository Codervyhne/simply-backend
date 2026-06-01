import os
import time
import sqlite3
import hashlib
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config (edit these) ───────────────────────────────────────────────────────

# Uses /data if the Render persistent disk is mounted, otherwise falls back to
# /tmp (data won't survive redeploys without the disk attached)
DB_PATH     = "/data/simply.db" if os.path.isdir("/data") else "/tmp/simply.db"
IP_SALT     = "12573a559d2dc72817dc6fd08504fb81badc5ba0f5c3b3a640548d8e28c32ad2"
CORS_ORIGIN = "https://html.cafe/x2cbd5005"   # set to your frontend URL e.g. "https://simply.example.com"

RATE_LIMIT_REQUESTS = 30     # max requests per IP per window
RATE_LIMIT_WINDOW   = 60     # window in seconds
BAN_THRESHOLD       = 120    # requests in window before auto-ban
BAN_DURATION        = 3600   # ban length in seconds (1 hour)

# ─────────────────────────────────────────────────────────────────────────────

request_log: dict[str, list[float]] = defaultdict(list)
banned_ips:  dict[str, float]       = {}


def get_client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host


def check_rate_limit(ip: str) -> tuple[bool, int]:
    now = time.time()

    ban_until = banned_ips.get(ip)
    if ban_until:
        if now < ban_until:
            return False, int(ban_until - now)
        del banned_ips[ip]

    window_start = now - RATE_LIMIT_WINDOW
    request_log[ip] = [t for t in request_log[ip] if t > window_start]

    if len(request_log[ip]) >= BAN_THRESHOLD:
        banned_ips[ip] = now + BAN_DURATION
        logger.warning(f"Auto-banned {ip} for {BAN_DURATION}s")
        return False, BAN_DURATION

    if len(request_log[ip]) >= RATE_LIMIT_REQUESTS:
        retry_after = int(RATE_LIMIT_WINDOW - (now - request_log[ip][0])) + 1
        return False, retry_after

    request_log[ip].append(now)
    return True, 0


def get_db() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    logger.info(f"Using database at {DB_PATH}")
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS visits (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                ip_hash TEXT    NOT NULL,
                page    TEXT    NOT NULL DEFAULT 'home'
            );
            CREATE TABLE IF NOT EXISTS game_launches (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER NOT NULL,
                game_url  TEXT    NOT NULL,
                game_name TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_visits_ts    ON visits (ts);
            CREATE INDEX IF NOT EXISTS idx_launches_ts  ON game_launches (ts);
            CREATE INDEX IF NOT EXISTS idx_launches_url ON game_launches (game_url);
        """)
    logger.info("Database ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Simply. Stats API", version="1.0.0", lifespan=lifespan, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in ("/", "/health"):
        return await call_next(request)
    ip = get_client_ip(request)
    allowed, retry_after = check_rate_limit(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": "Too many requests", "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


def hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{IP_SALT}{ip}".encode()).hexdigest()[:16]


def now_ts() -> int:
    return int(time.time())


@app.get("/")
def root():
    return {"status": "ok", "service": "Simply. Stats API"}


@app.get("/health")
def health():
    return {"status": "ok", "ts": now_ts(), "db": DB_PATH}


@app.post("/visit")
async def track_visit(request: Request):
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    page    = str(body.get("page", "home"))[:32]
    ip_hash = hash_ip(get_client_ip(request))
    with get_db() as conn:
        conn.execute(
            "INSERT INTO visits (ts, ip_hash, page) VALUES (?, ?, ?)",
            (now_ts(), ip_hash, page),
        )
    return {"ok": True}


@app.post("/launch")
async def track_launch(request: Request):
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    game_url  = str(body.get("url",  ""))[:512]
    game_name = str(body.get("name", ""))[:128]
    if not game_url:
        raise HTTPException(400, "url is required")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO game_launches (ts, game_url, game_name) VALUES (?, ?, ?)",
            (now_ts(), game_url, game_name),
        )
    return {"ok": True}


@app.get("/stats")
def get_stats():
    now  = now_ts()
    day  = now - 86_400
    week = now - 604_800

    with get_db() as conn:
        total_visits   = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        unique_all     = conn.execute("SELECT COUNT(DISTINCT ip_hash) FROM visits").fetchone()[0]
        unique_day     = conn.execute("SELECT COUNT(DISTINCT ip_hash) FROM visits WHERE ts >= ?", (day,)).fetchone()[0]
        unique_week    = conn.execute("SELECT COUNT(DISTINCT ip_hash) FROM visits WHERE ts >= ?", (week,)).fetchone()[0]
        visits_day     = conn.execute("SELECT COUNT(*) FROM visits WHERE ts >= ?", (day,)).fetchone()[0]
        visits_week    = conn.execute("SELECT COUNT(*) FROM visits WHERE ts >= ?", (week,)).fetchone()[0]
        total_launches = conn.execute("SELECT COUNT(*) FROM game_launches").fetchone()[0]
        launches_day   = conn.execute("SELECT COUNT(*) FROM game_launches WHERE ts >= ?", (day,)).fetchone()[0]
        top_games = conn.execute("""
            SELECT game_name, game_url, COUNT(*) as plays
            FROM game_launches GROUP BY game_url ORDER BY plays DESC LIMIT 5
        """).fetchall()
        hourly = conn.execute("""
            SELECT (ts / 3600) * 3600 AS hour_ts, COUNT(*) as count
            FROM visits WHERE ts >= ? GROUP BY hour_ts ORDER BY hour_ts
        """, (day,)).fetchall()

    return {
        "visits":          {"total": total_visits,   "today": visits_day,     "this_week": visits_week},
        "unique_visitors": {"total": unique_all,      "today": unique_day,     "this_week": unique_week},
        "launches":        {"total": total_launches,  "today": launches_day},
        "top_games":       [{"name": r["game_name"],  "url": r["game_url"],    "plays": r["plays"]} for r in top_games],
        "hourly_visits":   [{"ts": r["hour_ts"],      "count": r["count"]}     for r in hourly],
        "generated_at":    now,
    }
