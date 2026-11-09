import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비구역 실무형 정밀 종전연면적 조서기", layout="wide")
st.title("🏛️ 정비구역 실무형 종전연면적·노후도 정밀 검증 조서")

# ----------------------------------------------------
# 1. 시스템 환경 설정 (디코딩 키 내장)
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("⚙️ 시스템 인증 상태")
    st.success("건축HUB 공공데이터 인증키 장착 완료")
    st.markdown("---")
    st.markdown("""
    **핵심 기능**:
    - **외부 AI 키 불필요**: 공공데이터 API 단독 연동으로 초고속 작동
    - **19자리 PNU 코드** 전수 대조
    - **총괄/일반/동별** 위계 정제 (중복 배제)
    - **지번 목록 가감 인터페이스** 지원
    """)

# ----------------------------------------------------
# 2. 실무 대장 정제 및 PNU 생성 유틸리티
# ----------------------------------------------------
def clean_and_aggregate_registers(raw_df):
    df = raw_df.copy()
    str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun", "pnu"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    if "mgmBldrgstPk" in df.columns:
        df = df.drop_duplicates(subset=["mgmBldrgstPk"])

    df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
    df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)
    df["platArea"] = pd.to_numeric(df.get("platArea", 0), errors="coerce").fillna(0.0)

    valid_indices = []
    grouped = df.groupby("platPlc")

    for plat_plc, group in grouped:
        has_summary = (group["regstrKindCd"] == "2").any()
        if has_summary:
            summary_rows = group[group["regstrKindCd"] == "2"]
            valid_indices.extend(summary_rows.index.tolist())
        else:
            normal_rows = group[group["regstrKindCd"].isin(["1", "3", ""])]
            valid_indices.extend(normal_rows.index.tolist())

    return df.loc[valid_indices].copy()

def parse_parcel_text(text, default_bun=""):
    results = []
    lines = re.split(r'[\n,;\t\s]+', text.strip())
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "-" in line:
            parts = line.split("-")
            b = parts[0].strip().zfill(4)
            j = parts[1].strip().zfill(4)
            results.append((b, j))
        else:
            if default_bun:
                results.append((default_bun.zfill(4), line.zfill(4)))
            else:
                results.append((line.zfill(4), "0000"))
    return list(dict.fromkeys(results))

# ----------------------------------------------------
# 3. 사용자 화면 구성 (지번 가감 및 수집)
# ----------------------------------------------------
# 도면 캡처본에서 판독된 기본 필지 데이터셋
DEFAULT_BULGWANG_PARCELS = [
    "281-9", "281-10", "281-15", "281-16", "281-19", "281-20",
    "281-22", "281-24", "281-25", "281-26", "281-27", "281-28", "281-29",
    "281-30", "281-37", "281-38", "281-45", "281-48", "281-52",
    "281-61", "281-62", "281-65", "281-70", "281-72"
]

st.subheader("1️⃣ 대상 사업지 행정구역 및 편입 지번 설정")

c1, c2, c3 = st.columns(3)
with c1:
    in_sigungu = st.text_input("시군구코드 (5자리)", value="11380", help="은평구: 11380")
with c2:
    in_bjdong = st.text_input("법정동코드 (5자리)", value="10300", help="불광동
