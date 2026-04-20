# Flight Seat Watcher

`Naver Flights`와 `airport.co.kr`를 이용해 항공권 좌석 여부를 확인하는 도구입니다.

이 저장소는 다음 기능을 제공합니다.

- 텔레그램 봇 기반 항공권 모니터링
- 사용자별 조회 조건 저장
- 최대 5개 여정 동시 추적
- 편도 / 왕복 선택
- 국내선 / 국제선 구분
- 출발 / 복귀 시간대 필터
- `stdio` 기반 MCP 서버

## Quick Start

```bash
git clone https://github.com/kyc47/flight-seat-watcher.git
cd flight-seat-watcher
./bin/setup_local.sh
source .venv/bin/activate
```

## Telegram Bot

각 사용자는 본인 텔레그램 봇 토큰으로 별도 사용할 수 있습니다.

### 1. Bot 생성

1. 텔레그램에서 `@BotFather`를 엽니다.
2. `/newbot` 명령으로 새 봇을 생성합니다.
3. 발급받은 토큰을 `.env` 파일에 입력합니다.

예시:

```env
TELEGRAM_BOT_TOKEN=1234567890:your_token
TELEGRAM_CHAT_ID=
```

`TELEGRAM_CHAT_ID`는 선택 사항입니다.

### 2. 실행

```bash
./bin/run_telegram_bot.sh
```

### 3. 초기 등록 흐름

텔레그램에서 봇과 대화를 시작한 뒤 `/start`를 입력하면 다음 순서로 조회 조건을 등록할 수 있습니다.

1. 출발 공항 입력
2. 도착 공항 입력
3. 왕복 / 편도 선택
4. 출발 날짜 입력
5. 왕복일 경우 복귀 날짜 입력
6. 출발 시간대 입력 (`06:00~22:00`)
7. 왕복일 경우 복귀 시간대 입력 (`06:00~22:00`)

### 지원 명령

- `/start`
- `/add`
- `/list`
- `/remove`
- `/check`
- `/stop`
- `/help`

## MCP Server

이 프로젝트는 `stdio` 기반 MCP 서버로 실행할 수 있습니다.

### 실행

```bash
./bin/run_mcp.sh
```

### 제공 도구

- `check_flights`
- `get_manual_summary`
- `send_telegram_test`

### MCP 설정 예시

[.mcp.json.example](./.mcp.json.example)을 참고해 클라이언트 설정에 추가하면 됩니다.

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

`command` 경로는 각자의 로컬 클론 경로에 맞게 수정해야 합니다.

이 서버는 `Claude Desktop`, `Claude Code`, `Codex`, `Cursor`, `VS Code Agent Mode` 등 로컬 MCP 클라이언트에서 사용할 수 있습니다.

## Manual Check

단일 실행으로 상태를 확인하려면 아래 명령을 사용할 수 있습니다.

```bash
python3 scripts/naver_flight_watch.py
```

## Requirements

- Python 3
- Google Chrome
- `selenium`

의존성 설치:

```bash
python3 -m pip install -r requirements.txt
```

Chrome 경로가 자동으로 잡히지 않으면 `.env`에 아래 값을 추가할 수 있습니다.

```env
CHROME_BINARY=/path/to/chrome
```

## Notes

- 공항공사 조회는 현재 국내선만 지원합니다.
- 국제선은 현재 네이버 기준으로 조회합니다.
- MCP는 Claude 전용이 아니며, MCP를 지원하는 클라이언트에서 사용할 수 있습니다.
- ChatGPT custom connector에 직접 연결하려면 `stdio` 서버가 아니라 별도의 `HTTP/SSE remote MCP server` 구성이 필요합니다.
