import os
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 2분기 보고서에 나온 상위 제공사 + 기타 후보 키워드
PROVIDER_KEYWORDS = [
    "기상청", "웨더아이", "아큐웨더", "웨더뉴스", "웨더채널", "케이웨더",
    "산림청", "공공데이터포털", "국외기상청", "NOAA", "JMA", "ECMWF",
    "Foreca", "Windy", "OpenweatherMap", "Apple Weather", "MSN",
]


def fetch_page(target_url, timeout=8):
    """웹사이트 접속해서 HTML 텍스트를 가져옵니다. 실패하면 None을 돌려줍니다."""
    try:
        res = requests.get(target_url, headers=HEADERS, timeout=timeout)
        if res.status_code == 200:
            return res.text
    except requests.RequestException as e:
        print(f"  접속 실패: {e}")
    return None


def find_providers(html_text):
    """HTML 안에서 기상정보 제공사 키워드를 찾아 리스트로 돌려줍니다."""
    found = []
    for keyword in PROVIDER_KEYWORDS:
        if keyword in html_text:
            found.append(keyword)
    return found


def get_web_targets():
    """DB에서 media_type이 'Web'이고 url이 있는 대상을 전부 가져옵니다."""
    result = supabase.table("survey_targets").select("id, target_name, url").eq("media_type", "Web").execute()
    return [row for row in result.data if row.get("url")]


def get_last_result(target_id):
    """이 대상의 가장 최근 조사 결과를 가져옵니다 (변화 비교용)."""
    result = (
        supabase.table("survey_results")
        .select("source_marked, survey_date")
        .eq("target_id", target_id)
        .order("survey_date", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def save_result(target_id, providers):
    """오늘 조사 결과를 survey_results, source_providers에 저장합니다."""
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

    return source_marked


def run():
    targets = get_web_targets()
    print(f"총 {len(targets)}개 Web 대상 재조사를 시작합니다.\n")

    changed = []
    for i, target in enumerate(targets, start=1):
        name = target["target_name"]
        site_url = target["url"]
        print(f"[{i}/{len(targets)}] 조사 중: {name} ({site_url})")

        last = get_last_result(target["id"])
        html = fetch_page(site_url)

        if html is None:
            print("  -> 접속 불가 (사이트 이전/폐쇄 가능성)")
            time.sleep(0.5)
            continue

        providers = find_providers(html)
        new_marked = save_result(target["id"], providers)
        print(f"  -> 발견된 제공사: {providers if providers else '없음'} ({new_marked})")

        if last and last["source_marked"] != new_marked:
            changed.append((name, last["source_marked"], new_marked))

        time.sleep(0.5)  # 사이트에 너무 빠르게 연속 요청하지 않도록 약간 쉬어줍니다

    print("\n===== 조사 완료 =====")
    if changed:
        print(f"※ 이전 조사 대비 상태가 바뀐 대상 {len(changed)}건:")
        for name, before, after in changed:
            print(f"  - {name}: {before} -> {after}")
    else:
        print("이전 조사 대비 상태가 바뀐 대상은 없습니다.")


if __name__ == "__main__":
    run()
