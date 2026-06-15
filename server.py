"""
주루마블 실시간 멀티플레이 서버 (FastAPI + WebSocket).

서버가 게임 로직의 단일 진실원(authoritative)입니다.
클라이언트는 같은 오리진으로 ws/wss 에 연결하여 액션을 보내고,
서버가 검증·처리한 뒤 방 전체에 상태를 브로드캐스트합니다.

실행:
    uvicorn server:app --host 0.0.0.0 --port 8000
    (이 파일이 있는 디렉터리에서)
"""
from __future__ import annotations

import asyncio
import os
import random
import string
import time
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

BASE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="주루마블 실시간 서버")

# ──────────────────────────────────────────────────────────────────────────
# 게임 콘텐츠 (서버 권위 — 미션 텍스트의 단일 진실원)
# ──────────────────────────────────────────────────────────────────────────
GOLD = "#ffd43b"
CAT = {
    "talk":    {"emoji": "💬", "label": "토크", "color": "#845ef7"},
    "perform": {"emoji": "🎭", "label": "장기", "color": "#f06595"},
    "game":    {"emoji": "🎲", "label": "대결", "color": "#4dabf7"},
    "penalty": {"emoji": "🔥", "label": "벌칙", "color": "#ff922b"},
    "chance":  {"emoji": "🍀", "label": "찬스", "color": "#51cf66"},
}

TILES = [
    {"type": "corner", "key": "start", "emoji": "🏁", "label": "출발"},
    {"type": "penalty", "mild": "가위바위보 한 판! 옆 사람에게 지면 꿀밤 1대 받기.", "spicy": "원샷! 잔 비우기. 잔이 비어 있으면 옆 사람이 채워준 한 잔."},
    {"type": "talk",    "mild": "오늘 가장 고마웠던 사람에게 한마디 하기.", "spicy": "진실게임 1문제 답하기. 못 하면 한 잔."},
    {"type": "perform", "mild": "좋아하는 노래 한 소절 부르기.", "spicy": "노래 한 소절! 음 이탈하면 벌주 한 잔."},
    {"type": "chance"},
    {"type": "game",    "mild": "끝말잇기! 5초 안에 한 단어 잇기.", "spicy": "전원 가위바위보, 최종 패자 한 잔."},
    {"type": "corner", "key": "island", "emoji": "🏝️", "label": "무인도"},
    {"type": "talk",    "mild": "지금 가장 가고 싶은 여행지 말하기.", "spicy": "가장 가고 싶은 여행지 + 함께 갈 사람 지목, 못 정하면 한 잔."},
    {"type": "penalty", "mild": "다음 차례까지 사투리로만 말하기. 실수하면 꿀밤 1대.", "spicy": "폭탄주 직접 제조해서 본인이 원샷."},
    {"type": "perform", "mild": "성대모사 하나 선보이기 (연예인·동물 등).", "spicy": "성대모사 도전, 어색하면 한 잔."},
    {"type": "game",    "mild": "초성 게임 'ㅅㄱ'! 3초 안에 단어 말하기.", "spicy": "병뚜껑 튕기기 도전, 실패하면 한 모금."},
    {"type": "chance"},
    {"type": "corner", "key": "rest", "emoji": "🛋️", "label": "휴게소"},
    {"type": "perform", "mild": "몸으로 단어 설명하고 옆 사람이 맞히기.", "spicy": "제스처 게임! 못 맞히면 출제자·정답자 같이 한 모금."},
    {"type": "game",    "mild": "눈치게임! 1부터 순서 없이 외치기.", "spicy": "눈치게임, 마지막까지 못 외친 사람 한 잔."},
    {"type": "talk",    "mild": "왼쪽 사람 칭찬 3가지 말하기.", "spicy": "이 중 술 제일 약한 사람 지목 → 그 사람과 짠 후 한 모금."},
    {"type": "penalty", "mild": "10초 안 웃기 챌린지! 다들 웃겨도 버티면 통과.", "spicy": "시계 방향으로 전원 한 모금씩."},
    {"type": "chance"},
    {"type": "corner", "key": "roulette", "emoji": "🎡", "label": "룰렛존"},
    {"type": "game",    "mild": "3·6·9 게임 한 바퀴 돌기.", "spicy": "3·6·9 게임! 틀린 사람 벌주 한 잔."},
    {"type": "perform", "mild": "엉덩이로 내 이름 쓰기 ✍️", "spicy": "옆 사람과 장기자랑 대결, 진 쪽이 한 잔."},
    {"type": "talk",    "mild": "로또 1등 당첨되면 가장 먼저 할 일 말하기.", "spicy": "로또 1등 당첨되면? 5초 안에 답 못 하면 한 잔."},
    {"type": "penalty", "mild": "다음 내 차례까지 말끝마다 \"~다람쥐\" 붙이기! 실수하면 꿀밤 1대.", "spicy": "마실 사람 1명 지목해서 같이 한 잔 (흑기사 가능)."},
    {"type": "chance"},
]

CHANCE = [
    {"mild": "럭키! 다음 차례 한 번 쉬어도 OK (패스권 1회 획득).", "spicy": "행운의 흑기사권 1회 획득! 마실 차례를 남에게 넘길 수 있어요."},
    {"mild": "전원 하이파이브 한 바퀴! 👋", "spicy": "다 같이 건배 후 한 모금 🍻"},
    {"mild": "왼쪽 사람과 자리 바꾸기.", "spicy": "왼쪽 사람과 잔 바꿔서 한 잔."},
    {"mild": "주사위 한 번 더 굴리기! 🎲", "spicy": "주사위 한 번 더! 단, 더블 나오면 한 잔.", "again": True},
    {"mild": "지목한 사람과 묵찌빠, 진 사람 꿀밤 1대.", "spicy": "지목한 사람과 묵찌빠, 진 사람 한 잔."},
    {"mild": "옆 사람과 3초 눈싸움! 먼저 웃으면 꿀밤 1대.", "spicy": "옆 사람과 눈싸움, 먼저 웃은 사람 한 잔."},
    {"mild": "30초 침묵! 먼저 말하면 꿀밤 1대.", "spicy": "30초 침묵! 먼저 말한 사람 벌주."},
    {"mild": "전원 기립, 가장 늦게 일어난 사람 애교 한 번.", "spicy": "전원 기립! 제일 늦게 일어난 사람 한 잔."},
    {"mild": "다 같이 셀카 한 장 찍기! 📸", "spicy": "다 같이 짠하고 한 모금 🍻 인증샷은 덤 📸"},
    {"mild": "옆 사람과 동시에 점프 5번! 박자 틀리면 꿀밤.", "spicy": "옆 사람과 동시에 한 모금, 박자 틀리면 한 잔 더."},
]

CORNER_TEXT = {
    "start":    {"mild": "출발 칸 정착 🏁 패스권 1회 획득! (미션 한 번 면제)", "spicy": "출발 칸 정착 🏁 패스권 1회! (마실 차례 1번 면제)", "sub": "한 바퀴 돌아 정확히 도착했네요!"},
    "island":   {"mild": "무인도 표류 🏝️ 다음 차례는 쉬어요.", "spicy": "무인도 표류 🏝️ 한 잔 마시고 다음 차례 쉬기.", "sub": "다음 내 차례에 \"탭\"으로 넘기면 됩니다."},
    "rest":     {"mild": "휴게소 도착 😌 안주 타임! 이번엔 미션 없이 편히 쉬어요.", "spicy": "휴게소 😌 안주 챙기는 시간! 벌주·미션 없음.", "sub": "안전지대 — 편하게 쉬어가세요."},
    "roulette": {"mild": "룰렛 당첨 🎡 찬스 카드 1장 뽑기!", "spicy": "룰렛 당첨 🎡 찬스 카드 1장 뽑기!", "sub": ""},
}
PASS_START = {"mild": "한 바퀴 완주 🎉 다 같이 박수 한 번 👏", "spicy": "한 바퀴 완주 🍻 다 같이 짠, 한 모금!"}

EMOJIS = ["🦊", "🐰", "🐻", "🐼", "🐯", "🐸", "🐵", "🐶"]
COLORS = ["#ff6b6b", "#4dabf7", "#51cf66", "#ffd43b", "#cc5de8", "#ff922b"]
MAX_PLAYERS = 6
ROOM_TTL = 60 * 60  # 비활성 방 정리(초)

# ──────────────────────────────────────────────────────────────────────────
# 상태 저장소
# ──────────────────────────────────────────────────────────────────────────
rooms: Dict[str, dict] = {}          # code -> room dict (직렬화 대상)
conns: Dict[str, Dict[str, WebSocket]] = {}  # code -> {playerId: websocket}
LOCK = asyncio.Lock()                # 전역 락(저트래픽 파티게임 — 단순/안전 우선)


def gen_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0/O/1/I 제외
    while True:
        code = "".join(random.choice(alphabet) for _ in range(4))
        if code not in rooms:
            return code


def gen_pid() -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))


def free_slot(room: dict) -> int:
    used = {p["slot"] for p in room["players"]}
    for i in range(MAX_PLAYERS):
        if i not in used:
            return i
    return len(room["players"]) % MAX_PLAYERS


def mk_mission(badge, emoji, text, sub, color, for_pid):
    return {"badge": badge, "emoji": emoji, "text": text, "sub": sub, "color": color, "forPlayerId": for_pid}


def draw_chance(room: dict) -> dict:
    card = random.choice(CHANCE)
    if card.get("again"):
        room["extraRoll"] = True
        room["extraRollMsg"] = "🍀 행운! 주사위 한 번 더 굴려요!"
    return card


def set_event(room: dict, kind: str, text: str):
    room["eventSeq"] += 1
    room["lastEvent"] = {"id": room["eventSeq"], "kind": kind, "text": text}


def advance_turn(room: dict):
    """연결된 다음 플레이어로 턴을 넘긴다(끊긴 사람은 건너뜀)."""
    n = len(room["players"])
    if n == 0:
        return
    i = room["currentIdx"]
    for _ in range(n):
        i = (i + 1) % n
        if room["players"][i]["connected"]:
            room["currentIdx"] = i
            return
    room["currentIdx"] = (room["currentIdx"] + 1) % n  # 전원 끊김 폴백


def cur_player(room: dict):
    if not room["players"]:
        return None
    return room["players"][room["currentIdx"] % len(room["players"])]


def find_player(room: dict, pid: str):
    for p in room["players"]:
        if p["id"] == pid:
            return p
    return None


def resolve_landing(room: dict, p: dict) -> dict:
    """말이 멈춘 칸의 미션을 결정하고(부수효과 포함) 미션 dict를 반환."""
    idx = p["pos"]
    mode = room["mode"]
    if idx == 0:
        c = CORNER_TEXT["start"]
        return mk_mission("🏁 출발", "🏁", c[mode], c["sub"], GOLD, p["id"])
    t = TILES[idx]
    if t["type"] == "corner":
        c = CORNER_TEXT[t["key"]]
        if t["key"] == "island":
            p["skip"] = True
            room["extraRoll"] = False
            return mk_mission("🏝️ 무인도", "🏝️", c[mode], c["sub"], GOLD, p["id"])
        if t["key"] == "rest":
            return mk_mission("🛋️ 휴게소", "🛋️", c[mode], c["sub"], GOLD, p["id"])
        if t["key"] == "roulette":
            card = draw_chance(room)
            return mk_mission("🎡 룰렛존 → 🍀 찬스", "🍀", card[mode], "룰렛이 뽑은 찬스 카드!", CAT["chance"]["color"], p["id"])
    if t["type"] == "chance":
        card = draw_chance(room)
        return mk_mission("🍀 찬스 카드", "🍀", card[mode], "", CAT["chance"]["color"], p["id"])
    cc = CAT[t["type"]]
    return mk_mission(f"{cc['emoji']} {cc['label']}", cc["emoji"], t[mode], "", cc["color"], p["id"])


def serialize(room: dict) -> dict:
    return {
        "code": room["code"],
        "mode": room["mode"],
        "phase": room["phase"],
        "hostId": room["hostId"],
        "currentIdx": room["currentIdx"],
        "dice": room["dice"],
        "mission": room["mission"],
        "rollSeq": room["rollSeq"],
        "lastRoll": room["lastRoll"],
        "lastEvent": room["lastEvent"],
        "players": [
            {k: pl[k] for k in ("id", "name", "emoji", "color", "pos", "laps", "skip", "connected")}
            for pl in room["players"]
        ],
    }


async def send(ws: WebSocket, obj: dict) -> bool:
    try:
        await ws.send_json(obj)
        return True
    except Exception:
        return False


async def broadcast(room: dict):
    room["lastActive"] = time.time()
    payload = {"type": "state", "state": serialize(room)}
    dead = []
    for pid, ws in list(conns.get(room["code"], {}).items()):
        ok = await send(ws, payload)
        if not ok:
            dead.append(pid)
    for pid in dead:
        conns.get(room["code"], {}).pop(pid, None)
        pl = find_player(room, pid)
        if pl:
            pl["connected"] = False


# ──────────────────────────────────────────────────────────────────────────
# 액션 핸들러
# ──────────────────────────────────────────────────────────────────────────
def new_room(code: str, mode: str) -> dict:
    return {
        "code": code, "mode": mode if mode in ("mild", "spicy") else "mild",
        "phase": "lobby", "hostId": None, "currentIdx": 0,
        "dice": None, "mission": None, "rollSeq": 0, "lastRoll": None,
        "extraRoll": False, "extraRollMsg": "",
        "eventSeq": 0, "lastEvent": None,
        "players": [], "lastActive": time.time(),
    }


def add_player(room: dict, name: str) -> dict:
    slot = free_slot(room)
    pid = gen_pid()
    p = {
        "id": pid, "name": (name or "").strip()[:8] or f"플레이어 {len(room['players'])+1}",
        "emoji": EMOJIS[slot % len(EMOJIS)], "color": COLORS[slot % len(COLORS)],
        "slot": slot, "pos": 0, "laps": 0, "skip": False, "connected": True,
    }
    room["players"].append(p)
    return p


async def handle_create(ws, msg):
    code = gen_code()
    room = new_room(code, msg.get("mode", "mild"))
    rooms[code] = room
    conns[code] = {}
    p = add_player(room, msg.get("name", ""))
    room["hostId"] = p["id"]
    conns[code][p["id"]] = ws
    await send(ws, {"type": "joined", "room": code, "playerId": p["id"]})
    await broadcast(room)
    return code, p["id"]


async def handle_join(ws, msg):
    code = (msg.get("room") or "").strip().upper()
    room = rooms.get(code)
    if not room:
        await send(ws, {"type": "error", "message": "방을 찾을 수 없어요. 코드를 확인해 주세요."})
        return None, None
    if room["phase"] != "lobby":
        await send(ws, {"type": "error", "message": "이미 시작된 게임이에요."})
        return None, None
    if len(room["players"]) >= MAX_PLAYERS:
        await send(ws, {"type": "error", "message": f"방이 꽉 찼어요 (최대 {MAX_PLAYERS}명)."})
        return None, None
    p = add_player(room, msg.get("name", ""))
    conns[code][p["id"]] = ws
    await send(ws, {"type": "joined", "room": code, "playerId": p["id"]})
    await broadcast(room)
    return code, p["id"]


async def handle_rejoin(ws, msg):
    code = (msg.get("room") or "").strip().upper()
    pid = msg.get("playerId")
    room = rooms.get(code)
    if not room or not find_player(room, pid):
        await send(ws, {"type": "expired"})
        return None, None
    p = find_player(room, pid)
    p["connected"] = True
    conns.setdefault(code, {})[pid] = ws
    await send(ws, {"type": "joined", "room": code, "playerId": pid})
    await broadcast(room)
    return code, pid


async def handle_set_mode(room, pid, msg):
    if pid != room["hostId"]:
        return
    mode = msg.get("mode")
    if mode in ("mild", "spicy"):
        room["mode"] = mode
        set_event(room, "mode", "😇 순한맛으로 전환" if mode == "mild" else "🔥 매운맛으로 전환")
        await broadcast(room)


async def handle_start(room, pid, msg):
    if pid != room["hostId"] or room["phase"] != "lobby":
        return
    if len(room["players"]) < 2:
        ws = conns.get(room["code"], {}).get(pid)
        if ws:
            await send(ws, {"type": "error", "message": "2명 이상이어야 시작할 수 있어요."})
        return
    room["phase"] = "playing"
    room["currentIdx"] = 0
    for p in room["players"]:
        p["pos"] = 0; p["laps"] = 0; p["skip"] = False
    room["mission"] = None; room["dice"] = None; room["extraRoll"] = False
    set_event(room, "start", f"🎲 게임 시작! {room['players'][0]['name']}님부터")
    await broadcast(room)


async def handle_roll(room, pid, msg):
    if room["phase"] != "playing" or room["mission"] is not None:
        return
    p = cur_player(room)
    if not p or p["id"] != pid:
        return
    # 무인도에서 쉬는 차례
    if p["skip"]:
        p["skip"] = False
        set_event(room, "skip", f"🏝️ {p['name']}님 무인도에서 한 턴 쉼!")
        advance_turn(room)
        await broadcast(room)
        return
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    dbl = d1 == d2
    room["extraRoll"] = dbl
    room["extraRollMsg"] = "🎲 더블! 한 번 더 굴려요!" if dbl else ""
    steps = d1 + d2
    prev = p["pos"]
    passed = False
    for i in range(steps):
        p["pos"] = (p["pos"] + 1) % 24
        if p["pos"] == 0:
            p["laps"] += 1
            if i < steps - 1:
                passed = True
    room["dice"] = [d1, d2]
    room["rollSeq"] += 1
    room["lastRoll"] = {"playerId": p["id"], "from": prev, "steps": steps, "d1": d1, "d2": d2, "double": dbl}
    if passed:
        set_event(room, "pass", PASS_START[room["mode"]])
    room["mission"] = resolve_landing(room, p)
    await broadcast(room)


async def handle_confirm(room, pid, msg):
    m = room["mission"]
    if not m or m.get("forPlayerId") != pid:
        return
    room["mission"] = None
    if room["extraRoll"]:
        room["extraRoll"] = False
        set_event(room, "extra", room["extraRollMsg"] or "🎲 한 번 더 굴려요!")
    else:
        advance_turn(room)
    await broadcast(room)


async def handle_restart(room, pid, msg):
    if pid != room["hostId"]:
        return
    for p in room["players"]:
        p["pos"] = 0; p["laps"] = 0; p["skip"] = False
    room["currentIdx"] = 0; room["mission"] = None; room["dice"] = None
    room["extraRoll"] = False
    set_event(room, "restart", "↺ 처음부터 다시!")
    await broadcast(room)


async def handle_disconnect(code, pid):
    room = rooms.get(code)
    if not room:
        return
    conns.get(code, {}).pop(pid, None)
    p = find_player(room, pid)
    if not p:
        return
    if room["phase"] == "lobby":
        # 로비에서는 나가면 제거
        room["players"] = [x for x in room["players"] if x["id"] != pid]
        if room["hostId"] == pid:
            room["hostId"] = room["players"][0]["id"] if room["players"] else None
        if not room["players"]:
            rooms.pop(code, None); conns.pop(code, None)
            return
    else:
        p["connected"] = False
        # 미션 수행 중 행위자가 끊기면 자동 확인 처리
        if room["mission"] and room["mission"].get("forPlayerId") == pid:
            room["mission"] = None
            if room["extraRoll"]:
                room["extraRoll"] = False
            advance_turn(room)
        elif cur_player(room) and cur_player(room)["id"] == pid:
            advance_turn(room)
        if room["hostId"] == pid:
            alive = [x for x in room["players"] if x["connected"]]
            room["hostId"] = alive[0]["id"] if alive else room["hostId"]
    await broadcast(room)


HANDLERS = {
    "setMode": handle_set_mode,
    "start": handle_start,
    "roll": handle_roll,
    "confirm": handle_confirm,
    "restart": handle_restart,
}


# ──────────────────────────────────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True, "rooms": len(rooms)})


@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE, "client.html"))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    code = pid = None
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            async with LOCK:
                if mtype == "create":
                    code, pid = await handle_create(ws, msg)
                elif mtype == "join":
                    code, pid = await handle_join(ws, msg)
                elif mtype == "rejoin":
                    code, pid = await handle_rejoin(ws, msg)
                elif mtype == "ping":
                    await send(ws, {"type": "pong"})
                elif mtype == "leave":
                    if code and pid:
                        await handle_disconnect(code, pid)
                        code = pid = None
                elif mtype in HANDLERS and code and pid:
                    room = rooms.get(code)
                    if room:
                        await HANDLERS[mtype](room, pid, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if code and pid:
            async with LOCK:
                await handle_disconnect(code, pid)


@app.on_event("startup")
async def _start_sweeper():
    async def sweep():
        while True:
            await asyncio.sleep(120)
            now = time.time()
            async with LOCK:
                for code in [c for c, r in rooms.items() if now - r["lastActive"] > ROOM_TTL]:
                    rooms.pop(code, None)
                    conns.pop(code, None)
    asyncio.create_task(sweep())
