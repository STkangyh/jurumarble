# 🎲 주루마블 — 실시간 멀티플레이

**관리자 1명이 진행**하고 나머지는 **각자 폰으로 관람**하는 실시간 술자리 보드게임. FastAPI + WebSocket 단일 서비스(서버가 게임 로직의 단일 진실원).

- **관리자**(방 생성자): 로비에서 팀을 만들고, 게임 중 **모든 팀의 주사위를 대신 굴리고 미션 확인까지** 진행.
- **관람객**(나머지 전원): 보드를 실시간으로 보기만 하며, **이모티콘**을 날리면 모든 화면의 맵 위로 4초간 떠올랐다 사라짐.

```
.
├─ server.py        # FastAPI + WebSocket 서버 (게임 로직)
├─ client.html      # 모바일 클라이언트 (서버가 / 로 서빙)
├─ offline.html     # 서버 없이 한 폰으로 돌리는 오프라인 단일 파일 버전
├─ requirements.txt
├─ Dockerfile       # 컨테이너 배포
├─ Procfile         # Heroku/Railway 등
└─ render.yaml      # Render.com 블루프린트
```

## 로컬 실행

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

- 같은 노트북: <http://localhost:8000>
- 같은 와이파이의 폰: `http://<노트북-LAN-IP>:8000` (예: `http://10.50.75.243:8000`)
- WebSocket 주소는 클라이언트가 접속한 오리진에서 자동 결정(`ws`/`wss`) — 별도 설정 없음.
- 서버 없이 빠르게 보고 싶으면 `offline.html` 을 브라우저로 바로 열면 됩니다(혼자/한 폰용).

## 배포 (어디서나 접속)

> ⚠️ GitHub Pages 같은 정적 호스팅은 **불가** — WebSocket 서버가 필요합니다.
> 아래 호스트들은 HTTPS를 자동 제공하고, 클라이언트는 자동으로 `wss://`로 붙습니다.

### 1) Render.com — 가장 쉬움 (무료 플랜)
1. 이 레포를 GitHub에 푸시.
2. Render → **New ▸ Blueprint** → 레포 선택 → `render.yaml` 자동 인식.
3. 배포 완료되면 `https://jurumarble-xxxx.onrender.com` 으로 접속.
- 무료 플랜은 미사용 시 슬립 → 첫 접속이 느릴 수 있고, 클라이언트의 자동 재연결이 처리합니다.

### 2) Railway
1. New Project → Deploy from GitHub → 이 레포 선택.
2. `Procfile` 의 start 커맨드를 자동 사용. (없으면: `uvicorn server:app --host 0.0.0.0 --port $PORT`)

### 3) Fly.io
```bash
fly launch --dockerfile Dockerfile   # 앱 이름 지정, 빌더는 Dockerfile
fly deploy
```

### 4) 임의의 Docker 호스트
```bash
docker build -t jurumarble .
docker run -p 8000:8000 jurumarble
```

## 통신 프로토콜 (요약)

클라이언트 → 서버 (JSON):
- 공통: `create`(관리자로) / `join`(관람객으로) / `rejoin` / `leave` / `ping` / `emoji`(이모티콘)
- 관리자 전용: `addTeam` / `removeTeam` / `renameTeam` / `start` / `roll` / `confirm` / `restart`

서버 → 클라이언트: `joined`(방코드·참가자ID·역할) / `state`(방 전체 상태 브로드캐스트) / `emoji`(이모티콘 전파) / `error` / `expired` / `pong`

- 모든 게임 판정(주사위·이동·미션 선택·턴 전환)은 **서버**에서 수행 → 모든 기기 동기화·치팅 방지.
- 관리자 전용 액션은 서버가 `adminId`로 검증 → 관람객은 보기 전용.
- 이모티콘은 허용 목록만 통과 + 참가자별 0.2초 스로틀(도배 방지).
- 새로고침/네트워크 끊김 시 `localStorage`의 방코드+참가자ID로 **자동 재접속**(역할 유지).
- 비활성 방은 1시간 후 자동 정리.

## 게임 규칙

관리자가 팀을 만들고(2~6팀), 팀 순서대로 **관리자가 주사위를 대신 굴림**. 멈춘 칸의 미션 수행 후 관리자가 확인 → 다음 팀.
카테고리: 💬토크 · 🎭장기 · 🎲대결 · 🔥벌칙 · 🍀찬스.
특수칸: 🏁출발(보너스) · 🏝️무인도(한 턴 쉬기) · 🛋️휴게소(안전) · 🎡룰렛존(찬스카드).
더블 시 한 번 더. 미션은 본격 술게임 세트(매운맛) 단일 구성. 관람객은 이모티콘으로 참여.
