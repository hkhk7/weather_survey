import requests
import time
from datetime import date
import os

from dotenv import load_dotenv
from supabase import create_client


load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)


SEARCH_KEYWORDS = [
    "날씨", "기상", "기후", "weather",
    "생활기상지수", "보건기상지수", "레저", "태풍", "집중호우",
]

PROVIDER_KEYWORDS = {
    "기상청": ["기상청", "KMA"],
    "웨더아이": ["웨더아이", "WeatherI"],
    "아큐웨더": ["아큐웨더", "AccuWeather"],
    "웨더뉴스": ["웨더뉴스", "Weathernews"],
    "웨더채널": ["웨더채널", "The Weather Channel"],
    "케이웨더": ["케이웨더", "kweather"],
    "산림청": ["산림청"],
    "공공데이터포털": ["공공데이터포털", "data.go.kr"],
    "국외기상청": ["NOAA", "JMA", "ECMWF"],
}

DOMESTIC_PROVIDERS = ["기상청", "웨더아이", "케이웨더", "산림청", "공공데이터포털"]

HEADERS = {"User-Agent": "Mozilla/5.0 (WeatherSurveyBot/1.0)"}


def fetch_page(url):
    """웹페이지의 HTML 텍스트를 가져옵니다. 실패하면 None을 반환합니다."""
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        return res.text
    except requests.RequestException as e:
        print(f"  ! 접속 실패: {url} ({e})")
        return None


def find_providers(html_text):
    """페이지 안에서 어떤 기상정보 제공사가 언급되는지 찾아 리스트로 반환합니다."""
    found = []
    lowered = html_text.lower()
    for provider, keywords in PROVIDER_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in lowered:
                found.append(provider)
                break
    return found


def get_or_create_target(media_type, target_name, url):
    """이미 DB에 등록된 대상인지 확인하고, 없으면 새로 등록한 뒤 id를 반환합니다."""
    existing = supabase.table("survey_targets").select("id").eq("url", url).execute()
    if existing.data:
        return existing.data[0]["id"]

    inserted = supabase.table("survey_targets").insert({
        "media_type": media_type,
        "target_name": target_name,
        "url": url,
        "first_found_quarter": "2026-Q3",
    }).execute()
    return inserted.data[0]["id"]


def save_result(target_id, providers_found):
    """조사 결과(출처 기재 여부)와 발견된 제공사들을 DB에 저장합니다."""
    source_marked = "기재" if providers_found else "미기재"

    result = supabase.table("survey_results").insert({
        "target_id": target_id,
        "survey_date": str(date.today()),
        "source_marked": source_marked,
        "action_status": "해당없음" if providers_found else "조치불가",
    }).execute()
    result_id = result.data[0]["id"]

    for provider in providers_found:
        supabase.table("source_providers").insert({
            "result_id": result_id,
            "provider_name": provider,
            "is_domestic": provider in DOMESTIC_PROVIDERS,
        }).execute()


def survey_site(media_type, target_name, url):
    """사이트 하나를 처음부터 끝까지 조사하는 전체 과정입니다."""
    print(f"조사 중: {target_name} ({url})")
    html = fetch_page(url)
    if html is None:
        return

    providers = find_providers(html)
    target_id = get_or_create_target(media_type, target_name, url)
    save_result(target_id, providers)

    print(f"  -> 발견된 제공사: {providers if providers else '없음'}")


if __name__ == "__main__":
    # 우선 테스트용으로 몇 개 사이트만 넣어서 잘 작동하는지 확인합니다.
    # 나중에 이 목록을 실제 조사 대상(웹 126개)으로 확장하면 됩니다.
    seed_sites = [
        ("Web", "네이버 날씨", "https://weather.naver.com"),
        ("Web", "기상청 날씨누리", "https://www.weather.go.kr"),
    ]

    for media_type, name, url in seed_sites:
        survey_site(media_type, name, url)
        time.sleep(1)  # 상대 서버에 부담을 주지 않기 위해 1초씩 쉬어갑니다