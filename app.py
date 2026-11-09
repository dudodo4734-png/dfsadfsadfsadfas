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
# 1. 국토부 공공데이터 건축HUB API 키
# ----------------------------------------------------
BLD_SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_BLD_KEY = unquote(BLD_SERVICE_KEY)
BASE_BLD_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("📌 정비사업 실무 가이드")
    st.markdown("""
    - **토지이음 도면 추출 데이터 전용**:
      지목(대, 도 등)을 배제한 순수 지번 리스트를 건축HUB 대장 규격(본번4자리, 부번4자리)으로 자동 정렬합니다.
    - **중복 연면적 방지**:
      총괄표제부(2)가 존재하는 필지는 동별표제부(3) 중복 집계를 배제하고 실물 실연면적만 계산합니다.
    """)

# ----------------------------------------------------
# 2. 지능형 파서 & 대장 위계 정제 엔진
# ----------------------------------------------------
def parse_land_input(text):
    """
    텍스트에서 행정코드(시군구, 법정동) 및 본번-부번 지번을 추출
    """
    sigungu = "11380"  # 기본값: 은평구
    bjdong = "10300"   # 기본값: 불광동

    # 행정구역 코드 추출
    code_match = re.search(r'\[행정구역\]\s*(\d{5})[\s,]+(\d{5})', text)
    if code_match:
        sigungu = code_match.group(1)
        bjdong = code_match.group(2)
    else:
        # 단독 5자리 2개 연속 패턴 체크
        codes = re.findall(r'\b\d{5}\b', text)
        if len(codes) >= 2:
            sigungu = codes[0]
            bjdong = codes[1]

    # 지번 목록 추출 ("600-1", "605", "12-4" 등)
    tokens = re.findall(r'\b\d+(?:-\d+)?\b', text)
    parcels = []

    for tok in tokens:
        # 5자리 행정코드는 지번 목록에서 배제
        if len(tok) == 5 and "-" not in tok:
            continue
        if "-" in tok:
            parts = tok.split("-")
            b = parts[0].zfill(4)
            j = parts[1].zfill(4)
            parcels.append((b, j))
        else:
            parcels.append((tok.zfill(4), "0000"))

    # 중복 지번 제거
    unique_parcels = list(dict.fromkeys(parcels))

    results = []
    for b, j in unique_parcels:
        pnu = f"{sigungu}{bjdong}1{b}{j}"
        results.append({
            "sigungu": sigungu,
            "bjdong": bjdong,
            "bun": b,
            "ji": j,
            "pnu": pnu,
            "label": f"{int(b)}-{int(j)}" if int(j) > 0 else f"{int(b)}"
        })

    return sigungu, bjdong, results

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

    # 총괄표제부(2) 존재 시 개별 동별표제부(3) 중복 합산 배제
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

# ----------------------------------------------------
# 3. 사용자 인터페이스
# ----------------------------------------------------
c_name, c_blank = st.columns([2, 1])
with c_name:
    zone_name = st.text_input("구역명 (임의 입력)", value="불광동 정비사업구역")

raw_input_text = st.text_area(
    "📥 AI가 추출한 데이터 텍스트를 붙여넣으세요:",
    value="""[행정구역] 11380, 10300
[지번목록] 600-1, 600-2, 600-3, 600-4, 600-5, 600-6, 600-7, 600-8, 600-9, 600-10, 600-11, 600-12, 600-13, 601-1, 601-2, 601-3, 601-4, 601-6, 601-7, 601-8, 601-9, 601-10, 601-11, 601-12, 601-13, 602-1, 602-2, 602-3, 602-4, 602-5, 602-6, 602-7, 602-8, 602-9, 603-1, 603-2, 603-3, 603-4, 603-5, 603-6, 603-7, 603-8, 603-9, 605-1, 605-2, 605-3, 605-4, 605-6, 605-7, 605-8, 605-9, 605-10, 606-1, 606-2, 606-3, 606-4, 606-5, 606-6, 606-7, 606-8, 606-9, 606-16, 606-17, 606-18, 606-19, 606-20, 606-21, 606-22, 607-1, 607-2, 607-3, 607-4, 607-5, 607-6, 607-7, 607-8, 607-9, 608-9, 608-15""",
    height=200
)

# ----------------------------------------------------
# 4. 국토부 건축HUB 대장 전수 수집 및 집계
# ----------------------------------------------------
if st.button("🚀 종전연면적 및 노후도 조서 산출", use_container_width=True):
    if not raw_input_text.strip():
        st.warning("붙여넣은 데이터가 없습니다.")
    else:
        s_code, b_code, target_parcels = parse_land_input(raw_input_text)
        total_p = len(target_parcels)

        if total_p == 0:
            st.error("유효한 지번 데이터를 찾을 수 없습니다.")
        else:
            display_title = zone_name.strip() if zone_name.strip() else "대상구역"
            st.info(f"행정코드 [{s_code} / {b_code}] 인식 완료 | 총 {total_p}개 필지에 대해 건축물대장 조회를 시작합니다.")

            all_records = []
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, item in enumerate(target_parcels):
                status_box.text(f"건축HUB 대장 조회 중 ({idx+1}/{total_p}): {item['label']}번지")
                params = {
                    "serviceKey": CLEAN_BLD_KEY,
                    "sigunguCd": item["sigungu"],
                    "bjdongCd": item["bjdong"],
                    "platGbCd": "0",
                    "bun": item["bun"],
                    "ji": item["ji"],
                    "numOfRows": 50,
                    "pageNo": 1,
                    "_type": "json"
                }
                try:
                    r = requests.get(BASE_BLD_URL, params=params, timeout=8)
                    res_json = r.json()
                    bld_items = res_json.get("response", {}).get("body", {}).get("items", {}).get("item", [])

                    if isinstance(bld_items, dict):
                        bld_items["pnu"] = item["pnu"]
                        all_records.append(bld_items)
                    elif isinstance(bld_items, list):
                        for b in bld_items:
                            b["pnu"] = item["pnu"]
                            all_records.append(b)
                except Exception:
                    pass

                prog.progress((idx + 1) / total_p)
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            if not all_records:
                st.warning("조회된 건축물대장이 없습니다. 행정코드와 지번을 다시 확인해 주세요.")
            else:
                df_raw = pd.DataFrame(all_records)
                raw_cnt = len(df_raw)

                # 위계 정제
                df = clean_and_aggregate_registers(df_raw)
                refined_cnt = len(df)

                # 노후도(30년) 계산
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
                st.subheader(f"📊 {display_title} 종전연면적 및 노후도 최종 집계표")

                if raw_cnt != refined_cnt:
                    st.info(f"💡 대장 위계 정제: 원천 대장 {raw_cnt}건 중 총괄-동별 중복 {raw_cnt - refined_cnt}건을 배제하고 실물 {refined_cnt}동 기준으로 산출했습니다.")

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
                            "pnu": "19자리 공인 PNU",
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
                    label=f"📥 {display_title}_정밀조서_다운로드.csv",
                    data=df.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"{display_title}_종전연면적조서.csv",
                    mime="text/csv"
                )
