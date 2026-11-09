import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 종전연면적 범용 조서기", layout="wide")
st.title("🏛️ 정비사업 실무형 종전연면적·노후도 범용 조서기")

# ----------------------------------------------------
# 1. 고정 API 키 설정 (공공데이터 건축HUB)
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("📌 범용 사용 가이드")
    st.markdown("""
    **어떤 구역이든 3단계로 끝납니다**:
    1. AI에게 도면 캡처를 주고 받은 데이터를 복사
    2. 구역명 입력 후 아래 텍스트창에 `Ctrl+V`
    3. **[대장 수집 및 조서 산출]** 클릭
    
    *※ 콤마, 줄바꿈, 탭, 본번-부번 혼합 입력 모두 자동 인식합니다.*
    """)

# ----------------------------------------------------
# 2. 지능형 텍스트 파서 & 대장 위계 정제
# ----------------------------------------------------
def parse_raw_text(text, default_sigungu="11380", default_bjdong="10300"):
    """
    사용자가 AI 대화창에서 통째로 긁어온 텍스트에서 
    행정코드(있는 경우)와 지번 리스트(본번, 부번)를 유연하게 추출
    """
    sigungu = default_sigungu
    bjdong = default_bjdong

    # 행정코드 패턴 감지: [행정구역] 11380, 10300 또는 11380 10300
    code_match = re.search(r'(\d{5})[\s,]+(\d{5})', text)
    if code_match:
        sigungu = code_match.group(1)
        bjdong = code_match.group(2)

    # 텍스트 내 지번 후보 추출
    # "281-9", "281-10", 또는 단독 번호
    raw_tokens = re.findall(r'\b\d+(?:-\d+)?\b', text)
    
    parcels = []
    base_bun = None

    for tok in raw_tokens:
        # 5자리 연속 숫자는 시군구/법정동 코드일 확률이 높으므로 지번에서 제외
        if len(tok) == 5 and not "-" in tok:
            continue
        
        if "-" in tok:
            parts = tok.split("-")
            b = parts[0].zfill(4)
            j = parts[1].zfill(4)
            if base_bun is None:
                base_bun = b
            parcels.append((b, j))
        else:
            # 부번만 있거나 본번만 있는 단일 숫자
            if base_bun:
                parcels.append((base_bun, tok.zfill(4)))
            else:
                parcels.append((tok.zfill(4), "0000"))

    # 중복 제거
    unique_parcels = list(dict.fromkeys(parcels))
    return sigungu, bjdong, unique_parcels

def clean_and_aggregate_registers(raw_df):
    df = raw_df.copy()
    str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun", "pnu"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    # 중복 대장 배제
    if "mgmBldrgstPk" in df.columns:
        df = df.drop_duplicates(subset=["mgmBldrgstPk"])

    df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
    df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)
    df["platArea"] = pd.to_numeric(df.get("platArea", 0), errors="coerce").fillna(0.0)

    # 총괄표제부(2) 우선 반영 (동별표제부 중복 배제)
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
# 3. 사용자 입력 화면 (범용 단일 입력창)
# ----------------------------------------------------
col_title, col_help = st.columns([2, 1])
with col_title:
    zone_name = st.text_input("구역명 (보고서 표기용)", value="", placeholder="예: 불광8구역, 성수전략정비1지구 등")
with col_help:
    st.caption("※ 구역명을 비워두면 '대상구역'으로 자동 표기됩니다.")

raw_input_data = st.text_area(
    "📥 AI에게 받은 데이터 텍스트를 그대로 붙여넣으세요:",
    placeholder="예시 1:\n[행정구역] 11380, 10300\n[지번목록] 281-9, 281-10, 281-15, 281-16, 281-22\n\n예시 2 (지번만 있는 경우):\n281-9, 281-10, 281-15\n\n예시 3 (엑셀 복사본 등 엔터 구분 전부 지원)",
    height=180
)

# 기본 행정코드 수동 보정용 접이식 메뉴 (필요할 때만 열어서 확인)
with st.expander("🛠️ 행정구역 코드 직접 확인 / 수동 변경 (선택사항)"):
    sub_c1, sub_c2 = st.columns(2)
    with sub_c1:
        manual_sigungu = st.text_input("시군구코드 (5자리)", value="11380", help="붙여넣은 텍스트에 코드가 없으면 이 기본값을 사용합니다.")
    with sub_c2:
        manual_bjdong = st.text_input("법정동코드 (5자리)", value="10300", help="붙여넣은 텍스트에 코드가 없으면 이 기본값을 사용합니다.")

# ----------------------------------------------------
# 4. 건축HUB 수집 및 정밀 조서 산출
# ----------------------------------------------------
if st.button("🚀 대장 수집 및 종전연면적 조서 산출", use_container_width=True):
    if not raw_input_data.strip():
        st.warning("붙여넣은 데이터가 없습니다. AI가 준 텍스트를 붙여넣어 주세요.")
    else:
        # 지능형 파싱 실행
        final_sigungu, final_bjdong, target_parcels = parse_raw_text(
            raw_input_data, manual_sigungu.strip(), manual_bjdong.strip()
        )

        display_zone_title = zone_name.strip() if zone_name.strip() else "대상구역"

        if not target_parcels:
            st.warning("유효한 지번 형태(본번-부번 또는 숫자)를 찾지 못했습니다. 텍스트를 확인해 주세요.")
        else:
            st.success(f"행정코드 [{final_sigungu} / {final_bjdong}] 감지 | 총 {len(target_parcels)}개 필지 식별 완료")
            
            all_records = []
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, (b, j) in enumerate(target_parcels):
                status_box.text(f"건축물대장 조회 중 ({idx+1}/{len(target_parcels)}): {int(b)}-{int(j)}")
                params = {
                    "serviceKey": CLEAN_KEY,
                    "sigunguCd": final_sigungu,
                    "bjdongCd": final_bjdong,
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

                    pnu_code = f"{final_sigungu}{final_bjdong}1{b}{j}"
                    if isinstance(items, dict):
                        items["pnu"] = pnu_code
                        all_records.append(items)
                    elif isinstance(items, list):
                        for it in items:
                            it["pnu"] = pnu_code
                            all_records.append(it)
                except Exception:
                    pass

                prog.progress((idx + 1) / len(target_parcels))
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            if not all_records:
                st.warning("국토부 건축물대장에서 조회된 건축물이 없습니다. 법정동코드와 지번을 확인해 주세요.")
            else:
                df_raw = pd.DataFrame(all_records)
                raw_cnt = len(df_raw)

                # 위계 정제
                df = clean_and_aggregate_registers(df_raw)
                refined_cnt = len(df)

                # 노후도 계산
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
                st.subheader(f"📊 {display_zone_title} 종전연면적 및 노후도 최종 집계표")

                if raw_cnt != refined_cnt:
                    st.info(f"💡 대장 위계 정제: 원천 대장 {raw_cnt}건 중 총괄-동별 중복 {raw_cnt - refined_cnt}건을 자동 배제하고 실물 {refined_cnt}동 기준으로 산출했습니다.")

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
                    label=f"📥 {display_zone_title}_정밀조서_다운로드.csv",
                    data=df.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"{display_zone_title}_종전연면적조서.csv",
                    mime="text/csv"
                )
