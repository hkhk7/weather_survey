"""
backfill_providers.py
survey_results.source_names(텍스트, "기상청, 웨더아이" 형태)에는 있지만
source_providers 테이블에는 개별로 안 들어간 데이터를 채워 넣는 1회성 스크립트입니다.
(App, YouTube 초기 시딩 데이터가 여기 해당합니다)

실행: python backfill_providers.py
"""

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)


def already_has_providers(result_id):
    res = supabase.table("source_providers").select("id").eq("result_id", result_id).limit(1).execute()
    return len(res.data) > 0


def run():
    results = supabase.table("survey_results").select("id, source_names").execute().data
    filled = 0
    skipped = 0

    for r in results:
        if not r.get("source_names"):
            continue
        if already_has_providers(r["id"]):
            skipped += 1
            continue

        provider_names = [p.strip() for p in r["source_names"].split(",") if p.strip()]
        for name in provider_names:
            supabase.table("source_providers").insert({
                "result_id": r["id"],
                "provider_name": name,
            }).execute()
        filled += 1

    print(f"완료: {filled}건 새로 채움, {skipped}건은 이미 있어서 건너뜀")


if __name__ == "__main__":
    run()
