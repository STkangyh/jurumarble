"""
주루마블 실시간 서버 (FastAPI + WebSocket) — 관리자 진행 + 관람객 모델.

게임 규칙(이번 버전):
- 관리자(방 생성자) 1명이 팀을 구성하고 모든 팀의 주사위를 대신 굴린다.
- 관람객은 보기 전용 + 이모티콘.
- 주사위는 1개, 눈은 1·2·3만. 한 바퀴(출발칸 복귀)를 먼저 완주한 팀이 우승.
- 완주를 어렵게 하는 장치들:
  · 🕳️ 함정칸: 뒤로 N칸 밀림
  · 🌀 소용돌이칸: 출발점으로 원위치
  · 🏝️ 무인도: 다음 차례 쉬기
  · 🎯 정확히 골인: 출발칸에 딱 맞게 도착해야 우승(넘으면 튕겨나감)
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
# 게임 콘텐츠
# ──────────────────────────────────────────────────────────────────────────
GOLD = "#ffd43b"
CAT = {
    "talk":    {"emoji": "💬", "label": "토크", "color": "#845ef7"},
    "perform": {"emoji": "🎭", "label": "장기", "color": "#f06595"},
    "game":    {"emoji": "🎲", "label": "대결", "color": "#4dabf7"},
    "penalty": {"emoji": "🔥", "label": "벌칙", "color": "#ff922b"},
    "chance":  {"emoji": "🍀", "label": "찬스", "color": "#51cf66"},
}
TRAP_COLOR = "#e8590c"
RESET_COLOR = "#7048e8"
BOMB_COLOR = "#c92a2a"
INGREDIENT_COLOR = "#9c36b5"
QUIZ_COLOR = "#1098ad"
CHOSUNG_COLOR = "#f06595"
CHOSUNG = [  # 초성 게임 (cho: 초성, ex: 예시 답)
    {"cho": "ㅅㄱ", "ex": "사과·시계·수건"},
    {"cho": "ㄱㅈ", "ex": "과자·감자·가족"},
    {"cho": "ㅁㄹ", "ex": "머리·마늘·마루"},
    {"cho": "ㅂㅅ", "ex": "버스·박수·방석"},
    {"cho": "ㅎㄱ", "ex": "학교·한국·향기"},
    {"cho": "ㄷㄹ", "ex": "다리·도로·달력"},
    {"cho": "ㅈㄱ", "ex": "장기·자기·종교"},
    {"cho": "ㅊㄱ", "ex": "친구·축구·창고"},
    {"cho": "ㅇㅈ", "ex": "의자·우주·양주"},
    {"cho": "ㅋㅍ", "ex": "커피·카페"},
    {"cho": "ㄴㅁ", "ex": "나무·남매"},
    {"cho": "ㅂㄹ", "ex": "바람·보리·불량"},
]
INGREDIENTS = ["소주", "맥주", "양주", "콜라", "사이다", "매실주", "막걸리"]  # 폭탄주 재료(랜덤)
QUIZ = [  # 상식 퀴즈 (q: 문제, a: 정답)
    {"q": "세계에서 가장 넓은 대양은?", "a": "태평양"},
    {"q": "무지개는 모두 몇 가지 색깔?", "a": "7가지"},
    {"q": "태양계에서 가장 큰 행성은?", "a": "목성"},
    {"q": "한글을 만든 조선의 왕은?", "a": "세종대왕"},
    {"q": "빛의 삼원색(RGB)은?", "a": "빨강·초록·파랑"},
    {"q": "펭귄이 사는 곳은 남극일까 북극일까?", "a": "남극"},
    {"q": "거미의 다리는 모두 몇 개?", "a": "8개"},
    {"q": "올림픽은 몇 년마다 열릴까?", "a": "4년"},
    {"q": "에펠탑이 있는 나라는?", "a": "프랑스"},
    {"q": "물의 화학식은?", "a": "H₂O"},
    {"q": "대한민국에서 가장 높은 산은?", "a": "한라산"},
    {"q": "세계에서 가장 긴 강은?", "a": "나일강"},
    {"q": "우리 몸에서 피를 펌프질하는 기관은?", "a": "심장"},
    {"q": "1년은 모두 몇 개월?", "a": "12개월"},
]

# 24칸. trap=뒤로 N칸, reset=출발 복귀.
TILES = [
    {"type": "corner", "key": "start", "emoji": "🏁", "label": "출발"},
    {"type": "penalty", "text": "걸린 팀만 원샷! 🍺"},
    {"type": "ingredient"},
    {"type": "trap", "back": 2},
    {"type": "chance"},
    {"type": "game",    "text": "각 팀에서 술 제일 센 사람 나와서 가위바위보! 진 팀 한 잔 🍺"},
    {"type": "corner", "key": "island", "emoji": "🏝️", "label": "무인도"},
    {"type": "ingredient"},
    {"type": "penalty", "text": "의리 게임! 우리 팀끼리 소주 한 병을 나눠 마시기 🍶🤝"},
    {"type": "trap", "back": 3},
    {"type": "quiz"},
    {"type": "chance"},
    {"type": "corner", "key": "rest", "emoji": "🛋️", "label": "휴게소"},
    {"type": "chosung"},
    {"type": "game",    "text": "눈치게임, 마지막까지 못 외친 사람 한 잔."},
    {"type": "reset"},
    {"type": "penalty", "text": "시계 방향으로 전원 한 모금씩."},
    {"type": "chance"},
    {"type": "corner", "key": "roulette", "emoji": "🎡", "label": "룰렛존"},
    {"type": "game",    "text": "3·6·9 게임! 틀린 사람 벌주 한 잔."},
    {"type": "perform", "text": "옆 팀과 장기자랑 대결, 진 쪽이 한 잔."},
    {"type": "bomb"},
    {"type": "trap", "back": 2},
    {"type": "chance"},
]

CHANCE = [
    {"text": "행운의 흑기사권 1회 획득! 마실 차례를 남에게 넘길 수 있어요."},
    {"text": "다 같이 건배 후 한 모금 🍻"},
    {"text": "양옆 팀이 한 잔씩! 🍺"},
    {"text": "주사위 한 번 더! 🎲", "again": True},
    {"text": "지목한 팀과 묵찌빠, 진 팀 한 잔."},
    {"text": "옆 팀과 눈싸움, 먼저 웃은 팀 한 잔."},
    {"text": "진행자(관리자)가 한 잔! 🍻"},
    {"text": "다 같이 짠하고 한 모금 🍻 인증샷은 덤 📸"},
]

CORNER_TEXT = {
    "island":   {"text": "무인도 표류 🏝️ 다음 차례는 쉬어요.", "sub": "이 팀은 다음 차례에 자동으로 한 번 쉽니다."},
    "rest":     {"text": "휴게소 😌 안주 챙기는 시간! 벌주·미션 없음.", "sub": "안전지대 — 편하게 쉬어가세요."},
    "roulette": {"text": "룰렛 당첨 🎡 찬스 카드 1장 뽑기!", "sub": ""},
}

TEAM_EMOJIS = ["🦊", "🐰", "🐻", "🐼", "🐯", "🐸", "🐵", "🐶"]
TEAM_COLORS = ["#ff6b6b", "#4dabf7", "#51cf66", "#ffd43b", "#cc5de8", "#ff922b"]
REACTIONS = ["😂", "❤️", "🔥", "👏", "🎉", "😮", "👍", "🍻", "💯", "😭"]
MAX_TEAMS = 6
MAX_SPECTATORS = 80          # 방당 관람 인원 상한(이모티콘 팬아웃 폭주 방지)
EMOJI_THROTTLE = 0.2         # 참가자별 최소 간격(초)
ROOM_EMOJI_MIN = 0.04        # 방 전체 이모티콘 최소 간격(초) ≈ 25/s
ROOM_TTL = 60 * 60
TILE_COUNT = 24

# ──────────────────────────────────────────────────────────────────────────
# 상태 저장소
# ──────────────────────────────────────────────────────────────────────────
rooms: Dict[str, dict] = {}
conns: Dict[str, Dict[str, WebSocket]] = {}
LOCK = asyncio.Lock()


def gen_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choice(alphabet) for _ in range(4))
        if code not in rooms:
            return code


def gen_id() -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))


# ── 방/참가자/팀 ──────────────────────────────────────────────────────────
def new_room(code: str) -> dict:
    return {
        "code": code, "phase": "lobby", "adminId": None, "currentIdx": 0,
        "teams": [], "participants": [], "winnerId": None, "bomb": [],
        "die": None, "mission": None, "rollSeq": 0, "lastRoll": None,
        "extraRoll": False, "extraRollMsg": "",
        "eventSeq": 0, "lastEvent": None, "lastActive": time.time(),
    }


def add_participant(room: dict, name: str, role: str) -> dict:
    p = {"id": gen_id(), "name": (name or "").strip()[:10] or ("관리자" if role == "admin" else "관람객"),
         "role": role, "connected": True, "emojiTs": 0.0}
    room["participants"].append(p)
    return p


def find_participant(room: dict, pid: str):
    return next((p for p in room["participants"] if p["id"] == pid), None)


def spectator_count(room: dict) -> int:
    return sum(1 for p in room["participants"] if p["role"] == "spectator" and p["connected"])


def admin_name(room: dict) -> str:
    a = find_participant(room, room["adminId"]) if room["adminId"] else None
    return a["name"] if a else "관리자"


def free_team_slot(room: dict) -> int:
    used = {t["slot"] for t in room["teams"]}
    for i in range(MAX_TEAMS):
        if i not in used:
            return i
    return len(room["teams"]) % MAX_TEAMS


def add_team(room: dict, name: str) -> dict:
    slot = free_team_slot(room)
    t = {"id": gen_id(), "name": (name or "").strip()[:8] or f"{slot + 1}팀",
         "emoji": TEAM_EMOJIS[slot % len(TEAM_EMOJIS)], "color": TEAM_COLORS[slot % len(TEAM_COLORS)],
         "slot": slot, "pos": 0, "skip": False}
    room["teams"].append(t)
    return t


def cur_team(room: dict):
    return room["teams"][room["currentIdx"] % len(room["teams"])] if room["teams"] else None


def advance_turn(room: dict):
    n = len(room["teams"])
    if n:
        room["currentIdx"] = (room["currentIdx"] + 1) % n


def find_team(room: dict, tid: str):
    return next((t for t in room["teams"] if t["id"] == tid), None)


# ── 게임 로직 ─────────────────────────────────────────────────────────────
def mk_mission(badge, emoji, text, sub, color, team_id):
    return {"badge": badge, "emoji": emoji, "text": text, "sub": sub, "color": color, "teamId": team_id}


def draw_chance(room: dict) -> dict:
    card = random.choice(CHANCE)
    if card.get("again"):
        room["extraRoll"] = True
        room["extraRollMsg"] = "🍀 행운! 주사위 한 번 더 굴려요!"
    return card


def set_event(room: dict, kind: str, text: str):
    room["eventSeq"] += 1
    room["lastEvent"] = {"id": room["eventSeq"], "kind": kind, "text": text}


def _land_special(s: int, path: list):
    """도착 칸 s 가 함정/소용돌이면 적용(path 연장)하고 (최종칸, effect, meta) 반환."""
    tile = TILES[s]
    if tile["type"] == "trap":
        back = tile["back"]
        for _ in range(back):
            s = (s - 1) % TILE_COUNT
            path.append(s)
        return s, "trap", back
    if tile["type"] == "reset":
        while s != 0:
            s = (s - 1) % TILE_COUNT
            path.append(s)
        return 0, "reset", 0
    return s, None, 0


def compute_move(team: dict, die: int):
    """team['pos'] 를 갱신하고 (애니메이션 경로 path, effect, meta) 반환.
    effect: None(일반) | 'win' | 'bounce' | 'trap' | 'reset'."""
    pos = team["pos"]
    path = []
    target = pos + die
    # 출발칸(0) 정확히 도착해야 골인 — 넘으면 튕겨나감
    if pos != 0 and target >= TILE_COUNT:
        s = pos
        while s != 0:
            s = (s + 1) % TILE_COUNT
            path.append(s)              # ...→0 까지 전진
        if target == TILE_COUNT:
            team["pos"] = 0
            return path, "win", 0
        overshoot = target - TILE_COUNT
        s = 0
        for _ in range(overshoot):
            s = (s - 1) % TILE_COUNT
            path.append(s)              # 0 에서 overshoot 만큼 뒤로 튕김
        # 튕겨 멈춘 칸도 함정/소용돌이면 일반 도착과 동일하게 적용
        fin, eff, meta = _land_special(s, path)
        team["pos"] = fin
        return path, (eff or "bounce"), (meta if eff else overshoot)
    # 일반 전진
    s = pos
    for _ in range(die):
        s = (s + 1) % TILE_COUNT
        path.append(s)
    fin, eff, meta = _land_special(s, path)
    team["pos"] = fin
    return path, eff, meta


def resolve_tile(room: dict, team: dict) -> dict:
    """일반 칸 도착 시 미션(또는 특수칸 안내) 반환."""
    idx = team["pos"]
    tid = team["id"]
    t = TILES[idx]
    if t["type"] == "corner":
        c = CORNER_TEXT[t["key"]]
        if t["key"] == "island":
            team["skip"] = True
            return mk_mission("🏝️ 무인도", "🏝️", c["text"], c["sub"], GOLD, tid)
        if t["key"] == "rest":
            return mk_mission("🛋️ 휴게소", "🛋️", c["text"], c["sub"], GOLD, tid)
        if t["key"] == "roulette":
            card = draw_chance(room)
            return mk_mission("🎡 룰렛존 → 🍀 찬스", "🍀", card["text"], "룰렛이 뽑은 찬스 카드!", CAT["chance"]["color"], tid)
    if t["type"] == "chance":
        card = draw_chance(room)
        return mk_mission("🍀 찬스 카드", "🍀", card["text"], "", CAT["chance"]["color"], tid)
    if t["type"] == "quiz":
        q = random.choice(QUIZ)
        return mk_mission("🧠 상식 퀴즈", "🧠", f"Q. {q['q']}",
                          f"정답: {q['a']} · 관리자가 읽어주고 못 맞히면 한 잔!", QUIZ_COLOR, tid)
    if t["type"] == "chosung":
        c = random.choice(CHOSUNG)
        return mk_mission("🔤 초성 게임", "🔤", f"'{c['cho']}' 초성으로 단어 말하기! 5초 안에 못 대면 한 잔",
                          f"예: {c['ex']}", CHOSUNG_COLOR, tid)
    if t["type"] == "ingredient":
        ing = random.choice(INGREDIENTS)
        room["bomb"].append(ing)
        n = len(room["bomb"])
        return mk_mission("🍶 폭탄주 재료", "🍶",
                          f"폭탄주에 '{ing}' 추가! 지금 {n}잔 분량 쌓였어요 💥",
                          "💣 폭탄주 칸을 밟는 팀이 전부 마셔요!", INGREDIENT_COLOR, tid)
    if t["type"] == "bomb":
        b = room["bomb"]
        if b:
            n = len(b); items = " + ".join(b); room["bomb"] = []
            return mk_mission("💣 폭탄주 폭발!", "💣",
                              f"{team['name']}이(가) {n}잔짜리 폭탄주({items}) 원샷! 🍻💥",
                              "폭탄주가 초기화됐어요.", BOMB_COLOR, tid)
        return mk_mission("💣 폭탄주", "💣", "폭탄주가 아직 비어 있어요! 운 좋게 통과 😅", "", BOMB_COLOR, tid)
    cc = CAT[t["type"]]
    return mk_mission(f"{cc['emoji']} {cc['label']}", cc["emoji"], t["text"], "", cc["color"], tid)


def obstacle_notice(effect: str, meta: int, team: dict) -> dict:
    tid = team["id"]; nm = team["name"]
    if effect == "trap":
        return mk_mission("🕳️ 함정", "🕳️", f"{nm} 함정에 걸려 {meta}칸 뒤로 밀려났어요! 😵", "한 바퀴가 멀어졌네요…", TRAP_COLOR, tid)
    if effect == "reset":
        return mk_mission("🌀 소용돌이", "🌀", f"{nm} 소용돌이에 휩쓸려 출발점으로! 😱", "처음부터 다시 시작…", RESET_COLOR, tid)
    # bounce
    return mk_mission("🎯 골인 실패", "🎯", f"정확한 수가 아니라 {meta}칸 튕겨나갔어요!", "골인은 출발칸에 딱 맞게 도착해야 해요.", "#f76707", tid)


# ── 직렬화/전송 ───────────────────────────────────────────────────────────
def serialize(room: dict) -> dict:
    return {
        "code": room["code"], "phase": room["phase"], "adminId": room["adminId"],
        "adminName": admin_name(room), "spectatorCount": spectator_count(room),
        "currentIdx": room["currentIdx"], "winnerId": room["winnerId"],
        "die": room["die"], "mission": room["mission"],
        "rollSeq": room["rollSeq"], "lastRoll": room["lastRoll"], "lastEvent": room["lastEvent"],
        "tileCount": TILE_COUNT, "bombCount": len(room["bomb"]), "bombItems": list(room["bomb"]),
        "teams": [{k: t[k] for k in ("id", "name", "emoji", "color", "pos", "skip")} for t in room["teams"]],
    }


async def send(ws: WebSocket, obj: dict) -> bool:
    try:
        await ws.send_json(obj)
        return True
    except Exception:
        return False


async def broadcast_msg(room: dict, obj: dict):
    for pid, ws in list(conns.get(room["code"], {}).items()):
        if not await send(ws, obj):
            conns.get(room["code"], {}).pop(pid, None)
            pl = find_participant(room, pid)
            if pl:
                pl["connected"] = False


async def broadcast(room: dict):
    room["lastActive"] = time.time()
    await broadcast_msg(room, {"type": "state", "state": serialize(room)})


# ──────────────────────────────────────────────────────────────────────────
# 액션 핸들러
# ──────────────────────────────────────────────────────────────────────────
async def handle_create(ws, msg):
    code = gen_code()
    room = new_room(code)
    rooms[code] = room
    conns[code] = {}
    p = add_participant(room, msg.get("name", ""), "admin")
    room["adminId"] = p["id"]
    conns[code][p["id"]] = ws
    await send(ws, {"type": "joined", "room": code, "playerId": p["id"], "role": "admin"})
    await broadcast(room)
    return code, p["id"]


async def handle_join(ws, msg):
    code = (msg.get("room") or "").strip().upper()
    room = rooms.get(code)
    if not room:
        await send(ws, {"type": "error", "message": "방을 찾을 수 없어요. 코드를 확인해 주세요."})
        return None, None
    if spectator_count(room) >= MAX_SPECTATORS:
        await send(ws, {"type": "error", "message": "관람 인원이 가득 찼어요."})
        return None, None
    p = add_participant(room, msg.get("name", ""), "spectator")
    conns[code][p["id"]] = ws
    await send(ws, {"type": "joined", "room": code, "playerId": p["id"], "role": "spectator"})
    await broadcast(room)
    return code, p["id"]


async def handle_rejoin(ws, msg):
    code = (msg.get("room") or "").strip().upper()
    pid = msg.get("playerId")
    room = rooms.get(code)
    p = find_participant(room, pid) if room else None
    if not room or not p:
        await send(ws, {"type": "expired"})
        return None, None
    p["connected"] = True
    conns.setdefault(code, {})[pid] = ws
    await send(ws, {"type": "joined", "room": code, "playerId": pid, "role": p["role"]})
    await broadcast(room)
    return code, pid


def is_admin(room, pid):
    return pid == room["adminId"]


async def handle_add_team(room, pid, msg):
    if not is_admin(room, pid) or room["phase"] != "lobby":
        return
    if len(room["teams"]) >= MAX_TEAMS:
        ws = conns.get(room["code"], {}).get(pid)
        if ws:
            await send(ws, {"type": "error", "message": f"팀은 최대 {MAX_TEAMS}개까지예요."})
        return
    add_team(room, msg.get("name", ""))
    await broadcast(room)


async def handle_remove_team(room, pid, msg):
    if not is_admin(room, pid) or room["phase"] != "lobby":
        return
    room["teams"] = [t for t in room["teams"] if t["id"] != msg.get("teamId")]
    await broadcast(room)


async def handle_rename_team(room, pid, msg):
    if not is_admin(room, pid) or room["phase"] != "lobby":
        return
    t = find_team(room, msg.get("teamId"))
    if t:
        t["name"] = (msg.get("name") or "").strip()[:8] or t["name"]
    await broadcast(room)


def reset_board(room):
    for t in room["teams"]:
        t["pos"] = 0; t["skip"] = False
    room["currentIdx"] = 0; room["mission"] = None; room["die"] = None
    room["extraRoll"] = False; room["winnerId"] = None; room["lastRoll"] = None; room["bomb"] = []


async def handle_start(room, pid, msg):
    if not is_admin(room, pid) or room["phase"] != "lobby":
        return
    if len(room["teams"]) < 2:
        ws = conns.get(room["code"], {}).get(pid)
        if ws:
            await send(ws, {"type": "error", "message": "팀이 2개 이상이어야 시작할 수 있어요."})
        return
    reset_board(room)
    room["phase"] = "playing"
    set_event(room, "start", f"🎲 게임 시작! {room['teams'][0]['name']}부터 · 한 바퀴 먼저 완주하면 우승!")
    await broadcast(room)


async def handle_roll(room, pid, msg):
    if not is_admin(room, pid) or room["phase"] != "playing" or room["mission"] is not None:
        return
    team = cur_team(room)
    if not team:
        return
    if team["skip"]:
        team["skip"] = False
        set_event(room, "skip", f"🏝️ {team['name']} 무인도에서 한 턴 쉼!")
        advance_turn(room)
        await broadcast(room)
        return
    room["extraRoll"] = False
    die = random.randint(1, 3)
    path, effect, meta = compute_move(team, die)
    room["die"] = die
    room["rollSeq"] += 1
    room["lastRoll"] = {"teamId": team["id"], "die": die, "path": path, "effect": effect, "meta": meta}
    if effect == "win":
        room["phase"] = "finished"
        room["winnerId"] = team["id"]
        room["mission"] = None
        set_event(room, "win", f"🎉 {team['name']} 한 바퀴 완주 — 우승! 🏆")
        await broadcast(room)
        return
    if effect in ("trap", "reset", "bounce"):
        room["mission"] = obstacle_notice(effect, meta, team)
    else:
        room["mission"] = resolve_tile(room, team)
    await broadcast(room)


async def handle_confirm(room, pid, msg):
    if not is_admin(room, pid) or not room["mission"]:
        return
    room["mission"] = None
    if room["extraRoll"]:
        room["extraRoll"] = False
        set_event(room, "extra", room["extraRollMsg"] or "🎲 한 번 더 굴려요!")
    else:
        advance_turn(room)
    await broadcast(room)


async def handle_restart(room, pid, msg):
    if not is_admin(room, pid):
        return
    reset_board(room)
    room["phase"] = "playing"
    set_event(room, "restart", "↺ 처음부터 다시!")
    await broadcast(room)


async def handle_to_lobby(room, pid, msg):
    if not is_admin(room, pid):
        return
    reset_board(room)
    room["phase"] = "lobby"
    await broadcast(room)


async def handle_emoji(room, pid, msg):
    e = msg.get("e")
    if e not in REACTIONS:
        return
    p = find_participant(room, pid)
    now = time.time()
    if not p or now - p.get("emojiTs", 0.0) < EMOJI_THROTTLE:
        return
    if now - room.get("emojiRoomTs", 0.0) < ROOM_EMOJI_MIN:   # 방 전체 폭주 방지
        return
    p["emojiTs"] = now
    room["emojiRoomTs"] = now
    room["lastActive"] = now
    await broadcast_msg(room, {"type": "emoji", "e": e})


HANDLERS = {
    "addTeam": handle_add_team, "removeTeam": handle_remove_team, "renameTeam": handle_rename_team,
    "start": handle_start, "roll": handle_roll, "confirm": handle_confirm,
    "restart": handle_restart, "toLobby": handle_to_lobby, "emoji": handle_emoji,
}


async def handle_disconnect(code, pid, *, leaving=False):
    room = rooms.get(code)
    if not room:
        return
    conns.get(code, {}).pop(pid, None)
    p = find_participant(room, pid)
    if not p:
        return
    was_admin = (p["role"] == "admin")
    if leaving:
        room["participants"] = [x for x in room["participants"] if x["id"] != pid]
    else:
        p["connected"] = False
    # 관리자가 빠지면(자발적 나가기든 연결 끊김이든) 연결된 다른 참가자에게 즉시 이양
    if was_admin and room["adminId"] == pid:
        nxt = next((x for x in room["participants"] if x["connected"] and x["id"] != pid), None)
        if nxt:
            nxt["role"] = "admin"
            room["adminId"] = nxt["id"]
            if not leaving:
                p["role"] = "spectator"  # 끊긴 옛 관리자는 재접속 시 관람객으로
        elif leaving:
            rooms.pop(code, None); conns.pop(code, None); return
        # not leaving & 연결된 다른 참가자 없음 → adminId 유지(본인 재접속으로 복귀)
    await broadcast(room)


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
                        await handle_disconnect(code, pid, leaving=True)
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
