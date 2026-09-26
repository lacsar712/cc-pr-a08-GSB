import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}

# 默认禁投钟点窗 02:00–06:00（本地钟点，闭区间）
DEFAULT_BAN_START = 2 * 60
DEFAULT_BAN_END = 6 * 60


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS ban_window (
    id smallint PRIMARY KEY DEFAULT 1,
    start_minute integer NOT NULL,
    end_minute integer NOT NULL,
    updated_by text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ban_events (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    start_minute integer NOT NULL,
    end_minute integer NOT NULL,
    now_minute integer NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float


class BanWindowIn(BaseModel):
    start_minute: int
    end_minute: int


def in_ban_window(now_minute: int, start_minute: int, end_minute: int) -> bool:
    """钟点是否落在禁投闭区间内；起止相同视为全天禁投，跨午夜按两段处理。"""
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= now_minute <= end_minute
    return now_minute >= start_minute or now_minute <= end_minute


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可操作")
    return user


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        exists_window = conn.execute("SELECT COUNT(*) AS n FROM ban_window WHERE id = 1").fetchone()["n"]
        if exists_window == 0:
            conn.execute(
                "INSERT INTO ban_window (id, start_minute, end_minute) VALUES (1, %s, %s)",
                (DEFAULT_BAN_START, DEFAULT_BAN_END),
            )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "print-register-review"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/jobs")
def list_jobs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()


@app.get("/api/ban-window")
def get_ban_window(_user: dict = Depends(current_user)):
    with connect() as conn:
        win = conn.execute("SELECT start_minute, end_minute, updated_by, updated_at FROM ban_window WHERE id = 1").fetchone()
    now = datetime.now().astimezone()
    now_minute = now.hour * 60 + now.minute
    return {
        "start_minute": win["start_minute"],
        "end_minute": win["end_minute"],
        "updated_by": win["updated_by"],
        "updated_at": win["updated_at"],
        "server_clock": now.strftime("%Y-%m-%d %H:%M:%S"),
        "now_minute": now_minute,
        "banned": in_ban_window(now_minute, win["start_minute"], win["end_minute"]),
    }


@app.put("/api/ban-window")
def update_ban_window(body: BanWindowIn, user: dict = Depends(require_writer)):
    if not (0 <= body.start_minute <= 1439 and 0 <= body.end_minute <= 1439):
        raise HTTPException(status_code=400, detail="钟点需在 00:00–23:59 之间")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        conn.execute(
            """UPDATE ban_window SET start_minute = %s, end_minute = %s, updated_by = %s, updated_at = %s
               WHERE id = 1""",
            (body.start_minute, body.end_minute, user["username"], now),
        )
        conn.commit()
    return get_ban_window(user)


@app.get("/api/ban-events")
def list_ban_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT id, sheet, cyan_mm, magenta_mm, start_minute, end_minute, now_minute, created_by,
                      to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-Dd HH24:MI:SS') AS created_at_text
               FROM ban_events ORDER BY id DESC LIMIT 100"""
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        win = conn.execute("SELECT start_minute, end_minute FROM ban_window WHERE id = 1").fetchone()
        now = datetime.now().astimezone()
        now_minute = now.hour * 60 + now.minute
        if in_ban_window(now_minute, win["start_minute"], win["end_minute"]):
            conn.execute(
                """INSERT INTO ban_events
                   (sheet, cyan_mm, magenta_mm, start_minute, end_minute, now_minute, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    body.sheet.strip(),
                    body.cyan_mm,
                    body.magenta_mm,
                    win["start_minute"],
                    win["end_minute"],
                    now_minute,
                    user["username"],
                    now,
                ),
            )
            conn.commit()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"当前处于禁投钟点窗（{win['start_minute'] // 60:02d}:{win['start_minute'] % 60:02d}"
                    f"–{win['end_minute'] // 60:02d}:{win['end_minute'] % 60:02d}，投递已退回"
                ),
            )
        row = conn.execute(
            """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, status, verdict""",
            (body.sheet.strip(), body.cyan_mm, body.magenta_mm, user["username"], now),
        ).fetchone()
        conn.commit()
    return row
