"""
dashboard.py (v2)
2분기 보고서 구조를 그대로 따라가는 대시보드:
  1) 조사 개요 (요약 + 매체별 출처표기 현황)
  2) 제공사 분석 (기상청/웨더아이/아큐웨더 등 점유율)
  3) 매체별 상세 (KSIC 분류, 앱 다운로드 분포, 유튜브 랭킹)
  4) 신규발굴 검토 (화면에서 바로 승인/반려)
  5) 추이 (분기별 변화)

실행: streamlit run dashboard.py
"""

import os
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def get_config(name):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


url = get_config("SUPABASE_URL")
key = get_config("SUPABASE_KEY")

if not url or not key:
    st.error(
        "SUPABASE_URL / SUPABASE_KEY 값을 찾을 수 없습니다.\n\n"
        "- 로컬 실행: .env 파일을 확인해주세요.\n"
        "- Streamlit Cloud: Manage app → Settings → Secrets 를 확인해주세요."
    )
    st.stop()

supabase = create_client(url, key)

st.set_page_config(page_title="기상정보 유통현황 대시보드", page_icon="🌦", layout="wide")

# ---------- 보고서와 통일된 색상 ----------
MEDIA_COLORS = {"Web": "#3E7CB1", "App": "#F5A623", "YouTube": "#8C8C8C"}
STATUS_COLORS = {"기재": "#3E9B4F", "미기재": "#D9432E", "표출중단": "#9E9E9E"}

st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    div[data-testid="stMetric"] {
        background-color: #F7F9FB;
        border: 1px solid #E3E8EE;
        border-radius: 10px;
        padding: 14px 16px;
    }
    h2, h3 { color: #1F3B57; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def load_table(name):
    return pd.DataFrame(supabase.table(name).select("*").execute().data)


def refresh():
    st.cache_data.clear()
    st.rerun()


targets = load_table("survey_targets")
results = load_table("survey_results")
providers = load_table("source_providers")
app_stats = load_table("app_stats")
youtube_stats = load_table("youtube_stats")

st.title("🌦 기상정보 유통현황 자동조사 대시보드")

top_l, top_r = st.columns([5, 1])
with top_r:
    if st.button("🔄 새로고침", use_container_width=True):
        refresh()

if targets.empty:
    st.warning("아직 DB에 데이터가 없습니다. seed_data.py를 먼저 실행해주세요.")
    st.stop()

# ---------- 최신 조사 결과만 대상별로 하나씩 ----------
if not results.empty:
    results["survey_date"] = pd.to_datetime(results["survey_date"])
    latest_results = results.sort_values("survey_date").groupby("target_id").tail(1)
else:
    latest_results = pd.DataFrame(columns=["target_id", "source_marked"])

merged = targets.merge(latest_results, left_on="id", right_on="target_id", how="left", suffixes=("", "_r"))

verified = merged[merged["discovery_status"] == "verified"]
candidates_df = targets[targets["discovery_status"] == "candidate"]

# ---------- 사이드바 필터 ----------
st.sidebar.header("필터")
quarter_options = ["전체"] + sorted(targets["first_found_quarter"].dropna().unique().tolist())
selected_quarter = st.sidebar.selectbox("조사 분기", quarter_options)

view_merged = merged if selected_quarter == "전체" else merged[merged["first_found_quarter"] == selected_quarter]
view_verified = view_merged[view_merged["discovery_status"] == "verified"]

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📋 조사 개요", "🏢 제공사 분석", "📱 매체별 상세", "🔍 신규발굴 검토", "📈 추이"]
)

# ================= TAB 1: 조사 개요 =================
with tab1:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("전체 조사대상", len(view_verified))
    missing = view_verified[view_verified["source_marked"] == "미기재"]
    col2.metric("출처 미기재", len(missing))
    stopped = view_verified[view_verified["source_marked"] == "표출중단"]
    col3.metric("표출중단", len(stopped))
    col4.metric("신규발굴 검토대기", len(candidates_df))

    st.subheader("기상정보 출처 표기 조사결과")
    status_data = view_verified.dropna(subset=["source_marked"])
    if not status_data.empty:
        media_cols = [m for m in ["Web", "App", "YouTube"] if m in status_data["media_type"].unique()]
        counts = (
            status_data.pivot_table(index="source_marked", columns="media_type", values="id", aggfunc="count", fill_value=0)
            .reindex(index=["기재", "미기재", "표출중단"], fill_value=0)
            .reindex(columns=media_cols, fill_value=0)
        )
        totals_by_media = counts.sum(axis=0)
        percent = counts.div(totals_by_media.replace(0, 1), axis=1) * 100

        # 보고서와 동일한 형태: 구분 | Web 사례수/비율 | App 사례수/비율 | YouTube 사례수/비율 | 합계 사례수/비율
        rows = []
        for status in ["기재", "미기재", "표출중단"]:
            row = {"구분": status}
            for m in media_cols:
                row[f"{m} 사례수"] = int(counts.loc[status, m])
                row[f"{m} 비율(%)"] = round(percent.loc[status, m], 1)
            row["합계 사례수"] = int(counts.loc[status].sum())
            row["합계 비율(%)"] = round(counts.loc[status].sum() / max(counts.values.sum(), 1) * 100, 1)
            rows.append(row)

        total_row = {"구분": "합계"}
        for m in media_cols:
            total_row[f"{m} 사례수"] = int(totals_by_media[m])
            total_row[f"{m} 비율(%)"] = 100.0
        total_row["합계 사례수"] = int(counts.values.sum())
        total_row["합계 비율(%)"] = 100.0
        rows.append(total_row)

        report_table = pd.DataFrame(rows).set_index("구분")
        st.dataframe(report_table, use_container_width=True)
        st.caption(f"※ {selected_quarter if selected_quarter != '전체' else '전체 기간'} 기준, 최신 조사결과 1건씩 반영")

        chart_data = status_data.groupby(["media_type", "source_marked"]).size().reset_index(name="건수")
        fig = px.bar(
            chart_data, x="media_type", y="건수", color="source_marked", barmode="stack",
            color_discrete_map=STATUS_COLORS, text="건수",
        )
        fig.update_traces(textposition="inside")
        fig.update_layout(xaxis_title=None, yaxis_title="건수", legend_title=None, height=350)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("아직 조사 결과가 없습니다.")

# ================= TAB 2: 제공사 분석 =================
with tab2:
    st.subheader("기상정보 제공사별 유통현황 조사결과")
    if not providers.empty and not results.empty:
        prov = providers.merge(results[["id", "target_id"]], left_on="result_id", right_on="id", how="left")
        prov = prov.merge(targets[["id", "media_type"]], left_on="target_id", right_on="id", how="left", suffixes=("", "_t"))

        media_list = [m for m in ["Web", "App", "YouTube"] if m in prov["media_type"].unique()]

        # ---- 보고서와 동일한 형태의 표: 제공사 | 매체별 사례수/비율 | 합계 사례수/비율 ----
        provider_counts = prov.pivot_table(index="provider_name", columns="media_type", values="result_id",
                                            aggfunc="count", fill_value=0).reindex(columns=media_list, fill_value=0)
        totals_by_media = provider_counts.sum(axis=0)
        provider_percent = provider_counts.div(totals_by_media.replace(0, 1), axis=1) * 100

        table_rows = []
        for provider_name in provider_counts.sort_values(by=media_list[0], ascending=False).index:
            row = {"기상정보 제공사": provider_name}
            for m in media_list:
                row[f"{m} 사례수"] = int(provider_counts.loc[provider_name, m])
                row[f"{m} 비율(%)"] = round(provider_percent.loc[provider_name, m], 1)
            total_cases = int(provider_counts.loc[provider_name].sum())
            row["합계 사례수"] = total_cases
            row["합계 비율(%)"] = round(total_cases / max(provider_counts.values.sum(), 1) * 100, 1)
            table_rows.append(row)

        provider_table = pd.DataFrame(table_rows).sort_values("합계 사례수", ascending=False).set_index("기상정보 제공사")
        st.dataframe(provider_table, use_container_width=True)
        st.caption("※ 동일 대상 내 복수의 제공사가 함께 표기된 경우 각각 집계되어, 합계가 조사대상 건수와 다를 수 있습니다.")

        st.markdown("#### 매체별 점유율")
        cols = st.columns(len(media_list)) if media_list else st.columns(1)
        for i, media in enumerate(media_list):
            with cols[i]:
                st.markdown(f"**{media}**")
                subset = prov[prov["media_type"] == media]["provider_name"].value_counts().reset_index()
                subset.columns = ["제공사", "건수"]
                if not subset.empty:
                    fig_pie = px.pie(subset, names="제공사", values="건수", hole=0.4)
                    fig_pie.update_traces(textinfo="percent+label", textposition="inside")
                    fig_pie.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=340)
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.caption("데이터 없음")

        st.markdown("#### 매체별 기상정보 제공처 현황 (전체 비교)")
        provider_media_counts = prov.groupby(["provider_name", "media_type"]).size().reset_index(name="사례수")
        order = provider_media_counts.groupby("provider_name")["사례수"].sum().sort_values(ascending=True).index
        fig_bar = px.bar(
            provider_media_counts, y="provider_name", x="사례수", color="media_type", orientation="h",
            category_orders={"provider_name": list(order)}, color_discrete_map=MEDIA_COLORS, text="사례수",
        )
        fig_bar.update_traces(textposition="outside")
        fig_bar.update_layout(yaxis_title=None, xaxis_title="사례수(건)", legend_title=None, height=550)
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("아직 제공사 데이터가 없습니다. crawl_web_v2.py / youtube_source_check.py를 실행해주세요.")

# ================= TAB 3: 매체별 상세 =================
with tab3:
    st.subheader("한국표준산업분류(KSIC)별 현황 (Web/App)")
    ksic_data = targets[targets["media_type"].isin(["Web", "App"])].dropna(subset=["ksic_name"]) if "ksic_name" in targets.columns else pd.DataFrame()
    if not ksic_data.empty:
        ksic_pivot = ksic_data.pivot_table(index="ksic_name", columns="media_type", values="id", aggfunc="count", fill_value=0)
        ksic_pivot["합계"] = ksic_pivot.sum(axis=1)
        ksic_pivot = ksic_pivot.sort_values("합계", ascending=False)
        st.dataframe(ksic_pivot, use_container_width=True)

        ksic_counts = ksic_data.groupby(["ksic_name", "media_type"]).size().reset_index(name="건수")
        fig3 = px.bar(ksic_counts, x="ksic_name", y="건수", color="media_type", barmode="group",
                      color_discrete_map=MEDIA_COLORS, text="건수")
        fig3.update_traces(textposition="outside")
        fig3.update_layout(xaxis_title=None, legend_title=None, height=400)
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("아직 KSIC 분류 데이터가 없습니다.")

    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("앱 다운로드 수 분포")
        if not app_stats.empty and app_stats["download_range"].notna().any():
            dl = app_stats["download_range"].dropna().value_counts().reset_index()
            dl.columns = ["다운로드 구간", "사례수"]
            dl["비율(%)"] = round(dl["사례수"] / dl["사례수"].sum() * 100, 1)
            st.dataframe(dl.set_index("다운로드 구간"), use_container_width=True)
            st.bar_chart(dl.set_index("다운로드 구간")["사례수"])
        else:
            st.info("아직 앱 다운로드 데이터가 없습니다.")

    with col_b:
        st.subheader("유튜브 채널 랭킹 (구독자 수 상위 10)")
        if not youtube_stats.empty:
            yt = youtube_stats.merge(targets[["id", "target_name"]], left_on="target_id", right_on="id", how="left")
            top10 = yt.sort_values("subscriber_count", ascending=False, na_position="last").head(10)
            display_cols = ["target_name", "subscriber_count", "total_views", "main_source"]
            display_cols = [c for c in display_cols if c in top10.columns]
            st.dataframe(
                top10[display_cols].rename(columns={
                    "target_name": "채널명", "subscriber_count": "구독자수",
                    "total_views": "총조회수", "main_source": "주요출처",
                }),
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("아직 유튜브 통계 데이터가 없습니다.")

# ================= TAB 4: 신규발굴 검토 =================
with tab4:
    st.subheader("신규발굴 후보 검토")
    st.caption(
        "new_target_discovery.py가 자동으로 찾은 후보 목록입니다. "
        "실제로 기상정보를 다루는 대상이 맞는지 확인하고, 아래에서 '승인' 또는 '반려'를 선택한 뒤 "
        "'선택 적용' 버튼을 누르면 그 자리에서 DB에 반영됩니다."
    )

    if candidates_df.empty:
        st.success("검토 대기 중인 신규 후보가 없습니다.")
    else:
        review_df = candidates_df[["id", "target_name", "media_type", "url", "discovered_by_keyword"]].copy()
        review_df.insert(0, "결정", "보류")
        review_df = review_df.rename(columns={
            "target_name": "이름", "media_type": "매체", "url": "링크", "discovered_by_keyword": "발견 키워드",
        })

        edited = st.data_editor(
            review_df,
            column_config={
                "id": None,  # 화면에는 안 보이게 숨김 (내부 참조용)
                "결정": st.column_config.SelectboxColumn("결정", options=["보류", "승인", "반려"], required=True),
                "링크": st.column_config.LinkColumn("링크"),
            },
            hide_index=True,
            use_container_width=True,
            key="candidate_editor",
        )

        if st.button("✅ 선택 적용", type="primary"):
            approved = edited[edited["결정"] == "승인"]
            rejected = edited[edited["결정"] == "반려"]

            for _, row in approved.iterrows():
                supabase.table("survey_targets").update({"discovery_status": "verified"}).eq("id", row["id"]).execute()

            for _, row in rejected.iterrows():
                supabase.table("survey_targets").delete().eq("id", row["id"]).execute()

            st.success(f"승인 {len(approved)}건 반영, 반려 {len(rejected)}건 삭제 완료.")
            refresh()

# ================= TAB 5: 추이 =================
with tab5:
    st.subheader("시점별 출처 미기재 건수 추이")
    if not results.empty:
        trend = results[results["source_marked"] == "미기재"].copy()
        if not trend.empty:
            trend_counts = trend.groupby(trend["survey_date"].dt.date).size().reset_index(name="미기재건수")
            fig4 = px.line(trend_counts, x="survey_date", y="미기재건수", markers=True)
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.success("아직 '미기재' 이력이 없습니다 (좋은 신호입니다!).")

    st.subheader("분기별 조사대상 누적 건수")
    quarter_counts = targets.groupby(["first_found_quarter", "media_type"]).size().reset_index(name="건수")
    if not quarter_counts.empty:
        fig5 = px.bar(quarter_counts, x="first_found_quarter", y="건수", color="media_type",
                      barmode="group", color_discrete_map=MEDIA_COLORS)
        fig5.update_layout(xaxis_title=None, legend_title=None)
        st.plotly_chart(fig5, use_container_width=True)
