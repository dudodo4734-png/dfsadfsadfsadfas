import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 종전연면적 범용 조서기", layout="wide")
st.title("🏛️ 정비사업 종전연면적·노후도 범용 조서기")

# ----------------------------------------------------
# 1. 공공데이터 건축HUB API 키
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

# ----------------------------------------------------
# 2. 전국 범용 지능형 파서 & 누락 방지 대장 정제 엔진
# ----------------------------------------------------
def parse_universal_data(text, input_sig="", input_bjd=""):
    sigungu = input_sig.strip()
    bjdong = input_bjd.strip()

    # 1. 텍스트 내 행정구역 5자리 코드 2개 탐색
    code_match = re.search(r'(\d{5})[\s,]+(\d{5})', text)
    if code_match:
        sigungu = code_match.group(1)
        bjdong = code_match.group(2)

    # 2. 지번 추출 (19자리 PNU 또는 본번-부번)
    pnu_matches = re.findall(r'\b\d{19}\b', text)
    if pnu_matches:
        results = []
        for p in set(pnu_matches):
            results.append({
                "sigungu": p[:5], "bjdong": p[5:10], "bun": p[11:15], "ji": p[15:19], "pnu": p
            })
        return sigungu, bjdong, results

    tokens = re.findall(r'\b\d+(?:-\d+)?\b', text)
    parcels = []
    for tok in tokens:
        if len(tok) == 5 and "-" not in tok:
            continue
        if "-" in tok:
            parts = tok.split("-")
            parcels.append((parts[0].zfill(4), parts[1].zfill(4)))
        else:
            parcels.append((tok.zfill(4), "0000"))

    unique_parcels = list(dict.fromkeys(parcels))

    results = []
    for b, j in unique_parcels:
        pnu = f"{sigungu}{bjdong}1{b}{j}" if (sigungu and bjdong) else ""
        results.append({
            "sigungu": sigungu, "bjdong": bjdong, "bun": b, "ji": j, "pnu": pnu
        })

    return sigungu, bjdong, results

def clean_and_aggregate_registers(raw_df):
    """
    모아타운/빌라 밀집지 누락 방지 핵심 알고리즘:
    1. 총괄표제부(2)가 존재하고 실제 totArea > 0 인 경우에만 총괄표제부 적용
    2. 총괄표제부의 totArea가 0이거나 비어있는 경우: 동별표제부(3)의 실연면적 전수 합산
    3. 총괄이 없는 일반 필지는 일반표제부(1) 및 동별표제부(3) 정상 전수 합산
    """
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
    group_col = "platPlc" if ("platPlc" in df.columns and df["platPlc"].str.strip().ne("").any()) else "pnu"
    grouped = df.groupby(group_col)

    for plat_key, group in grouped:
        summary_rows = group[group["regstrKindCd"] == "2"]
        
        # 총괄표제부의 실제 연면적이 0보다 큰 경우에만 총괄을 우선 반영
        if not summary_rows.empty and summary_rows["totArea"].sum() > 0:
            valid_indices.extend(summary_rows.index.tolist())
        else:
            # 총괄 연면적이 0이거나 총괄이 없는 경우 -> 동별표제부(3)와 일반표제부(1) 전수 합산
            detail_rows = group[group["regstrKindCd"].isin(["1", "3", ""])]
            valid_indices.extend(detail_rows.index.tolist())

    return df.loc[valid_indices].copy()

# ----------------------------------------------------
# 3. 사용자 화면 구성
# ----------------------------------------------------
col_meta1, col_meta2, col_meta3 = st.columns([2, 1, 1])
with col_meta1:
    zone_name = st.text_input("구역명 (보고서 제목용)", value="", placeholder="예: 수유동 모아타운 사업지")
with col_meta2:
    manual_sig = st.text_input("시군구코드 (5자리)", value="", placeholder="텍스트에 없을 때만 입력")
with col_meta3:
    manual_bjd = st.text_input("법정동코드 (5자리)", value="", placeholder="텍스트에 없을 때만 입력")

raw_input_text = st.text_area(
    "📥 AI가 추출한 데이터 블록을 그대로 붙여넣으세요:",
    value="",
    placeholder="AI가 제공한 텍스트를 그대로 Ctrl+V 하세요.\n\n[입력 예시]\n[행정구역] 11305, 10300\n[지번목록] 129-10, 129-14, 130-43 ...",
    height=220
)

# ----------------------------------------------------
# 4. 건축HUB 전수 수집 및 조서 산출
# ----------------------------------------------------
if st.button("🚀 건축물대장 수집 및 조서 산출", use_container_width=True):
    if not raw_input_text.strip():
        st.warning("붙여넣은 데이터가 없습니다.")
    else:
        sig_code, bjd_code, query_list = parse_universal_data(raw_input_text, manual_sig, manual_bjd)

        if not sig_code or not bjd_code:
            st.error("시군구코드(5자리) 또는 법정동코드(5자리)를 인식할 수 없습니다. 텍스트에 포함되어 있거나 상단 입력칸에 채워져 있어야 합니다.")
        elif not query_list:
            st.warning("인식된 지번 데이터가 없습니다.")
        else:
            for it in query_list:
                if not it["sigungu"]:
                    it["sigungu"] = sig_code
                if not it["bjdong"]:
                    it["bjdong"] = bjd_code
                if not it["pnu"]:
                    it["pnu"] = f"{sig_code}{bjd_code}1{it['bun']}{it['ji']}"

            total_p = len(query_list)
            display_title = zone_name.strip() if zone_name.strip() else "대상구역"
            st.info(f"행정코드 [{sig_code} / {bjd_code}] 적용 | 총 {total_p}개 필지에 대해 건축물대장 조회를 시작합니다.")

            all_records = []
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, item in enumerate(query_list):
                status_box.text(f"대장 수집 중 ({idx+1}/{total_p}): {int(item['bun'])}-{int(item['ji'])}")
                params = {
                    "serviceKey": CLEAN_KEY,
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
                    r = requests.get(BASE_URL, params=params, timeout=8)
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
                st.warning("건축HUB에서 대장 정보를 찾을 수 없습니다. 행정구역 코드와 지번을 확인해 주세요.")
            else:
                df_raw = pd.DataFrame(all_records)
                raw_cnt = len(df_raw)

                # 누락 방지 정제 실행
                df = clean_and_aggregate_registers(df_raw)
                refined_cnt = len(df)

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
                    st.info(f"💡 대장 위계 정제: 원천 대장 {raw_cnt}건 중 총괄 연면적 0인 가짜 표제부를 걸러내고 실물 {refined_cnt}동 기준으로 정상 합산했습니다.")

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
