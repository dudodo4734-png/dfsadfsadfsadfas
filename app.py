import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 종전연면적 정밀 조서기", layout="wide")
st.title("🏛️ 정비사업 실무형 종전연면적·노후도 조서기")

# ----------------------------------------------------
# 1. 시스템 환경 설정 (디코딩 키 내장)
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("📌 실무 워크플로우")
    st.info("""
    1. 대화창에 도면 캡처본 투입
    2. AI가 알려준 **시군구/법정동 코드** 입력
    3. 원하는 **구역명** 직접 타이핑
    4. AI가 추출한 **지번 텍스트 붙여넣기**
    5. **[조서 산출]** 클릭
    """)

# ----------------------------------------------------
# 2. 대장 위계 정제 및 파싱 유틸리티
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

    # 총괄표제부(2) 우선 정제: 총괄 존재 시 동별(3) 배제
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

def parse_parcel_text(text):
    results = []
    tokens = re.split(r'[\n,;\t\s]+', text.strip())
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-")
            b = parts[0].strip().zfill(4)
            j = parts[1].strip().zfill(4)
            results.append((b, j))
        else:
            results.append((token.zfill(4), "0000"))
    return list(dict.fromkeys(results))

# ----------------------------------------------------
# 3. 사용자 입력 화면 구성
# ----------------------------------------------------
st.subheader("1️⃣ 사업지 기본 정보 입력")

c1, c2, c3 = st.columns(3)
with c1:
    in_sigungu = st.text_input("시군구코드 (AI 제공 5자리)", value="11380", help="예: 은평구 11380, 성동구 11200")
with c2:
    in_bjdong = st.text_input("법정동코드 (AI 제공 5자리)", value="10300", help="예: 불광동 10300, 성수동1가 10600")
with c3:
    in_zone_nm = st.text_input("구역명 (실무자 직접 입력)", value="불광8구역")

st.subheader("2️⃣ AI 추출 지번 목록 붙여넣기")
raw_parcels = st.text_area(
    "지번 텍스트 (콤마, 줄바꿈, 띄어쓰기 자동 파싱)",
    value="281-9, 281-10, 281-15, 281-16, 281-19, 281-20, 281-22, 281-24, 281-25, 281-26, 281-27, 281-28, 281-29, 281-30, 281-37, 281-38, 281-45, 281-48, 281-52, 281-61, 281-62, 281-65, 281-70, 281-72",
    height=130,
    help="AI가 대화창에서 제공한 텍스트 덩어리를 그대로 복사해 넣으세요."
)

# ----------------------------------------------------
# 4. 건축HUB 전수 수집 및 검증 조서 산출
# ----------------------------------------------------
if st.button("🚀 종전연면적 및 노후도 조서 산출", use_container_width=True):
    target_tuples = parse_parcel_text(raw_parcels)

    if not target_tuples:
        st.warning("입력된 지번이 없습니다. 지번을 붙여넣어 주세요.")
    else:
        st.info(f"선별된 총 {len(target_tuples)}개 필지에 대해 건축물대장을 수집합니다.")

        all_records = []
        prog = st.progress(0.0)
        status_box = st.empty()

        for idx, (b, j) in enumerate(target_tuples):
            status_box.text(f"대장 수집 중 ({idx+1}/{len(target_tuples)}): 본번 {int(b)}-부번 {int(j)}")
            params = {
                "serviceKey": CLEAN_KEY,
                "sigunguCd": in_sigungu.strip(),
                "bjdongCd": in_bjdong.strip(),
                "platGbCd": "0",
                "bun": b,
                "ji": j,
                "numOfRows": 50,
                "pageNo": 1,
                "_type": "json"
            }
            try:
                r = requests.get(BASE_URL, params=params, timeout=8)
                res_json = r.json()
                items = res_json.get("response", {}).get("body", {}).get("items", {}).get("item", [])

                pnu_code = f"{in_sigungu.strip()}{in_bjdong.strip()}1{b}{j}"
                if isinstance(items, dict):
                    items["pnu"] = pnu_code
                    all_records.append(items)
                elif isinstance(items, list):
                    for it in items:
                        it["pnu"] = pnu_code
                        all_records.append(it)
            except Exception:
                pass

            prog.progress((idx + 1) / len(target_tuples))
            time.sleep(0.02)

        status_box.empty()
        prog.empty()

        if not all_records:
            st.warning("조회된 건축물대장이 없습니다. 행정코드 및 지번을 확인해 주세요.")
        else:
            df_raw = pd.DataFrame(all_records)
            raw_cnt = len(df_raw)

            # 총괄-동별 위계 정제
            df = clean_and_aggregate_registers(df_raw)
            refined_cnt = len(df)

            # 노후도(30년) 판정
            current_year = datetime.now().year
            def calc_age(d):
                if pd.isna(d) or str(d).strip() == "":
                    return None
                d_s = str(d).strip()
                if len(d_s) >= 4:
                    try:
                        return current_year - int(d_s[:4])
                    except:
                        return None
                return None

            df["age"] = df["useAprDay"].apply(calc_age) if "useAprDay" in df.columns else None
            df["is_old_30"] = df["age"].apply(lambda x: True if x is not None and x >= 30 else False)

            tot_m2 = float(df["totArea"].sum())
            tot_py = tot_m2 / 3.3058
            vl_m2 = float(df["vlRatEstmTotArea"].sum())
            vl_py = vl_m2 / 3.3058

            valid_age = df[df["age"].notnull()]
            old_cnt = int(df["is_old_30"].sum())
            old_ratio = (old_cnt / len(valid_age) * 100) if len(valid_age) > 0 else 0.0

            st.markdown("---")
            st.subheader(f"📊 {in_zone_nm} 종전연면적 및 노후도 최종 집계표")

            if raw_cnt != refined_cnt:
                st.info(f"💡 위계 정제: 원천 대장 {raw_cnt}건 중 총괄-동별 중복 {raw_cnt - refined_cnt}건을 배제하고 실물 {refined_cnt}동 기준으로 산출했습니다.")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("실물 건축물 동수", f"{refined_cnt:,} 동", f"원천 대장 {raw_cnt}건")
            m2.metric("순수 종전연면적", f"{tot_m2:,.1f} ㎡", f"{tot_py:,.1f} 평")
            m3.metric("용적률산정용 연면적", f"{vl_m2:,.1f} ㎡", f"{vl_py:,.1f} 평")
            m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

            t1, t2 = st.tabs(["📑 19자리 PNU 지번별 소계 조서", "📋 대장 원천 상세 데이터"])

            with t1:
                summary = df.groupby(["pnu", "platPlc"]).agg(
                    동수=("totArea", "count"),
                    연면적_m2=("totArea", "sum"),
                    용적률면적_m2=("vlRatEstmTotArea", "sum"),
                    건물명=("bldNm", lambda x: ", ".join([str(v) for v in x if str(v).strip() != ""]))
                ).reset_index()
                summary["연면적_평"] = (summary["연면적_m2"] / 3.3058).round(1)
                summary["용적률면적_평"] = (summary["용적률면적_m2"] / 3.3058).round(1)

                st.dataframe(
                    summary.rename(columns={
                        "pnu": "19자리 토지고유번호(PNU)",
                        "platPlc": "법정동 대지위치",
                        "연면적_m2": "연면적(㎡)",
                        "연면적_평": "연면적(평)",
                        "용적률면적_m2": "용적률면적(㎡)",
                        "용적률면적_평": "용적률면적(평)"
                    }),
                    use_container_width=True
                )

            with t2:
                disp_cols = ["pnu", "platPlc", "bldNm", "regstrKindCdNm", "totArea", "vlRatEstmTotArea", "useAprDay", "age", "is_old_30"]
                avail = [c for c in disp_cols if c in df.columns]
                st.dataframe(df[avail], use_container_width=True)

            st.download_button(
                label=f"📥 {in_zone_nm}_정밀조서_다운로드.csv",
                data=df.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"{in_zone_nm}_종전연면적조서.csv",
                mime="text/csv"
            )
