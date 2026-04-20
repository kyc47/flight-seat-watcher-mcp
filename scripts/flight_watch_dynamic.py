#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from naver_flight_watch import KAC_AGENT_NAMES, KAC_COMPS, build_driver, load_env_file


MAX_WATCHES_PER_CHAT = 5
DEFAULT_POLL_WINDOW = "1440,2400"
DEFAULT_WAIT_SECONDS = 15
KOREAN_AIRPORTS = {
    "CJJ", "CJU", "GMP", "ICN", "PUS", "TAE", "USN", "RSU", "MWX",
    "YNY", "KPO", "KUV", "HIN", "WJU", "JJU", "PCN", "KAG", "CNU",
}


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def load_text_file(path: Path) -> str | None:
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value or None


def save_text_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def current_schedule_slot() -> str | None:
    now = time.localtime()
    if now.tm_min % 10 != 0:
        return None
    return f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d} {now.tm_hour:02d}:{now.tm_min:02d}"


def getenv_any(*keys: str) -> str:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def get_telegram_token() -> str:
    return getenv_any("TELEGRAM_BOT_TOKEN", "TELEGRAMBOT")


def get_telegram_updates(token: str, offset: int | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{quote(token, safe=':')}/getUpdates"
    if offset is not None:
        url = f"{url}?offset={offset}"
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def telegram_api_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{quote(token, safe=':')}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def send_telegram_message(
    token: str,
    chat_id: str,
    message: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api_call(token, "sendMessage", payload)


def answer_telegram_callback(token: str, callback_query_id: str, text: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return telegram_api_call(token, "answerCallbackQuery", payload)


def edit_telegram_message_reply_markup(
    token: str,
    chat_id: str,
    message_id: int,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup or {"inline_keyboard": []},
    }
    return telegram_api_call(token, "editMessageReplyMarkup", payload)


def load_offset(offset_file: Path) -> int | None:
    raw = load_text_file(offset_file)
    return int(raw) if raw else None


def save_offset(offset_file: Path, offset: int) -> None:
    save_text_file(offset_file, str(offset))


def load_subscribers(path: Path) -> list[str]:
    raw = load_json_file(path, [])
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def save_subscribers(path: Path, subscribers: list[str]) -> None:
    save_json_file(path, sorted(set(subscribers)))


def load_watch_configs(path: Path) -> dict[str, list[dict[str, Any]]]:
    raw = load_json_file(path, {})
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for chat_id, watches in raw.items():
        if not isinstance(watches, list):
            continue
        normalized[str(chat_id)] = [watch for watch in watches if isinstance(watch, dict)]
    return normalized


def save_watch_configs(path: Path, configs: dict[str, list[dict[str, Any]]]) -> None:
    save_json_file(path, configs)


def load_chat_sessions(path: Path) -> dict[str, dict[str, Any]]:
    raw = load_json_file(path, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(chat_id): value
        for chat_id, value in raw.items()
        if isinstance(value, dict)
    }


def save_chat_sessions(path: Path, sessions: dict[str, dict[str, Any]]) -> None:
    save_json_file(path, sessions)


def load_signatures(path: Path) -> dict[str, str]:
    raw = load_json_file(path, {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def save_signatures(path: Path, signatures: dict[str, str]) -> None:
    save_json_file(path, signatures)


def load_paused_chats(path: Path) -> list[str]:
    return load_subscribers(path)


def save_paused_chats(path: Path, chats: list[str]) -> None:
    save_subscribers(path, chats)


def normalize_airport_code(text: str) -> str | None:
    candidate = re.sub(r"[^A-Za-z]", "", text or "").upper()
    if len(candidate) != 3:
        return None
    return candidate


def parse_date_input(text: str) -> str | None:
    candidate = (text or "").strip().replace(".", "-").replace("/", "-")
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return None


def parse_time_range_input(text: str) -> str | None:
    candidate = (text or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{2}:\d{2}~\d{2}:\d{2}", candidate):
        return None
    start, end = candidate.split("~", 1)
    if start > end:
        return None
    return candidate


def format_date_text(date_text: str | None) -> str:
    if not date_text:
        return "-"
    return f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"


def infer_market(origin: str, destination: str) -> str:
    if origin in KOREAN_AIRPORTS and destination in KOREAN_AIRPORTS:
        return "domestic"
    return "international"


def build_trip_type_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "편도", "callback_data": "trip_type:oneway"},
            {"text": "왕복", "callback_data": "trip_type:roundtrip"},
        ]]
    }


def parse_departure_clock(label: str | None) -> str | None:
    if not label:
        return None
    match = re.match(r"(\d{2}:\d{2})", label.strip())
    return match.group(1) if match else None


def time_in_range(clock_text: str | None, range_text: str | None) -> bool:
    if not range_text:
        return True
    if not clock_text:
        return False
    start, end = range_text.split("~", 1)
    return start <= clock_text <= end


def watch_label(watch: dict[str, Any]) -> str:
    trip = "왕복" if watch["trip_type"] == "roundtrip" else "편도"
    market = "국내선" if watch["market"] == "domestic" else "국제선"
    return (
        f"{watch['origin']}->{watch['destination']} {market} {trip} "
        f"{format_date_text(watch['departure_date'])}"
        + (
            f" / {format_date_text(watch.get('return_date'))}"
            if watch["trip_type"] == "roundtrip"
            else ""
        )
    )


def watch_signature_key(watch: dict[str, Any]) -> str:
    return json.dumps(watch, ensure_ascii=False, sort_keys=True)


def build_watch_from_draft(draft: dict[str, Any], existing_count: int) -> dict[str, Any]:
    origin = draft["origin"]
    destination = draft["destination"]
    market = infer_market(origin, destination)
    watch = {
        "id": f"watch-{existing_count + 1}-{int(time.time())}",
        "origin": origin,
        "destination": destination,
        "market": market,
        "trip_type": draft["trip_type"],
        "departure_date": draft["departure_date"],
        "return_date": draft.get("return_date"),
        "departure_time_range": draft["departure_time_range"],
        "return_time_range": draft.get("return_time_range"),
        "adults": 1,
    }
    return watch


def build_naver_url(watch: dict[str, Any]) -> str:
    market = watch["market"]
    origin = watch["origin"]
    destination = watch["destination"]
    departure_date = watch["departure_date"]
    adults = int(watch.get("adults", 1))
    fare_type = "YC" if market == "domestic" else "Y"
    base = (
        f"https://flight.naver.com/flights/{market}/"
        f"{origin}:airport-{destination}:airport-{departure_date}"
    )
    if watch["trip_type"] == "roundtrip" and watch.get("return_date"):
        base += f"/{destination}:airport-{origin}:airport-{watch['return_date']}"
    return f"{base}?adult={adults}&fareType={fare_type}&isDirect=false"


def parse_visible_flights(driver: Any, watch: dict[str, Any]) -> list[dict[str, Any]]:
    cards = driver.find_elements("css selector", "div[class^='domestic_Flight__'], div[class*='Flight__']")
    flights: list[dict[str, Any]] = []
    for card in cards:
        airline_nodes = card.find_elements("xpath", ".//*[contains(@class,'airline_name__')]")
        airline = airline_nodes[0].text.strip() if airline_nodes else None
        if not airline:
            continue

        codes = [node.text.strip() for node in card.find_elements("xpath", ".//*[contains(@class,'route_code__')]") if node.text.strip()]
        times = [node.text.strip() for node in card.find_elements("xpath", ".//*[contains(@class,'route_time__')]") if node.text.strip()]
        if len(codes) < 2 or len(times) < 2:
            continue

        departure_text = f"{times[0]} {codes[0]}"
        arrival_text = f"{times[1]} {codes[1]}"
        return_departure = None
        return_arrival = None
        if len(codes) >= 4 and len(times) >= 4:
            return_departure = f"{times[2]} {codes[2]}"
            return_arrival = f"{times[3]} {codes[3]}"

        if not time_in_range(parse_departure_clock(departure_text), watch.get("departure_time_range")):
            continue
        if watch["trip_type"] == "roundtrip" and watch.get("return_time_range"):
            if return_departure and not time_in_range(parse_departure_clock(return_departure), watch["return_time_range"]):
                continue

        duration_nodes = card.find_elements("xpath", ".//*[contains(@class,'route_info__')]")
        duration = duration_nodes[0].text.strip() if duration_nodes else None
        carrier_nodes = card.find_elements("xpath", ".//*[contains(@class,'airline_info__')]")
        carrier_note = carrier_nodes[0].text.strip() if carrier_nodes else None
        price_items = card.find_elements(
            "xpath",
            ".//div[contains(@class,'domestic_prices__')]/div[contains(@class,'domestic_item__')]"
            "| .//div[contains(@class,'prices__')]/div[contains(@class,'item__')]",
        )
        seat = None
        price = None
        if price_items:
            primary = price_items[0]
            seat_nodes = primary.find_elements("xpath", ".//*[contains(@class,'domestic_type__') or contains(@class,'type__')]")
            price_nodes = primary.find_elements("xpath", ".//*[contains(@class,'domestic_num__') or contains(@class,'num__')]")
            seat = seat_nodes[0].text.strip() if seat_nodes else None
            raw_price = price_nodes[0].text.strip() if price_nodes else ""
            price = re.sub(r"[^0-9]", "", raw_price)

        flights.append(
            {
                "airline": airline,
                "carrier_note": carrier_note,
                "departure": departure_text,
                "arrival": arrival_text,
                "return_departure": return_departure,
                "return_arrival": return_arrival,
                "duration": duration,
                "seat": seat,
                "price": price,
            }
        )

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for flight in flights:
        key = json.dumps(flight, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(flight)
    return unique


def extract_search_events(driver: Any, url: str, wait_seconds: int, watch: dict[str, Any]) -> dict[str, Any]:
    driver.get(url)
    deadline = time.time() + wait_seconds
    flights: list[dict[str, Any]] = []
    no_results = False
    body_text = ""
    while time.time() < deadline:
        body = driver.find_elements("tag name", "body")
        body_text = body[0].text if body else ""
        no_results = "검색된 항공편이 없습니다" in body_text
        flights = parse_visible_flights(driver, watch)
        if no_results or flights:
            break
        time.sleep(1)
    return {
        "body_text": body_text,
        "no_results": no_results,
        "flights": flights,
    }


def fetch_kac_state(watch: dict[str, Any]) -> dict[str, Any] | None:
    if watch["market"] != "domestic":
        return None

    available_agents: list[str] = []
    agent_statuses: dict[str, str] = {}
    for comp in KAC_COMPS:
        form = {
            "pDep": watch["origin"],
            "pArr": watch["destination"],
            "pDepDate": watch["departure_date"],
            "pArrDate": watch.get("return_date") or watch["departure_date"],
            "pAdt": str(int(watch.get("adults", 1))),
            "pChd": "0",
            "pInf": "0",
            "pSeat": "ALL",
            "comp": comp,
            "carCode": "ALL",
        }
        body = urlencode(form).encode()
        req = Request(
            "https://www.airport.co.kr/booking/ajaxf/frAirticketSvc/getData.do",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        try:
            with urlopen(req, timeout=20) as response:
                payload = json.load(response)
        except Exception as exc:  # noqa: BLE001
            agent_statuses[KAC_AGENT_NAMES[comp]] = f"error:{type(exc).__name__}"
            continue

        header = payload.get("data", {}).get("header", {})
        data_items = payload.get("data", {}).get("data", [])
        cnt = header.get("cnt", 0) or 0
        if isinstance(cnt, str) and cnt.isdigit():
            cnt = int(cnt)
        if data_items or cnt > 0:
            available_agents.append(KAC_AGENT_NAMES[comp])
            agent_statuses[KAC_AGENT_NAMES[comp]] = "available"
        else:
            agent_statuses[KAC_AGENT_NAMES[comp]] = str(header.get("errorDesc") or header.get("errorCode") or "none")

    return {
        "available": bool(available_agents),
        "available_agents": sorted(set(available_agents)),
        "agent_statuses": agent_statuses,
    }


def gather_watch_state(watch: dict[str, Any], wait_seconds: int, window_size: str) -> dict[str, Any]:
    driver = build_driver(window_size)
    url = build_naver_url(watch)
    try:
        result = extract_search_events(driver, url, wait_seconds, watch)
    finally:
        driver.quit()

    return {
        "watch": watch,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "naver": {
            "url": url,
            "no_results": result["no_results"],
            "flights": result["flights"],
        },
        "kac": fetch_kac_state(watch),
    }


def gather_state_for_watches(watches: list[dict[str, Any]], wait_seconds: int, window_size: str) -> dict[str, Any]:
    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": [gather_watch_state(watch, wait_seconds, window_size) for watch in watches],
    }


def has_any_availability(state: dict[str, Any]) -> bool:
    for item in state.get("items", []):
        if item.get("naver", {}).get("flights"):
            return True
        if item.get("kac", {}).get("available"):
            return True
    return False


def availability_signature(state: dict[str, Any]) -> str:
    items = []
    for item in state.get("items", []):
        items.append(
            {
                "watch": item.get("watch", {}),
                "naver": item.get("naver", {}).get("flights", []),
                "kac": {
                    "available": item.get("kac", {}).get("available", False) if item.get("kac") else False,
                    "available_agents": item.get("kac", {}).get("available_agents", []) if item.get("kac") else [],
                },
            }
        )
    return json.dumps(items, ensure_ascii=False, sort_keys=True)


def format_watch_list(watches: list[dict[str, Any]]) -> str:
    if not watches:
        return "등록된 조회 조건이 없습니다.\n/add 로 새 항공편 조회를 추가하세요."
    lines = [f"등록된 조회 조건 {len(watches)}/{MAX_WATCHES_PER_CHAT}"]
    for idx, watch in enumerate(watches, start=1):
        lines.append(f"{idx}. {watch_label(watch)}")
        lines.append(f"   출발 시간대: {watch.get('departure_time_range') or '-'}")
        if watch["trip_type"] == "roundtrip":
            lines.append(f"   복귀 시간대: {watch.get('return_time_range') or '-'}")
    return "\n".join(lines)


def format_state_summary(state: dict[str, Any], header: str = "[수동확인]") -> str:
    lines = [f"{header} {state['checked_at']}", ""]
    for idx, item in enumerate(state.get("items", []), start=1):
        watch = item["watch"]
        lines.append(f"[{idx}] {watch_label(watch)}")
        lines.append(f"출발 시간대: {watch.get('departure_time_range') or '-'}")
        if watch["trip_type"] == "roundtrip":
            lines.append(f"복귀 시간대: {watch.get('return_time_range') or '-'}")
        naver = item["naver"]
        kac = item.get("kac")
        lines.append(
            f"네이버: {'있음' if naver['flights'] else ('없음' if naver['no_results'] else '확인불완전')} "
            f"({len(naver['flights'])}건)"
        )
        if kac is None:
            lines.append("공항공사: 국제선 미지원")
        else:
            lines.append(
                f"공항공사: {'있음' if kac['available'] else '없음'} "
                f"({', '.join(kac['available_agents']) if kac['available_agents'] else '-'})"
            )
        for flight_index, flight in enumerate(naver["flights"][:3], start=1):
            flight_line = (
                f"{flight_index}. {flight.get('airline') or '-'} "
                f"{flight.get('departure') or '-'} -> {flight.get('arrival') or '-'}"
            )
            if flight.get("return_departure"):
                flight_line += f" / {flight['return_departure']} -> {flight.get('return_arrival') or '-'}"
            if flight.get("seat"):
                flight_line += f" {flight['seat']}"
            if flight.get("price"):
                flight_line += f" {flight['price']}원"
            lines.append(flight_line)
        lines.append(f"네이버 URL: {naver['url']}")
        lines.append("")
    if len(lines) == 2:
        lines.append("등록된 조회 조건이 없습니다.")
    return "\n".join(lines).strip()


def render_watch_created_message(watch: dict[str, Any], count: int) -> str:
    lines = [
        "조회 조건이 저장되었습니다.",
        f"등록 수: {count}/{MAX_WATCHES_PER_CHAT}",
        watch_label(watch),
        f"출발 시간대: {watch.get('departure_time_range') or '-'}",
    ]
    if watch["trip_type"] == "roundtrip":
        lines.append(f"복귀 시간대: {watch.get('return_time_range') or '-'}")
    lines.append("10분마다 자동 확인하고, 표가 생기면 알림을 보냅니다.")
    return "\n".join(lines)
