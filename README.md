# Flight Seat Watcher

Naver Flights and airport.co.kr 기반 항공권 모니터링 도구다.

현재 텔레그램 봇 기능:

- 사용자별 조회 조건 등록
- 최대 5개 여정 동시 추적
- 편도 / 왕복 선택
- 국내선 / 국제선 자동 판별
- 출발 / 복귀 시간대 필터
- 10분마다 자동 확인
- 표가 생겼을 때만 알림 전송

## Telegram

다른 사람이 자기 컴퓨터에서 쓰는 기본 순서:

```bash
git clone https://github.com/kyc47/flight-seat-watcher.git
cd flight-seat-watcher
./bin/setup_local.sh
source .venv/bin/activate
```

각자 본인 텔레그램 봇을 만들어서 사용할 수 있다.

1. Telegram에서 `@BotFather`를 연다.
2. `/newbot` 으로 새 봇을 만든다.
3. 받은 토큰을 `.env` 파일의 `TELEGRAM_BOT_TOKEN` 에 넣는다.
4. 봇과 대화를 시작하고 `/start` 를 보낸다.
5. 필요하면 `TELEGRAM_CHAT_ID` 를 직접 넣을 수도 있다.

예시:

```env
TELEGRAM_BOT_TOKEN=1234567890:your_token
TELEGRAM_CHAT_ID=
```

설치:

```bash
python3 -m pip install -r requirements.txt
```

실행:

```bash
./bin/run_telegram_bot.sh
```

지원 명령:

- `/start`
- `/add`
- `/list`
- `/remove`
- `/check`
- `/stop`
- `/help`

텔레그램에서 첫 등록 흐름:

1. 출발 공항 입력
2. 도착 공항 입력
3. 왕복 / 편도 버튼 선택
4. 출발 날짜 입력
5. 왕복이면 복귀 날짜 입력
6. 출발 시간대 입력 (`06:00~22:00`)
7. 왕복이면 복귀 시간대 입력 (`06:00~22:00`)

## MCP Server

이 프로젝트는 `stdio` 기반 MCP 서버로도 실행할 수 있다.

실행:

```bash
./bin/run_mcp.sh
```

제공 툴:

- `check_flights`
- `get_manual_summary`
- `send_telegram_test`

Claude Desktop, Claude Code, Codex, Cursor, VS Code Agent Mode 같은 로컬 MCP 클라이언트에 연결할 수 있다.

예시 설정 파일은 [.mcp.json.example](./.mcp.json.example)에 있다.
`/ABSOLUTE/PATH/TO/flight-seat-watcher/bin/run_mcp.sh` 부분만 자기 컴퓨터의 클론 경로로 바꾸면 된다.

예시:

```json
{
  "mcpServers": {
    "flight-seat-watcher": {
      "command": "/Users/alice/dev/flight-seat-watcher/bin/run_mcp.sh",
      "args": []
    }
  }
}
```

## Local Check

수동 점검:

```bash
python3 scripts/naver_flight_watch.py
```

## Notes

- Naver 조회는 Selenium과 로컬 Chrome이 필요하다.
- Chrome 경로가 자동으로 잡히지 않으면 `.env`에 `CHROME_BINARY=/path/to/chrome` 를 넣으면 된다.
- 공항공사 조회는 국내선만 지원한다.
- 국제선은 현재 네이버 기준으로 조회한다.
- MCP는 Claude 전용이 아니다. MCP를 지원하는 클라이언트면 붙일 수 있다.
- ChatGPT custom connector로 붙이려면 `stdio` 대신 공개 HTTP/SSE 기반 remote MCP 서버가 추가로 필요하다.
