import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

print("URL:", url)
print("KEY 앞 10글자:", key[:10] if key else None)

supabase = create_client(url, key)

data = {
    "media_type": "Web",
    "target_name": "테스트사이트",
    "url": "https://example.com",
    "first_found_quarter": "2026-Q3"
}

result = supabase.table("survey_targets").insert(data).execute()
print("데이터 삽입 성공!")
print(result)