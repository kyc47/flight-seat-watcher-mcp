#!/usr/bin/env python3
import argparse
import os
import time
from pathlib import Path

from naver_flight_watch import (
    availability_signature,
    current_schedule_slot,
    format_manual_check_message,
    format_telegram_message,
    gather_current_state,
    get_telegram_updates,
    has_any_availability,
    load_env_file,
    load_offset,
    load_subscribers,
    load_text_file,
    save_offset,
    save_subscribers,
    save_text_file,
    send_telegram_message,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--wait-seconds", type=int, default=15)
    parser.add_argument("--window-size", default="1440,2400")
    parser.add_argument(
        "--telegram-offset-file",
        default=str(Path(".state") / "telegram_update_offset.txt"),
    )
    parser.add_argument(
        "--telegram-chat-id-file",
        default=str(Path(".state") / "telegram_chat_id.txt"),
    )
    parser.add_argument(
        "--telegram-subscribers-file",
        default=str(Path(".state") / "telegram_subscribers.json"),
    )
    parser.add_argument(
        "--schedule-state-file",
        default=str(Path(".state") / "last_scheduled_slot.txt"),
    )
    parser.add_argument(
        "--paused-file",
        default=str(Path(".state") / "paused.txt"),
    )
    return parser.parse_args()


def get_fresh_state(args: argparse.Namespace) -> dict:
    return gather_current_state(args.wait_seconds, args.window_size)


def process_updates(
    token: str,
    args: argparse.Namespace,
    cached_state: dict | None,
) -> tuple[bool, dict | None]:
    offset_file = Path(args.telegram_offset_file)
    chat_id_file = Path(args.telegram_chat_id_file)
    subscribers_file = Path(args.telegram_subscribers_file)
    schedule_state_file = Path(args.schedule_state_file)

    offset = load_offset(offset_file)
    payload = get_telegram_updates(token, offset)
    updates = payload.get("result", [])
    if not updates:
        return False, cached_state

    subscribers = load_subscribers(subscribers_file)
    paused_file = Path(args.paused_file)
    highest_update_id = offset or 0
    handled_any = False

    for item in updates:
        update_id = item.get("update_id")
        if isinstance(update_id, int) and update_id > highest_update_id:
            highest_update_id = update_id

        message = item.get("message") or item.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        chat_id_str = str(chat_id)
        save_text_file(chat_id_file, chat_id_str)

        if text.startswith("/start"):
            if chat_id_str not in subscribers:
                subscribers.append(chat_id_str)
                save_subscribers(subscribers_file, subscribers)
            if paused_file.exists():
                paused_file.unlink()
            send_telegram_message(
                token,
                chat_id_str,
                "항공권 모니터링 봇에 오신 것을 환영합니다!\n자동 검색이 시작되었습니다.\n\n"
                "/stop : 자동 검색 중지\n"
                "/start : 자동 검색 재개\n"
                "/check : 지금 바로 항공권 확인\n"
                "/unsubscribe : 구독 해제\n"
                "/help : 도움말",
            )
            handled_any = True
        elif text.startswith("/subscribe"):
            if chat_id_str not in subscribers:
                subscribers.append(chat_id_str)
                save_subscribers(subscribers_file, subscribers)
            send_telegram_message(
                token,
                chat_id_str,
                "구독 완료\n10분마다 항공권 현황을 보내드립니다.\n수동 확인은 /check, 구독 해지는 /unsubscribe 입니다.",
            )
            handled_any = True
        elif text.startswith("/unsubscribe"):
            subscribers = [s for s in subscribers if s != chat_id_str]
            save_subscribers(subscribers_file, subscribers)
            send_telegram_message(token, chat_id_str, "구독 해제 완료")
            handled_any = True
        elif text.startswith("/check"):
            cached_state = get_fresh_state(args)
            send_telegram_message(token, chat_id_str, format_manual_check_message(cached_state))
            handled_any = True
        elif text.startswith("/stop"):
            paused_file.parent.mkdir(parents=True, exist_ok=True)
            paused_file.touch()
            send_telegram_message(
                token,
                chat_id_str,
                "자동 검색이 중지되었습니다.\n재개하려면 /start 를 입력하세요.",
            )
            handled_any = True
        elif text.startswith("/reset"):
            if schedule_state_file.exists():
                schedule_state_file.unlink()
            cached_state = None
            send_telegram_message(
                token,
                chat_id_str,
                "상태 초기화 완료\n캐시가 비워졌고 다음 스케줄 슬롯에 즉시 발송됩니다.",
            )
            handled_any = True
        elif text.startswith("/help"):
            is_paused = paused_file.exists()
            send_telegram_message(
                token,
                chat_id_str,
                f"현재 상태: {'중지됨 (/start 로 재개)' if is_paused else '실행중 (/stop 으로 중지)'}\n\n"
                "/stop : 자동 검색 중지\n"
                "/start : 자동 검색 재개\n"
                "/check : 지금 바로 항공권 확인\n"
                "/unsubscribe : 구독 해제\n"
                "/reset : 상태 초기화\n"
                "/help : 도움말",
            )
            handled_any = True

    if highest_update_id:
        save_offset(offset_file, highest_update_id + 1)
    return handled_any, cached_state


def maybe_send_scheduled(
    token: str,
    args: argparse.Namespace,
    cached_state: dict | None,
) -> dict | None:
    if Path(args.paused_file).exists():
        return cached_state

    subscribers = load_subscribers(Path(args.telegram_subscribers_file))
    if not subscribers:
        return cached_state

    slot = current_schedule_slot()
    if not slot:
        return cached_state

    schedule_state_file = Path(args.schedule_state_file)
    last_slot = load_text_file(schedule_state_file)
    if slot == last_slot:
        return cached_state

    previous_signature = availability_signature(cached_state) if cached_state else None
    cached_state = get_fresh_state(args)
    current_signature = availability_signature(cached_state)
    if not has_any_availability(cached_state):
        save_text_file(schedule_state_file, slot)
        return cached_state

    if current_signature == previous_signature:
        save_text_file(schedule_state_file, slot)
        return cached_state

    message = format_telegram_message(cached_state, [])
    for subscriber in subscribers:
        send_telegram_message(token, subscriber, message)
    save_text_file(schedule_state_file, slot)
    return cached_state


def main() -> None:
    args = parse_args()
    env_file = load_env_file()
    for key, value in env_file.items():
        os.environ.setdefault(key, value)

    token = os.environ.get("TELEGRAMBOT", "").strip()
    if not token:
        raise SystemExit("Missing TELEGRAMBOT")

    cached_state = None
    while True:
        try:
            _, cached_state = process_updates(token, args, cached_state)
            cached_state = maybe_send_scheduled(token, args, cached_state)
        except Exception as exc:  # noqa: BLE001
            log_path = Path(".state") / "bot_runner_error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as fp:
                fp.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(exc).__name__}: {exc}\n")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
