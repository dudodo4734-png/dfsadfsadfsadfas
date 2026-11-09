import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 종전연면적 조서기 (검증·수기보완형)", layout="wide")
st.title("🏛️ 정비사업 종전연면적·노후도 조서기")

# ----------------------------------------------------
# 1. 공공데이터 건축HUB API 키
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

# ----------------------------------------------------
# 2. 범용 지능형 파서 & 대장 위계 정제
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
        label = f"{int(b)}-{int(j)}" if int(j) > 0 else f"{int(b)}"
        results.append({
            "sigungu": sigungu, "bjdong": bjdong, "bun": b, "ji": j, "pnu": pnu, "label": label
        })
    return sigungu, bjdong, results

def clean_and_aggregate_registers(raw_df):
    df = raw_df.copy()
    str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun", "pnu", "query_label"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    if "mgmBldrgstPk" in df.columns:
        df = df.drop_duplicates(subset=["mgmBldrgstPk"])

    df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
    df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)

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
    placeholder="[행정구역] 11305, 10300\n[지번목록] 129-10, 129-14, 130-43 ...",
    height=180
)

# ----------------------------------------------------
# 4. 데이터 수집 및 상태 저장
# ----------------------------------------------------
if st.button("🚀 건축물대장 수집 및 기본 조서 생성", use_container_width=True):
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
            st.session_state["total_parcels_cnt"] = total_p
            st.session_state["zone_title"] = zone_name.strip() if zone_name.strip() else "대상구역"

            all_records = []
            found_labels = set()
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, item in enumerate(query_list):
                status_box.text(f"대장 수집 중 ({idx+1}/{total_p}): {item['label']}번지")
                params = {
                    "serviceKey": CLEAN_KEY,
                    "sigunguCd": item["sigungu"],
                    "bjdongCd": item["bjdong"],
                    "platGbCd": "0",
                    "bun": item["bun"],
                    "ji": item["ji"],
                    "numOfRows": 100,
                    "pageNo": 1,
                    "_type": "json"
                }
                try:
                    r = requests.get(BASE_URL, params=params, timeout=8)
                    items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
                    if isinstance(items, dict):
                        items["pnu"] = item["pnu"]
                        items["query_label"] = item["label"]
                        all_records.append(items)
                        found_labels.add(item["label"])
                    elif isinstance(items, list) and items:
                        for it in items:
                            it["pnu"] = item["pnu"]
                            it["query_label"] = item["label"]
                            all_records.append(it)
                        found_labels.add(item["label"])
                except Exception:
                    pass

                prog.progress((idx + 1) / total_p)
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            # 대장 정제
            df_cleaned = clean_and_aggregate_registers(pd.DataFrame(all_records)) if all_records else pd.DataFrame()
            st.session_state["raw_bld_df"] = df_cleaned

            # 필지별 소계 조서 테이블 구성 (미조회 필지 포함 전수 매핑)
            summary_list = []
            for item in query_list:
                lbl = item["label"]
                if not df_cleaned.empty and lbl in df_cleaned["query_label"].values:
                    sub = df_cleaned[df_cleaned["query_label"] == lbl]
                    tot_area = float(sub["totArea"].sum())
                    vl_area = float(sub["vlRatEstmTotArea"].sum())
                    dong_cnt = int(len(sub))
                    b_names = ", ".join([str(v) for v in sub["bldNm"] if str(v).strip()])
                    plat_plc = sub["platPlc"].iloc[0] if "platPlc" in sub.columns and sub["platPlc"].iloc[0] else "-"
                    summary_list.append({
                        "구분": "정상 조회",
                        "지번": lbl,
                        "19자리 PNU": item["pnu"],
                        "대지위치": plat_plc,
                        "건축물 동수": dong_cnt,
                        "종전연면적(㎡)": round(tot_area, 2),
                        "용적률산정면적(㎡)": round(vl_area, 2),
                        "건물명": b_names,
                        "비고": "API 자동수집"
                    })
                else:
                    summary_list.append({
                        "구분": "⚠️ 대장 미조회",
                        "지번": lbl,
                        "19자리 PNU": item["pnu"],
                        "대지위치": "-",
                        "건축물 동수": 0,
                        "종전연면적(㎡)": 0.0,
                        "용적률산정면적(㎡)": 0.0,
                        "건물명": "-",
                        "비고": "수기 입력 필요 (무허가/합필 등)"
                    })

            st.session_state["summary_df"] = pd.DataFrame(summary_list)

# ----------------------------------------------------
# 5. 상호작용형 조서 표출 및 실시간 수기 보완 에디터
# ----------------------------------------------------
if "summary_df" in st.session_state and not st.session_state["summary_df"].empty:
    display_title = st.session_state.get("zone_title", "대상구역")
    df_editor_source = st.session_state["summary_df"].copy()

    st.markdown("---")
    st.subheader(f"📊 {display_title} 필지별 종전연면적 조서 및 수기 보정 테이블")

    unfound_cnt = len(df_editor_source[df_editor_source["구분"].str.contains("미조회")])
    if unfound_cnt > 0:
        st.warning(f"⚠️ 총 {len(df_editor_source)}개 필지 중 **{unfound_cnt}개 필지가 '대장 미조회' 상태**입니다. 아래 표에서 **주황/빨간색 표시된 행의 [종전연면적(㎡)] 열을 더블클릭하여 수기 입력**하시면 상단 대시보드가 즉시 갱신됩니다.")

    # 사용자가 표에서 직접 수정할 수 있는 st.data_editor
    edited_df = st.data_editor(
        df_editor_source,
        use_container_width=True,
        num_rows="fixed",
        disabled=["구분", "지번", "19자리 PNU", "대지위치"],
        column_config={
            "종전연면적(㎡)": st.column_config.NumberColumn(
                "종전연면적(㎡)",
                help="수기 보완치를 입력하면 상단 합계에 즉각 반영됩니다.",
                min_value=0.0,
                format="%.2f ㎡"
            ),
            "구분": st.column_config.TextColumn(
                "조회 상태",
                help="정상 조회 여부"
            )
        }
    )

    # 수정된 값 기반 실시간 재집계
    final_tot_m2 = float(pd.to_numeric(edited_df["종전연면적(㎡)"], errors="coerce").fillna(0.0).sum())
    final_tot_py = final_tot_m2 / 3.3058
    final_vl_m2 = float(pd.to_numeric(edited_df["용적률산정면적(㎡)"], errors="coerce").fillna(0.0).sum())
    final_vl_py = final_vl_m2 / 3.3058
    final_dong_cnt = int(pd.to_numeric(edited_df["건축물 동수"], errors="coerce").fillna(0).sum())

    # 노후도 계산
    raw_df = st.session_state.get("raw_bld_df", pd.DataFrame())
    old_ratio = 0.0
    old_cnt = 0
    valid_age_cnt = 0
    if not raw_df.empty and "useAprDay" in raw_df.columns:
        current_year = datetime.now().year
        def calc_age(d):
            if pd.isna(d) or str(d).strip() == "": return None
            d_s = str(d).strip()
            if len(d_s) >= 4:
                try: return current_year - int(d_s[:4])
                except: return None
            return None
        ages = raw_df["useAprDay"].apply(calc_age)
        valid_ages = ages.dropna()
        valid_age_cnt = len(valid_ages)
        old_cnt = int((valid_ages >= 30).sum())
        old_ratio = (old_cnt / valid_age_cnt * 100) if valid_age_cnt > 0 else 0.0

    # 상단 요약 카드 (수정값 실시간 연동)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("확인 건축물 동수", f"{final_dong_cnt:,} 동", f"총 {len(edited_df)}개 필지")
    m2.metric("최종 종전연면적", f"{final_tot_m2:,.1f} ㎡", f"{final_tot_py:,.1f} 평")
    m3.metric("용적률산정용 연면적", f"{final_vl_m2:,.1f} ㎡", f"{final_vl_py:,.1f} 평")
    m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {valid_age_cnt}동 기준")

    # CSV 다운로드
    edited_df["종전연면적(평)"] = (pd.to_numeric(edited_df["종전연면적(㎡)"]) / 3.3058).round(1)
    st.download_button(
        label=f"📥 {display_title}_최종수정조서_다운로드.csv",
        data=edited_df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"{display_title}_종전연면적조서_완성본.csv",
        mime="text/csv"
    )
