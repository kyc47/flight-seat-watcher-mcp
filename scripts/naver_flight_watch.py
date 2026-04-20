#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
except ModuleNotFoundError:
    webdriver = None
    Options = None
    By = None


DEFAULT_URL = (
    "https://flight.naver.com/flights/domestic/"
    "CJJ:airport-CJU:airport-20260430/"
    "CJU:airport-CJJ:airport-20260504?adult=2&fareType=YC&isDirect=false"
)
API_URL = "https://flight-api.naver.com/flight/domestic/searchFlights"
ORIGINS = ["CJJ", "TAE"]
DESTINATION = "CJU"
DEPARTURE_DATES = ["20260501"]
RETURN_DATE = "20260504"
KAC_COMPS = ["WT", "OT", "LT", "JD", "SM", "YB2", "JC"]
KAC_AGENT_NAMES = {
    "WT": "webtour",
    "OT": "onlinetour",
    "LT": "lotte",
    "JD": "jejudo",
    "SM": "sunmin",
    "YB2": "yellowballoon",
    "JC": "jejucom",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--state-file",
        default=str(Path(".state") / "naver_flight_watch_state.json"),
    )
    parser.add_argument("--wait-seconds", type=int, default=15)
    parser.add_argument("--window-size", default="1440,2400")
    parser.add_argument("--send-telegram", action="store_true")
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
    return parser.parse_args()


def load_env_file(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return env
    for line in file_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def getenv_any(*keys: str) -> str:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def get_telegram_token() -> str:
    return getenv_any("TELEGRAM_BOT_TOKEN", "TELEGRAMBOT")


def build_driver(window_size: str) -> webdriver.Chrome:
    if webdriver is None or Options is None:
        raise RuntimeError("selenium is not installed. Install it before running flight checks.")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--incognito")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disk-cache-size=0")
    options.add_argument("--media-cache-size=0")
    options.add_argument("--disable-cache")
    options.add_argument(f"--window-size={window_size}")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    chrome_binary = getenv_any("CHROME_BINARY", "GOOGLE_CHROME_BIN")
    if chrome_binary:
        options.binary_location = chrome_binary
    return webdriver.Chrome(options=options)


def safe_json_loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def summarize_flight(payload: dict[str, Any]) -> dict[str, Any]:
    segments = payload.get("segments") or payload.get("segmentList") or []
    first_segment = segments[0] if segments else {}
    fares = payload.get("fares") or payload.get("fareList") or []
    first_fare = fares[0] if fares else {}
    airline = (
        payload.get("airlineName")
        or first_segment.get("airlineName")
        or first_segment.get("marketingAirlineName")
    )
    flight_no = (
        payload.get("flightNo")
        or first_segment.get("flightNo")
        or first_segment.get("flightNumber")
    )
    departure = first_segment.get("departureTime") or first_segment.get("departureDateTime")
    arrival = first_segment.get("arrivalTime") or first_segment.get("arrivalDateTime")
    price = (
        payload.get("minFare")
        or payload.get("fare")
        or first_fare.get("fare")
        or first_fare.get("price")
        or first_fare.get("totalFare")
    )
    seller = (
        first_fare.get("sellerName")
        or first_fare.get("agencyName")
        or first_fare.get("vendorName")
    )
    return {
        "airline": airline,
        "flight_no": flight_no,
        "departure": departure,
        "arrival": arrival,
        "price": price,
        "seller": seller,
    }


def normalize_results(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []

    candidates = []
    if isinstance(body.get("flights"), list):
        candidates = body["flights"]
    elif isinstance(body.get("items"), list):
        candidates = body["items"]
    elif isinstance(body.get("results"), list):
        candidates = body["results"]
    elif isinstance(body.get("data"), dict):
        data = body["data"]
        for key in ("flights", "items", "results"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break

    return [summarize_flight(item) for item in candidates if isinstance(item, dict)]


def extract_search_events(
    driver: webdriver.Chrome,
    url: str,
    wait_seconds: int,
    departure_date: str,
) -> dict[str, Any]:
    driver.get(url)
    deadline = time.time() + wait_seconds
    flights: list[dict[str, Any]] = []
    no_results = False
    body_text = ""

    while time.time() < deadline:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        no_results = "검색된 항공편이 없습니다" in body_text
        flights = parse_visible_flights(driver, departure_date)
        if no_results or flights:
            break
        time.sleep(1)

    return {
        "body_text": body_text,
        "no_results": no_results,
        "flights": flights,
    }


def text_or_none(element: Any, xpath: str) -> str | None:
    found = element.find_elements(By.XPATH, xpath)
    if not found:
        return None
    text = found[0].text.strip()
    return text or None


def is_saturday_evening_only(departure_date: str) -> bool:
    return datetime.strptime(departure_date, "%Y%m%d").weekday() == 5


def should_include_flight(departure_date: str, departure_text: str) -> bool:
    if not is_saturday_evening_only(departure_date):
        return True
    match = re.match(r"(\d{2}):(\d{2})\s", departure_text)
    if not match:
        return False
    hour = int(match.group(1))
    minute = int(match.group(2))
    return (hour, minute) >= (20, 0)


def display_date(departure_date: str) -> str:
    return f"{departure_date[:4]}-{departure_date[4:6]}-{departure_date[6:8]}"


def parse_visible_flights(driver: webdriver.Chrome, departure_date: str) -> list[dict[str, Any]]:
    cards = driver.find_elements(By.CSS_SELECTOR, "div[class^='domestic_Flight__']")
    flights: list[dict[str, Any]] = []

    for card in cards:
        airline = text_or_none(card, ".//*[contains(@class,'airline_name__')]")
        codes = [node.text.strip() for node in card.find_elements(By.XPATH, ".//*[contains(@class,'route_code__')]")]
        times = [node.text.strip() for node in card.find_elements(By.XPATH, ".//*[contains(@class,'route_time__')]")]
        duration = text_or_none(card, ".//*[contains(@class,'route_info__')]")
        carrier_note = text_or_none(card, ".//*[contains(@class,'airline_info__')]")
        price_items = card.find_elements(
            By.XPATH,
            ".//div[contains(@class,'domestic_prices__')]/div[contains(@class,'domestic_item__')]",
        )
        if not price_items:
            continue
        primary_price = price_items[0]
        seat = text_or_none(primary_price, ".//*[contains(@class,'domestic_type__')]")
        price = text_or_none(primary_price, ".//*[contains(@class,'domestic_num__')]")

        if not (airline and len(codes) >= 2 and len(times) >= 2 and seat and price):
            continue

        departure_text = f"{times[0]} {codes[0]}"
        if not should_include_flight(departure_date, departure_text):
            continue

        flights.append(
            {
                "airline": airline,
                "carrier_note": carrier_note,
                "departure": departure_text,
                "arrival": f"{times[1]} {codes[1]}",
                "duration": duration,
                "seat": seat,
                "price": re.sub(r"[^0-9]", "", price),
            }
        )

    unique_flights = []
    seen = set()
    for flight in flights:
        key = json.dumps(flight, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_flights.append(flight)
    return unique_flights


def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text())


def save_state(state_file: Path, payload: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def load_offset(offset_file: Path) -> int | None:
    if not offset_file.exists():
        return None
    raw = offset_file.read_text().strip()
    return int(raw) if raw else None


def save_offset(offset_file: Path, offset: int) -> None:
    offset_file.parent.mkdir(parents=True, exist_ok=True)
    offset_file.write_text(str(offset))


def load_text_file(path: Path) -> str | None:
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value or None


def save_text_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


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


def load_subscribers(path: Path) -> list[str]:
    raw = load_json_file(path, [])
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def save_subscribers(path: Path, subscribers: list[str]) -> None:
    save_json_file(path, sorted(set(subscribers)))


def current_schedule_slot() -> str | None:
    now = time.localtime()
    if now.tm_min % 10 != 0:
        return None
    return f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d} {now.tm_hour:02d}:{now.tm_min:02d}"


def build_naver_url(origin: str, departure_date: str) -> str:
    return (
        "https://flight.naver.com/flights/domestic/"
        f"{origin}:airport-{DESTINATION}:airport-{departure_date}/"
        f"{DESTINATION}:airport-{origin}:airport-{RETURN_DATE}"
        "?adult=2&fareType=YC&isDirect=false"
    )


def fetch_kac_origin(origin: str, departure_date: str) -> dict[str, Any]:
    available_agents: list[str] = []
    agent_statuses: dict[str, str] = {}

    for comp in KAC_COMPS:
        form = {
            "pDep": origin,
            "pArr": DESTINATION,
            "pDepDate": departure_date,
            "pArrDate": RETURN_DATE,
            "pAdt": "2",
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
            error_desc = header.get("errorDesc") or header.get("errorCode") or "none"
            agent_statuses[KAC_AGENT_NAMES[comp]] = str(error_desc)

    return {
        "origin": origin,
        "departure_date": departure_date,
        "available": bool(available_agents),
        "available_agents": sorted(set(available_agents)),
        "agent_statuses": agent_statuses,
    }


def build_result(result: dict[str, Any], url: str) -> dict[str, Any]:
    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": url,
        "no_results": result["no_results"],
        "flights": result["flights"],
    }


def gather_current_state(wait_seconds: int, window_size: str) -> dict[str, Any]:
    state = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "naver": {},
        "kac": {},
    }

    for origin in ORIGINS:
        state["naver"][origin] = {}
        for departure_date in DEPARTURE_DATES:
            driver = build_driver(window_size)
            try:
                url = build_naver_url(origin, departure_date)
                result = extract_search_events(driver, url, wait_seconds, departure_date)
            finally:
                driver.quit()
            payload = build_result(result, url)
            payload["departure_date"] = departure_date
            state["naver"][origin][departure_date] = payload

    for origin in ORIGINS:
        state["kac"][origin] = {}
        for departure_date in DEPARTURE_DATES:
            state["kac"][origin][departure_date] = fetch_kac_origin(origin, departure_date)

    return state


def flatten_naver_flights(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for origin, date_map in state.get("naver", {}).items():
        for departure_date, payload in date_map.items():
            for flight in payload.get("flights", []):
                merged = dict(flight)
                merged["origin"] = origin
                merged["departure_date"] = departure_date
                items.append(merged)
    return items


def has_any_availability(state: dict[str, Any]) -> bool:
    if flatten_naver_flights(state):
        return True
    for origin in ORIGINS:
        for departure_date in DEPARTURE_DATES:
            if state.get("kac", {}).get(origin, {}).get(departure_date, {}).get("available"):
                return True
    return False


def availability_signature(state: dict[str, Any]) -> str:
    payload = {
        "naver": sorted(
            flatten_naver_flights(state),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
        "kac": {
            origin: {
                departure_date: {
                    "available": state.get("kac", {}).get(origin, {}).get(departure_date, {}).get("available", False),
                    "available_agents": state.get("kac", {}).get(origin, {}).get(departure_date, {}).get("available_agents", []),
                }
                for departure_date in DEPARTURE_DATES
            }
            for origin in ORIGINS
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def diff_flights(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    prev = {
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in flatten_naver_flights(previous)
    }
    added = []
    for item in flatten_naver_flights(current):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in prev:
            added.append(item)
    return added


def print_summary(current: dict[str, Any], added: list[dict[str, Any]]) -> None:
    print(f"checked_at={current['checked_at']}")
    for origin in ORIGINS:
        for departure_date in DEPARTURE_DATES:
            naver = current["naver"][origin][departure_date]
            kac = current["kac"][origin][departure_date]
            print(
                f"naver_{origin}_{departure_date}=no_results:{naver['no_results']} "
                f"visible_flights:{len(naver['flights'])} url:{naver['url']}"
            )
            print(
                f"kac_{origin}_{departure_date}=available:{kac['available']} "
                f"agents:{','.join(kac['available_agents']) if kac['available_agents'] else '-'}"
            )

    if added:
        print("new_flights_detected=true")
        for idx, flight in enumerate(added, start=1):
            print(
                f"[new {idx}] origin={flight.get('origin')} date={flight.get('departure_date')} airline={flight.get('airline')} "
                f"departure={flight.get('departure')} arrival={flight.get('arrival')} "
                f"seat={flight.get('seat')} price={flight.get('price')}"
            )
        return

    if flatten_naver_flights(current) or any(
        current["kac"][origin][departure_date]["available"]
        for origin in ORIGINS
        for departure_date in DEPARTURE_DATES
    ):
        print("new_flights_detected=false")
        print(
            "availability_summary="
            + ",".join(
                f"{origin}-{departure_date}:"
                f"N{len(current['naver'][origin][departure_date]['flights'])}/"
                f"K{int(current['kac'][origin][departure_date]['available'])}"
                for origin in ORIGINS
                for departure_date in DEPARTURE_DATES
            )
        )
        return

    print("new_flights_detected=false")
    print("note=no_flights_visible")


def resolve_telegram_chat_id(token: str, explicit_chat_id: str | None) -> str | None:
    if explicit_chat_id:
        return explicit_chat_id

    payload = get_telegram_updates(token)

    for item in reversed(payload.get("result", [])):
        message = item.get("message") or item.get("channel_post") or item.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            return str(chat_id)
    return None


def get_telegram_updates(token: str, offset: int | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{quote(token, safe=':')}/getUpdates"
    if offset is not None:
        url = f"{url}?offset={offset}"
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def send_telegram_message(token: str, chat_id: str, message: str) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{quote(token, safe=':')}/sendMessage"
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
    ).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def format_telegram_message(current: dict[str, Any], added: list[dict[str, Any]]) -> str:
    lines = [
        f"[항공권 체크] {current['checked_at']}",
        f"조건: 왕복 검색 ({', '.join(display_date(date) for date in DEPARTURE_DATES)} 출발 / {display_date(RETURN_DATE)} 복귀)",
        "상세 추적: 현재는 가는 편 기준",
        "토요일 출발은 20:00 이후만 표시",
        "",
    ]

    for origin in ORIGINS:
        for departure_date in DEPARTURE_DATES:
            naver = current["naver"][origin][departure_date]
            kac = current["kac"][origin][departure_date]
            lines.append(f"[{origin} / {display_date(departure_date)}]")
            lines.append(
                f"네이버: {'있음' if naver['flights'] else ('없음' if naver['no_results'] else '확인불완전')} "
                f"({len(naver['flights'])}건)"
            )
            lines.append(
                f"공항공사: {'있음' if kac['available'] else '없음'} "
                f"({', '.join(kac['available_agents']) if kac['available_agents'] else '-'})"
            )
            if naver["flights"]:
                for idx, flight in enumerate(naver["flights"][:3], start=1):
                    lines.append(
                        f"{idx}. {flight['airline']} "
                        f"{flight['departure']} -> {flight['arrival']} "
                        f"{flight['seat']} {flight['price']}원"
                    )
                lines.append(f"네이버 URL: {naver['url']}")
            lines.append("")

    if added:
        lines.append("[새로 잡힌 항공편]")
        for idx, flight in enumerate(added[:10], start=1):
            lines.append(
                f"{idx}. {flight['origin']} {display_date(flight['departure_date'])} {flight['airline']} "
                f"{flight['departure']} -> {flight['arrival']} "
                f"{flight['seat']} {flight['price']}원"
            )
    else:
        lines.append("새 항공편: 없음")

    return "\n".join(lines)


def format_manual_check_message(current: dict[str, Any]) -> str:
    lines = [
        f"[수동확인] {current['checked_at']}",
        f"조건: 왕복 검색 ({', '.join(display_date(date) for date in DEPARTURE_DATES)} 출발 / {display_date(RETURN_DATE)} 복귀)",
        "상세 추적: 현재는 가는 편 기준",
        "토요일 출발은 20:00 이후만 표시",
        "",
    ]
    all_flights = flatten_naver_flights(current)
    for origin in ORIGINS:
        for departure_date in DEPARTURE_DATES:
            naver = current["naver"][origin][departure_date]
            kac = current["kac"][origin][departure_date]
            lines.append(f"[{origin} / {display_date(departure_date)}]")
            lines.append(
                f"네이버: {'있음' if naver['flights'] else ('없음' if naver['no_results'] else '확인불완전')} "
                f"({len(naver['flights'])}건)"
            )
            lines.append(
                f"공항공사: {'있음' if kac['available'] else '없음'} "
                f"({', '.join(kac['available_agents']) if kac['available_agents'] else '-'})"
            )
            if naver["flights"]:
                lines.append(f"네이버 URL: {naver['url']}")
            lines.append("")
    if all_flights:
        lines.append(f"[네이버 상세 항공편] {len(all_flights)}건")
        for idx, flight in enumerate(all_flights[:10], start=1):
            lines.append(
                f"{idx}. {flight['origin']} {display_date(flight['departure_date'])} {flight['airline']} "
                f"{flight['departure']} -> {flight['arrival']} "
                f"{flight['seat']} {flight['price']}원"
            )
    else:
        lines.append("보이는 항공편: 없음")
    return "\n".join(lines)


def process_telegram_commands(
    token: str,
    offset_file: Path,
    chat_id_file: Path,
    subscribers_file: Path,
    current_state: dict[str, Any],
) -> tuple[bool, str | None]:
    offset = load_offset(offset_file)
    payload = get_telegram_updates(token, offset)
    updates = payload.get("result", [])
    if not updates:
        return False, None

    highest_update_id = offset or 0
    handled = False
    subscribers = load_subscribers(subscribers_file)

    for item in updates:
        update_id = item.get("update_id")
        if isinstance(update_id, int) and update_id > highest_update_id:
            highest_update_id = update_id

        message = item.get("message") or item.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            chat_id_str = str(chat_id)
            save_text_file(chat_id_file, chat_id_str)
            if chat_id_str not in subscribers:
                pass
        else:
            chat_id_str = None

        if text.startswith("/subscribe") and chat_id_str is not None:
            if chat_id_str not in subscribers:
                subscribers.append(chat_id_str)
                save_subscribers(subscribers_file, subscribers)
            send_telegram_message(
                token,
                chat_id_str,
                "구독 완료\n10분마다 항공권 현황을 보내드립니다.\n수동 확인은 /check, 구독 해지는 /unsubscribe 입니다.",
            )
            handled = True
        elif text.startswith("/unsubscribe") and chat_id_str is not None:
            subscribers = [item for item in subscribers if item != chat_id_str]
            save_subscribers(subscribers_file, subscribers)
            send_telegram_message(token, chat_id_str, "구독 해제 완료")
            handled = True
        elif text.startswith("/check") and chat_id_str is not None:
            send_telegram_message(token, chat_id_str, format_manual_check_message(current_state))
            handled = True

    if highest_update_id:
        save_offset(offset_file, highest_update_id + 1)
    return handled, str(highest_update_id) if highest_update_id else None


if __name__ == "__main__":
    args = parse_args()
    env_file = load_env_file()
    for key, value in env_file.items():
        os.environ.setdefault(key, value)
    state_file = Path(args.state_file)
    previous_state = load_state(state_file)
    current_state = gather_current_state(args.wait_seconds, args.window_size)
    added_flights = diff_flights(previous_state, current_state)
    save_state(state_file, current_state)
    print_summary(current_state, added_flights)

    if args.send_telegram:
        token = get_telegram_token()
        chat_id_file = Path(args.telegram_chat_id_file)
        subscribers_file = Path(args.telegram_subscribers_file)
        schedule_state_file = Path(args.schedule_state_file)
        explicit_chat_id = (
            getenv_any("TELEGRAM_CHAT_ID")
            or load_text_file(chat_id_file)
            or None
        )
        offset_file = Path(args.telegram_offset_file)
        if not token:
            print("telegram_sent=false")
            print("telegram_note=missing_token")
        else:
            try:
                handled_command, update_marker = process_telegram_commands(
                    token,
                    offset_file,
                    chat_id_file,
                    subscribers_file,
                    current_state,
                )
                if handled_command:
                    print("telegram_command_handled=true")
                elif update_marker is not None:
                    print("telegram_command_handled=false")
                    print("telegram_note=no_check_command")

                subscribers = load_subscribers(subscribers_file)
                if explicit_chat_id and explicit_chat_id not in subscribers:
                    subscribers.append(explicit_chat_id)
                    save_subscribers(subscribers_file, subscribers)

                if not subscribers:
                    print("telegram_sent=false")
                    print("telegram_note=no_subscribers_use_subscribe")
                else:
                    slot = current_schedule_slot()
                    last_slot = load_text_file(schedule_state_file)
                    should_send = False
                    if slot and slot != last_slot:
                        should_send = True
                        save_text_file(schedule_state_file, slot)
                        print(f"telegram_schedule_slot={slot}")
                    elif added_flights:
                        should_send = True
                        print("telegram_schedule_slot=change_detected")

                    if should_send:
                        message = format_telegram_message(current_state, added_flights)
                        sent_count = 0
                        for subscriber in subscribers:
                            send_telegram_message(token, subscriber, message)
                            sent_count += 1
                        print("telegram_sent=true")
                        print(f"telegram_sent_count={sent_count}")
                    else:
                        print("telegram_sent=false")
                        print("telegram_note=not_scheduled_slot")
            except (HTTPError, URLError, TimeoutError) as exc:
                print("telegram_sent=false")
                print(f"telegram_note={type(exc).__name__}")
            except Exception as exc:  # noqa: BLE001
                print("telegram_sent=false")
                print(f"telegram_note={type(exc).__name__}")

    if added_flights:
        sys.exit(10)
