import os
import glob
import subprocess
from dotenv import load_dotenv
from supabase import create_client
from faster_whisper import WhisperModel

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

TEMP_DIR = "yt_temp_audio"
VIDEOS_PER_CHANNEL = 3          # 채널당 최근 영상 몇 개까지 검사할지
MAX_SECONDS_PER_VIDEO = 180     # 영상당 앞부분 3분만 다운로드 (출처는 보통 초반 소개에 나옴 + 시간 절약)

WEATHER_KEYWORDS = [
    "날씨", "기상", "기후", "weather", "생활기상지수", "보건기상지수",
    "레저", "태풍", "집중호우",
]
PROVIDER_KEYWORDS = [
    "기상청", "웨더아이", "아큐웨더", "웨더뉴스", "웨더채널", "케이웨더",
    "산림청", "공공데이터포털", "국외기상청", "NOAA", "JMA", "ECMWF", "윈디", "Windy",
]

# faster-whisper 모델 로드 (처음 실행 시 모델 파일을 자동으로 내려받습니다. 시간이 좀 걸릴 수 있어요)
# CPU만 있는 컴퓨터라면 "small" 이하 모델을 추천합니다. GPU가 있으면 device="cuda"로 바꿔도 됩니다.
print("음성인식 모델을 불러오는 중입니다 (처음 한 번만 시간이 걸려요)...")
model = WhisperModel("small", device="cpu", compute_type="int8")


def get_youtube_targets():
    """DB에서 YouTube 채널 대상을 가져옵니다. youtube_stats에 저장된 계정명(account_name)을 사용합니다."""
    targets = supabase.table("survey_targets").select("id, target_name").eq("media_type", "YouTube").execute().data
    result = []
    for t in targets:
        stats = (
            supabase.table("youtube_stats")
            .select("account_name")
            .eq("target_id", t["id"])
            .limit(1)
            .execute()
            .data
        )
        account_name = stats[0]["account_name"] if stats else None
        if account_name:
            result.append({"id": t["id"], "target_name": t["target_name"], "account_name": account_name})
    return result


def get_recent_video_urls(account_name, limit=VIDEOS_PER_CHANNEL):
    """yt-dlp로 채널의 최신 영상 URL 목록을 가져옵니다 (다운로드 없이 목록만)."""
    channel_url = f"https://www.youtube.com/{account_name}/videos"
    cmd = [
        "yt-dlp", "--flat-playlist", "--print", "%(id)s|||%(title)s",
        "--playlist-end", str(limit), channel_url,
    ]
    try:
        output = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print("  채널 목록 조회 시간 초과")
        return []

    videos = []
    for line in output.stdout.strip().split("\n"):
        if "|||" not in line:
            continue
        video_id, title = line.split("|||", 1)
        videos.append({
            "id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
    return videos


def download_audio(video_url, out_path_no_ext):
    """영상의 앞부분(MAX_SECONDS_PER_VIDEO)만 오디오로 다운로드합니다."""
    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        "--extract-audio", "--audio-format", "mp3",
        "--download-sections", f"*0-{MAX_SECONDS_PER_VIDEO}",
        "-o", out_path_no_ext + ".%(ext)s",
        video_url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print("  오디오 다운로드 시간 초과")
        return None

    matches = glob.glob(out_path_no_ext + ".*")
    return matches[0] if matches else None


def transcribe_audio(audio_path):
    """faster-whisper로 오디오를 텍스트로 변환합니다."""
    segments, _ = model.transcribe(audio_path, language="ko")
    return " ".join(segment.text for segment in segments)


def analyze_text(title, transcript):
    full_text = f"{title} {transcript}"
    weather_hits = [kw for kw in WEATHER_KEYWORDS if kw in full_text]
    provider_hits = [kw for kw in PROVIDER_KEYWORDS if kw in full_text]
    return weather_hits, provider_hits


def save_result(target_id, video, weather_hits, provider_hits):
    source_marked = "기재" if provider_hits else "미기재"
    supabase.table("survey_results").insert({
        "target_id": target_id,
        "source_marked": source_marked,
        "source_names": ", ".join(provider_hits) if provider_hits else None,
        "etc_note": f"[STT분석] {video['title']} ({video['url']}) / 기상키워드: {', '.join(weather_hits) if weather_hits else '없음'}",
        "action_status": "해당없음",
    }).execute()


def run():
    os.makedirs(TEMP_DIR, exist_ok=True)
    channels = get_youtube_targets()
    print(f"총 {len(channels)}개 채널을 검사합니다.\n")

    for i, channel in enumerate(channels, start=1):
        print(f"[{i}/{len(channels)}] 채널: {channel['target_name']} ({channel['account_name']})")
        videos = get_recent_video_urls(channel["account_name"])

        if not videos:
            print("  -> 영상 목록을 가져오지 못했습니다 (채널 비공개/삭제 가능성)")
            continue

        for video in videos:
            print(f"  - 영상: {video['title']}")
            audio_path = download_audio(video["url"], os.path.join(TEMP_DIR, video["id"]))

            if audio_path is None:
                print("    오디오 다운로드 실패, 건너뜁니다.")
                continue

            transcript = transcribe_audio(audio_path)
            os.remove(audio_path)  # 다 쓴 오디오 파일은 바로 삭제 (용량 절약)

            weather_hits, provider_hits = analyze_text(video["title"], transcript)
            print(f"    기상 키워드: {weather_hits or '없음'} / 제공사 언급: {provider_hits or '없음'}")

            save_result(channel["id"], video, weather_hits, provider_hits)

    print("\n===== 전체 검사 완료 =====")


if __name__ == "__main__":
    run()
