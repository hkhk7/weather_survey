"""
youtube_source_check.py
유튜브 채널의 기상정보 출처 표기를 4가지 경로로 종합 확인합니다.

  1) 채널 소개란(About)
  2) 영상 설명란(Description)
  3) 자막(수동 자막 우선, 없으면 자동생성 자막)
  4) 위 3곳 어디에도 없을 때만 → 음성을 STT로 텍스트화 (최후 수단, 시간이 오래 걸림)

사전 설치:
    pip install yt-dlp
    (STT까지 쓰려면 추가로) pip install faster-whisper  + ffmpeg 설치 필요

실행: python youtube_source_check.py
"""

import os
import re
import json
import glob
import subprocess
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

TEMP_DIR = "yt_temp"
VIDEOS_PER_CHANNEL = 3
MAX_SECONDS_FOR_STT = 180   # STT까지 가야 할 경우, 영상 앞부분 3분만 사용

WEATHER_KEYWORDS = ["날씨", "기상", "기후", "weather", "생활기상지수", "보건기상지수", "레저", "태풍", "집중호우"]
PROVIDER_KEYWORDS = [
    "기상청", "웨더아이", "아큐웨더", "웨더뉴스", "웨더채널", "케이웨더",
    "산림청", "공공데이터포털", "국외기상청", "NOAA", "JMA", "ECMWF", "윈디", "Windy",
]

_whisper_model = None  # 필요할 때만 불러옵니다 (안 쓰면 로딩 시간도 안 걸림)


# ---------- 공통 유틸 ----------

def run_yt_dlp_json(args):
    """yt-dlp를 --dump-single-json 모드로 실행하고 결과를 파이썬 dict로 돌려줍니다."""
    cmd = ["yt-dlp", "--skip-download", "--dump-single-json"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def find_keywords(text, keyword_list):
    return [kw for kw in keyword_list if kw in text]


# ---------- 1) 채널 소개란 ----------

def get_channel_description(account_name):
    """채널 About 설명을 가져옵니다. --playlist-items 0 으로 영상 목록은 건너뛰고 채널 정보만 빠르게 받습니다."""
    channel_url = f"https://www.youtube.com/{account_name}"
    data = run_yt_dlp_json(["--playlist-items", "0", channel_url])
    if data:
        return data.get("description", "") or ""
    return ""


# ---------- 영상 목록 ----------

def get_recent_videos(account_name, limit=VIDEOS_PER_CHANNEL):
    channel_url = f"https://www.youtube.com/{account_name}/videos"
    cmd = ["yt-dlp", "--flat-playlist", "--print", "%(id)s|||%(title)s",
           "--playlist-end", str(limit), channel_url]
    try:
        output = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []

    videos = []
    for line in output.stdout.strip().split("\n"):
        if "|||" not in line:
            continue
        vid, title = line.split("|||", 1)
        videos.append({"id": vid, "title": title, "url": f"https://www.youtube.com/watch?v={vid}"})
    return videos


# ---------- 2) 영상 설명란 ----------

def get_video_description(video_url):
    data = run_yt_dlp_json([video_url])
    if data:
        return data.get("description", "") or ""
    return ""


# ---------- 3) 자막 ----------

def vtt_to_text(vtt_path):
    """자막 파일(.vtt)에서 타임스탬프/태그를 빼고 순수 텍스트만 뽑습니다."""
    with open(vtt_path, encoding="utf-8") as f:
        lines = f.readlines()

    texts = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line)  # <00:00:01.234> 같은 태그 제거
        if line and (not texts or texts[-1] != line):  # 자동자막 특성상 반복되는 줄 제거
            texts.append(line)
    return " ".join(texts)


def get_captions_text(video_url, out_path_no_ext):
    """수동 자막을 먼저 시도하고, 없으면 자동생성 자막을 받아옵니다."""
    cmd = [
        "yt-dlp", "--skip-download",
        "--write-sub", "--write-auto-sub",
        "--sub-lang", "ko,ko-orig,en",
        "--sub-format", "vtt",
        "-o", out_path_no_ext,
        video_url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return ""

    matches = glob.glob(out_path_no_ext + "*.vtt")
    if not matches:
        return ""
    text = vtt_to_text(matches[0])
    for m in matches:
        os.remove(m)
    return text


# ---------- 4) 음성인식(STT) - 최후 수단 ----------

def get_stt_text(video_url, out_path_no_ext):
    global _whisper_model
    if _whisper_model is None:
        print("    (자막이 없어 음성인식 모델을 불러옵니다 - 처음엔 시간이 걸려요)")
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")

    cmd = [
        "yt-dlp", "-f", "bestaudio",
        "--extract-audio", "--audio-format", "mp3",
        "--download-sections", f"*0-{MAX_SECONDS_FOR_STT}",
        "-o", out_path_no_ext + ".%(ext)s",
        video_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return ""

    matches = glob.glob(out_path_no_ext + ".*")
    if not matches:
        print(f"    [STT 실패] {result.stderr.strip().splitlines()[-1] if result.stderr else ''}")
        return ""

    audio_path = matches[0]
    segments, _ = _whisper_model.transcribe(audio_path, language="ko")
    text = " ".join(seg.text for seg in segments)
    os.remove(audio_path)
    return text


# ---------- DB ----------

def get_youtube_targets():
    targets = supabase.table("survey_targets").select("id, target_name").eq("media_type", "YouTube").execute().data
    result = []
    for t in targets:
        stats = supabase.table("youtube_stats").select("account_name").eq("target_id", t["id"]).limit(1).execute().data
        account_name = stats[0]["account_name"] if stats else None
        if account_name:
            result.append({"id": t["id"], "target_name": t["target_name"], "account_name": account_name})
    return result


def save_result(target_id, note, providers_by_source):
    all_providers = sorted({p for plist in providers_by_source.values() for p in plist})
    source_marked = "기재" if all_providers else "미기재"
    detail = "; ".join(f"{src}: {', '.join(plist)}" for src, plist in providers_by_source.items() if plist)
    result = supabase.table("survey_results").insert({
        "target_id": target_id,
        "source_marked": source_marked,
        "source_names": ", ".join(all_providers) if all_providers else None,
        "etc_note": f"{note} | 발견경로: {detail if detail else '없음'}",
        "action_status": "해당없음",
    }).execute()
    result_id = result.data[0]["id"]

    # 대시보드의 "제공사 분석" 탭이 이 테이블을 기준으로 그려지므로, 개별 제공사도 함께 저장합니다.
    for provider in all_providers:
        supabase.table("source_providers").insert({
            "result_id": result_id,
            "provider_name": provider,
        }).execute()


# ---------- 메인 ----------

def check_channel(channel):
    print(f"채널: {channel['target_name']} ({channel['account_name']})")
    os.makedirs(TEMP_DIR, exist_ok=True)

    # 1) 채널 소개란 - 채널당 한 번만 확인
    channel_desc = get_channel_description(channel["account_name"])
    channel_providers = find_keywords(channel_desc, PROVIDER_KEYWORDS)
    print(f"  [채널소개] 제공사 언급: {channel_providers or '없음'}")

    videos = get_recent_videos(channel["account_name"])
    if not videos:
        print("  영상 목록을 가져오지 못했습니다.")
        return

    for video in videos:
        print(f"  영상: {video['title']}")
        providers_by_source = {"채널소개": channel_providers}

        # 2) 영상 설명란
        desc = get_video_description(video["url"])
        providers_by_source["영상설명"] = find_keywords(desc, PROVIDER_KEYWORDS)

        # 3) 자막
        caption_text = get_captions_text(video["url"], os.path.join(TEMP_DIR, video["id"]))
        providers_by_source["자막"] = find_keywords(caption_text, PROVIDER_KEYWORDS)

        found_so_far = any(providers_by_source.values())
        has_text_source = bool(caption_text)  # 자막이 있었다면 굳이 STT까지 안 감

        # 4) 위 세 곳에 아무것도 없고, 자막조차 없을 때만 STT 실행 (시간이 오래 걸리는 마지막 수단)
        if not found_so_far and not has_text_source:
            print("    자막/설명/소개 어디에도 없어 음성인식(STT)까지 확인합니다...")
            stt_text = get_stt_text(video["url"], os.path.join(TEMP_DIR, video["id"] + "_audio"))
            providers_by_source["음성인식"] = find_keywords(stt_text, PROVIDER_KEYWORDS)
        else:
            providers_by_source["음성인식"] = []  # 건너뜀

        all_weather_text = f"{video['title']} {desc} {caption_text}"
        weather_hits = find_keywords(all_weather_text, WEATHER_KEYWORDS)

        print(f"    -> 제공사 발견: { {k: v for k, v in providers_by_source.items() if v} or '없음' }")
        print(f"    -> 기상 키워드: {weather_hits or '없음'}")

        note = f"[{video['title']}]({video['url']}) 기상키워드: {', '.join(weather_hits) if weather_hits else '없음'}"
        save_result(channel["id"], note, providers_by_source)


def run():
    channels = get_youtube_targets()
    print(f"총 {len(channels)}개 채널을 검사합니다.\n")
    for i, channel in enumerate(channels, start=1):
        print(f"\n===== [{i}/{len(channels)}] =====")
        check_channel(channel)
    print("\n전체 검사 완료!")


if __name__ == "__main__":
    run()