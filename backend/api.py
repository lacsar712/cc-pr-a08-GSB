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
CREATE TABLE IF NOT EXISTS forbidden_window (
    id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    start_minute integer,
    end_minute integer,
    updated_by text NOT NULL DEFAULT '',
    updated_at timestamptz
);
CREATE TABLE IF NOT EXISTS forbidden_logs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    attempted_by text NOT NULL,
    attempted_at timestamptz NOT NULL,
    server_clock text NOT NULL,
    start_minute integer NOT NULL,
    end_minute integer NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float


class WindowIn(BaseModel):
    start_minute: int | None = None
    end_minute: int | None = None


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


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def in_closed_window(now_minute: int, start_minute: int, end_minute: int) -> bool:
    """服务器时刻是否落在禁投闭区间内；start>end 表示跨午夜。"""
    if start_minute <= end_minute:
        return start_minute <= now_minute <= end_minute
    return now_minute >= start_minute or now_minute <= end_minute


def server_now() -> datetime:
    return datetime.now(timezone.utc)


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = server_now()
            conn.execute(
                """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        # 默认不设禁投钟点（NULL 表示关闭）
        conn.execute(
            """INSERT INTO forbidden_window (id, start_minute, end_minute)
               VALUES (1, NULL, NULL) ON CONFLICT (id) DO NOTHING"""
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


def load_window(conn) -> dict | None:
    return conn.execute(
        "SELECT start_minute, end_minute, updated_by, updated_at FROM forbidden_window WHERE id = 1"
    ).fetchone()


def window_status(win: dict, now: datetime) -> dict:
    now_minute = now.hour * 60 + now.minute
    start_minute = win["start_minute"]
    end_minute = win["end_minute"]
    active = (
        start_minute is not None
        and end_minute is not None
        and in_closed_window(now_minute, start_minute, end_minute)
    )
    return {
        "start_minute": start_minute,
        "end_minute": end_minute,
        "start_label": hhmm(start_minute) if start_minute is not None else None,
        "end_label": hhmm(end_minute) if end_minute is not None else None,
        "forbidden_now": active,
        "server_time": now.isoformat(),
        "server_clock": hhmm(now_minute),
        "updated_by": win["updated_by"],
        "updated_at": win["updated_at"].isoformat() if win["updated_at"] else None,
    }


@app.get("/api/forbidden-window")
def get_window(_user: dict = Depends(current_user)):
    with connect() as conn:
        win = load_window(conn)
    return window_status(win, server_now())


@app.put("/api/forbidden-window")
def put_window(body: WindowIn, user: dict = Depends(require_writer)):
    start_minute, end_minute = body.start_minute, body.end_minute
    if (start_minute is None) != (end_minute is None):
        raise HTTPException(status_code=422, detail="起止钟点须同时设置或同时清空")
    for value in (start_minute, end_minute):
        if value is not None and not (0 <= value <= 1439):
            raise HTTPException(status_code=422, detail="钟点须在 00:00 至 23:59 之间")
    now = server_now()
    with connect() as conn:
        conn.execute(
            """UPDATE forbidden_window
               SET start_minute = %s, end_minute = %s, updated_by = %s, updated_at = %s
               WHERE id = 1""",
            (start_minute, end_minute, user["username"], now),
        )
        conn.commit()
        win = load_window(conn)
    return window_status(win, now)


@app.get("/api/forbidden-logs")
def list_logs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT id, sheet, cyan_mm, magenta_mm, attempted_by, attempted_at,
                      server_clock, start_minute, end_minute
               FROM forbidden_logs ORDER BY id DESC LIMIT 100"""
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    now = server_now()
    now_minute = now.hour * 60 + now.minute
    with connect() as conn:
        win = load_window(conn)
        start_minute = win["start_minute"]
        end_minute = win["end_minute"]
        blocked = (
            start_minute is not None
            and end_minute is not None
            and in_closed_window(now_minute, start_minute, end_minute)
        )
        if blocked:
            conn.execute(
                """INSERT INTO forbidden_logs
                   (sheet, cyan_mm, magenta_mm, attempted_by, attempted_at,
                    server_clock, start_minute, end_minute)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    body.sheet.strip(),
                    body.cyan_mm,
                    body.magenta_mm,
                    user["username"],
                    now,
                    hhmm(now_minute),
                    start_minute,
                    end_minute,
                ),
            )
            conn.commit()
            raise HTTPException(
                status_code=403,
                detail=(
                    f"服务器时刻 {hhmm(now_minute)} 处于禁投钟点窗 "
                    f"{hhmm(start_minute)}–{hhmm(end_minute)}（闭区间），投递已退回，移出该钟点窗后才允许入队"
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
