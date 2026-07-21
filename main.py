import os
import re
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ──────────────────────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="지역별 연령별 인구구조", layout="wide")
st.title("📊 지역별 연령별 인구구조 (꺾은선 그래프)")
st.caption("행정안전부 주민등록 연령별 인구현황 CSV 기반")

# 코드 파일과 같은 폴더에 위치한 데이터 파일명
DATA_FILENAME = "202606_202606_연령별인구현황_월간.csv"
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), DATA_FILENAME)


# ──────────────────────────────────────────────────────────────
# 데이터 로딩 & 전처리
# ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="데이터를 불러오는 중입니다...")
def load_data(path: str) -> pd.DataFrame:
    """행정안전부 연령별 인구현황 CSV를 long-format DataFrame으로 변환한다."""

    # 인코딩 자동 판별 (해당 데이터는 보통 CP949/EUC-KR)
    df = None
    for enc in ("cp949", "euc-kr", "utf-8"):
        try:
            df = pd.read_csv(path, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise ValueError("CSV 인코딩을 확인할 수 없습니다 (cp949/euc-kr/utf-8 모두 실패).")

    # 첫 번째 열은 "행정구역명(행정코드)" 형태
    region_col = df.columns[0]

    # 행정구역명에서 코드(괄호) 제거한 표시용 이름 생성
    df["지역명"] = df[region_col].astype(str).str.replace(r"\s*\(\d+\)\s*$", "", regex=True).str.strip()

    # 연령별 인구수 열 패턴: "YYYY년MM월_성별_N세" 또는 "YYYY년MM월_성별_100세 이상"
    age_col_pattern = re.compile(r"^(\d{4}년\d{2}월)_(계|남|여)_(\d+세|100세 이상)$")

    gender_map = {"계": "전체", "남": "남자", "여": "여자"}
    records = []

    age_cols = [c for c in df.columns if age_col_pattern.match(c)]
    if not age_cols:
        raise ValueError("연령별 인구수 열을 찾을 수 없습니다. CSV 형식을 확인해 주세요.")

    for col in age_cols:
        _, gender_raw, age_raw = age_col_pattern.match(col).groups()
        gender = gender_map[gender_raw]
        age = 100 if age_raw == "100세 이상" else int(age_raw.replace("세", ""))

        # 콤마 제거 후 숫자 변환
        values = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
            .replace("", "0")
            .astype(float)
        )

        records.append(
            pd.DataFrame(
                {
                    "지역명": df["지역명"].values,
                    "성별": gender,
                    "연령": age,
                    "인구수": values.values,
                }
            )
        )

    long_df = pd.concat(records, ignore_index=True)
    return long_df


# ──────────────────────────────────────────────────────────────
# 데이터 로드 (같은 폴더의 고정 파일 사용)
# ──────────────────────────────────────────────────────────────
if not os.path.exists(DATA_PATH):
    st.error(
        f"데이터 파일을 찾을 수 없습니다.\n\n"
        f"`app.py`와 같은 폴더에 `{DATA_FILENAME}` 파일이 있는지 확인해 주세요."
    )
    st.stop()

try:
    long_df = load_data(DATA_PATH)
except ValueError as e:
    st.error(str(e))
    st.stop()

all_regions = sorted(long_df["지역명"].unique().tolist())

# ──────────────────────────────────────────────────────────────
# 사이드바: 지역 선택 (드롭다운에서 선택 + 직접 검색/입력 모두 가능)
# ──────────────────────────────────────────────────────────────
st.sidebar.header("⚙️ 설정")
st.sidebar.subheader("🏙️ 지역 선택")
st.sidebar.caption("목록에서 선택하거나, 지역명을 입력해 검색할 수 있습니다.")

default_region = [all_regions[0]] if all_regions else []
selected_regions = st.sidebar.multiselect(
    "지역명 (여러 지역 비교 가능)",
    options=all_regions,
    default=default_region,
    placeholder="지역명을 입력하거나 목록에서 선택하세요",
)

if not selected_regions:
    st.warning("최소 한 개 이상의 지역을 선택해 주세요.")
    st.stop()

# ──────────────────────────────────────────────────────────────
# 사이드바: 성별 선택
# ──────────────────────────────────────────────────────────────
gender_option = st.sidebar.radio("성별", options=["전체", "남자", "여자", "남/여 비교"], index=0)

# ──────────────────────────────────────────────────────────────
# 그래프 그리기 (Plotly)
# ──────────────────────────────────────────────────────────────
fig = go.Figure()

line_dash_map = {"전체": "solid", "남자": "dash", "여자": "dot"}

for region in selected_regions:
    region_df = long_df[long_df["지역명"] == region]

    if gender_option == "남/여 비교":
        genders_to_plot = ["남자", "여자"]
    else:
        genders_to_plot = [gender_option]

    for g in genders_to_plot:
        plot_df = region_df[region_df["성별"] == g].sort_values("연령")
        if plot_df.empty:
            continue
        label = f"{region} - {g}" if len(selected_regions) > 1 or gender_option == "남/여 비교" else region
        fig.add_trace(
            go.Scatter(
                x=plot_df["연령"],
                y=plot_df["인구수"],
                mode="lines",
                name=label,
                line=dict(dash=line_dash_map.get(g, "solid")),
                hovertemplate="연령: %{x}세<br>인구수: %{y:,.0f}명<extra>" + label + "</extra>",
            )
        )

fig.update_layout(
    xaxis_title="연령 (세, 100=100세 이상)",
    yaxis_title="인구수 (명)",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=600,
)
fig.update_yaxes(tickformat=",")

st.plotly_chart(fig, use_container_width=True)

# ──────────────────────────────────────────────────────────────
# 요약 테이블
# ──────────────────────────────────────────────────────────────
with st.expander("📋 선택 지역 요약 통계 보기"):
    summary_rows = []
    for region in selected_regions:
        region_total = long_df[(long_df["지역명"] == region) & (long_df["성별"] == "전체")]["인구수"].sum()
        summary_rows.append({"지역명": region, "총인구수": int(region_total)})
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
