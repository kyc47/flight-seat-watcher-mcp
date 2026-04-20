#!/usr/bin/env python3
import argparse
import os
import time
from pathlib import Path

from flight_watch_dynamic import (
    DEFAULT_POLL_WINDOW,
    DEFAULT_WAIT_SECONDS,
    MAX_WATCHES_PER_CHAT,
    answer_telegram_callback,
    availability_signature,
    build_trip_type_keyboard,
    build_watch_from_draft,
    current_schedule_slot,
    edit_telegram_message_reply_markup,
    format_state_summary,
    format_watch_list,
    gather_state_for_watches,
    get_telegram_token,
    get_telegram_updates,
    load_chat_sessions,
    load_env_file,
    load_offset,
    load_paused_chats,
    load_signatures,
    load_subscribers,
    load_watch_configs,
    normalize_airport_code,
    parse_date_input,
    parse_time_range_input,
    render_watch_created_message,
    save_chat_sessions,
    save_offset,
    save_paused_chats,
    save_signatures,
    save_subscribers,
    save_text_file,
    save_watch_configs,
    send_telegram_message,
    has_any_availability,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--wait-seconds", type=int, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--window-size", default=DEFAULT_POLL_WINDOW)
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
        "--watch-configs-file",
        default=str(Path(".state") / "watch_configs.json"),
    )
    parser.add_argument(
        "--chat-sessions-file",
        default=str(Path(".state") / "chat_sessions.json"),
    )
    parser.add_argument(
        "--signatures-file",
        default=str(Path(".state") / "availability_signatures.json"),
    )
    parser.add_argument(
        "--paused-chats-file",
        default=str(Path(".state") / "paused_chats.json"),
    )
    return parser.parse_args()


def start_watch_wizard(
    token: str,
    chat_id: str,
    sessions: dict[str, dict],
    watch_configs: dict[str, list[dict]],
    sessions_file: Path,
) -> None:
    existing = watch_configs.get(chat_id, [])
    if len(existing) >= MAX_WATCHES_PER_CHAT:
        send_telegram_message(token, chat_id, f"최대 {MAX_WATCHES_PER_CHAT}개까지만 등록할 수 있습니다.\n/remove 로 먼저 삭제하세요.")
        return
    sessions[chat_id] = {"step": "origin", "draft": {}}
    save_chat_sessions(sessions_file, sessions)
    send_telegram_message(token, chat_id, "출발 공항을 입력해주세요.\n예: ICN")


def finalize_watch(
    token: str,
    chat_id: str,
    sessions: dict[str, dict],
    watch_configs: dict[str, list[dict]],
    sessions_file: Path,
    watch_configs_file: Path,
) -> None:
    draft = sessions[chat_id]["draft"]
    current = watch_configs.get(chat_id, [])
    watch = build_watch_from_draft(draft, len(current))
    current.append(watch)
    watch_configs[chat_id] = current
    save_watch_configs(watch_configs_file, watch_configs)
    sessions.pop(chat_id, None)
    save_chat_sessions(sessions_file, sessions)
    send_telegram_message(token, chat_id, render_watch_created_message(watch, len(current)))


def handle_session_message(
    token: str,
    chat_id: str,
    text: str,
    sessions: dict[str, dict],
    watch_configs: dict[str, list[dict]],
    sessions_file: Path,
    watch_configs_file: Path,
) -> bool:
    session = sessions.get(chat_id)
    if not session:
        return False

    draft = session.setdefault("draft", {})
    step = session.get("step")

    if step == "origin":
        origin = normalize_airport_code(text)
        if not origin:
            send_telegram_message(token, chat_id, "출발 공항 코드를 다시 입력해주세요.\n예: ICN")
            return True
        draft["origin"] = origin
        session["step"] = "destination"
        save_chat_sessions(sessions_file, sessions)
        send_telegram_message(token, chat_id, "도착 공항을 입력해주세요.\n예: NRT")
        return True

    if step == "destination":
        destination = normalize_airport_code(text)
        if not destination:
            send_telegram_message(token, chat_id, "도착 공항 코드를 다시 입력해주세요.\n예: NRT")
            return True
        draft["destination"] = destination
        session["step"] = "trip_type"
        save_chat_sessions(sessions_file, sessions)
        send_telegram_message(
            token,
            chat_id,
            "왕복/편도를 선택해주세요.",
            reply_markup=build_trip_type_keyboard(),
        )
        return True

    if step == "departure_date":
        departure_date = parse_date_input(text)
        if not departure_date:
            send_telegram_message(token, chat_id, "출발 날짜를 다시 입력해주세요.\n형식: YYYY-MM-DD")
            return True
        draft["departure_date"] = departure_date
        if draft["trip_type"] == "roundtrip":
            session["step"] = "return_date"
            save_chat_sessions(sessions_file, sessions)
            send_telegram_message(token, chat_id, "복귀 날짜를 입력해주세요.\n형식: YYYY-MM-DD")
        else:
            session["step"] = "departure_time_range"
            save_chat_sessions(sessions_file, sessions)
            send_telegram_message(token, chat_id, "출발 시간대를 입력해주세요.\n예: 06:00~22:00")
        return True

    if step == "return_date":
        return_date = parse_date_input(text)
        if not return_date:
            send_telegram_message(token, chat_id, "복귀 날짜를 다시 입력해주세요.\n형식: YYYY-MM-DD")
            return True
        if return_date < draft["departure_date"]:
            send_telegram_message(token, chat_id, "복귀 날짜는 출발 날짜보다 같거나 늦어야 합니다.")
            return True
        draft["return_date"] = return_date
        session["step"] = "departure_time_range"
        save_chat_sessions(sessions_file, sessions)
        send_telegram_message(token, chat_id, "출발 시간대를 입력해주세요.\n예: 06:00~22:00")
        return True

    if step == "departure_time_range":
        time_range = parse_time_range_input(text)
        if not time_range:
            send_telegram_message(token, chat_id, "출발 시간대를 다시 입력해주세요.\n예: 06:00~22:00")
            return True
        draft["departure_time_range"] = time_range
        if draft["trip_type"] == "roundtrip":
            session["step"] = "return_time_range"
            save_chat_sessions(sessions_file, sessions)
            send_telegram_message(token, chat_id, "복귀 시간대를 입력해주세요.\n예: 06:00~22:00")
        else:
            finalize_watch(token, chat_id, sessions, watch_configs, sessions_file, watch_configs_file)
        return True

    if step == "return_time_range":
        time_range = parse_time_range_input(text)
        if not time_range:
            send_telegram_message(token, chat_id, "복귀 시간대를 다시 입력해주세요.\n예: 06:00~22:00")
            return True
        draft["return_time_range"] = time_range
        finalize_watch(token, chat_id, sessions, watch_configs, sessions_file, watch_configs_file)
        return True

    return False


def handle_callback_query(
    token: str,
    callback: dict,
    sessions: dict[str, dict],
    watch_configs: dict[str, list[dict]],
    sessions_file: Path,
    watch_configs_file: Path,
) -> bool:
    callback_id = callback.get("id")
    data = (callback.get("data") or "").strip()
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "").strip()
    message_id = message.get("message_id")
    if not callback_id or not chat_id:
        return False

    if data.startswith("trip_type:"):
        trip_type = data.split(":", 1)[1]
        session = sessions.get(chat_id)
        if not session or session.get("step") != "trip_type":
            answer_telegram_callback(token, callback_id, "이 선택은 만료되었습니다.")
            return True
        session.setdefault("draft", {})["trip_type"] = trip_type
        session["step"] = "departure_date"
        save_chat_sessions(sessions_file, sessions)
        if isinstance(message_id, int):
            edit_telegram_message_reply_markup(token, chat_id, message_id)
        answer_telegram_callback(token, callback_id, "선택되었습니다.")
        send_telegram_message(token, chat_id, "출발 날짜를 입력해주세요.\n형식: YYYY-MM-DD")
        return True

    if data.startswith("remove_watch:"):
        watch_id = data.split(":", 1)[1]
        current = watch_configs.get(chat_id, [])
        updated = [watch for watch in current if watch.get("id") != watch_id]
        watch_configs[chat_id] = updated
        save_watch_configs(watch_configs_file, watch_configs)
        if isinstance(message_id, int):
            edit_telegram_message_reply_markup(token, chat_id, message_id)
        answer_telegram_callback(token, callback_id, "삭제되었습니다.")
        send_telegram_message(token, chat_id, format_watch_list(updated))
        return True

    answer_telegram_callback(token, callback_id)
    return True


def build_remove_keyboard(watches: list[dict]) -> dict:
    rows = []
    for idx, watch in enumerate(watches, start=1):
        rows.append([{"text": f"{idx}. {watch['origin']}->{watch['destination']}", "callback_data": f"remove_watch:{watch['id']}"}])
    return {"inline_keyboard": rows}


def process_updates(token: str, args: argparse.Namespace) -> None:
    offset_file = Path(args.telegram_offset_file)
    chat_id_file = Path(args.telegram_chat_id_file)
    subscribers_file = Path(args.telegram_subscribers_file)
    watch_configs_file = Path(args.watch_configs_file)
    sessions_file = Path(args.chat_sessions_file)
    paused_file = Path(args.paused_chats_file)

    offset = load_offset(offset_file)
    payload = get_telegram_updates(token, offset)
    updates = payload.get("result", [])
    if not updates:
        return

    sessions = load_chat_sessions(sessions_file)
    watch_configs = load_watch_configs(watch_configs_file)
    subscribers = load_subscribers(subscribers_file)
    paused_chats = load_paused_chats(paused_file)
    highest_update_id = offset or 0

    for item in updates:
        update_id = item.get("update_id")
        if isinstance(update_id, int) and update_id > highest_update_id:
            highest_update_id = update_id

        if item.get("callback_query"):
            handle_callback_query(
                token,
                item["callback_query"],
                sessions,
                watch_configs,
                sessions_file,
                watch_configs_file,
            )
            continue

        message = item.get("message") or item.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        chat_id_str = str(chat_id)
        save_text_file(chat_id_file, chat_id_str)
        if chat_id_str not in subscribers:
            subscribers.append(chat_id_str)
            save_subscribers(subscribers_file, subscribers)

        if handle_session_message(
            token,
            chat_id_str,
            text,
            sessions,
            watch_configs,
            sessions_file,
            watch_configs_file,
        ):
            continue

        if text.startswith("/start"):
            if chat_id_str in paused_chats:
                paused_chats = [item for item in paused_chats if item != chat_id_str]
                save_paused_chats(paused_file, paused_chats)
            watches = watch_configs.get(chat_id_str, [])
            if watches:
                send_telegram_message(
                    token,
                    chat_id_str,
                    "항공권 모니터링이 활성화되었습니다.\n"
                    "/add : 새 조회 추가\n"
                    "/list : 등록 목록 보기\n"
                    "/remove : 조회 삭제\n"
                    "/check : 지금 조회\n"
                    "/stop : 자동 알림 중지\n"
                    "/help : 도움말",
                )
            else:
                start_watch_wizard(token, chat_id_str, sessions, watch_configs, sessions_file)
        elif text.startswith("/add"):
            start_watch_wizard(token, chat_id_str, sessions, watch_configs, sessions_file)
        elif text.startswith("/list"):
            send_telegram_message(token, chat_id_str, format_watch_list(watch_configs.get(chat_id_str, [])))
        elif text.startswith("/remove"):
            watches = watch_configs.get(chat_id_str, [])
            if not watches:
                send_telegram_message(token, chat_id_str, "삭제할 조회 조건이 없습니다.")
            else:
                send_telegram_message(token, chat_id_str, "삭제할 조회 조건을 선택해주세요.", reply_markup=build_remove_keyboard(watches))
        elif text.startswith("/check"):
            watches = watch_configs.get(chat_id_str, [])
            if not watches:
                send_telegram_message(token, chat_id_str, "등록된 조회 조건이 없습니다.\n/add 로 새 조회를 추가하세요.")
            else:
                state = gather_state_for_watches(watches, args.wait_seconds, args.window_size)
                send_telegram_message(token, chat_id_str, format_state_summary(state))
        elif text.startswith("/stop"):
            if chat_id_str not in paused_chats:
                paused_chats.append(chat_id_str)
                save_paused_chats(paused_file, paused_chats)
            send_telegram_message(token, chat_id_str, "자동 알림이 중지되었습니다.\n재개하려면 /start 를 입력하세요.")
        elif text.startswith("/help"):
            send_telegram_message(
                token,
                chat_id_str,
                "/start : 자동 알림 시작 또는 재개\n"
                "/add : 새 항공편 조회 추가\n"
                "/list : 등록 목록 보기\n"
                "/remove : 조회 삭제\n"
                "/check : 지금 바로 조회\n"
                "/stop : 자동 알림 중지\n"
                "/help : 도움말",
            )

    if highest_update_id:
        save_offset(offset_file, highest_update_id + 1)


def maybe_send_scheduled(token: str, args: argparse.Namespace) -> None:
    slot = current_schedule_slot()
    if not slot:
        return

    schedule_state_file = Path(args.schedule_state_file)
    last_slot = Path(args.schedule_state_file).read_text().strip() if schedule_state_file.exists() else None
    if slot == last_slot:
        return

    subscribers = load_subscribers(Path(args.telegram_subscribers_file))
    if not subscribers:
        save_text_file(schedule_state_file, slot)
        return

    watch_configs = load_watch_configs(Path(args.watch_configs_file))
    signatures = load_signatures(Path(args.signatures_file))
    paused_chats = set(load_paused_chats(Path(args.paused_chats_file)))

    for chat_id in subscribers:
        if chat_id in paused_chats:
            continue
        watches = watch_configs.get(chat_id, [])
        if not watches:
            continue
        state = gather_state_for_watches(watches, args.wait_seconds, args.window_size)
        current_signature = availability_signature(state)
        previous_signature = signatures.get(chat_id)
        signatures[chat_id] = current_signature
        if not has_any_availability(state):
            continue
        if current_signature == previous_signature:
            continue
        send_telegram_message(token, chat_id, format_state_summary(state, header="[항공권 알림]"))

    save_signatures(Path(args.signatures_file), signatures)
    save_text_file(schedule_state_file, slot)


def main() -> None:
    args = parse_args()
    env_file = load_env_file()
    for key, value in env_file.items():
        os.environ.setdefault(key, value)

    token = get_telegram_token()
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAMBOT")

    while True:
        try:
            process_updates(token, args)
            maybe_send_scheduled(token, args)
        except Exception as exc:  # noqa: BLE001
            log_path = Path(".state") / "bot_runner_error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as fp:
                fp.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(exc).__name__}: {exc}\n")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
