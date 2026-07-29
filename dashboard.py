"""
dashboard.py
기상정보 유통현황 조사 결과를 한눈에 보는 대시보드입니다.

사전 설치:
    pip install streamlit plotly pandas

실행 (다른 스크립트와 다르게 python이 아니라 streamlit으로 실행합니다):
    streamlit run dashboard.py

실행하면 자동으로 브라우저가 열리고 http://localhost:8501 에서 볼 수 있습니다.
"""

import os
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def get_config(name):
    """로컬에서는 .env 파일을, Streamlit Cloud에 배포했을 때는 그쪽의 Secrets 설정을 읽습니다."""
    if hasattr(st, "secrets") and name in st.secrets:
        return st.secrets[name]
    return os.environ.get(name)


url = get_config("SUPABASE_URL")
key = get_config("SUPABASE_KEY")
supabase = create_client(url, key)

st.set_page_config(page_title="기상정보 유통현황 대시보드", layout="wide")


# 5분 동안은 DB를 다시 안 불러오고 캐시해둡니다 (매번 새로고침할 때마다 DB 부담 주지 않기 위함)
@st.cache_data(ttl=300)
def load_table(name):
    return pd.DataFrame(supabase.table(name).select("*").execute().data)


targets = load_table("survey_targets")
results = load_table("survey_results")
providers = load_table("source_providers")
app_stats = load_table("app_stats")
youtube_stats = load_table("youtube_stats")

st.title("🌦 기상정보 유통현황 자동조사 대시보드")

if st.button("🔄 데이터 새로고침"):
    st.cache_data.clear()
    st.rerun()

if targets.empty:
    st.warning("아직 DB에 데이터가 없습니다. seed_data.py를 먼저 실행해주세요.")
    st.stop()

# ---------- 최신 조사 결과만 대상별로 하나씩 추리기 ----------
if not results.empty:
    results["survey_date"] = pd.to_datetime(results["survey_date"])
    latest_results = results.sort_values("survey_date").groupby("target_id").tail(1)
else:
    latest_results = pd.DataFrame(columns=["target_id", "source_marked"])

merged = targets.merge(latest_results, left_on="id", right_on="target_id", how="left", suffixes=("", "_r"))

# ---------- 상단 요약 카드 ----------
col1, col2, col3, col4 = st.columns(4)
verified = merged[merged["discovery_status"] == "verified"]
candidates = merged[merged["discovery_status"] == "candidate"]
missing = merged[merged["source_marked"] == "미기재"]

col1.metric("전체 조사대상", len(merged))
col2.metric("검증완료(기존)", len(verified))
col3.metric("신규 발굴 후보(검토대기)", len(candidates))
col4.metric("출처 미기재", len(missing))

st.divider()

# ---------- 매체별 출처 표기 현황 ----------
st.subheader("매체별 출처 표기 현황")
status_data = merged.dropna(subset=["source_marked"])
if not status_data.empty:
    status_counts = status_data.groupby(["media_type", "source_marked"]).size().reset_index(name="건수")
    fig = px.bar(status_counts, x="media_type", y="건수", color="source_marked", barmode="stack",
                 color_discrete_map={"기재": "#4CAF50", "미기재": "#F44336", "표출중단": "#9E9E9E"})
    st.plotly_chart(fig, use_container_width=True)
else:
    st.write("아직 조사 결과가 없습니다.")

# ---------- 제공사별 유통현황 ----------
st.subheader("제공사별 유통현황")
if not providers.empty and not results.empty:
    prov = providers.merge(results[["id", "target_id"]], left_on="result_id", right_on="id", how="left")
    prov = prov.merge(targets[["id", "media_type"]], left_on="target_id", right_on="id", how="left", suffixes=("", "_t"))

    media_options = ["전체"] + sorted(prov["media_type"].dropna().unique().tolist())
    selected_media = st.selectbox("매체 선택", media_options)
    filtered = prov if selected_media == "전체" else prov[prov["media_type"] == selected_media]

    provider_counts = filtered["provider_name"].value_counts().reset_index()
    provider_counts.columns = ["제공사", "건수"]
    fig2 = px.pie(provider_counts, names="제공사", values="건수", hole=0.4)
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.write("아직 제공사 데이터가 없습니다.")

# ---------- KSIC 분류별 현황 ----------
st.subheader("한국표준산업분류(KSIC)별 현황 (Web/App)")
ksic_data = targets[targets["media_type"].isin(["Web", "App"])].dropna(subset=["ksic_name"]) if "ksic_name" in targets.columns else pd.DataFrame()
if not ksic_data.empty:
    ksic_counts = ksic_data.groupby(["ksic_name", "media_type"]).size().reset_index(name="건수")
    fig3 = px.bar(ksic_counts, x="ksic_name", y="건수", color="media_type", barmode="group")
    st.plotly_chart(fig3, use_container_width=True)
else:
    st.write("아직 KSIC 분류 데이터가 없습니다.")

# ---------- 앱 다운로드 분포 ----------
st.subheader("앱 다운로드 수 분포")
if not app_stats.empty and app_stats["download_range"].notna().any():
    dl = app_stats["download_range"].dropna().value_counts().reset_index()
    dl.columns = ["다운로드 구간", "건수"]
    st.bar_chart(dl.set_index("다운로드 구간"))
else:
    st.write("아직 앱 다운로드 데이터가 없습니다.")

# ---------- 유튜브 채널 랭킹 ----------
st.subheader("유튜브 채널 랭킹 (구독자 수 기준 상위 10)")
if not youtube_stats.empty:
    yt = youtube_stats.merge(targets[["id", "target_name"]], left_on="target_id", right_on="id", how="left")
    top10 = yt.sort_values("subscriber_count", ascending=False, na_position="last").head(10)
    st.dataframe(top10[["target_name", "subscriber_count", "total_views", "account_name"]]
                 .rename(columns={"target_name": "채널명", "subscriber_count": "구독자수",
                                   "total_views": "총조회수", "account_name": "계정명"}))
else:
    st.write("아직 유튜브 통계 데이터가 없습니다.")

# ---------- 신규 발굴 후보 검토 목록 ----------
st.subheader("신규 발굴 후보 (검토 대기)")
if not candidates.empty:
    st.dataframe(candidates[["target_name", "media_type", "url", "discovered_by_keyword"]]
                 .rename(columns={"target_name": "이름", "media_type": "매체", "url": "링크", "discovered_by_keyword": "발견 키워드"}))
    st.caption("이 목록은 아직 사람 확인이 필요한 신규 후보입니다. 확인 후 Supabase Table Editor에서 discovery_status를 'verified'로 바꿔주세요.")
else:
    st.write("검토 대기 중인 신규 후보가 없습니다.")

# ---------- 시점별 출처 미기재 건수 추이 ----------
st.subheader("시점별 출처 미기재 건수 추이")
if not results.empty:
    trend = results[results["source_marked"] == "미기재"].copy()
    if not trend.empty:
        trend_counts = trend.groupby(trend["survey_date"].dt.date).size().reset_index(name="미기재건수")
        fig4 = px.line(trend_counts, x="survey_date", y="미기재건수", markers=True)
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.write("아직 '미기재' 이력이 없습니다 (좋은 신호입니다!).")