#!/usr/bin/env python3
"""14天备考证券从业考试 — 授权与学习记录后端。

设计取舍（见仓库 README）：
- 只有两种人（admin/user），能不能看内容由 licenses.expires_at 决定，不是 RBAC。
- 错题本不建表，是 attempts 上的窗口函数查询：答对一次自动移出，无需删除写操作。
- SQLite + WAL。100 人在线约 3–5 写/秒，比单机上限低两个数量级，不需要 Redis/MQ。
- 端点写成同步 def，FastAPI 会放进线程池：既不阻塞事件循环，SQLite 写入也天然串行。
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------- config

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SEC14_DB", ROOT / "data" / "sec14.db"))
SECRET = os.environ.get("SEC14_SECRET") or ""
TOKEN_DAYS = int(os.environ.get("SEC14_TOKEN_DAYS", "30"))
MAX_DEVICES = int(os.environ.get("SEC14_MAX_DEVICES", "2"))
CONTENT_JSON = Path(os.environ.get("SEC14_CONTENT", ROOT / "data" / "content.json"))

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 去掉 0/O/1/I/L，买家不会输错
PLANS: dict[str, int] = {"trial7": 7, "d14": 14, "season": 120, "life": 3650}

UTC = timezone.utc


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- db

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users(
  id            INTEGER PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,
  pw_hash       TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'user',
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS licenses(
  code        TEXT PRIMARY KEY,
  plan        TEXT NOT NULL,
  days        INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'unused',
  user_id     INTEGER REFERENCES users(id),
  batch_no    TEXT,
  channel     TEXT,
  order_no    TEXT,
  note        TEXT,
  created_at  TEXT NOT NULL,
  dispatched_at TEXT,
  activated_at TEXT,
  expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_lic_user  ON licenses(user_id);
CREATE INDEX IF NOT EXISTS ix_lic_batch ON licenses(batch_no);

CREATE TABLE IF NOT EXISTS devices(
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  ua          TEXT,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  UNIQUE(user_id, fingerprint)
);

-- qkey 与前端 qid() 完全同构：source|subject|chapter|stem
CREATE TABLE IF NOT EXISTS questions(
  id      INTEGER PRIMARY KEY,
  qkey    TEXT NOT NULL UNIQUE,
  subject TEXT NOT NULL,
  chapter TEXT,
  kind    TEXT NOT NULL,
  source  TEXT NOT NULL,
  stem    TEXT NOT NULL,
  opts    TEXT NOT NULL,
  correct INTEGER NOT NULL,
  explain TEXT,
  pool    TEXT NOT NULL,
  ord     INTEGER NOT NULL,
  active  INTEGER NOT NULL DEFAULT 1
);

-- 日程/清单/笔记整块存，它们从不按行查询
CREATE TABLE IF NOT EXISTS content_blobs(
  key TEXT PRIMARY KEY, json TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- 只追加，永不更新。错题本与正确率都从这里算出来。
CREATE TABLE IF NOT EXISTS attempts(
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  chosen      INTEGER,
  is_correct  INTEGER NOT NULL,
  ms_spent    INTEGER,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_att_uq ON attempts(user_id, question_id, id DESC);

CREATE TABLE IF NOT EXISTS progress(
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, key)
);
"""

PROGRESS_KEYS = {"doneDays", "checklist", "notesOpen", "examDate"}


@contextlib.contextmanager
def db(write: bool = False) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if write:
            con.execute("COMMIT")
    except Exception:
        if write:
            with contextlib.suppress(Exception):
                con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        con.executescript(SCHEMA)
        # 已有的库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）
        cols = {r[1] for r in con.execute("PRAGMA table_info(licenses)")}
        if "dispatched_at" not in cols:
            con.execute("ALTER TABLE licenses ADD COLUMN dispatched_at TEXT")
    finally:
        con.close()


# ---------------------------------------------------------------- crypto

def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, want = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def make_token(uid: int, role: str) -> str:
    return jwt.encode(
        {"sub": str(uid), "role": role,
         "exp": datetime.now(UTC) + timedelta(days=TOKEN_DAYS),
         "iat": datetime.now(UTC)},
        SECRET, algorithm="HS256")


def gen_code() -> str:
    g = lambda k: "".join(secrets.choice(CODE_ALPHABET) for _ in range(k))
    return f"SEC-{g(4)}-{g(4)}-{g(4)}"


def norm_code(s: str) -> str:
    """买家会漏横线、用小写、多打空格。这些都接受。

    0/O/1/I/L 不在码表里，所以打成这些字符时无处可映射——直接判无效，
    让前端给出「第 N 位不是有效字符」这类能自己改对的提示，别猜。
    """
    s = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    body = s[3:] if s.startswith("SEC") else s
    if len(body) != 12 or any(c not in CODE_ALPHABET for c in body):
        return ""
    return f"SEC-{body[0:4]}-{body[4:8]}-{body[8:12]}"


# ---------------------------------------------------------------- rate limit

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def rate_limit(key: str, limit: int, window: int) -> None:
    t = time.monotonic()
    with _hits_lock:
        q = [x for x in _hits.get(key, []) if t - x < window]
        if len(q) >= limit:
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        q.append(t)
        _hits[key] = q
        if len(_hits) > 5000:  # 别让字典无限长
            for k in [k for k, v in _hits.items() if not v or t - v[-1] > window]:
                _hits.pop(k, None)


def client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else None) or (
        req.client.host if req.client else "?")


# ---------------------------------------------------------------- auth dep

class Principal(BaseModel):
    id: int
    email: str
    role: str
    expires_at: str | None
    active: bool


def current_user(authorization: str = Header(default="")) -> Principal:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        claims = jwt.decode(authorization[7:], SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "登录凭证无效")
    with db() as con:
        u = con.execute("SELECT * FROM users WHERE id=?", (claims["sub"],)).fetchone()
        if not u:
            raise HTTPException(401, "账号不存在")
        if u["status"] != "active":
            raise HTTPException(403, "账号已被停用")
        exp = con.execute(
            "SELECT MAX(expires_at) e FROM licenses "
            "WHERE user_id=? AND status='activated'", (u["id"],)).fetchone()["e"]
    return Principal(id=u["id"], email=u["email"], role=u["role"],
                     expires_at=exp, active=bool(exp and exp > now()))


def paid_user(p: Principal = Depends(current_user)) -> Principal:
    """内容访问的唯一闸门：卡还没过期。跟 role 无关。"""
    if p.role == "admin" or p.active:
        return p
    raise HTTPException(402, "授权已到期，请续期后继续使用")


def admin_user(p: Principal = Depends(current_user)) -> Principal:
    if p.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return p


# ---------------------------------------------------------------- device

def touch_device(con: sqlite3.Connection, uid: int, fp: str, ua: str) -> None:
    """注册设备；超过上限就顶掉最久未用的那台。比封号温和，又抬高共享成本。"""
    if not fp:
        return
    t = now()
    con.execute(
        "INSERT INTO devices(user_id,fingerprint,ua,first_seen,last_seen) "
        "VALUES(?,?,?,?,?) ON CONFLICT(user_id,fingerprint) "
        "DO UPDATE SET last_seen=excluded.last_seen, ua=excluded.ua",
        (uid, fp[:80], (ua or "")[:200], t, t))
    # last_seen 只到秒，同秒登录会打平 —— 用 id 兜底，保证淘汰的是真正最旧的那台
    stale = con.execute(
        "SELECT id FROM devices WHERE user_id=? ORDER BY last_seen DESC, id DESC "
        "LIMIT -1 OFFSET ?", (uid, MAX_DEVICES)).fetchall()
    for row in stale:
        con.execute("DELETE FROM devices WHERE id=?", (row["id"],))


# ---------------------------------------------------------------- app

api = FastAPI(title="sec14", docs_url=None, redoc_url=None, openapi_url=None)


@api.exception_handler(HTTPException)
def http_err(_: Request, e: HTTPException) -> JSONResponse:
    return JSONResponse({"error": e.detail}, status_code=e.status_code)


@api.get("/health")
def health() -> dict[str, Any]:
    with db() as con:
        n = con.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
        u = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    return {"ok": True, "questions": n, "users": u, "time": now()}


# ---- 激活 / 登录 ----

class ActivateIn(BaseModel):
    code: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    fp: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    fp: str = ""


@api.post("/activate")
def activate(body: ActivateIn, req: Request) -> dict[str, Any]:
    ip = client_ip(req)
    rate_limit(f"act:{ip}", 10, 600)  # 卡密爆破的主要防线
    code = norm_code(body.code)
    if not code:
        raise HTTPException(400, "激活码格式不对，应为 SEC-XXXX-XXXX-XXXX")
    email = body.email.strip().lower()
    ua = req.headers.get("user-agent", "")
    with db(write=True) as con:
        lic = con.execute("SELECT * FROM licenses WHERE code=?", (code,)).fetchone()
        if not lic:
            raise HTTPException(404, "激活码不存在，请核对后重试")
        if lic["status"] == "revoked":
            raise HTTPException(403, "该激活码已作废，请联系卖家")
        u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

        if lic["status"] == "activated":
            # 已激活的码只能由本人用来续期式绑定，不能给别人开新号
            if not u or u["id"] != lic["user_id"]:
                raise HTTPException(409, "该激活码已被使用")
            if not verify_pw(body.password, u["pw_hash"]):
                raise HTTPException(401, "该激活码已绑定此邮箱，密码不正确")
            uid = u["id"]
        else:
            if u:
                # 老用户拿新卡续期：验密码后叠加时长
                if not verify_pw(body.password, u["pw_hash"]):
                    raise HTTPException(409, "该邮箱已注册，请用原密码登录后再激活")
                uid = u["id"]
            else:
                cur = con.execute(
                    "INSERT INTO users(email,pw_hash,role,created_at,last_login_at) "
                    "VALUES(?,?,'user',?,?)",
                    (email, hash_pw(body.password), now(), now()))
                uid = int(cur.lastrowid)
            # 续期从「当前剩余到期日」往后接，不是从今天重算
            base = con.execute(
                "SELECT MAX(expires_at) e FROM licenses "
                "WHERE user_id=? AND status='activated'", (uid,)).fetchone()["e"]
            start = datetime.fromisoformat(base) if base and base > now() else datetime.now(UTC)
            exp = (start + timedelta(days=lic["days"])).replace(microsecond=0).isoformat()
            con.execute(
                "UPDATE licenses SET status='activated', user_id=?, activated_at=?, "
                "expires_at=? WHERE code=?", (uid, now(), exp, code))
        touch_device(con, uid, body.fp, ua)
        row = con.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    return {"token": make_token(uid, row["role"]), "email": email}


@api.post("/login")
def login(body: LoginIn, req: Request) -> dict[str, Any]:
    ip = client_ip(req)
    email = body.email.strip().lower()
    rate_limit(f"login:{ip}", 20, 600)
    rate_limit(f"login:{email}", 10, 600)
    with db(write=True) as con:
        u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not verify_pw(body.password, u["pw_hash"]):
            raise HTTPException(401, "邮箱或密码不正确")
        if u["status"] != "active":
            raise HTTPException(403, "账号已被停用")
        con.execute("UPDATE users SET last_login_at=? WHERE id=?", (now(), u["id"]))
        touch_device(con, u["id"], body.fp, req.headers.get("user-agent", ""))
    return {"token": make_token(u["id"], u["role"]), "email": email}


@api.get("/me")
def me(p: Principal = Depends(current_user)) -> dict[str, Any]:
    with db() as con:
        dev = con.execute("SELECT COUNT(*) c FROM devices WHERE user_id=?",
                          (p.id,)).fetchone()["c"]
        att = con.execute("SELECT COUNT(*) c FROM attempts WHERE user_id=?",
                          (p.id,)).fetchone()["c"]
    days = None
    if p.expires_at:
        days = max(0, (datetime.fromisoformat(p.expires_at) - datetime.now(UTC)).days)
    return {"uid": p.id, "email": p.email, "role": p.role, "active": p.active,
            "expires_at": p.expires_at, "days_left": days,
            "devices": dev, "max_devices": MAX_DEVICES, "attempts": att}


# ---- 内容 ----

_content_cache: dict[str, Any] = {}
_content_lock = threading.Lock()


def content_payload() -> tuple[str, str]:
    """返回 (json_text, etag)。内容极少变，进程内缓存一份。"""
    with _content_lock:
        if _content_cache:
            return _content_cache["body"], _content_cache["etag"]
        with db() as con:
            qs = [dict(r) for r in con.execute(
                "SELECT id,qkey,subject,chapter,kind,source,stem,opts,correct,explain,pool "
                "FROM questions WHERE active=1 ORDER BY ord")]
            blobs = {r["key"]: json.loads(r["json"]) for r in
                     con.execute("SELECT key,json FROM content_blobs")}
        for q in qs:
            q["opts"] = json.loads(q["opts"])
        base = [q for q in qs if q["pool"] == "base"]
        zhenti = [q for q in qs if q["pool"] == "zhenti"]
        shape = lambda q: {"id": q["id"], "subject": q["subject"],
                           "chapter": q["chapter"], "kind": q["kind"],
                           "source": q["source"], "q": q["stem"],
                           "opts": q["opts"], "correct": q["correct"],
                           "explain": q["explain"]}
        body = json.dumps({
            "quizData": [shape(q) for q in base],
            "zhentiData": [shape(q) for q in zhenti],
            "schedule": blobs.get("schedule", []),
            "checklistData": blobs.get("checklistData", []),
            "notesData": blobs.get("notesData", []),
        }, ensure_ascii=False, separators=(",", ":"))
        etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:24] + '"'
        _content_cache.update(body=body, etag=etag)
        return body, etag


@api.get("/content")
def content(if_none_match: str = Header(default=""),
            p: Principal = Depends(paid_user)) -> Response:
    body, etag = content_payload()
    if if_none_match and etag in if_none_match:
        return Response(status_code=304, headers={"ETag": etag,
                                                  "Cache-Control": "private, max-age=0"})
    return Response(body, media_type="application/json",
                    headers={"ETag": etag, "Cache-Control": "private, max-age=0"})


# ---- 学习记录 ----

class Attempt(BaseModel):
    qkey: str
    chosen: int | None = None
    is_correct: bool
    ms_spent: int | None = None


@api.post("/attempts")
def post_attempts(items: list[Attempt], p: Principal = Depends(paid_user)) -> dict[str, Any]:
    if not items:
        return {"saved": 0}
    if len(items) > 200:
        raise HTTPException(400, "单次最多提交 200 条")
    t = now()
    with db(write=True) as con:
        ids = {r["qkey"]: r["id"] for r in con.execute(
            "SELECT id,qkey FROM questions WHERE qkey IN (%s)"
            % ",".join("?" * len(items)), [i.qkey for i in items])}
        rows = [(p.id, ids[i.qkey], i.chosen, int(i.is_correct), i.ms_spent, t)
                for i in items if i.qkey in ids]
        con.executemany(
            "INSERT INTO attempts(user_id,question_id,chosen,is_correct,ms_spent,created_at)"
            " VALUES(?,?,?,?,?,?)", rows)
    return {"saved": len(rows), "unknown": len(items) - len(rows)}


WRONG_SQL = """
SELECT q.qkey FROM (
  SELECT question_id, is_correct,
         ROW_NUMBER() OVER (PARTITION BY question_id ORDER BY id DESC) rn
  FROM attempts WHERE user_id=?
) a JOIN questions q ON q.id = a.question_id
WHERE a.rn = 1 AND a.is_correct = 0
"""

# 别名不能叫 right —— SQLite 3.39 起 RIGHT 是关键字（RIGHT JOIN）
STATS_SQL = """
SELECT q.subject, q.chapter, q.source, q.pool,
       SUM(a.is_correct) AS n_right, COUNT(*) AS n_total
FROM attempts a JOIN questions q ON q.id = a.question_id
WHERE a.user_id = ?
GROUP BY q.subject, q.chapter, q.source, q.pool
"""


def stats_for(con: sqlite3.Connection, uid: int) -> dict[str, dict[str, int]]:
    """键必须和前端 recordAnswer 里的一致：有章节用「科目｜章节」，真题用来源标签。"""
    labels = {"2024·金融": "2024年6月·金融真题", "2024·法规": "2024年6月·法规真题",
              "2023·金融": "2023年9月·金融真题", "基础题": "基础题"}
    out: dict[str, dict[str, int]] = {}
    for r in con.execute(STATS_SQL, (uid,)):
        key = (f"{r['subject']}｜{r['chapter']}" if r["chapter"]
               else labels.get(r["source"], r["source"]))
        d = out.setdefault(key, {"right": 0, "wrong": 0})
        d["right"] += r["n_right"] or 0
        d["wrong"] += (r["n_total"] or 0) - (r["n_right"] or 0)
    return out


@api.get("/study")
def study(p: Principal = Depends(paid_user)) -> dict[str, Any]:
    """一次拿回错题本 + 正确率 + 进度，省两个往返。"""
    with db() as con:
        wrong = [r["qkey"] for r in con.execute(WRONG_SQL, (p.id,))]
        st = stats_for(con, p.id)
        prog = {r["key"]: json.loads(r["value"]) for r in con.execute(
            "SELECT key,value FROM progress WHERE user_id=?", (p.id,))}
    return {"wrong": wrong, "stats": st, "progress": prog}


@api.delete("/attempts")
def clear_attempts(p: Principal = Depends(paid_user)) -> dict[str, Any]:
    with db(write=True) as con:
        n = con.execute("DELETE FROM attempts WHERE user_id=?", (p.id,)).rowcount
    return {"deleted": n}


class ProgressIn(BaseModel):
    key: Literal["doneDays", "checklist", "notesOpen", "examDate"]
    value: Any


@api.put("/progress")
def put_progress(body: ProgressIn, p: Principal = Depends(paid_user)) -> dict[str, Any]:
    raw = json.dumps(body.value, ensure_ascii=False)
    if len(raw) > 100_000:
        raise HTTPException(413, "进度数据过大")
    with db(write=True) as con:
        con.execute(
            "INSERT INTO progress(user_id,key,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at", (p.id, body.key, raw, now()))
    return {"ok": True}


@api.delete("/progress")
def clear_progress(p: Principal = Depends(paid_user)) -> dict[str, Any]:
    with db(write=True) as con:
        con.execute("DELETE FROM progress WHERE user_id=?", (p.id,))
        con.execute("DELETE FROM attempts WHERE user_id=?", (p.id,))
    return {"ok": True}


# ---- 后台 ----

class MintIn(BaseModel):
    n: int = Field(ge=1, le=500)
    plan: Literal["trial7", "d14", "season", "life"] = "season"
    channel: str = "taobao"
    note: str = ""


@api.post("/admin/licenses")
def mint(body: MintIn, _: Principal = Depends(admin_user)) -> dict[str, Any]:
    batch = "B" + datetime.now(UTC).strftime("%y%m%d%H%M%S")
    codes = []
    with db(write=True) as con:
        while len(codes) < body.n:
            c = gen_code()
            try:
                con.execute(
                    "INSERT INTO licenses(code,plan,days,batch_no,channel,note,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (c, body.plan, PLANS[body.plan], batch,
                     body.channel[:20], body.note[:200], now()))
            except sqlite3.IntegrityError:
                continue  # 撞码概率约 1e-15，撞了就再抽一个
            codes.append(c)
    return {"batch_no": batch, "plan": body.plan, "days": PLANS[body.plan],
            "codes": codes}


class DispatchIn(BaseModel):
    order_no: str = ""
    plan: Literal["trial7", "d14", "season", "life", ""] = ""


@api.post("/admin/licenses/dispatch")
def dispatch(body: DispatchIn, _: Principal = Depends(admin_user)) -> dict[str, Any]:
    """取一个还没发出去的码并盖上发货戳。

    整个操作在一个 IMMEDIATE 事务里完成，所以同时开两个后台标签页也不会
    把同一个码发给两个买家——这是闲鱼手动发货最容易踩的坑。
    """
    with db(write=True) as con:
        sql = ("SELECT code, plan, days FROM licenses "
               "WHERE status='unused' AND dispatched_at IS NULL")
        args: list[Any] = []
        if body.plan:
            sql += " AND plan=?"; args.append(body.plan)
        sql += " ORDER BY created_at, code LIMIT 1"
        row = con.execute(sql, args).fetchone()
        if not row:
            raise HTTPException(409, "没有可发的卡密了，请先在上方生成一批")
        con.execute("UPDATE licenses SET dispatched_at=?, order_no=? WHERE code=?",
                    (now(), body.order_no.strip()[:64] or None, row["code"]))
        left = con.execute(
            "SELECT COUNT(*) c FROM licenses "
            "WHERE status='unused' AND dispatched_at IS NULL").fetchone()["c"]
    return {"code": row["code"], "plan": row["plan"], "days": row["days"],
            "remaining": left}


@api.get("/admin/licenses")
def list_licenses(status: str = "", batch: str = "", limit: int = 200,
                  _: Principal = Depends(admin_user)) -> dict[str, Any]:
    sql = ("SELECT l.code,l.plan,l.status,l.channel,l.batch_no,l.created_at,"
           "l.dispatched_at,l.order_no,l.activated_at,l.expires_at,"
           "u.email,u.id AS uid FROM licenses l "
           "LEFT JOIN users u ON u.id=l.user_id WHERE 1=1")
    args: list[Any] = []
    if status == "stock":          # 库存：还没发给任何人
        sql += " AND l.status='unused' AND l.dispatched_at IS NULL"
    elif status == "sent":         # 已发出，买家还没激活
        sql += " AND l.status='unused' AND l.dispatched_at IS NOT NULL"
    elif status:
        sql += " AND l.status=?"; args.append(status)
    if batch:
        sql += " AND l.batch_no=?"; args.append(batch)
    sql += " ORDER BY l.created_at DESC LIMIT ?"; args.append(min(limit, 1000))
    with db() as con:
        rows = [dict(r) for r in con.execute(sql, args)]
        agg = {r["status"]: r["c"] for r in con.execute(
            "SELECT status, COUNT(*) c FROM licenses GROUP BY status")}
        agg["stock"] = con.execute(
            "SELECT COUNT(*) c FROM licenses "
            "WHERE status='unused' AND dispatched_at IS NULL").fetchone()["c"]
        agg["sent"] = (agg.get("unused", 0) - agg["stock"])
    return {"summary": agg, "licenses": rows}


@api.post("/admin/licenses/{code}/revoke")
def revoke(code: str, _: Principal = Depends(admin_user)) -> dict[str, Any]:
    c = norm_code(code)
    with db(write=True) as con:
        n = con.execute("UPDATE licenses SET status='revoked' WHERE code=?", (c,)).rowcount
    if not n:
        raise HTTPException(404, "激活码不存在")
    return {"ok": True, "code": c}


@api.get("/admin/overview")
def overview(_: Principal = Depends(admin_user)) -> dict[str, Any]:
    with db() as con:
        users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        lic = {r["status"]: r["c"] for r in con.execute(
            "SELECT status, COUNT(*) c FROM licenses GROUP BY status")}
        lic["stock"] = con.execute(
            "SELECT COUNT(*) c FROM licenses "
            "WHERE status='unused' AND dispatched_at IS NULL").fetchone()["c"]
        active = con.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM licenses "
            "WHERE status='activated' AND expires_at > ?", (now(),)).fetchone()["c"]
        dau = con.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM attempts WHERE created_at > ?",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(),)).fetchone()["c"]
        # 全站最难的 20 题 —— 下一版该往哪加题，也是小红书的现成选题
        hardest = [dict(r) for r in con.execute("""
            SELECT q.subject, q.chapter, q.source, substr(q.stem,1,60) stem,
                   COUNT(*) tries,
                   ROUND(100.0*SUM(a.is_correct)/COUNT(*),1) pct
            FROM attempts a JOIN questions q ON q.id=a.question_id
            GROUP BY a.question_id HAVING tries >= 5
            ORDER BY pct ASC, tries DESC LIMIT 20""")]
        chapters = [dict(r) for r in con.execute("""
            SELECT q.subject, q.chapter, COUNT(*) tries,
                   ROUND(100.0*SUM(a.is_correct)/COUNT(*),1) pct
            FROM attempts a JOIN questions q ON q.id=a.question_id
            WHERE q.chapter IS NOT NULL
            GROUP BY q.subject, q.chapter ORDER BY pct ASC""")]
    return {"users": users, "licenses": lic, "paid_active": active,
            "dau_24h": dau, "hardest": hardest, "chapters": chapters}


# ---------------------------------------------------------------- CLI

def cli_import(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    init_db()
    with db(write=True) as con:
        for q in data["questions"]:
            con.execute(
                "INSERT INTO questions(qkey,subject,chapter,kind,source,stem,opts,"
                "correct,explain,pool,ord) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(qkey) DO UPDATE SET explain=excluded.explain, "
                "opts=excluded.opts, correct=excluded.correct, stem=excluded.stem, "
                "chapter=excluded.chapter, ord=excluded.ord, active=1",
                (q["qkey"], q["subject"], q["chapter"], q["kind"], q["source"],
                 q["stem"], json.dumps(q["opts"], ensure_ascii=False),
                 q["correct"], q["explain"], q["pool"], q["ord"]))
        for k in ("schedule", "checklistData", "notesData"):
            con.execute(
                "INSERT INTO content_blobs(key,json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET json=excluded.json, "
                "updated_at=excluded.updated_at",
                (k, json.dumps(data[k], ensure_ascii=False), now()))
        n = con.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    print(f"导入完成：questions={n} blobs=3")


def cli_admin(email: str, pw: str | None = None) -> None:
    # 和 /login 用同一套校验，否则会建出一个永远登不进去的管理员
    from pydantic import TypeAdapter
    try:
        email = TypeAdapter(EmailStr).validate_python(email.strip().lower())
    except Exception:
        sys.exit(f"邮箱不合法：{email}（需要真实域名，API 登录用的是同一套校验）")
    if pw is None:
        # 不从命令行收密码——那会留在 shell 历史和 ps 里
        import getpass
        pw = getpass.getpass(f"给 {email} 设置密码（至少 8 位，输入时不显示）: ")
        if pw != getpass.getpass("再输一次确认: "):
            sys.exit("两次输入不一致")
    if len(pw) < 8:
        sys.exit("密码至少 8 位")
    init_db()
    with db(write=True) as con:
        con.execute(
            "INSERT INTO users(email,pw_hash,role,created_at) VALUES(?,?,'admin',?) "
            "ON CONFLICT(email) DO UPDATE SET pw_hash=excluded.pw_hash, role='admin'",
            (email.strip().lower(), hash_pw(pw), now()))
    print(f"管理员就绪：{email}")


def cli_mint(n: int, plan: str, channel: str) -> None:
    init_db()
    with db(write=True) as con:
        batch = "B" + datetime.now(UTC).strftime("%y%m%d%H%M%S")
        out = []
        while len(out) < n:
            c = gen_code()
            try:
                con.execute(
                    "INSERT INTO licenses(code,plan,days,batch_no,channel,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (c, plan, PLANS[plan], batch, channel, now()))
            except sqlite3.IntegrityError:
                continue
            out.append(c)
    print(f"批次 {batch} · {plan} · {PLANS[plan]} 天")
    print("\n".join(out))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit("用法: app.py initdb | import <content.json> | "
                 "admin <email> [password] | mint <n> <plan> <channel>\n"
                 "  admin 不带密码时会交互式提示，不会留在命令历史里")
    if a[0] == "initdb":
        init_db(); print(f"库已就绪：{DB_PATH}")
    elif a[0] == "import":
        cli_import(a[1])
    elif a[0] == "admin":
        cli_admin(a[1], a[2] if len(a) > 2 else None)
    elif a[0] == "mint":
        cli_mint(int(a[1]), a[2] if len(a) > 2 else "season",
                 a[3] if len(a) > 3 else "taobao")
    else:
        sys.exit(f"未知命令 {a[0]}")
