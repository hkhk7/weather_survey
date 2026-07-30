"""
app_verify.py
Web의 crawl_web_v2.py, YouTube의 youtube_source_check.py에 해당하는 App용 자동 재검증 도구입니다.

DB에 있는 App 대상 전체에 대해:
  - Play스토어/앱스토어에서 최신 설명·다운로드수·가격·광고여부를 다시 조회
  - 기상정보 제공사 키워드가 설명에 있는지 재확인
  - 오늘 날짜로 survey_results / source_providers / app_stats 에 저장

사전 설치: pip install google-play-scraper requests
실행: python app_verify.py
"""

import os
import re
import time
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

PROVIDER_KEYWORDS = [
    "기상청", "웨더아이", "아큐웨더", "웨더뉴스", "웨더채널", "케이웨더",
    "산림청", "공공데이터포털", "국외기상청", "NOAA", "JMA", "ECMWF",
    "Foreca", "Windy", "OpenweatherMap", "Apple Weather", "MSN",
]

# 보고서와 동일한 다운로드 구간 기준
DOWNLOAD_BUCKETS = [
    (1_000, "1,000 이하"),
    (100_000, "1,000 초과 ~ 100,000 이하"),
    (1_000_000, "100,000 초과 ~ 1,000,000 이하"),
    (10_000_000, "1,000,000 초과 ~ 10,000,000 이하"),
    (100_000_000, "10,000,000 초과 ~ 100,000,000 이하"),
    (1_000_000_000, "100,000,000 초과 ~ 1,000,000,000 이하"),
    (float("inf"), "1,000,000,000 초과"),
]


def bucket_downloads(n):
    for limit, label in DOWNLOAD_BUCKETS:
        if n <= limit:
            return label
    return DOWNLOAD_BUCKETS[-1][1]


def find_providers(text):
    text = text or ""
    return [kw for kw in PROVIDER_KEYWORDS if kw in text]


def get_app_targets():
    return (
        supabase.table("survey_targets")
        .select("id, target_name, url, store_type")
        .eq("media_type", "App")
        .execute()
        .data
    )


def extract_play_id(app_url):
    query = parse_qs(urlparse(app_url).query)
    return query.get("id", [None])[0]


def extract_appstore_id(app_url):
    match = re.search(r"id(\d+)", app_url or "")
    return match.group(1) if match else None


def verify_playstore(app_id):
    from google_play_scraper import app as gp_app
    try:
        detail = gp_app(app_id, lang="ko", country="kr")
    except Exception as e:
        print(f"    조회 실패: {e}")
        return None

    description = detail.get("description", "") or ""
    installs_raw = detail.get("installs", "0") or "0"
    installs_num = int(re.sub(r"[^\d]", "", installs_raw) or 0)

    return {
        "providers": find_providers(f"{detail.get('title', '')} {description}"),
        "download_range": bucket_downloads(installs_num) if installs_num > 0 else None,
        "price_type": "무료" if detail.get("free") else f"유료({detail.get('price')})",
        "ad_status": "광고있음" if detail.get("containsAds") else "광고없음",
    }


def verify_appstore(track_id):
    try:
        res = requests.get(
            "https://itunes.apple.com/lookup", params={"id": track_id, "country": "kr"}, timeout=10
        )
        results = res.json().get("results", [])
    except (requests.RequestException, ValueError):
        results = []

    if not results:
        return None

    detail = results[0]
    description = detail.get("description", "") or ""
    price = detail.get("price", 0)

    return {
        "providers": find_providers(f"{detail.get('trackName', '')} {description}"),
        "download_range": None,  # 앱스토어는 공식적으로 다운로드 수를 공개하지 않음
        "price_type": "무료" if price == 0 else f"유료({detail.get('formattedPrice', '')})",
        "ad_status": None,  # 앱스토어 공개 API로는 확인 불가
    }


def save_result(target_id, info):
    providers = info["providers"]
    source_marked = "기재" if providers else "미기재"

    result = supabase.table("survey_results").insert({
        "target_id": target_id,
        "source_marked": source_marked,
        "source_names": ", ".join(providers) if providers else None,
        "action_status": "해당없음",
    }).execute()
    result_id = result.data[0]["id"]

    for provider in providers:
        supabase.table("source_providers").insert({
            "result_id": result_id,
            "provider_name": provider,
        }).execute()

    supabase.table("app_stats").insert({
        "target_id": target_id,
        "download_range": info["download_range"],
        "price_type": info["price_type"],
        "ad_status": info["ad_status"],
    }).execute()

    return source_marked


def run():
    targets = get_app_targets()
    print(f"총 {len(targets)}개 App 대상 재검증을 시작합니다.\n")

    for i, target in enumerate(targets, start=1):
        name = target["target_name"]
        app_url = target["url"]
        store_type = target.get("store_type")
        print(f"[{i}/{len(targets)}] {name} ({store_type})")

        if not app_url:
            print("  링크가 없어 건너뜁니다.")
            continue

        if store_type == "playstore":
            app_id = extract_play_id(app_url)
            info = verify_playstore(app_id) if app_id else None
        elif store_type == "appstore":
            track_id = extract_appstore_id(app_url)
            info = verify_appstore(track_id) if track_id else None
        else:
            print("  store_type을 알 수 없어 건너뜁니다.")
            continue

        if info is None:
            print("  조회 실패 (스토어에서 내려간 앱일 수 있음)")
            time.sleep(0.3)
            continue

        marked = save_result(target["id"], info)
        print(
            f"  -> 제공사: {info['providers'] or '없음'} ({marked}) / "
            f"다운로드: {info['download_range']} / {info['price_type']} / {info['ad_status']}"
        )
        time.sleep(0.3)  # 스토어 요청 사이 약간의 텀

    print("\n===== App 재검증 완료 =====")


if __name__ == "__main__":
    run()
