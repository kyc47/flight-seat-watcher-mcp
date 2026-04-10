#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


DEFAULT_URL = (
    "https://flight.naver.com/flights/domestic/"
    "CJJ:airport-CJU:airport-20260501/"
    "CJU:airport-CJJ:airport-20260504?adult=2&fareType=YC&isDirect=false"
)
API_URL = "https://flight-api.naver.com/flight/domestic/searchFlights"
ORIGINS = ["CJJ", "TAE"]
DESTINATION = "CJU"
DEPARTURE_DATE = "20260501"
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


def build_driver(window_size: str) -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={window_size}")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
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


def extract_search_events(driver: webdriver.Chrome, url: str, wait_seconds: int) -> dict[str, Any]:
    driver.get(url)
    deadline = time.time() + wait_seconds
    flights: list[dict[str, Any]] = []
    no_results = False
    body_text = ""

    while time.time() < deadline:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        no_results = "검색된 항공편이 없습니다" in body_text
        flights = parse_visible_flights(driver)
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


def parse_visible_flights(driver: webdriver.Chrome) -> list[dict[str, Any]]:
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

        flights.append(
            {
                "airline": airline,
                "carrier_note": carrier_note,
                "departure": f"{times[0]} {codes[0]}",
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


def build_naver_url(origin: str) -> str:
    return (
        "https://flight.naver.com/flights/domestic/"
        f"{origin}:airport-{DESTINATION}:airport-{DEPARTURE_DATE}/"
        f"{DESTINATION}:airport-{origin}:airport-{RETURN_DATE}"
        "?adult=2&fareType=YC&isDirect=false"
    )


def fetch_kac_origin(origin: str) -> dict[str, Any]:
    available_agents: list[str] = []
    agent_statuses: dict[str, str] = {}

    for comp in KAC_COMPS:
        form = {
            "pDep": origin,
            "pArr": DESTINATION,
            "pDepDate": DEPARTURE_DATE,
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
        driver = build_driver(window_size)
        try:
            result = extract_search_events(driver, build_naver_url(origin), wait_seconds)
        finally:
            driver.quit()
        state["naver"][origin] = build_result(result, build_naver_url(origin))

    for origin in ORIGINS:
        state["kac"][origin] = fetch_kac_origin(origin)

    return state


def flatten_naver_flights(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for origin, payload in state.get("naver", {}).items():
        for flight in payload.get("flights", []):
            merged = dict(flight)
            merged["origin"] = origin
            items.append(merged)
    return items


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
        naver = current["naver"][origin]
        kac = current["kac"][origin]
        print(
            f"naver_{origin}=no_results:{naver['no_results']} "
            f"visible_flights:{len(naver['flights'])} url:{naver['url']}"
        )
        print(
            f"kac_{origin}=available:{kac['available']} "
            f"agents:{','.join(kac['available_agents']) if kac['available_agents'] else '-'}"
        )

    if added:
        print("new_flights_detected=true")
        for idx, flight in enumerate(added, start=1):
            print(
                f"[new {idx}] origin={flight.get('origin')} airline={flight.get('airline')} "
                f"departure={flight.get('departure')} arrival={flight.get('arrival')} "
                f"seat={flight.get('seat')} price={flight.get('price')}"
            )
        return

    if flatten_naver_flights(current) or any(current["kac"][origin]["available"] for origin in ORIGINS):
        print("new_flights_detected=false")
        print(
            "availability_summary="
            + ",".join(
                f"{origin}:"
                f"N{len(current['naver'][origin]['flights'])}/"
                f"K{int(current['kac'][origin]['available'])}"
                for origin in ORIGINS
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
        "",
    ]

    for origin in ORIGINS:
        naver = current["naver"][origin]
        kac = current["kac"][origin]
        lines.append(f"[{origin}]")
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
                f"{idx}. {flight['origin']} {flight['airline']} "
                f"{flight['departure']} -> {flight['arrival']} "
                f"{flight['seat']} {flight['price']}원"
            )
    else:
        lines.append("새 항공편: 없음")

    return "\n".join(lines)


def format_manual_check_message(current: dict[str, Any]) -> str:
    lines = [
        f"[수동확인] {current['checked_at']}",
        "",
    ]
    all_flights = flatten_naver_flights(current)
    for origin in ORIGINS:
        naver = current["naver"][origin]
        kac = current["kac"][origin]
        lines.append(f"[{origin}]")
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
                f"{idx}. {flight['origin']} {flight['airline']} "
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
    current_state: dict[str, Any],
) -> tuple[bool, str | None]:
    offset = load_offset(offset_file)
    payload = get_telegram_updates(token, offset)
    updates = payload.get("result", [])
    if not updates:
        return False, None

    highest_update_id = offset or 0
    handled = False

    for item in updates:
        update_id = item.get("update_id")
        if isinstance(update_id, int) and update_id > highest_update_id:
            highest_update_id = update_id

        message = item.get("message") or item.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            save_text_file(chat_id_file, str(chat_id))
        if text.startswith("/check") and chat_id is not None:
            send_telegram_message(token, str(chat_id), format_manual_check_message(current_state))
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
        token = os.environ.get("TELEGRAMBOT", "").strip()
        chat_id_file = Path(args.telegram_chat_id_file)
        explicit_chat_id = (
            os.environ.get("TELEGRAM_CHAT_ID", "").strip()
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
                    current_state,
                )
                if handled_command:
                    print("telegram_command_handled=true")
                elif update_marker is not None:
                    print("telegram_command_handled=false")
                    print("telegram_note=no_check_command")

                chat_id = resolve_telegram_chat_id(token, explicit_chat_id)
                if not chat_id:
                    print("telegram_sent=false")
                    print("telegram_note=missing_chat_id_send_message_to_bot_once")
                else:
                    message = format_telegram_message(current_state, added_flights)
                    send_telegram_message(token, chat_id, message)
                    print("telegram_sent=true")
            except (HTTPError, URLError, TimeoutError) as exc:
                print("telegram_sent=false")
                print(f"telegram_note={type(exc).__name__}")
            except Exception as exc:  # noqa: BLE001
                print("telegram_sent=false")
                print(f"telegram_note={type(exc).__name__}")

    if added_flights:
        sys.exit(10)
