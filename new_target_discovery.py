"""
new_target_discovery.py
기상 관련 키워드로 Web/App/YouTube 신규 대상을 검색해서, DB에 없는 것만 새로 등록합니다.
(등록만 하고 출처 검증은 안 함 -> 검증은 crawl_web_v2.py / youtube_source_check.py 등 기존 도구가 담당)

API 키가 전혀 필요 없는 방식으로 구성했습니다 (기관 단위 프로젝트라 개인 API 키 발급이
어려운 상황을 고려한 방법입니다):
  - Web: DuckDuckGo 검색결과 페이지를 직접 조회 (키 불필요)
  - App: google-play-scraper(플레이스토어) + iTunes Search API(앱스토어) - 원래부터 키 불필요
  - YouTube: yt-dlp의 검색 기능(ytsearch)으로 검색 - 키 불필요

사전 설치:
    pip install requests beautifulsoup4 google-play-scraper yt-dlp

실행: python new_target_discovery.py
"""

import os
import json
import time
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 보고서에 나온 조사 키워드 그대로 사용
KEYWORDS = ["날씨", "기상", "기후", "weather", "생활기상지수", "보건기상지수", "레저", "태풍", "집중호우"]
MIN_SUBSCRIBERS = 1000  # 보고서 기준: 구독자 1,000명 이상 채널만
RESULTS_PER_KEYWORD = 20

# 앱 설명에 이 중 하나라도 있어야 "진짜 날씨 관련 앱"으로 인정 (스토어 검색결과의 엉뚱한 앱 걸러내기용)
WEATHER_RELEVANCE_KEYWORDS = ["날씨", "기상", "기후", "weather", "미세먼지", "우산", "태풍", "장마", "강수"]

# "기상"은 '기상청의 기상(날씨)'과 '잠에서 깨어남(morning alarm)' 두 뜻이 있어 중의적입니다.
# 아래 두 목록으로, "알람/수면 앱스러운 단어"만 있고 "확실한 날씨 단어"가 하나도 없으면 걸러냅니다.
ALARM_CONTEXT_KEYWORDS = ["알람", "알림음", "모닝콜", "수면", "잠에서", "기상시간", "알람시계", "웨이크업", "루틴"]
STRONG_WEATHER_KEYWORDS = ["날씨", "예보", "기온", "미세먼지", "강수", "태풍", "기상청", "장마", "황사", "체감온도", "우산", "레이더"]


def is_weather_relevant(*texts):
    combined = " ".join(t for t in texts if t)
    combined_lower = combined.lower()

    has_weather_hit = any(kw.lower() in combined_lower for kw in WEATHER_RELEVANCE_KEYWORDS)
    if not has_weather_hit:
        return False

    # "기상" 중의성 예외처리: 알람 앱 느낌이면서 확실한 날씨 단어가 하나도 없으면 제외
    looks_like_alarm_app = any(kw in combined for kw in ALARM_CONTEXT_KEYWORDS)
    has_strong_weather = any(kw.lower() in combined_lower for kw in STRONG_WEATHER_KEYWORDS)
    if looks_like_alarm_app and not has_strong_weather:
        return False

    return True

# 방송사/언론사로 보이는 채널은 "개인 채널 발굴"에서 제외
BROADCASTER_KEYWORDS = [
    "MBC", "KBS", "SBS", "YTN", "JTBC", "연합뉴스", "뉴스1", "채널A", "TV조선",
    "MBN", "OBS", "EBS", "news", "News", "NEWS", "방송", "언론사",
]


def is_broadcaster(channel_name, channel_is_verified):
    """채널명에 방송사 키워드가 있거나, 유튜브 인증배지가 있으면 방송사/언론사로 간주해 제외합니다."""
    if channel_is_verified:
        return True
    name = channel_name or ""
    return any(kw.lower() in name.lower() for kw in BROADCASTER_KEYWORDS)


def target_exists(media_type, target_name):
    """이미 DB에 같은 이름의 대상이 있는지 확인합니다 (중복 방지)."""
    result = (
        supabase.table("survey_targets")
        .select("id")
        .eq("media_type", media_type)
        .eq("target_name", target_name)
        .execute()
    )
    return len(result.data) > 0


def save_candidate(media_type, target_name, url_value, keyword, category=None, store_type=None):
    if target_exists(media_type, target_name):
        return False
    supabase.table("survey_targets").insert({
        "media_type": media_type,
        "target_name": target_name,
        "url": url_value,
        "category": category,
        "store_type": store_type,
        "discovery_status": "candidate",
        "discovered_by_keyword": keyword,
        "first_found_quarter": "2026-Q3",
    }).execute()
    return True


# ---------- Web: DuckDuckGo 검색결과 스크래핑 (키 불필요) ----------

def unwrap_duckduckgo_link(href):
    """DuckDuckGo 결과 링크는 //duckduckgo.com/l/?uddg=실제주소 형태로 감싸져 있어서 실제 주소만 뽑아냅니다."""
    if href.startswith("//duckduckgo.com/l/") or "duckduckgo.com/l/" in href:
        parsed = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
        return unquote(parsed.get("uddg", [href])[0])
    return href


def discover_web():
    print("\n[Web] DuckDuckGo(lite) 검색으로 신규 사이트 발굴 중...")
    new_count = 0
    session = requests.Session()  # 같은 세션을 유지하면 매번 새 요청보다 차단될 확률이 낮아집니다

    for keyword in KEYWORDS:
        time.sleep(2)  # 요청 사이에 텀을 둬서 봇 감지(rate limit)를 피합니다
        try:
            res = session.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": f"{keyword} 날씨"},
                headers={**HEADERS, "Accept-Language": "ko-KR,ko;q=0.9",
                         "Referer": "https://lite.duckduckgo.com/"},
                timeout=10,
            )
        except requests.RequestException as e:
            print(f"  '{keyword}' 검색 실패: {e}")
            continue

        if res.status_code != 200:
            print(f"  '{keyword}' 검색 실패 (status={res.status_code})")
            continue

        soup = BeautifulSoup(res.text, "html.parser")
        count_this_keyword = 0
        for a in soup.find_all("a"):
            href = a.get("href", "")
            link = unwrap_duckduckgo_link(href)
            title = a.get_text(strip=True)
            if not title or not link.startswith("http") or "duckduckgo.com" in link:
                continue
            if not is_weather_relevant(title):
                continue
            if save_candidate("Web", title, link, keyword):
                new_count += 1
                count_this_keyword += 1
                print(f"  + 신규 발견: {title} ({link})")
            if count_this_keyword >= RESULTS_PER_KEYWORD:
                break

    print(f"  Web 신규 후보 {new_count}건 등록")


# ---------- App(Play스토어): google-play-scraper ----------

def discover_app_playstore():
    print("\n[App-PlayStore] Play스토어 검색으로 신규 앱 발굴 중...")
    try:
        from google_play_scraper import search, app as gp_app_detail
    except ImportError:
        print("  google-play-scraper가 설치되지 않아 건너뜁니다. (pip install google-play-scraper)")
        return

    new_count = 0
    skipped_count = 0
    for keyword in KEYWORDS:
        try:
            results = search(keyword, lang="ko", country="kr", n_hits=20)
        except Exception as e:
            print(f"  '{keyword}' 검색 실패: {e}")
            continue

        for app in results:
            name = app["title"]
            app_id = app["appId"]

            if target_exists("App", name):
                continue  # 이미 있는 앱이면 상세조회 없이 바로 건너뜀 (호출 절약)

            # search() 결과엔 설명이 없는 경우가 많아, 상세 정보를 따로 조회해서 진짜 날씨 앱인지 확인
            try:
                detail = gp_app_detail(app_id, lang="ko", country="kr")
            except Exception:
                skipped_count += 1
                continue

            summary_text = f"{detail.get('summary', '')} {detail.get('description', '')}"
            if not is_weather_relevant(name, summary_text):
                skipped_count += 1
                continue

            app_url = f"https://play.google.com/store/apps/details?id={app_id}"
            if save_candidate("App", name, app_url, keyword, store_type="playstore"):
                new_count += 1
                print(f"  + 신규 발견: {name}")

    print(f"  App(PlayStore) 신규 후보 {new_count}건 등록 (관련없어 제외 {skipped_count}건)")


# ---------- App(앱스토어): iTunes Search API ----------

def discover_app_appstore():
    print("\n[App-AppStore] 앱스토어 검색으로 신규 앱 발굴 중...")
    new_count = 0
    for keyword in KEYWORDS:
        res = requests.get(
            "https://itunes.apple.com/search",
            params={"term": keyword, "country": "kr", "entity": "software", "limit": 20},
            timeout=10,
        )
        if res.status_code != 200:
            print(f"  '{keyword}' 검색 실패 (status={res.status_code})")
            continue

        for app in res.json().get("results", []):
            name = app["trackName"]
            if target_exists("App", name):
                continue

            description = app.get("description", "")
            if not is_weather_relevant(name, description):
                continue

            app_url = app["trackViewUrl"]
            if save_candidate("App", name, app_url, keyword, store_type="appstore"):
                new_count += 1
                print(f"  + 신규 발견: {name}")

    print(f"  App(AppStore) 신규 후보 {new_count}건 등록")


# ---------- YouTube: yt-dlp 검색 기능(ytsearch) - 키 불필요 ----------

def discover_youtube():
    print("\n[YouTube] yt-dlp 검색으로 신규 채널 발굴 중...")
    new_count = 0
    seen_channel_ids = set()

    for keyword in KEYWORDS:
        cmd = ["yt-dlp", "--dump-json", "--skip-download", f"ytsearch{RESULTS_PER_KEYWORD}:{keyword} 날씨"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            print(f"  '{keyword}' 검색 시간 초과")
            continue

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                video = json.loads(line)
            except json.JSONDecodeError:
                continue

            channel_id = video.get("channel_id")
            if not channel_id or channel_id in seen_channel_ids:
                continue
            seen_channel_ids.add(channel_id)

            name = video.get("channel") or video.get("uploader")
            if is_broadcaster(name, video.get("channel_is_verified")):
                continue

            subs = video.get("channel_follower_count")
            if subs is None or subs < MIN_SUBSCRIBERS:
                continue

            channel_url = video.get("channel_url") or f"https://www.youtube.com/channel/{channel_id}"
            if save_candidate("YouTube", name, channel_url, keyword):
                new_count += 1
                print(f"  + 신규 발견: {name} (구독자 {subs:,}명)")

    print(f"  YouTube 신규 후보 {new_count}건 등록")


if __name__ == "__main__":
    discover_web()
    discover_app_playstore()
    discover_app_appstore()
    discover_youtube()
    print("\n===== 전체 발굴 완료 =====")
    print("주의: 방금 등록된 건들은 discovery_status='candidate' 상태입니다.")
    print("실제 기상정보 출처가 맞는지는 사람이 한 번 훑어보거나, 기존 검증 도구를 이 후보들에도 돌려서 확인해주세요.")