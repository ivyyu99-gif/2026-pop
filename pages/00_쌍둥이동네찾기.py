import os
import re
import glob
import unicodedata
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ──────────────────────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="지역별 연령별 인구구조", layout="wide")
st.title("📊 지역별 연령별 인구구조 & 전국 유사 지역 분석")
st.caption("행정안전부 주민등록 연령별 인구현황 CSV 기반")

# 데이터 파일명 (업로드한 원본 파일명 그대로)
DATA_FILENAME = "202606_202606_연령별인구현황_월간.csv"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 이 스크립트가 저장소 루트(main.py 옆)가 아니라 `pages/` 하위 폴더에 있는
# 멀티페이지 앱 구조일 수도 있으므로, 아래 후보 폴더들을 순서대로 찾아본다:
#   1) 현재 작업 디렉터리 (Streamlit Cloud는 보통 저장소 루트에서 실행됨)
#   2) 이 스크립트 파일이 있는 폴더
#   3) 이 스크립트 파일 폴더의 상위 폴더 (예: pages/ 의 부모 = 저장소 루트)
CANDIDATE_DIRS = list(
    dict.fromkeys(
        [
            os.getcwd(),
            SCRIPT_DIR,
            os.path.dirname(SCRIPT_DIR),
        ]
    )
)


def find_data_file(candidate_dirs, target_filename: str):
    """여러 후보 폴더를 돌면서 데이터 파일을 찾는다.

    한글 파일명은 macOS(NFD)와 Linux/GitHub(NFC)에서 유니코드 정규화 형태가 달라
    '눈에는 똑같은 파일명'인데도 os.path.exists()가 실패하는 경우가 흔하다.
    그래서 각 후보 폴더마다 (1) 정확히 일치, (2) 유니코드 정규화 후 일치,
    (3) 파일명에 핵심 키워드 포함, 순서로 폴백하며 찾는다.
    """
    target_norm = unicodedata.normalize("NFC", target_filename)
    checked = []  # 진단용: [(폴더, 그 폴더의 csv 목록), ...]

    for directory in candidate_dirs:
        if not directory or not os.path.isdir(directory):
            continue

        exact_path = os.path.join(directory, target_filename)
        if os.path.exists(exact_path):
            return exact_path, checked

        csv_files = glob.glob(os.path.join(directory, "*.csv"))

        # 유니코드 정규화 형태만 다른 동일 파일명
        for f in csv_files:
            if unicodedata.normalize("NFC", os.path.basename(f)) == target_norm:
                return f, checked

        # 핵심 키워드가 포함된 csv (파일명이 조금 다르게 저장된 경우 대비)
        for f in csv_files:
            if "연령별인구현황" in unicodedata.normalize("NFC", os.path.basename(f)):
                return f, checked

        # 그 폴더에 csv가 이 파일 하나뿐이라면 그것을 사용
        if len(csv_files) == 1:
            return csv_files[0], checked

        checked.append((directory, csv_files))

    return None, checked


# ──────────────────────────────────────────────────────────────
# 데이터 로딩 & 전처리
# ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="데이터를 불러오는 중입니다...")
def load_data(path: str) -> pd.DataFrame:
    """행정안전부 연령별 인구현황 CSV를 long-format DataFrame으로 변환한다."""

    df = None
    for enc in ("cp949", "euc-kr", "utf-8"):
        try:
            df = pd.read_csv(path, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise ValueError("CSV 인코딩을 확인할 수 없습니다 (cp949/euc-kr/utf-8 모두 실패).")

    region_col = df.columns[0]

    # 원본 문자열의 중복 공백만 정리 (행정코드는 그대로 유지 → 지역명 유일성 보장)
    # 예: "세종특별자치시  (3600000000)" -> "세종특별자치시 (3600000000)"
    df["지역명"] = df[region_col].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()

    # 코드를 제거한 이름 부분만 따로 추출 → 행정구역 단위(시도/시군구/읍면동) 판별용
    df["_이름부분"] = df["지역명"].str.replace(r"\s*\(\d+\)\s*$", "", regex=True).str.strip()

    def classify_level(name: str) -> str:
        tokens = name.split()
        if len(tokens) <= 1:
            return "시도"
        elif len(tokens) == 2:
            return "시군구"
        else:
            return "읍면동"

    df["행정구역단위"] = df["_이름부분"].apply(classify_level)

    # 연령별 인구수 열 패턴: "YYYY년MM월_성별_N세" 또는 "YYYY년MM월_성별_100세 이상"
    age_col_pattern = re.compile(r"^(\d{4}년\d{2}월)_(계|남|여)_(\d+세|100세 이상)$")
    gender_map = {"계": "전체", "남": "남자", "여": "여자"}

    age_cols = [c for c in df.columns if age_col_pattern.match(c)]
    if not age_cols:
        raise ValueError("연령별 인구수 열을 찾을 수 없습니다. CSV 형식을 확인해 주세요.")

    records = []
    for col in age_cols:
        _, gender_raw, age_raw = age_col_pattern.match(col).groups()
        gender = gender_map[gender_raw]
        age = 100 if age_raw == "100세 이상" else int(age_raw.replace("세", ""))

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
                    "행정구역단위": df["행정구역단위"].values,
                    "성별": gender,
                    "연령": age,
                    "인구수": values.values,
                }
            )
        )

    long_df = pd.concat(records, ignore_index=True)
    return long_df


@st.cache_data(show_spinner="유사 지역 계산을 위한 인구 비율표를 만드는 중입니다...")
def build_proportion_matrix(long_df: pd.DataFrame) -> pd.DataFrame:
    """지역별 연령(0~100) 인구 '비율' 매트릭스를 만든다 (전체 성별 기준).

    규모(인구수)가 전혀 다른 지역끼리도 '인구구조의 모양'을 비교할 수 있도록
    각 지역의 연령별 인구수를 해당 지역 총인구수로 나눈 비율로 정규화한다.
    """
    base = long_df[long_df["성별"] == "전체"]
    pivot = base.pivot_table(index="지역명", columns="연령", values="인구수", aggfunc="sum", fill_value=0)
    pivot = pivot.reindex(columns=range(0, 101), fill_value=0)

    totals = pivot.sum(axis=1)
    valid = totals > 0
    prop = pivot.loc[valid].div(totals.loc[valid], axis=0)
    return prop


def find_similar_regions(
    prop: pd.DataFrame, target_region: str, candidate_regions: list, top_n: int = 5
) -> pd.DataFrame:
    """유클리드 거리(연령별 인구비율 기준) 기준으로 target_region과 가장 유사한 지역 top_n을 찾는다."""
    target_vec = prop.loc[target_region].values
    candidates = prop.loc[[r for r in candidate_regions if r != target_region and r in prop.index]]

    diff = candidates.values - target_vec
    dist = np.sqrt((diff ** 2).sum(axis=1))

    result = pd.DataFrame({"지역명": candidates.index, "거리": dist})
    result = result.sort_values("거리").head(top_n).reset_index(drop=True)
    result.index = result.index + 1  # 1위부터 표시
    return result


# ──────────────────────────────────────────────────────────────
# 데이터 로드 (같은 폴더의 고정 파일 사용)
# ──────────────────────────────────────────────────────────────
DATA_PATH, checked_dirs = find_data_file(CANDIDATE_DIRS, DATA_FILENAME)

if DATA_PATH is None:
    st.error(
        f"데이터 파일을 찾을 수 없습니다.\n\n"
        f"`{DATA_FILENAME}` 파일이 저장소 안에 있는지 확인해 주세요."
    )
    with st.expander("🔍 진단 정보 (실제로 확인한 폴더와 파일 목록)"):
        for directory, csv_files in checked_dirs:
            st.write(f"확인한 폴더: `{directory}`")
            try:
                all_files = os.listdir(directory)
            except Exception as e:
                all_files = [f"(폴더 목록을 읽는 중 오류: {e})"]
            st.code("\n".join(all_files) if all_files else "(파일 없음)")
            if csv_files:
                st.caption("이 폴더의 .csv 파일: " + ", ".join(os.path.basename(f) for f in csv_files))
            st.divider()
        st.caption(
            "저장소(GitHub)에 CSV 파일이 실제로 커밋되어 있는지, "
            "또는 파일명에 오타나 공백 차이가 없는지 확인해 주세요. "
            "이 앱이 `pages/` 폴더 안의 스크립트라면, 저장소 루트에 CSV가 있어야 위 후보 폴더 중 하나에서 찾아집니다."
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
st.sidebar.subheader("🏙️ 지역 선택 (인구구조 비교용)")
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

gender_option = st.sidebar.radio("성별", options=["전체", "남자", "여자", "남/여 비교"], index=0)

# ──────────────────────────────────────────────────────────────
# 1. 선택 지역 인구구조 (원 인구수 기준 꺾은선 그래프)
# ──────────────────────────────────────────────────────────────
st.header("1️⃣ 선택 지역 인구구조")

fig1 = go.Figure()
line_dash_map = {"전체": "solid", "남자": "dash", "여자": "dot"}

for region in selected_regions:
    region_df = long_df[long_df["지역명"] == region]
    genders_to_plot = ["남자", "여자"] if gender_option == "남/여 비교" else [gender_option]

    for g in genders_to_plot:
        plot_df = region_df[region_df["성별"] == g].sort_values("연령")
        if plot_df.empty:
            continue
        label = f"{region} - {g}" if len(selected_regions) > 1 or gender_option == "남/여 비교" else region
        fig1.add_trace(
            go.Scatter(
                x=plot_df["연령"],
                y=plot_df["인구수"],
                mode="lines",
                name=label,
                line=dict(dash=line_dash_map.get(g, "solid")),
                hovertemplate="연령: %{x}세<br>인구수: %{y:,.0f}명<extra>" + label + "</extra>",
            )
        )

fig1.update_layout(
    xaxis_title="연령 (세, 100=100세 이상)",
    yaxis_title="인구수 (명)",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=550,
)
fig1.update_yaxes(tickformat=",")
st.plotly_chart(fig1, use_container_width=True)

with st.expander("📋 선택 지역 요약 통계 보기"):
    summary_rows = []
    for region in selected_regions:
        region_total = long_df[(long_df["지역명"] == region) & (long_df["성별"] == "전체")]["인구수"].sum()
        summary_rows.append({"지역명": region, "총인구수": int(region_total)})
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

st.divider()

# ──────────────────────────────────────────────────────────────
# 2. 전국 유사 지역 Top 5
# ──────────────────────────────────────────────────────────────
st.header("2️⃣ 전국에서 인구구조가 가장 유사한 지역 Top 5")
st.caption(
    "지역별 연령(0~100세) 인구 **비율**(해당 지역 총인구수 대비)의 유클리드 거리를 기준으로 계산합니다. "
    "인구 규모가 아니라 '인구구조의 모양(연령대별 비중)'이 얼마나 비슷한지를 비교합니다."
)

prop_matrix = build_proportion_matrix(long_df)

col1, col2 = st.columns([2, 1])
with col1:
    target_default = selected_regions[0] if selected_regions[0] in prop_matrix.index else all_regions[0]
    target_region = st.selectbox(
        "기준 지역 선택 (목록 선택 또는 직접 입력 검색)",
        options=sorted(prop_matrix.index.tolist()),
        index=sorted(prop_matrix.index.tolist()).index(target_default),
    )
with col2:
    same_level_only = st.checkbox(
        "동일 행정구역 단위끼리만 비교 (시도/시군구/읍면동)",
        value=True,
        help="체크 시, 예를 들어 '읍면동'을 선택하면 다른 읍면동끼리만 비교합니다. "
        "해제하면 시/도, 시/군/구, 읍/면/동을 모두 섞어서 비교합니다.",
    )

target_level = long_df.loc[long_df["지역명"] == target_region, "행정구역단위"].iloc[0]

if same_level_only:
    candidate_pool = long_df.loc[long_df["행정구역단위"] == target_level, "지역명"].unique().tolist()
else:
    candidate_pool = all_regions

top5_df = find_similar_regions(prop_matrix, target_region, candidate_pool, top_n=5)

if top5_df.empty:
    st.warning("비교할 후보 지역이 없습니다. '동일 행정구역 단위끼리만 비교' 옵션을 해제해 보세요.")
else:
    # 총인구수 붙이기
    totals_map = (
        long_df[long_df["성별"] == "전체"].groupby("지역명")["인구수"].sum().to_dict()
    )
    top5_df["총인구수"] = top5_df["지역명"].map(totals_map).astype(int)
    top5_df_display = top5_df.rename(columns={"거리": "거리(작을수록 유사)"})
    top5_df_display["거리(작을수록 유사)"] = top5_df_display["거리(작을수록 유사)"].round(5)

    st.dataframe(top5_df_display, use_container_width=True)

    # ── 유사 지역 인구구조 비교 그래프 (Plotly, 인구 비율 기준) ──
    fig2 = go.Figure()

    target_vec = prop_matrix.loc[target_region] * 100  # %
    fig2.add_trace(
        go.Scatter(
            x=target_vec.index,
            y=target_vec.values,
            mode="lines",
            name=f"⭐ {target_region} (기준)",
            line=dict(width=4, color="black"),
            hovertemplate="연령: %{x}세<br>비율: %{y:.2f}%%<extra>" + target_region + "</extra>",
        )
    )

    for _, row in top5_df.iterrows():
        r = row["지역명"]
        vec = prop_matrix.loc[r] * 100
        fig2.add_trace(
            go.Scatter(
                x=vec.index,
                y=vec.values,
                mode="lines",
                name=r,
                line=dict(width=1.8, dash="dash"),
                hovertemplate="연령: %{x}세<br>비율: %{y:.2f}%%<extra>" + r + "</extra>",
            )
        )

    fig2.update_layout(
        title=f"'{target_region}'과(와) 인구구조가 유사한 전국 Top 5 지역 비교",
        xaxis_title="연령 (세, 100=100세 이상)",
        yaxis_title="해당 연령 인구 비율 (%)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=600,
    )
    st.plotly_chart(fig2, use_container_width=True)
