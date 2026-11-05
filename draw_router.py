"""11月风险异常抽查顺序 · 会议大屏与手机参与端实时同步后端"""
import json
import random
import threading
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

router = APIRouter()

DATA_DIR = Path("/data")
STATE_FILE = DATA_DIR / "risk_draw_nov_state.json"
STATIC_DIR = Path(__file__).parent / "static" / "risk_draw"

NAMES = ["郫都", "龙泉", "宜宾", "双流"]
SPIN_MS = 4600

_lock = threading.Lock()


def _default_state():
    return {
        "claims": [],
        "spin": None,
        "version": 2,
        "updated": int(time.time() * 1000),
    }


def _load_state():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("claims"), list):
                return data
        except Exception:
            pass
    st = _default_state()
    _save_state(st)
    return st


def _save_state(st):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st["updated"] = int(time.time() * 1000)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _public_state(st):
    now_ms = int(time.time() * 1000)
    spin = st.get("spin")
    pub_spin = None
    if spin and isinstance(spin, dict):
        srv_ts = spin.get("server_ts", spin.get("time", now_ms))
        age_ms = max(0, now_ms - int(srv_ts))
        pub_spin = {
            "id": spin["id"],
            "branch": spin["branch"],
            "rank": spin["rank"],
            "age_ms": age_ms,
        }
    return {
        "claims": st.get("claims", []),
        "spin": pub_spin,
        "version": st.get("version", 2),
        "updated": st.get("updated", now_ms),
    }


class ClaimReq(BaseModel):
    branch: str
    id: str = ""


class DrawReq(BaseModel):
    branch: str
    rank: int | None = None
    spin_id: str = ""


@router.get("/draw")
def draw_root():
    return RedirectResponse(url="/draw/screen.html")


@router.get("/draw/screen.html")
@router.get("/screen.html")
def serve_screen():
    return FileResponse(
        STATIC_DIR / "screen.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/draw/participant.html")
@router.get("/participant.html")
def serve_participant():
    return FileResponse(
        STATIC_DIR / "participant.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/draw/qr.png")
def serve_qr():
    return FileResponse(
        STATIC_DIR / "qr_participant.png",
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/draw/api/state")
def api_get_state():
    with _lock:
        st = _load_state()
        return _public_state(st)


@router.post("/draw/api/claim")
def api_claim(req: ClaimReq):
    branch = (req.branch or "").strip()
    with _lock:
        st = _load_state()
        if branch not in NAMES:
            return {"ok": False, "error": "请选择所属分公司", "state": _public_state(st)}
        existing = next((x for x in st["claims"] if x["branch"] == branch), None)
        if existing:
            return {"ok": False, "error": "该分公司已经认领", "state": _public_state(st)}
        if len(st["claims"]) >= 4:
            return {"ok": False, "error": "四家分公司均已完成认领", "state": _public_state(st)}
        now_ms = int(time.time() * 1000)
        uid = req.id or f"{now_ms:x}-{random.getrandbits(24):06x}"
        item = {
            "id": uid,
            "branch": branch,
            "time": now_ms,
            "seat": len(st["claims"]) + 1,
            "rank": None,
        }
        st["claims"].append(item)
        _save_state(st)
        return {"ok": True, "item": item, "state": _public_state(st)}


@router.post("/draw/api/draw")
def api_draw(req: DrawReq):
    branch = (req.branch or "").strip()
    with _lock:
        st = _load_state()
        now_ms = int(time.time() * 1000)
        spin = st.get("spin")
        if spin and (now_ms - int(spin.get("server_ts", 0)) < SPIN_MS):
            return {"ok": False, "error": "上一轮正在揭晓，请稍候", "state": _public_state(st)}
        me = next((x for x in st["claims"] if x["branch"] == branch), None)
        if not me:
            return {"ok": False, "error": "请先认领抽签席位", "state": _public_state(st)}
        if me.get("rank"):
            return {"ok": False, "error": "该分公司已经完成抽签", "state": _public_state(st)}
        nxt = next((x for x in st["claims"] if not x.get("rank")), None)
        if not nxt or nxt["branch"] != branch:
            return {"ok": False, "error": "请按照认领先后顺序依次抽签", "state": _public_state(st)}
        taken_ranks = {x["rank"] for x in st["claims"] if x.get("rank")}
        available = [r for r in (1, 2, 3, 4) if r not in taken_ranks]
        if not available:
            return {"ok": False, "error": "本场抽签已经结束", "state": _public_state(st)}
        if req.rank in available:
            rank = req.rank
        else:
            rank = random.SystemRandom().choice(available)
        me["rank"] = rank
        spin_id = req.spin_id or f"{now_ms:x}-{random.getrandbits(24):06x}"
        st["spin"] = {
            "id": spin_id,
            "branch": branch,
            "rank": rank,
            "server_ts": now_ms,
        }
        _save_state(st)
        return {"ok": True, "rank": rank, "state": _public_state(st)}


@router.post("/draw/api/reset")
def api_reset():
    with _lock:
        st = _default_state()
        _save_state(st)
        return {"ok": True, "state": _public_state(st)}
