import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 종전연면적 범용 조서기 (정밀진단형)", layout="wide")
st.title("🏛️ 정비사업 종전연면적·노후도 범용 조서기")

# ----------------------------------------------------
# 1. 공공데이터 건축HUB API 키
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

# ----------------------------------------------------
# 2. 전국 범용 지능형 파서
# ----------------------------------------------------
def parse_universal_data(text, input_sig="", input_bjd=""):
    sigungu = input_sig.strip()
    bjdong = input_bjd.strip()

    code_match = re.search(r'(\d{5})[\s,]+(\d{5})', text)
    if code_match:
        sigungu = code_match.group(1)
        bjdong = code_match.group(2)

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
            "sigungu": sigungu, "bjdong": bjdong, "bun": b, "ji": j, "pnu": pnu,
            "label": f"{int(b)}-{int(j)}" if int(j) > 0 else f"{int(b)}"
        })
    return sigungu, bjdong, results

# ----------------------------------------------------
# 3. 누락 방지 건축HUB 전수 호출 엔진 (일반 0 + 산번지 1 자동 탐색)
# ----------------------------------------------------
def fetch_bld_info(sigungu, bjdong, bun, ji):
    all_items = []
    # 일반대지(0) 먼저 조회, 없으면 산번지(1) 추가 조회
    for plat_gb in ["0", "1"]:
        params = {
            "serviceKey": CLEAN_KEY,
            "sigunguCd": sigungu,
            "bjdongCd": bjdong,
            "platGbCd": plat_gb,
            "bun": bun,
            "ji": ji,
            "numOfRows": 100,
            "pageNo": 1,
            "_type": "json"
        }
        try:
            r = requests.get(BASE_URL, params=params, timeout=8)
            items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if isinstance(items, dict):
                all_items.append(items)
            elif isinstance(items, list):
                all_items.extend(items)
        except Exception:
            pass
        if all_items:
            break
    return all_items

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

    # 필지별 정제: 총괄(2) 연면적이 살아있으면 총괄 채택, 없거나 0이면 동별(3)+일반(1) 전수 합산
    valid_indices = []
    group_col = "platPlc" if ("platPlc" in df.columns and df["platPlc"].str.strip().ne("").any()) else "pnu"
    grouped = df.groupby(group_col)

    for _, group in grouped:
        summary_rows = group[group["regstrKindCd"] == "2"]
        if not summary_rows.empty and summary_rows["totArea"].sum() > 0:
            valid_indices.extend(summary_rows.index.tolist())
        else:
            detail_rows = group[group["regstrKindCd"].isin(["1", "3", ""])]
            valid_indices.extend(detail_rows.index.tolist())

    return df.loc[valid_indices].copy()

# ----------------------------------------------------
# 4. 사용자 화면 구성
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
    placeholder="[행정구역] 11305, 10300\n[지번목록] 129-10, 129-14, 130-43 ...",
    height=200
)

# ----------------------------------------------------
# 5. 실행 및 실시간 진단
# ----------------------------------------------------
if st.button("🚀 건축물대장 수집 및 정밀 진단 조서 산출", use_container_width=True):
    if not raw_input_text.strip():
        st.warning("붙여넣은 데이터가 없습니다.")
    else:
        sig_code, bjd_code, query_list = parse_universal_data(raw_input_text, manual_sig, manual_bjd)

        if not sig_code or not bjd_code:
            st.error("시군구코드 또는 법정동코드를 인식할 수 없습니다.")
        elif not query_list:
            st.warning("인식된 지번 데이터가 없습니다.")
        else:
            for it in query_list:
                if not it["sigungu"]: it["sigungu"] = sig_code
                if not it["bjdong"]: it["bjdong"] = bjd_code
                if not it["pnu"]: it["pnu"] = f"{sig_code}{bjd_code}1{it['bun']}{it['ji']}"

            total_p = len(query_list)
            display_title = zone_name.strip() if zone_name.strip() else "대상구역"
            st.info(f"행정코드 [{sig_code} / {bjd_code}] 적용 | 총 {total_p}개 필지에 대해 조회를 시작합니다.")

            all_records = []
            zero_parcels = [] # 대장이 아예 안 나오는 필지 추적
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, item in enumerate(query_list):
                status_box.text(f"대장 수집 중 ({idx+1}/{total_p}): {item['label']}번지")
                items = fetch_bld_info(item["sigungu"], item["bjdong"], item["bun"], item["ji"])
                
                if items:
                    for it in items:
                        it["pnu"] = item["pnu"]
                        it["query_label"] = item["label"]
                        all_records.append(it)
                else:
                    zero_parcels.append(item["label"])

                prog.progress((idx + 1) / total_p)
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            if not all_records:
                st.warning("조회된 건축물대장이 없습니다.")
            else:
                df_raw = pd.DataFrame(all_records)
                raw_cnt = len(df_raw)
                df = clean_and_aggregate_registers(df_raw)
                refined_cnt = len(df)

                current_year = datetime.now().year
                def calc_age(d):
                    if pd.isna(d) or str(d).strip() == "": return None
                    d_s = str(d).strip()
                    if len(d_s) >= 4:
                        try: return current_year - int(d_s[:4])
                        except: return None
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

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("실물 건축물 동수", f"{refined_cnt:,} 동", f"원천 대장 {raw_cnt}건")
                m2.metric("순수 종전연면적", f"{tot_m2:,.1f} ㎡", f"{tot_py:,.1f} 평")
                m3.metric("용적률산정용 연면적", f"{vl_m2:,.1f} ㎡", f"{vl_py:,.1f} 평")
                m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

                # 대장 누락 필지 즉각 알림
                if zero_parcels:
                    st.error(f"⚠️ **건축물대장이 0건으로 조회된 필지 ({len(zero_parcels)}개)**: {', '.join(zero_parcels)}")
                    st.caption("※ 나대지/도로이거나, 무허가건축물이 위치했거나, 인접 대표지번에 합필 등재된 필지입니다. 수작업 조서와 비교 시 이 필지들의 연면적을 확인하세요.")

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

                    st.dataframe(summary, use_container_width=True)

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
