#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
from typing import Any

from flight_watch_dynamic import (
    format_state_summary,
    gather_state_for_watches,
    get_telegram_token,
    load_env_file,
    load_text_file,
    send_telegram_message,
)


SERVER_INFO = {
    "name": "flight-seat-watcher",
    "version": "0.1.0",
}


TOOLS = [
    {
        "name": "check_flights",
        "description": (
            "Check configured flight watches using Naver Flights and airport.co.kr. "
            "Domestic routes also include airport.co.kr availability."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "watches": {
                    "type": "array",
                    "description": "Up to 5 watch objects.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "origin": {"type": "string"},
                            "destination": {"type": "string"},
                            "market": {"type": "string", "enum": ["domestic", "international"]},
                            "trip_type": {"type": "string", "enum": ["oneway", "roundtrip"]},
                            "departure_date": {"type": "string", "description": "YYYYMMDD"},
                            "return_date": {"type": "string", "description": "YYYYMMDD"},
                            "departure_time_range": {"type": "string", "description": "HH:MM~HH:MM"},
                            "return_time_range": {"type": "string", "description": "HH:MM~HH:MM"},
                            "adults": {"type": "integer"},
                        },
                        "required": ["origin", "destination", "market", "trip_type", "departure_date", "departure_time_range"],
                    },
                },
                "wait_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 15,
                    "description": "How long Selenium should wait for Naver results.",
                },
                "window_size": {
                    "type": "string",
                    "default": "1440,2400",
                    "description": "Chrome window size in WIDTH,HEIGHT format.",
                },
            },
        },
    },
    {
        "name": "get_manual_summary",
        "description": "Return the same human-readable flight summary used for /check in Telegram.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "watches": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "wait_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 15,
                },
                "window_size": {
                    "type": "string",
                    "default": "1440,2400",
                },
            },
        },
    },
    {
        "name": "send_telegram_test",
        "description": (
            "Send a test Telegram message using TELEGRAM_BOT_TOKEN or TELEGRAMBOT and "
            "TELEGRAM_CHAT_ID or the saved chat id file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message text to send.",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional override chat id.",
                },
            },
            "required": ["message"],
        },
    },
]


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8").strip()
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def make_text_result(text: str) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ]
    }


def make_json_result(payload: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "structuredContent": payload,
    }


def handle_initialize(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message["id"],
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "serverInfo": SERVER_INFO,
        },
    }


def handle_tools_call(message: dict[str, Any]) -> dict[str, Any]:
    params = message.get("params", {})
    name = params.get("name")
    arguments = params.get("arguments", {}) or {}

    watches = arguments.get("watches", []) or []
    if watches and (not isinstance(watches, list) or len(watches) > 5):
        raise RuntimeError("watches must be a list with at most 5 items")

    if name == "check_flights":
        wait_seconds = int(arguments.get("wait_seconds", 15))
        window_size = str(arguments.get("window_size", "1440,2400"))
        result = gather_state_for_watches(watches, wait_seconds, window_size)
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": make_json_result(result),
        }

    if name == "get_manual_summary":
        wait_seconds = int(arguments.get("wait_seconds", 15))
        window_size = str(arguments.get("window_size", "1440,2400"))
        result = gather_state_for_watches(watches, wait_seconds, window_size)
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": make_text_result(format_state_summary(result)),
        }

    if name == "send_telegram_test":
        env_file = load_env_file()
        for key, value in env_file.items():
            if key in ("TELEGRAM_BOT_TOKEN", "TELEGRAMBOT", "TELEGRAM_CHAT_ID") and value:
                os.environ.setdefault(key, value)

        token = get_telegram_token()
        if not token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAMBOT in environment")

        chat_id = str(arguments.get("chat_id") or "").strip()
        if not chat_id:
            chat_id = (
                load_text_file(Path(".state") / "telegram_chat_id.txt")
                or ""
            )
        if not chat_id:
            chat_id = ""
        if not chat_id:
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not chat_id:
            raise RuntimeError("Missing chat_id. Set TELEGRAM_CHAT_ID or pass chat_id.")

        response = send_telegram_message(token, chat_id, str(arguments["message"]))
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": make_json_result(response),
        }

    raise ValueError(f"Unknown tool: {name}")


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    if method == "initialize":
        return handle_initialize(message)
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {},
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "tools": TOOLS,
            },
        }
    if method == "tools/call":
        return handle_tools_call(message)
    if "id" in message:
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }
    return None


def main() -> None:
    while True:
        try:
            message = read_message()
            if message is None:
                break
            response = handle_message(message)
            if response is not None:
                write_message(response)
        except Exception as exc:  # noqa: BLE001
            error_id = None
            if "message" in locals() and isinstance(message, dict):
                error_id = message.get("id")
            payload = {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32000,
                    "message": str(exc),
                },
            }
            if error_id is not None:
                payload["id"] = error_id
            write_message(payload)


if __name__ == "__main__":
    main()
