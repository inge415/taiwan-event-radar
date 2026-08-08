from __future__ import annotations

import argparse
import copy
import datetime as dt
import email.utils
import hashlib
import html
import http.client
import json
import re
import ssl
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
OUTPUT = ROOT / "output"
STATE = OUTPUT / "state.json"
LATEST = OUTPUT / "latest.json"
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))

OPENTIX_API = "https://csm.api.opentix.life"
TICKETPLUS_S3 = "https://apis.ticketplus.com.tw/config/api/v1/getS3?path={path}"

SALE_KEYWORDS = (
    "全面啟售",
    "全面開賣",
    "正式開賣",
    "一般販售",
    "一般售票",
    "一般開賣",
    "開賣",
    "啟售",
    "售票",
)
EXCLUDE_HINTS = (
    "電影",
    "體育",
    "課程",
    "講座",
    "親子",
    "優惠券",
    "登記抽選",
    "vip pass",
    "限量加購",
    "1元加購",
    "特典",
)
LARGE_MUSIC_TYPES = ("大型演唱會", "大型音樂祭", "大型音樂活動")
LARGE_MUSIC_HINTS = (
    "world tour",
    "tour",
    "stadium",
    "arena",
    "巨蛋",
    "大巨蛋",
    "音樂祭",
    "演唱會",
)


@dataclass
class FetchRecord:
    name: str
    url: str
    ok: bool
    status: str
    bytes_read: int = 0


@dataclass
class SourceRun:
    name: str
    primary_method: str
    primary_ok: bool = False
    fallback_used: bool = False
    fallback_ok: bool = False
    error: str = ""
    discovery_count: int = 0
    detail_failures: int = 0
    events: list[dict[str, str]] = field(default_factory=list)
    records: list[FetchRecord] = field(default_factory=list)

    def health(self, previous: dict[str, Any], now: str) -> dict[str, Any]:
        can_reliably_discover = self.primary_ok and (self.discovery_count > 0 or self.name in ("OPENTIX", "Ticket Plus"))
        has_partial_success = can_reliably_discover or self.fallback_ok or bool(self.events)
        has_degradation = self.fallback_used or self.detail_failures > 0
        if can_reliably_discover and not has_degradation:
            status = "ok"
        elif has_partial_success:
            status = "degraded"
        else:
            status = "failed"
        last_success = now if status in ("ok", "degraded") else previous.get("last_success_at", "")
        return {
            "status": status,
            "last_success_at": last_success,
            "primary_method": self.primary_method,
            "fallback_used": self.fallback_used,
            "error": self.error,
        }


def now_iso() -> str:
    return dt.datetime.now(TAIPEI_TZ).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(url: str, timeout: int = 20) -> tuple[bool, str, str, int]:
    url = safe_url(url)
    headers = {
        "Accept": "application/json,text/html,application/rss+xml,*/*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 TaiwanEventRadar/0.3"
        ),
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return True, raw.decode(charset, errors="replace"), f"HTTP {resp.status}", len(raw)
    except HTTPError as exc:
        raw = exc.read()
        return False, raw.decode("utf-8", errors="replace"), f"HTTP {exc.code}", len(raw)
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            try:
                context = ssl._create_unverified_context()
                with urlopen(request, timeout=timeout, context=context) as resp:
                    raw = resp.read()
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return True, raw.decode(charset, errors="replace"), f"HTTP {resp.status}; TLS unverified", len(raw)
            except (HTTPError, URLError, TimeoutError, OSError) as retry_exc:
                return False, "", type(retry_exc).__name__ + f": {retry_exc}", 0
        return False, "", type(exc).__name__ + f": {exc}", 0
    except (TimeoutError, OSError, http.client.IncompleteRead) as exc:
        return False, "", type(exc).__name__ + f": {exc}", 0


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parsed.path, safe="/:%")
    query = urllib.parse.quote(parsed.query, safe="=&%:+,/?")
    fragment = urllib.parse.quote(parsed.fragment, safe="")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def fetch_json(url: str, timeout: int = 20) -> tuple[bool, Any, str, int]:
    ok, body, status, size = fetch(url, timeout=timeout)
    if not ok:
        return False, None, status, size
    try:
        return True, json.loads(body), status, size
    except json.JSONDecodeError as exc:
        return False, None, f"{status}; JSON decode error: {exc}", size


def visible_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return compact_space(html.unescape(text))


def compact_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_date(value: str) -> str:
    match = re.search(r"(20\d{2})[./年/-]\s*(\d{1,2})[./月/-]\s*(\d{1,2})", value)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def normalize_time(value: str) -> str:
    raw = compact_space(value)
    if not raw:
        return ""
    match = re.search(r"(\d{1,2})(?:\s*:\s*(\d{2})|\s*點)?", raw)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    upper = raw.upper()
    if "下午" in raw and hour < 12:
        hour += 12
    if "PM" in upper and hour < 12:
        hour += 12
    if ("上午" in raw or "AM" in upper) and hour == 12:
        hour = 0
    if "中午" in raw and hour == 12:
        hour = 12
    return f"{hour:02d}:{minute:02d}"


def parse_ts(value: Any) -> dt.datetime | None:
    if value in (None, "", 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000
    return dt.datetime.fromtimestamp(number, tz=TAIPEI_TZ)


def iso_date_time(date_time: dt.datetime | None) -> tuple[str, str]:
    if not date_time:
        return "", ""
    return date_time.date().isoformat(), date_time.strftime("%H:%M")


def date_in_next_days(date_value: str, today: dt.date, days: int = 90) -> bool:
    if not date_value:
        return True
    try:
        parsed = dt.date.fromisoformat(date_value)
    except ValueError:
        return True
    return today <= parsed <= today + dt.timedelta(days=days)


def parse_sale_from_text(text: str) -> tuple[str, str]:
    for keyword in SALE_KEYWORDS:
        for match in re.finditer(re.escape(keyword), text, flags=re.IGNORECASE):
            start = max(0, match.start() - 120)
            end = min(len(text), match.end() + 120)
            window = text[start:end]
            date_match = nearest_date(window, match.start() - start)
            if not date_match:
                continue
            return normalize_date(date_match.group(0)), parse_time_near_date(window, date_match.end())
    return "", ""


def nearest_date(text: str, center: int) -> re.Match[str] | None:
    matches = list(re.finditer(r"20\d{2}[./年/-]\s*\d{1,2}[./月/-]\s*\d{1,2}", text))
    if not matches:
        return None
    return min(matches, key=lambda item: abs(item.start() - center))


def parse_time_near_date(text: str, date_end: int) -> str:
    after_date = text[date_end : date_end + 50]
    patterns = (
        r"(上午|下午|中午)\s*\d{1,2}\s*(?::\s*\d{2}|點)?",
        r"\d{1,2}\s*(?::\s*\d{2})\s*(?:AM|PM|am|pm)?",
        r"\d{1,2}\s*(?:AM|PM|am|pm)",
        r"\d{1,2}\s*點",
    )
    for pattern in patterns:
        match = re.search(pattern, after_date)
        if match:
            return normalize_time(match.group(0))
    return ""


def city_from_text(text: str) -> str:
    if "新北" in text:
        return "新北市"
    if "台北" in text or "臺北" in text:
        return "台北市"
    if "桃園" in text:
        return "桃園市"
    if "台中" in text or "臺中" in text:
        return "台中市"
    if "台南" in text or "臺南" in text:
        return "台南市"
    if "高雄" in text:
        return "高雄市"
    return ""


def classify_event(name: str, category: str = "", venue: str = "") -> str:
    text = f"{name} {category} {venue}".lower()
    if "音樂祭" in text:
        return "大型音樂祭"
    if any(token in text for token in ("巨蛋", "stadium", "arena", "world tour")):
        return "大型演唱會"
    if "演唱會" in text or "concert" in text or "tour" in text:
        return "大型演唱會"
    if "fan meeting" in text or "fan concert" in text or "見面會" in text:
        return "Fan meeting"
    if "音樂劇" in text:
        return "音樂劇"
    if "舞台劇" in text or "劇場" in text:
        return "舞台劇"
    if "舞蹈" in text or "dance" in text:
        return "舞蹈"
    if "古典" in text or "交響" in text or "管弦" in text:
        return "古典音樂"
    if "歌劇" in text or "戲曲" in text:
        return "歌劇 / 戲曲"
    if "展" in text:
        return "展覽"
    return category or "其他藝文活動"


def is_large_music(event: dict[str, str]) -> bool:
    text = " ".join(str(event.get(key, "")) for key in ("type", "name", "venue", "note", "scope")).lower()
    if event.get("scope") == "large_music_taiwan":
        return True
    if any(kind in event.get("type", "") for kind in LARGE_MUSIC_TYPES):
        return True
    return any(token in text for token in LARGE_MUSIC_HINTS)


def is_taipei_city(event: dict[str, str]) -> bool:
    city = str(event.get("city", ""))
    venue = str(event.get("venue", ""))
    if "新北" in city or "新北" in venue:
        return False
    return "台北" in city or "臺北" in city or "台北" in venue or "臺北" in venue


def passes_geo_scope(event: dict[str, str]) -> bool:
    text = " ".join(str(event.get(key, "")) for key in ("name", "city", "venue", "note")).lower()
    if any(marker in text for marker in ("hong kong", "香港", "macau", "澳門", "singapore", "新加坡")):
        return False
    if is_large_music(event):
        return True
    return is_taipei_city(event)


def is_excluded(event: dict[str, str]) -> bool:
    text = " ".join(str(event.get(key, "")) for key in ("type", "name", "note")).lower()
    return any(hint.lower() in text for hint in EXCLUDE_HINTS)


def event_id(event: dict[str, str]) -> str:
    url = event.get("source_url", "")
    if url:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    key = compact_space(event.get("name", "")).lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def normalize_event(event: dict[str, str]) -> dict[str, str]:
    normalized = {
        "id": event.get("id", ""),
        "sale_date": event.get("sale_date", ""),
        "sale_time": event.get("sale_time", ""),
        "name": compact_space(event.get("name", "")),
        "type": compact_space(event.get("type", "")),
        "performance_date": compact_space(event.get("performance_date", "")),
        "city": compact_space(event.get("city", "")),
        "venue": compact_space(event.get("venue", "")),
        "source_url": compact_space(event.get("source_url", "")),
        "discovery": compact_space(event.get("discovery", "")),
        "scope": event.get("scope", ""),
        "note": compact_space(event.get("note", "")),
    }
    normalized["id"] = normalized["id"] or event_id(normalized)
    return normalized


def dedupe_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for raw in events:
        event = normalize_event(raw)
        if not event["name"]:
            continue
        if is_excluded(event) or not passes_geo_scope(event):
            continue
        current = seen.get(event["id"])
        if current is None or (not current.get("sale_date") and event.get("sale_date")):
            seen[event["id"]] = event
    return sorted(seen.values(), key=lambda e: (e.get("sale_date", "9999-99-99"), e.get("sale_time", ""), e.get("name", "")))


def bing_rss_links(query: str, run: SourceRun, limit: int = 12) -> list[str]:
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    ok, body, status, size = fetch(url, timeout=20)
    run.records.append(FetchRecord(f"{run.name} Bing RSS", url, ok, status, size))
    if not ok:
        run.error = run.error or status
        return []
    links: list[str] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        run.error = run.error or f"Bing RSS parse error: {exc}"
        return []
    for item in root.findall(".//item"):
        link = item.findtext("link") or ""
        if link and link not in links:
            links.append(link)
    return links[:limit]


def bing_rss_items(query: str, run: SourceRun, limit: int = 10) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    ok, body, status, size = fetch(url, timeout=20)
    run.records.append(FetchRecord(f"{run.name} Bing RSS", url, ok, status, size))
    if not ok:
        run.error = run.error or status
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        run.error = run.error or f"Bing RSS parse error: {exc}"
        return []
    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        link = item.findtext("link") or ""
        title = visible_text(item.findtext("title") or "")
        description = visible_text(item.findtext("description") or "")
        pub_date = item.findtext("pubDate") or ""
        if link:
            items.append({"link": link, "title": title, "description": description, "pub_date": pub_date})
    return items[:limit]


def discover_opentix(today: dt.date, max_pages: int = 8, row_count: int = 50) -> SourceRun:
    run = SourceRun("OPENTIX", "programs/detail API")
    for page in range(1, max_pages + 1):
        list_url = f"{OPENTIX_API}/programs?page={page}&rowCount={row_count}&sortBy=LAUNCHED_DATE_TIME"
        ok, payload, status, size = fetch_json(list_url)
        run.records.append(FetchRecord("OPENTIX programs API", list_url, ok, status, size))
        run.primary_ok = run.primary_ok or ok
        if not ok or not isinstance(payload, dict):
            run.error = run.error or status
            continue
        programs = payload.get("list") or payload.get("data") or payload.get("programs") or []
        if isinstance(programs, dict):
            programs = programs.get("content") or programs.get("list") or []
        if isinstance(programs, list):
            run.discovery_count += len(programs)
        for program in programs if isinstance(programs, list) else []:
            program_id = str(program.get("id") or "")
            if not program_id:
                continue
            detail_url = f"{OPENTIX_API}/programs/{urllib.parse.quote(program_id)}"
            detail_ok, detail, detail_status, detail_size = fetch_json(detail_url)
            run.records.append(FetchRecord("OPENTIX program detail API", detail_url, detail_ok, detail_status, detail_size))
            if not detail_ok or not isinstance(detail, dict):
                run.detail_failures += 1
                continue
            event = opentix_event_from_detail(detail, program_id)
            if event and date_in_next_days(event.get("sale_date", ""), today):
                run.events.append(event)
    return run


def opentix_event_from_detail(detail: dict[str, Any], program_id: str) -> dict[str, str] | None:
    name = compact_space(str(detail.get("name") or ""))
    category = compact_space(str(detail.get("programMainCategoryName") or detail.get("displayCategory") or ""))
    sale_dt = parse_ts(detail.get("onlineStartTime")) or parse_ts(detail.get("firstEventOnlineStartTime"))
    sale_date, sale_time = iso_date_time(sale_dt)
    if not sale_date:
        return None
    venues = detail.get("eventVenues") or []
    first_venue = venues[0] if isinstance(venues, list) and venues else {}
    venue_obj = first_venue.get("venue") if isinstance(first_venue, dict) else {}
    venue = compact_space(str(venue_obj.get("name") or "")) if isinstance(venue_obj, dict) else ""
    city = compact_space(str(venue_obj.get("cityName") or "")) if isinstance(venue_obj, dict) else ""
    city = city or city_from_text(venue + json.dumps(detail, ensure_ascii=False))
    event_type = classify_event(name, category, venue)
    return {
        "sale_date": sale_date,
        "sale_time": sale_time,
        "name": name,
        "type": event_type,
        "performance_date": format_opentix_performance(detail),
        "city": city,
        "venue": venue,
        "source_url": f"https://www.opentix.life/event/{program_id}",
        "discovery": "opentix_programs_api",
        "scope": "large_music_taiwan" if event_type in LARGE_MUSIC_TYPES else "taipei_only",
    }


def format_opentix_performance(detail: dict[str, Any]) -> str:
    start_dt = parse_ts(detail.get("startDateTime"))
    end_dt = parse_ts(detail.get("endDateTime"))
    if not start_dt:
        return ""
    if end_dt and end_dt.date() != start_dt.date():
        return f"{start_dt.date().isoformat()} ~ {end_dt.date().isoformat()}"
    return start_dt.strftime("%Y-%m-%d %H:%M")


def discover_ticketplus(today: dt.date, max_events: int = 140) -> SourceRun:
    run = SourceRun("Ticket Plus", "mainEvents/event JSON")
    main_url = TICKETPLUS_S3.format(path="main/mainEvents.json")
    ok, payload, status, size = fetch_json(main_url)
    run.records.append(FetchRecord("Ticket Plus mainEvents JSON", main_url, ok, status, size))
    run.primary_ok = ok
    if not ok or not isinstance(payload, dict):
        run.error = status
        return run
    main_infos = payload.get("allEventMainPageInfo") if isinstance(payload.get("allEventMainPageInfo"), dict) else {}
    ids = payload.get("allEventId") or list(main_infos.keys())
    run.discovery_count = len(ids)
    for event_id_value in [str(item) for item in ids[:max_events] if item]:
        detail_url = TICKETPLUS_S3.format(path=f"event/{event_id_value}/event.json")
        detail_ok, detail, detail_status, detail_size = fetch_json(detail_url)
        run.records.append(FetchRecord("Ticket Plus event JSON", detail_url, detail_ok, detail_status, detail_size))
        if not detail_ok or not isinstance(detail, dict):
            run.detail_failures += 1
            continue
        event = ticketplus_event_from_detail(detail, event_id_value, main_infos.get(event_id_value, {}))
        if event and date_in_next_days(event.get("sale_date", ""), today):
            run.events.append(event)
    return run


def ticketplus_event_from_detail(detail: dict[str, Any], event_id_value: str, main_info: dict[str, Any]) -> dict[str, str] | None:
    name = compact_space(str(detail.get("title") or detail.get("name") or ""))
    info_text = visible_text(str(detail.get("info") or ""))
    sale_date, sale_time = parse_sale_from_text(info_text)
    if not sale_date:
        return None
    venue = compact_space(str(detail.get("location") or detail.get("venue") or ""))
    address = compact_space(str(detail.get("address") or ""))
    event_type = classify_event(name, "", venue)
    return {
        "sale_date": sale_date,
        "sale_time": sale_time,
        "name": name,
        "type": event_type,
        "performance_date": ticketplus_performance(detail, info_text, main_info),
        "city": city_from_text(f"{venue} {address}"),
        "venue": venue,
        "source_url": f"https://ticketplus.com.tw/activity/{event_id_value}",
        "discovery": "ticketplus_s3_event_json",
        "scope": "large_music_taiwan" if event_type in LARGE_MUSIC_TYPES else "taipei_only",
    }


def ticketplus_performance(detail: dict[str, Any], info_text: str, main_info: dict[str, Any]) -> str:
    for source in (main_info, detail):
        start = source.get("start_date") or source.get("startDate") or source.get("start_time") or source.get("startTime")
        end = source.get("end_date") or source.get("endDate") or source.get("end_time") or source.get("endTime")
        start_text = compact_space(str(start or ""))
        end_text = compact_space(str(end or ""))
        if start_text and end_text and start_text != end_text:
            return f"{start_text} ~ {end_text}"
        if start_text:
            return start_text
    dates = re.findall(r"20\d{2}[./-]\d{1,2}[./-]\d{1,2}", info_text)
    if len(dates) >= 2:
        return f"{normalize_date(dates[0])} ~ {normalize_date(dates[1])}"
    if dates:
        return normalize_date(dates[0])
    return compact_space(str(detail.get("time") or ""))


def discover_kktix(today: dt.date) -> SourceRun:
    run = SourceRun("KKTIX", "public kktix.cc listing")
    listing = "https://kktix.cc/?locale=zh-TW"
    ok, body, status, size = fetch(listing)
    run.records.append(FetchRecord("KKTIX public listing", listing, ok, status, size))
    run.primary_ok = ok and "kktix.cc/events" in body
    candidate_urls = extract_urls(body, "kktix.cc/events") if ok else []
    run.discovery_count = len(unique_urls([url for url in candidate_urls if "kktix.cc/events/" in url]))
    if not run.primary_ok:
        run.fallback_used = True
        queries = [
            'site:kktix.cc/events 台北 售票 開賣',
            'site:kktix.cc/events Taipei ticket kktix',
            'site:kktix.cc/events 音樂祭 售票',
            'site:kktix.cc/events 演唱會 售票',
        ]
        for query in queries:
            candidate_urls.extend(bing_rss_links(query, run))
    candidate_urls = [url for url in unique_urls(candidate_urls) if "kktix.cc/events/" in url]
    run.events = parse_public_event_pages(candidate_urls, run, "kktix_search_index", today)
    run.fallback_ok = run.fallback_used and bool(run.events)
    if not run.primary_ok and not run.fallback_ok:
        run.error = run.error or status
    return run


def discover_ibon(today: dt.date) -> SourceRun:
    run = SourceRun("ibon", "ticket website listing")
    listing = "https://ticket.ibon.com.tw/"
    ok, body, status, size = fetch(listing)
    run.records.append(FetchRecord("ibon ticket listing", listing, ok, status, size))
    run.primary_ok = ok and ("ticket.ibon.com.tw" in body or "售票" in body)
    candidate_urls = extract_urls(body, "ticket.ibon.com.tw") if ok else []
    run.discovery_count = len(candidate_urls)
    if not run.primary_ok:
        run.fallback_used = True
        queries = [
            'site:ticket.ibon.com.tw 售票 開賣 台北',
            'site:ticket.ibon.com.tw 演唱會 售票',
            'site:ticket.ibon.com.tw 音樂祭 售票',
            'ibon 售票 開賣 台北 演唱會',
        ]
        for query in queries:
            candidate_urls.extend(bing_rss_links(query, run))
    candidate_urls = [
        url
        for url in unique_urls(candidate_urls)
        if "ticket.ibon.com.tw" in url and any(token in url.lower() for token in ("/event/", "/activity/", "activityinfo"))
    ]
    run.discovery_count = max(run.discovery_count, len(candidate_urls))
    run.events = parse_public_event_pages(candidate_urls, run, "ibon_search_index", today)
    run.fallback_ok = run.fallback_used and bool(run.events)
    if not run.primary_ok and not run.fallback_ok:
        run.error = run.error or status
    return run


def discover_tixcraft(today: dt.date) -> SourceRun:
    run = SourceRun("tixCraft", "activity listing")
    listing = "https://tixcraft.com/activity"
    ok, body, status, size = fetch(listing)
    run.records.append(FetchRecord("tixCraft activity listing", listing, ok, status, size))
    run.primary_ok = ok and ("activity/detail" in body or "活動" in body)
    candidate_urls = extract_urls(body, "tixcraft.com/activity/detail") if ok else []
    if ok:
        for match in re.finditer(r'href=["\'](/activity/detail/[^"\']+)["\']', body, flags=re.IGNORECASE):
            candidate_urls.append("https://tixcraft.com" + html.unescape(match.group(1)).rstrip("/"))
    run.discovery_count = len(unique_urls(candidate_urls))
    if not run.primary_ok:
        run.fallback_used = True
    candidate_urls = [url for url in unique_urls(candidate_urls[:80]) if "tixcraft.com/activity/detail/" in url]
    detail_events = parse_public_event_pages(candidate_urls, run, "tixcraft_listing", today)
    run.events.extend(detail_events)
    if not detail_events:
        run.fallback_used = True
        queries = [
            'site:tixcraft.com/activity/detail 售票 開賣 台北',
            'site:tixcraft.com/activity/detail 演唱會 售票',
            'site:tixcraft.com/activity/detail fan meeting 售票',
        ]
        urls: list[str] = []
        for query in queries:
            urls.extend(bing_rss_links(query, run))
        urls = [url for url in unique_urls(urls) if "tixcraft.com/activity/detail/" in url]
        run.events.extend(parse_public_event_pages(urls, run, "tixcraft_search_index", today))
    run.fallback_ok = run.fallback_used and bool(run.events)
    if not run.primary_ok and not run.fallback_ok:
        run.error = run.error or status
    return run


def discover_public_web_news(today: dt.date) -> SourceRun:
    run = SourceRun("Public Web/News", "Bing RSS search + public pages")
    queries = [
        "台灣 演唱會 開賣 售票 公告 2026",
        "台北 fan meeting fan concert 開賣 售票 2026",
        "台灣 音樂祭 開賣 售票 公告 2026",
        "台北 音樂劇 舞台劇 開賣 售票 2026",
        "台北 展覽 藝術節 開賣 售票 2026",
        "藝人 來台 活動 售票 公告 2026",
        "加場 正式開賣 台灣 演唱會 2026",
        "site:news.yahoo.com 台灣 演唱會 開賣 售票",
        "site:mintnews.tw 演唱會 開賣 台灣",
        "site:blow.streetvoice.com 音樂祭 售票 台灣",
    ]
    items: list[dict[str, str]] = []
    for query in queries:
        items.extend(bing_rss_items(query, run, limit=8))
    run.primary_ok = any(record.ok for record in run.records)
    unique_items: dict[str, dict[str, str]] = {}
    for item in items:
        link = unique_urls([item.get("link", "")])[0] if item.get("link") else ""
        if not link:
            continue
        unique_items.setdefault(link, {**item, "link": link})
    run.discovery_count = len(unique_items)
    for item in unique_items.values():
        event = public_search_item_to_event(item)
        if not event:
            continue
        if date_in_next_days(event.get("sale_date", ""), today, days=180):
            run.events.append(event)
    if not run.primary_ok:
        run.error = run.error or "all public search queries failed"
    return run


def public_search_item_to_event(item: dict[str, str]) -> dict[str, str] | None:
    title = compact_space(item.get("title", ""))
    description = compact_space(item.get("description", ""))
    link = item.get("link", "")
    text = f"{title} {description}"
    if not public_item_is_relevant(text):
        return None
    sale_date, sale_time = parse_sale_from_text(text)
    performance = parse_performance_from_text(text)
    venue = parse_venue_from_text(text)
    city = city_from_text(text)
    event_type = classify_event(title, "", venue)
    event = {
        "sale_date": sale_date,
        "sale_time": sale_time,
        "name": clean_search_title(title),
        "type": event_type,
        "performance_date": performance,
        "city": city,
        "venue": venue,
        "source_url": link,
        "discovery": "public_web_news_search",
        "scope": "large_music_taiwan" if event_type in LARGE_MUSIC_TYPES else "taipei_only",
        "note": "public search/news discovery",
    }
    if not event["name"]:
        return None
    return event


def public_item_is_relevant(text: str) -> bool:
    lower = text.lower()
    generic_noise = (
        "全攻略",
        "總整理",
        "持續更新",
        "售票系統",
        "活動售票報名",
        "票券 |",
        "tixcraft拓元售票",
        "kktix -",
        "寬宏售票",
        "年代售票",
        "ibon售票",
    )
    if any(noise.lower() in lower for noise in generic_noise):
        return False
    event_hints = (
        "演唱會",
        "fan meeting",
        "fan concert",
        "見面會",
        "音樂祭",
        "音樂劇",
        "舞台劇",
        "展覽",
        "藝術節",
        "來台",
        "live in taipei",
        "tour in taipei",
        "開賣",
        "售票",
        "加場",
    )
    place_hints = ("台灣", "臺灣", "台北", "臺北", "高雄", "台中", "臺中", "taipei", "kaohsiung", "taiwan")
    concrete_hints = ("開賣", "售票", "加場", "公告", "宣布", "登台", "來台", "live in", "tour in", "fan meeting", "fan concert")
    return (
        any(hint in lower for hint in event_hints)
        and any(hint.lower() in lower for hint in place_hints)
        and any(hint.lower() in lower for hint in concrete_hints)
    )


def clean_search_title(title: str) -> str:
    title = re.sub(r"\s*[-|]\s*(Yahoo奇摩新聞|Yahoo News|自由娛樂|ETtoday星光雲|鏡週刊|新聞).*$", "", title)
    return compact_space(title)


def extract_urls(body: str, contains: str) -> list[str]:
    urls: list[str] = []
    patterns = [
        r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+;=%-]+",
        r"href=[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            url = match.group(1) if match.lastindex else match.group(0)
            url = html.unescape(url)
            if url.startswith("/"):
                continue
            if contains in url and url not in urls:
                urls.append(url.rstrip("/"))
    return urls


def unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean = url.strip().strip('\'"\\,;')
        clean = clean.split("\\")[0].split('",')[0].split("?utm_")[0].rstrip("/")
        clean = clean.rstrip('\'"\\,;')
        if clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def parse_public_event_pages(urls: list[str], run: SourceRun, discovery: str, today: dt.date) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for url in urls[:40]:
        ok, body, status, size = fetch(url, timeout=20)
        run.records.append(FetchRecord(f"{run.name} public page", url, ok, status, size))
        if not ok:
            run.detail_failures += 1
            continue
        event = event_from_public_page(url, body, discovery)
        if not event:
            run.detail_failures += 1
            continue
        if event and date_in_next_days(event.get("sale_date", ""), today):
            events.append(event)
    return events


def event_from_public_page(url: str, body: str, discovery: str) -> dict[str, str] | None:
    text = visible_text(body)
    title = parse_title(body) or text[:80]
    sale_date, sale_time = parse_sale_from_text(text)
    performance = parse_performance_from_text(text)
    venue = parse_venue_from_text(text)
    city = city_from_text(text)
    event_type = classify_event(title, "", venue)
    if not sale_date and not any(token in text for token in SALE_KEYWORDS):
        return None
    return {
        "sale_date": sale_date,
        "sale_time": sale_time,
        "name": title,
        "type": event_type,
        "performance_date": performance,
        "city": city,
        "venue": venue,
        "source_url": url,
        "discovery": discovery,
        "scope": "large_music_taiwan" if event_type in LARGE_MUSIC_TYPES else "taipei_only",
    }


def parse_title(body: str) -> str:
    for pattern in (
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+name=["\']title["\']\s+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
    ):
        match = re.search(pattern, body, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = visible_text(match.group(1))
            title = re.sub(r"\s*[-|]\s*(KKTIX|拓元售票|tixCraft|ibon).*$", "", title, flags=re.IGNORECASE)
            return compact_space(title)
    return ""


def parse_performance_from_text(text: str) -> str:
    dates = re.findall(r"20\d{2}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?", text)
    normalized = [normalize_date(item) for item in dates]
    normalized = [item for item in normalized if item]
    unique = list(dict.fromkeys(normalized))
    if len(unique) >= 2:
        return f"{unique[0]} ~ {unique[1]}"
    if unique:
        return unique[0]
    return ""


def parse_venue_from_text(text: str) -> str:
    venue_match = re.search(r"(台北國際會議中心|臺北大巨蛋|台北大巨蛋|Legacy Taipei|Zepp New Taipei|Comedy Plus[^ ]*|烏日啤酒廠|高雄國家體育場)", text)
    return venue_match.group(1) if venue_match else ""


def load_static_events() -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for filename in ("manual_events.json", "discovery_fallback_events.json"):
        path = DATA / filename
        if path.exists():
            for event in read_json(path, []):
                item = dict(event)
                item["discovery"] = item.get("discovery", "manual_exception")
                events.append(item)
    return events


def discover_all(today: dt.date, include_static: bool = True) -> tuple[list[dict[str, str]], list[SourceRun]]:
    runs = [
        discover_opentix(today),
        discover_ticketplus(today),
        discover_kktix(today),
        discover_ibon(today),
        discover_tixcraft(today),
        discover_public_web_news(today),
    ]
    events: list[dict[str, str]] = []
    for run in runs:
        events.extend(run.events)
    if include_static:
        events.extend(load_static_events())
    return dedupe_events(events), runs


def build_source_health(runs: list[SourceRun], previous: dict[str, Any], generated_at: str) -> dict[str, Any]:
    previous_health = previous.get("source_health", {}) if isinstance(previous, dict) else {}
    return {
        run.name: run.health(previous_health.get(run.name, {}), generated_at)
        for run in runs
    }


def make_snapshot(events: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {event["id"]: event for event in events}


def diff_events(previous_events: dict[str, dict[str, str]], current_events: dict[str, dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    new_events: list[dict[str, str]] = []
    new_sale_info: list[dict[str, str]] = []
    changed_events: list[dict[str, Any]] = []
    important_fields = ("sale_date", "sale_time", "performance_date", "city", "venue", "name", "type")
    for event_id_value, event in current_events.items():
        previous = previous_events.get(event_id_value)
        if previous is None:
            new_events.append(event)
            continue
        had_sale = bool(previous.get("sale_date") or previous.get("sale_time"))
        has_sale = bool(event.get("sale_date") or event.get("sale_time"))
        if not had_sale and has_sale:
            new_sale_info.append(event)
            continue
        changes = {
            field: {"old": previous.get(field, ""), "new": event.get(field, "")}
            for field in important_fields
            if previous.get(field, "") != event.get(field, "")
        }
        if changes:
            changed_events.append({"id": event_id_value, "name": event.get("name", ""), "source_url": event.get("source_url", ""), "changes": changes})
    return new_events, new_sale_info, changed_events


def build_latest(today: dt.date, save_state: bool = True, state_path: Path = STATE, simulate_previous: dict[str, Any] | None = None, force_source_failure: str = "") -> dict[str, Any]:
    generated_at = now_iso()
    previous = simulate_previous if simulate_previous is not None else read_json(state_path, {"events": {}, "source_health": {}})
    events, runs = discover_all(today)
    source_health = build_source_health(runs, previous, generated_at)
    if force_source_failure and force_source_failure in source_health:
        source_health[force_source_failure]["status"] = "failed"
        source_health[force_source_failure]["error"] = "simulated failure"
    coverage_ok = calculate_coverage_ok(source_health)
    current_events = make_snapshot(events)
    new_events, new_sale_info, changed_events = diff_events(previous.get("events", {}), current_events)
    latest = {
        "generated_at": generated_at,
        "scan_success": True,
        "coverage_ok": coverage_ok,
        "new_events": new_events,
        "new_sale_info": new_sale_info,
        "changed_events": changed_events,
        "source_health": source_health,
    }
    if save_state:
        write_json(LATEST, latest)
        write_json(state_path, {"generated_at": generated_at, "events": current_events, "source_health": source_health})
    return latest


def calculate_coverage_ok(source_health: dict[str, Any]) -> bool:
    public_status = source_health.get("Public Web/News", {}).get("status")
    if public_status == "failed":
        return False
    primary_sources = ("OPENTIX", "Ticket Plus", "KKTIX", "tixCraft")
    working_primary = sum(
        1
        for name in primary_sources
        if source_health.get(name, {}).get("status") in ("ok", "degraded")
    )
    failed_primary = sum(1 for name in primary_sources if source_health.get(name, {}).get("status") == "failed")
    if working_primary < 3:
        return False
    if failed_primary >= 2:
        return False
    return True


def line_for(event: dict[str, str]) -> str:
    where = " / ".join(part for part in (event.get("city", ""), event.get("venue", "")) if part)
    sale = " ".join(part for part in (event.get("sale_date", ""), event.get("sale_time", "")) if part)
    return f"{sale or '尚未公布'}｜{event.get('name', '')}｜{event.get('performance_date', '')}｜{where}｜{event.get('type', '')}"


def write_markdown_report(today: dt.date, latest: dict[str, Any]) -> Path:
    lines = [
        "# Taiwan Event Radar Daily Diff",
        "",
        f"- generated_at: {latest['generated_at']}",
        f"- coverage_ok: {str(latest['coverage_ok']).lower()}",
        "",
        "## new_events",
        *[line_for(event) for event in latest["new_events"]],
        "",
        "## new_sale_info",
        *[line_for(event) for event in latest["new_sale_info"]],
        "",
        "## changed_events",
        *[json.dumps(item, ensure_ascii=False) for item in latest["changed_events"]],
        "",
        "## source_health",
        *[f"- {name}: {json.dumps(health, ensure_ascii=False)}" for name, health in latest["source_health"].items()],
    ]
    if len(lines) == 8:
        lines.append("(no changes)")
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"radar-{today.isoformat()}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def simulate_no_change(today: dt.date) -> dict[str, Any]:
    baseline = build_latest(today, save_state=False)
    previous = {"events": make_snapshot(dedupe_events([*baseline["new_events"]])), "source_health": baseline["source_health"]}
    return build_latest(today, save_state=False, simulate_previous=previous)


def simulate_new_sale_info(today: dt.date) -> dict[str, Any]:
    events, runs = discover_all(today)
    current = make_snapshot(events)
    previous_events = copy.deepcopy(current)
    first_with_sale = next((event for event in previous_events.values() if event.get("sale_date")), None)
    if first_with_sale:
        first_with_sale["sale_date"] = ""
        first_with_sale["sale_time"] = ""
    previous = {
        "events": previous_events,
        "source_health": build_source_health(runs, {}, now_iso()),
    }
    return build_latest(today, save_state=False, simulate_previous=previous)


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=dt.date.today().isoformat())
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--simulate", choices=("none", "no-change", "new-sale-info", "source-failure"), default="none")
    args = parser.parse_args(argv)
    today = parse_date(args.today)

    try:
        if args.simulate == "no-change":
            latest = simulate_no_change(today)
        elif args.simulate == "new-sale-info":
            latest = simulate_new_sale_info(today)
        elif args.simulate == "source-failure":
            latest = build_latest(today, save_state=False, force_source_failure="ibon")
        else:
            latest = build_latest(today, save_state=not args.no_save)
            write_markdown_report(today, latest)
    except Exception as exc:
        latest = {
            "generated_at": now_iso(),
            "scan_success": False,
            "coverage_ok": False,
            "new_events": [],
            "new_sale_info": [],
            "changed_events": [],
            "source_health": {},
            "error": type(exc).__name__ + f": {exc}",
        }
        if not args.no_save and args.simulate == "none":
            write_json(LATEST, latest)
        print(json.dumps(latest, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(latest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
