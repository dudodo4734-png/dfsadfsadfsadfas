import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 범용 종전연면적 조서기", layout="wide")
st.title("🏛️ 정비사업 실무형 종전연면적·노후도 산출 조서기")

# ----------------------------------------------------
# 1. 공공데이터 건축HUB API 키
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("📌 범용 사용법")
    st.markdown("""
    1. AI에게 도면 캡처본을 주고 텍스트를 받음
    2. 아래 입력창에 통째로 붙여넣기 (`Ctrl+V`)
    3. **[조서 산출]** 버튼 클릭
    
    *※ 행정코드 자동인식 / 19자리 PNU / 본번-부번 / 콤마·줄바꿈 등 전국 모든 구역 지원*
    """)

# ----------------------------------------------------
# 2. 전국 범용 지능형 데이터 파서
# ----------------------------------------------------
def parse_universal_input(text):
    """
    텍스트 내에서 전국 19자리 PNU 또는 (시군구5자리, 법정동5자리, 본번4자리, 부번4자리)를 자동 파싱
    """
    clean_lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    # 1. 19자리 PNU가 직접 들어온 경우 감지 (예: 1138010300102810009)
    pnu_matches = re.findall(r'\b\d{19}\b', text)
    if pnu_matches:
        results = []
        for p in set(pnu_matches):
            sigungu = p[:5]
            bjdong = p[5:10]
            bun = p[11:15]
            ji = p[15:19]
            results.append((sigungu, bjdong, bun, ji, p))
        return results

    # 2. [행정구역] 시군구코드(5), 법정동코드(5) 패턴 추출
    sigungu = ""
    bjdong = ""
    
    code_match = re.search(r'(\d{5})[\s,]+(\d{5})', text)
    if code_match:
        sigungu = code_match.group(1)
        bjdong = code_match.group(2)
        
    # 3. 지번 토큰 추출 ("본번-부번" 또는 "단일 번호")
    tokens = re.findall(r'\b\d+(?:-\d+)?\b', text)
    parcels = []
    
    for tok in tokens:
        # 5자리 코드는 지번 목록에서 배제
        if len(tok) == 5 and not "-" in tok:
            continue
        
        if "-" in tok:
            parts = tok.split("-")
            b = parts[0].zfill(4)
            j = parts[1].zfill(4)
            parcels.append((b, j))
        else:
            parcels.append((tok.zfill(4), "0000"))
            
    unique_parcels = list(dict.fromkeys(parcels))
    
    final_targets = []
    for b, j in unique_parcels:
        pnu = f"{sigungu}{bjdong}1{b}{j}" if sigungu and bjdong else ""
        final_targets.append((sigungu, bjdong, b, j, pnu))
        
    return final_targets

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
# 3. 사용자 화면 구성 (순수 빈 화면)
# ----------------------------------------------------
zone_name_input = st.text_input("구역명 (임의 입력, 예: 사업지명)", value="", placeholder="구역명을 입력하세요 (비워두어도 무방)")

raw_text_data = st.text_area(
    "📥 AI 대화창에서 복사한 데이터를 그대로 붙여넣으세요:",
    value="",
    placeholder="AI가 뱉어준 텍스트 덩어리를 여기에 그대로 Ctrl+V 하세요.\n\n[예시 규격]\n[행정구역] 11380, 10300\n[지번목록] 12-4, 18-2, 22-11, 28-1 ...\n(또는 19자리 PNU 목록 등)",
    height=200
)

# 텍스트에 행정코드가 아예 없는 경우를 대비한 수동 입력 폴더 (평소엔 닫혀있음)
with st.expander("⚙️ 행정구역 코드가 텍스트에 없는 경우 직접 지정"):
    c1, c2 = st.columns(2)
    with c1:
        fallback_sigungu = st.text_input("시군구코드 (5자리)", value="", placeholder="예: 11380")
    with c2:
        fallback_bjdong = st.text_input("법정동코드 (5자리)", value="", placeholder="예: 10300")

# ----------------------------------------------------
# 4. 건축HUB 전수 수집 및 조서 산출
# ----------------------------------------------------
if st.button("🚀 건축물대장 수집 및 조서 산출", use_container_width=True):
    if not raw_text_data.strip():
        st.warning("붙여넣은 데이터가 없습니다.")
    else:
        parsed_targets = parse_universal_input(raw_text_data)
        
        # 행정코드 보완
        final_query_targets = []
        for sig, bjd, b, j, pnu in parsed_targets:
            s_code = sig if sig else fallback_sigungu.strip()
            b_code = bjd if bjd else fallback_bjdong.strip()
            if not s_code or not b_code:
                st.error("시군구코드(5자리) 또는 법정동코드(5자리)를 인식할 수 없습니다. 텍스트에 포함하거나 아래 접이식 메뉴에서 입력해 주세요.")
                final_query_targets = []
                break
            final_pnu = f"{s_code}{b_code}1{b}{j}"
            final_query_targets.append((s_code, b_code, b, j, final_pnu))

        if final_query_targets:
            display_title = zone_name_input.strip() if zone_name_input.strip() else "대상구역"
            st.info(f"총 {len(final_query_targets)}개 필지에 대해 건축물대장 수집을 시작합니다.")

            all_records = []
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, (s_cd, b_cd, b, j, pnu) in enumerate(final_query_targets):
                status_box.text(f"대장 수집 중 ({idx+1}/{len(final_query_targets)}): {int(b)}-{int(j)}")
                params = {
                    "serviceKey": CLEAN_KEY,
                    "sigunguCd": s_cd,
                    "bjdongCd": b_cd,
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

                    if isinstance(items, dict):
                        items["pnu"] = pnu
                        all_records.append(items)
                    elif isinstance(items, list):
                        for it in items:
                            it["pnu"] = pnu
                            all_records.append(it)
                except Exception:
                    pass

                prog.progress((idx + 1) / len(final_query_targets))
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            if not all_records:
                st.warning("국토부 건축HUB 대장에서 조회된 데이터가 없습니다. 행정구역 코드 및 지번을 확인해 주세요.")
            else:
                df_raw = pd.DataFrame(all_records)
                raw_cnt = len(df_raw)

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
                    label=f"📥 {display_title}_정밀조서_다운로드.csv",
                    data=df.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"{display_title}_종전연면적조서.csv",
                    mime="text/csv"
                )
