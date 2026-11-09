import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 통합 종전연면적 조서기", layout="wide")
st.title("🏛️ 정비사업 실무형 종전연면적·노후도 조서기")

# ----------------------------------------------------
# 1. 공공데이터 인증키 설정 (건축HUB + 행안부 주소)
# ----------------------------------------------------
BLD_SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_BLD_KEY = unquote(BLD_SERVICE_KEY)
BASE_BLD_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

JUSO_API_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
JUSO_KEY = "U01TX0FVVEgyMDI0MDkyMjIzNDQxNjExNTEwNjQ="

with st.sidebar:
    st.header("📌 사용 안내")
    st.markdown("""
    **완전 범용 다중 도로명 처리**:
    - `진흥로15길 12-4`, `통일로 10` 등 여러 도로명이 섞여 있어도 자동 분류
    - 입력된 텍스트를 행정안전부 공식 주소 DB와 1차 대조하여 **진짜 법정동 지번 및 19자리 PNU** 자동 산출
    - 이후 국토부 건축HUB 대장을 호출하므로 지번 오인식 원천 차단
    """)

# ----------------------------------------------------
# 2. 엔진: 도로명/주소 ➔ 공인 지번 및 19자리 PNU 실시간 매핑
# ----------------------------------------------------
def resolve_to_pnu_and_jibun(query_text):
    """
    행정안전부 도로명주소 API를 호출하여 입력 텍스트(도로명주소 또는 건물명)로부터
    공인 법정동 시군구코드(5), 법정동코드(5), 본번(4), 부번(4), 19자리 PNU를 확정 반환
    """
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
# 3. 사용자 화면 구성 (기준 도로명 칸 제거, 순수 단일 입력창)
# ----------------------------------------------------
zone_name = st.text_input("구역명 (임의 입력)", value="", placeholder="예: 불광8구역")

raw_input_text = st.text_area(
    "📥 AI가 추출해 준 데이터 텍스트를 그대로 붙여넣으세요:",
    value="",
    placeholder="여러 도로명이 섞여 있어도 그대로 넣으시면 됩니다.\n\n[입력 예시]\n진흥로15길 12-4, 진흥로15길 18-2, 진흥로15길 22-11\n통일로78길 5, 통일로78길 7-1\n진흥로 215, 진흥로 217\n(또는 19자리 공인 PNU 목록)",
    height=200
)

# ----------------------------------------------------
# 4. 실시간 주소 해석 및 건축HUB 대장 수집 파이프라인
# ----------------------------------------------------
if st.button("🚀 종전연면적 및 노후도 조서 산출", use_container_width=True):
    if not raw_input_text.strip():
        st.warning("붙여넣은 데이터가 없습니다.")
    else:
        display_title = zone_name.strip() if zone_name.strip() else "대상구역"
        status_box = st.empty()
        prog = st.progress(0.0)

        # 1순위: 19자리 PNU 직입력 감지
        pnu_matches = list(set(re.findall(r'\b\d{19}\b', raw_input_text)))

        targets_to_query = []

        if pnu_matches:
            for p in pnu_matches:
                targets_to_query.append({
                    "sigungu": p[:5], "bjdong": p[5:10], "bun": p[11:15], "ji": p[15:19],
                    "pnu": p, "roadAddr": "", "jibunAddr": ""
                })
        else:
            # 2순위: 콤마, 줄바꿈, 세미콜론 등으로 구분된 개별 주소 항목 분리
            items = re.split(r'[\n,;\t]+', raw_input_text.strip())
            raw_items = [it.strip() for it in items if it.strip()]

            st.info(f"총 {len(raw_items)}개 항목 감지: 행정안전부 주소 DB를 통해 실제 법정동 지번 및 19자리 PNU로 정밀 매핑합니다.")

            resolved_dict = {}
            for idx, item in enumerate(raw_items):
                status_box.text(f"[1/2단계: 공인 주소/지번 매핑] {item}")
                res_addr = resolve_to_pnu_and_jibun(item)
                if res_addr and res_addr["pnu"] not in resolved_dict:
                    resolved_dict[res_addr["pnu"]] = res_addr

                prog.progress((idx + 1) / (len(raw_items) * 2))
                time.sleep(0.01)

            targets_to_query = list(resolved_dict.values())

        if not targets_to_query:
            status_box.empty()
            prog.empty()
            st.error("입력한 주소에서 공인 지번 정보를 찾지 못했습니다. 도로명칭이 올바르게 포함되어 있는지 확인해 주세요.")
        else:
            # 2단계: 국토부 건축HUB 대장 수집
            total_targets = len(targets_to_query)
            all_records = []

            for idx, p_info in enumerate(targets_to_query):
                status_box.text(f"[2/2단계: 건축HUB 대장 수집] ({idx+1}/{total_targets}) {p_info.get('jibunAddr', p_info['pnu'])}")
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
                st.warning("건축물대장에서 건축물 정보를 조회할 수 없습니다.")
            else:
                df_raw = pd.DataFrame(all_records)
                raw_cnt = len(df_raw)

                # 총괄-동별 위계 정제
                df = clean_and_aggregate_registers(df_raw)
                refined_cnt = len(df)

                # 30년 이상 노후도 계산
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

                t1, t2 = st.tabs(["📑 19자리 PNU·법정동 지번 조서", "📋 대장 원천 상세 데이터"])

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
                            "platPlc": "실제 법정동 대지위치(지번)",
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
