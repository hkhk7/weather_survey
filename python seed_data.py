import os
import math
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

EXCEL_PATH = "survey_2026Q2.xlsx"  # 암호 풀린 엑셀 파일을 이 이름으로 프로젝트 폴더에 두세요
SURVEY_QUARTER = "2026-Q2"
SURVEY_DATE = "2026-06-30"


def clean(value):
    """엑셀의 빈 칸(NaN)을 파이썬 None으로 바꿔줍니다. 안 하면 DB에 이상한 값이 들어갑니다."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and value.strip() in ("", "-"):
        return None
    return value


def clean_int(value):
    """숫자 컬럼(구독자수, 조회수 등)을 정수로 확실히 변환합니다.
    엑셀에서 읽으면 1723139.0 처럼 소수 형태로 들어와 DB의 정수 컬럼에서 오류가 나기 때문입니다."""
    value = clean(value)
    if value is None:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def map_source_marked(raw):
    """엑셀의 '출처기재' 같은 표현을 DB가 허용하는 값('기재'/'미기재'/'표출중단')으로 바꿔줍니다."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if "미기재" in raw:
        return "미기재"
    if "표출중단" in raw:
        return "표출중단"
    if "기재" in raw:
        return "기재"
    return None


def insert_target(media_type, target_name, url_value=None, ksic_name=None, category=None,
                   linked_site=None, direct_display=None, store_type=None):
    """survey_targets 테이블에 대상(사이트/앱/채널)을 하나 등록하고, 생성된 id를 돌려줍니다."""
    data = {
        "media_type": media_type,
        "target_name": clean(target_name),
        "url": clean(url_value),
        "ksic_name": clean(ksic_name),
        "category": clean(category),
        "linked_site": clean(linked_site),
        "direct_display": clean(direct_display),
        "store_type": clean(store_type),
        "first_found_quarter": SURVEY_QUARTER,
    }
    result = supabase.table("survey_targets").insert(data).execute()
    return result.data[0]["id"]


def insert_result(target_id, source_marked, source_names=None, provided_list=None,
                   provider_company=None, etc_note=None):
    """survey_results 테이블에 이번 분기 조사 결과를 하나 등록합니다."""
    data = {
        "target_id": target_id,
        "survey_date": SURVEY_DATE,
        "source_marked": map_source_marked(source_marked),
        "source_names": clean(source_names),
        "provided_list": clean(provided_list),
        "provider_company": clean(provider_company),
        "etc_note": clean(etc_note),
        "action_status": "해당없음",
    }
    supabase.table("survey_results").insert(data).execute()


def seed_web():
    print("\n[1/4] Web 시트 적재 중...")
    df = pd.read_excel(EXCEL_PATH, sheet_name="2026 2분기 기상정보 유통조사 (web)")
    count = 0
    for _, row in df.iterrows():
        if pd.isna(row.get("사이트명")):
            continue
        target_id = insert_target(
            media_type="Web",
            target_name=row.get("사이트명"),
            url_value=row.get("주소"),
            ksic_name=row.get("표준산업분류"),
            category=row.get("분류"),
            linked_site=row.get("연결(링크) 사이트"),
            direct_display=row.get("사이트상 직접표출"),
        )
        insert_result(
            target_id=target_id,
            source_marked=row.get("기상정보 출처"),
            source_names=row.get("출처명"),
            provided_list=row.get("제공목록"),
            provider_company=row.get("제공 업체"),
            etc_note=row.get("기타"),
        )
        count += 1
    print(f"  -> Web {count}건 완료")


def seed_app(sheet_name, store_type):
    print(f"\n[{'2/4' if store_type == 'playstore' else '3/4'}] App({store_type}) 시트 적재 중...")
    df = pd.read_excel(EXCEL_PATH, sheet_name=sheet_name)
    count = 0
    for _, row in df.iterrows():
        if pd.isna(row.get("사이트명")):
            continue
        target_id = insert_target(
            media_type="App",
            target_name=row.get("사이트명"),
            url_value=row.get("연결링크"),
            ksic_name=row.get("표준산업분류"),
            category=row.get("분류"),
            store_type=store_type,
        )
        insert_result(
            target_id=target_id,
            source_marked=row.get("기상정보 출처"),
            source_names=row.get("출처명"),
            provided_list=row.get("제공목록"),
            provider_company=row.get("제공 업체"),
        )
        download_raw = clean(row.get("다운로드 수"))
        is_preinstalled = (download_raw == "선탑재")
        supabase.table("app_stats").insert({
            "target_id": target_id,
            "download_range": None if is_preinstalled else download_raw,
            "is_preinstalled": is_preinstalled,
            "manufacturer_category": clean(row.get("분류")),
            "release_date": clean(row.get("출시일")),
            "price_type": clean(row.get("유료/무료")),
            "ad_status": clean(row.get("광고유무")),
        }).execute()
        count += 1
    print(f"  -> App({store_type}) {count}건 완료")


def seed_youtube():
    print("\n[4/4] YouTube 시트 적재 중...")
    df = pd.read_excel(EXCEL_PATH, sheet_name="2026 2분기 기상정보 유통조사 (Youtube)")
    count = 0
    for _, row in df.iterrows():
        if pd.isna(row.get("채널명")):
            continue
        target_id = insert_target(
            media_type="YouTube",
            target_name=row.get("채널명"),
        )
        insert_result(
            target_id=target_id,
            source_marked=row.get("기상정보 출처"),
            provider_company=row.get("주요자료출처"),
            etc_note=row.get("비고"),
        )
        supabase.table("youtube_stats").insert({
            "target_id": target_id,
            "subscriber_count": clean_int(row.get("구독자수")),
            "total_views": clean_int(row.get("총조회수")),
            "account_name": clean(row.get("계정명")),
            "open_date": clean(row.get("개설일")),
            "total_videos": clean_int(row.get("총영상수")),
            "most_viewed_title": clean(row.get("최다조회영상")),
            "most_viewed_link": clean(row.get("최다조회영상링크")),
            "most_viewed_views": clean_int(row.get("최다조회영상조회수")),
            "main_source": clean(row.get("주요자료출처")),
        }).execute()
        count += 1
    print(f"  -> YouTube {count}건 완료")


if __name__ == "__main__":
    if not os.path.exists(EXCEL_PATH):
        print(f"'{EXCEL_PATH}' 파일을 찾을 수 없습니다. 암호 풀린 엑셀을 이 이름으로 프로젝트 폴더에 넣어주세요.")
    else:
        seed_web()
        seed_app("2026 2분기 기상정보 유통조사(app-play스토어)", "playstore")
        seed_app("2026 2분기 기상정보 유통조사(app-앱스토어) ", "appstore")  # 시트 이름 끝 공백 주의
        seed_youtube()
        print("\n전체 적재 완료! Supabase Table Editor에서 확인해보세요.")