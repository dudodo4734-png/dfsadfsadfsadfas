import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 범용 종전연면적 조서기", layout="wide")
st.title("🏛️ 정비사업 실무형 종전연면적·노후도 범용 조서기")

# ----------------------------------------------------
# 1. 공공데이터 인증키 설정 (건축HUB + 행안부 주소)
# ----------------------------------------------------
BLD_SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_BLD_KEY = unquote(BLD_SERVICE_KEY)
BASE_BLD_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

JUSO_API_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
JUSO_KEY = "U01TX0FVVEgyMDI0MDkyMjIzNDQxNjExNTEwNjQ="

with st.sidebar:
    st.header("📌 전국 범용 조서 시스템")
    st.markdown("""
    **어떤 데이터든 자동 판별 지원**:
    - **도로명주소**: `진흥로15길 12-4` 형태
    - **도로명 결합**: 기준 도로명 지정 시 `12-4, 18-2` 번호만 입력 가능
    - **지번/PNU**: 19자리 PNU 또는 `[행정구역] + 지번목록`
    
    *※ 행안부 주소 DB를 통해 공인 19자리 PNU와 법정동 지번을 실시간 자동 매핑합니다.*
    """)

# ----------------------------------------------------
# 2. 엔진: 도로명 ➔ 공인 지번/PNU 실시간 변환
# ----------------------------------------------------
def resolve_road_to_jibun(query_text):
    params = {
        "confmKey": JUSO_KEY,
        "currentPage": 1,
        "countPerPage": 1,
        "keyword": query_text.strip(),
        "resultType": "json"
    }
    try:
        res = requests.get(JUSO_API_URL, params=params, timeout=5)
        j = res.json()
        juso_list = j.get("results", {}).get("juso", [])
        if juso_list:
            item = juso_list[0]
            adm_cd = item.get("admCd", "")
            sigungu = adm_cd[:5]
            bjdong = adm_cd[5:]
            lnbrMnnm = item.get("lnbrMnnm", "0").zfill(4)
            lnbrSlno = item.get("lnbrSlno", "0").zfill(4)
            pnu = f"{adm_cd}1{lnbrMnnm}{lnbrSlno}"
            
            return {
                "sigungu": sigungu,
                "bjdong": bjdong,
                "bun": lnbrMnnm,
                "ji": lnbrSlno,
                "pnu": pnu,
                "roadAddr": item.get("roadAddr", ""),
                "jibunAddr": item.get("jibunAddr", ""),
                "bdNm": item.get("bdNm", "")
            }
    except Exception:
        pass
    return None

def clean_and_aggregate_registers(raw_df):
    df = raw_df.copy()
    str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun", "pnu", "roadAddr"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    if "mgmBldrgstPk" in df.columns:
        df = df.drop_duplicates(subset=["mgmBldrgstPk"])

    df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
    df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)
    df["platArea"] = pd.to_numeric(df.get("platArea", 0), errors="coerce").fillna(0.0)

    # 총괄표제부(2) 존재 시 동별표제부(3) 중복 배제
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
# 3. 사용자 화면 구성 (어떤 입력이든 수용 가능한 UI)
# ----------------------------------------------------
col_meta1, col_meta2 = st.columns([1, 2])
with col_meta1:
    zone_name = st.text_input("구역명 (임의 입력)", value="", placeholder="예: 구역명 / 사업지명")
    base_road = st.text_input(
        "기준 도로명 (선택)",
        value="",
        placeholder="예: 진흥로15길, 봉은사로 등",
        help="도면에 도로명이 하나일 경우 여기에 적어두면, 우측 텍스트창에는 '12-4, 18-2' 번호만 적어도 자동 인식합니다."
    )

with col_meta2:
    raw_input_text = st.text_area(
        "📥 AI 대화창에서 추출한 데이터를 그대로 붙여넣으세요:",
        value="",
        placeholder="[모든 입력 형태 자동 지원]\n\n1. 도로명 풀주소: 진흥로15길 12-4, 진흥로15길 18-2 ...\n2. 기준 도로명이 있을 때: 12-4, 18-2, 22-11 ...\n3. 지번/PNU 포맷: [행정구역] 11380, 10300 / [지번목록] 12-4 ...\n\n(콤마, 줄바꿈, 엑셀 열 복사 전부 지원)",
        height=180
    )

# ----------------------------------------------------
# 4. 실시간 매핑 및 대장 전수 수집 파이프라인
# ----------------------------------------------------
if st.button("🚀 주소 매핑 및 종전연면적 조서 산출", use_container_width=True):
    if not raw_input_text.strip():
        st.warning("붙여넣은 데이터가 없습니다.")
    else:
        display_title = zone_name.strip() if zone_name.strip() else "대상구역"
        
        # 1. 19자리 PNU 직접 입력 감지
        pnu_matches = list(set(re.findall(r'\b\d{19}\b', raw_input_text)))
        
        # 2. [행정구역] 코드 감지
        code_match = re.search(r'(\d{5})[\s,]+(\d{5})', raw_input_text)
        
        targets_to_query = []
        status_box = st.empty()
        prog = st.progress(0.0)

        # 케이스 A: 19자리 PNU 목록인 경우
        if pnu_matches:
            for p in pnu_matches:
                targets_to_query.append({
                    "sigungu": p[:5], "bjdong": p[5:10], "bun": p[11:15], "ji": p[15:19],
                    "pnu": p, "roadAddr": "", "jibunAddr": ""
                })
        # 케이스 B: 행정코드 2개 + 지번목록인 경우
        elif code_match:
            sig = code_match.group(1)
            bjd = code_match.group(2)
            tokens = re.findall(r'\b\d+(?:-\d+)?\b', raw_input_text)
            for tok in tokens:
                if len(tok) == 5 and "-" not in tok:
                    continue
                if "-" in tok:
                    parts = tok.split("-")
                    b, j = parts[0].zfill(4), parts[1].zfill(4)
                else:
                    b, j = tok.zfill(4), "0000"
                p = f"{sig}{bjd}1{b}{j}"
                targets_to_query.append({
                    "sigungu": sig, "bjdong": bjd, "bun": b, "ji": j,
                    "pnu": p, "roadAddr": "", "jibunAddr": ""
                })
        # 케이스 C: 도로명 번호 또는 건물명 목록인 경우 (행안부 주소엔진 가동)
        else:
            items = re.split(r'[\n,;\t]+', raw_input_text.strip())
            raw_items = [it.strip() for it in items if it.strip()]
            
            st.info(f"도로명 건물번호 {len(raw_items)}개 감지: 행안부 주소 DB를 통해 실제 지번 및 19자리 PNU로 변환합니다.")
            
            resolved_dict = {}
            for idx, item in enumerate(raw_items):
                query = item
                if base_road.strip() and ("길" not in item and "로" not in item and "동" not in item):
                    query = f"{base_road.strip()} {item}"
                
                status_box.text(f"[1/2 주소변환] {query} ➔ 공인 지번 조회 중")
                res_addr = resolve_road_to_jibun(query)
                if res_addr and res_addr["pnu"] not in resolved_dict:
                    resolved_dict[res_addr["pnu"]] = res_addr
                
                prog.progress((idx + 1) / (len(raw_items) * 2))
                time.sleep(0.01)
                
            targets_to_query = list(resolved_dict.values())

        # 최종 대장 호출 단계
        if not targets_to_query:
            status_box.empty()
            prog.empty()
            st.error("유효한 주소 또는 지번을 식별할 수 없습니다. 입력값을 확인해 주세요.")
        else:
            total_targets = len(targets_to_query)
            all_records = []

            for idx, p_info in enumerate(targets_to_query):
                status_box.text(f"[2/2 대장수집] ({idx+1}/{total_targets}) 본번 {int(p_info['bun'])}-부번 {int(p_info['ji'])}")
                params = {
                    "serviceKey": CLEAN_BLD_KEY,
                    "sigunguCd": p_info["sigungu"],
                    "bjdongCd": p_info["bjdong"],
                    "platGbCd": "0",
                    "bun": p_info["bun"],
                    "ji": p_info["ji"],
                    "numOfRows": 50,
                    "pageNo": 1,
                    "_type": "json"
                }
                try:
                    r = requests.get(BASE_BLD_URL, params=params, timeout=8)
                    items_res = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
                    
                    if isinstance(items_res, dict):
                        items_res["pnu"] = p_info["pnu"]
                        items_res["roadAddr"] = p_info.get("roadAddr", "")
                        all_records.append(items_res)
                    elif isinstance(items_res, list):
                        for it in items_res:
                            it["pnu"] = p_info["pnu"]
                            it["roadAddr"] = p_info.get("roadAddr", "")
                            all_records.append(it)
                except Exception:
                    pass

                prog.progress(0.5 + ((idx + 1) / (total_targets * 2)))
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            if not all_records:
                st.warning("건축HUB에서 대장 정보를 찾을 수 없습니다. 법정동 코드 및 지번을 확인해 주세요.")
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
                st.subheader(f"📊 {display_title} 종전연면적 및 노후도 최종 집계표")

                if raw_cnt != refined_cnt:
                    st.info(f"💡 대장 위계 정제: 원천 대장 {raw_cnt}건 중 총괄-동별 중복 {raw_cnt - refined_cnt}건을 자동 배제하고 실물 {refined_cnt}동 기준으로 산출했습니다.")

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("실물 건축물 동수", f"{refined_cnt:,} 동", f"원천 대장 {raw_cnt}건")
                m2.metric("순수 종전연면적", f"{tot_m2:,.1f} ㎡", f"{tot_py:,.1f} 평")
                m3.metric("용적률산정용 연면적", f"{vl_m2:,.1f} ㎡", f"{vl_py:,.1f} 평")
                m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

                t1, t2 = st.tabs(["📑 19자리 PNU·지번별 소계 조서", "📋 대장 원천 상세 데이터"])

                with t1:
                    summary = df.groupby(["pnu", "platPlc"]).agg(
                        도로명주소=("roadAddr", lambda x: next((v for v in x if v), "-")),
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
                            "platPlc": "실제 법정동 대지위치",
                            "연면적_m2": "연면적(㎡)",
                            "연면적_평": "연면적(평)",
                            "용적률면적_m2": "용적률면적(㎡)"
                        }),
                        use_container_width=True
                    )

                with t2:
                    disp_cols = ["pnu", "platPlc", "roadAddr", "bldNm", "regstrKindCdNm", "totArea", "vlRatEstmTotArea", "useAprDay", "age", "is_old_30"]
                    avail = [c for c in disp_cols if c in df.columns]
                    st.dataframe(df[avail], use_container_width=True)

                st.download_button(
                    label=f"📥 {display_title}_정밀조서_다운로드.csv",
                    data=df.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"{display_title}_종전연면적조서.csv",
                    mime="text/csv"
                )
