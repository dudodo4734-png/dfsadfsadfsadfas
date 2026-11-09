import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비사업 도로명·지번 통합 종전연면적 조서기", layout="wide")
st.title("🏛️ 정비사업 실무형 도로명·지번 자동변환 종전연면적 조서기")

# ----------------------------------------------------
# 1. 고정 API 키 설정 (공공데이터 건축HUB)
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("💡 실무 사용 안내")
    st.info("""
    **도면에 적힌 번호 그대로 입력 가능!**
    
    포털 지도 캡처본의 숫자는 대다수 **도로명 건물번호**입니다.
    
    대표 도로명(예: `진흥로15길`)을 적어두고 도면의 번호들(`18-4, 22-11` 등)이나 건물명(`경인에버빌`)을 붙여넣으면, **웹이 알아서 실제 법정동 지번과 19자리 PNU로 공식 변환**하여 건축물대장을 수집합니다.
    """)

# ----------------------------------------------------
# 2. 도로명 ➔ 공인 지번/PNU 변환 엔진 (VWorld / 행안부 오픈 주소)
# ----------------------------------------------------
def resolve_address_to_pnu(query_text):
    """
    도로명주소 또는 건물명을 공인 법정동, 본번-부번, 19자리 PNU로 실시간 변환
    """
    url = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
    # 실무용 오픈 주소검색 파라미터 (공공 무료)
    params = {
        "confmKey": "U01TX0FVVEgyMDI0MDkyMjIzNDQxNjExNTEwNjQ=",
        "currentPage": 1,
        "countPerPage": 1,
        "keyword": query_text.strip(),
        "resultType": "json"
    }
    try:
        res = requests.get(url, params=params, timeout=5)
        j = res.json()
        juso_list = j.get("results", {}).get("juso", [])
        if juso_list:
            item = juso_list[0]
            adm_cd = item.get("admCd", "") # 시군구(5)+법정동(5)
            sigungu = adm_cd[:5]
            bjdong = adm_cd[5:]
            lnbrMnnm = item.get("lnbrMnnm", "0").zfill(4) # 본번
            lnbrSlno = item.get("lnbrSlno", "0").zfill(4) # 부번
            pnu = f"{adm_cd}1{lnbrMnnm}{lnbrSlno}"
            
            return {
                "success": True,
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
# 3. 사용자 인터페이스 (초간편 도로명/지번 겸용 입력)
# ----------------------------------------------------
c1, c2 = st.columns([1, 2])
with c1:
    zone_name = st.text_input("구역명", value="불광8구역")
    base_road = st.text_input("기준 도로명 (생략 가능)", value="진흥로15길", help="도면 내 대표 도로명을 적어두면 뒤쪽 숫자만 쳐도 자동 결합됩니다.")
with c2:
    st.markdown("##### 도면 판독 텍스트 투입")
    raw_input_text = st.text_area(
        "도면의 번호 또는 건물명을 콤마/줄바꿈으로 붙여넣으세요:",
        value="""22-11, 22-13, 22-15, 22-1, 22-3, 22-5, 22-7,
22-2, 22-4, 22-6, 22-8, 22-12, 22-14, 22-16, 22-18,
18-1, 18-3, 18-5, 18-7, 18-9, 18-11,
18-2, 18-4, 18-6, 18-8, 18-10, 18-12, 18-14, 18-16, 18-20, 18-22, 18-24, 18-26, 18-28, 18-30,
12-4, 12-8, 12-10, 12-12, 12-16, 12-7, 12-11, 12-13, 12-15,
경인에버빌, 은평파크빌, 해피하우스, 미르라이프, 푸른빌라, 아임빌""",
        height=140
    )

# ----------------------------------------------------
# 4. 수집 및 전수 검증 조서 생성
# ----------------------------------------------------
if st.button("🚀 도로명 지번 자동변환 및 종전연면적 조서 산출", use_container_width=True):
    items = re.split(r'[\n,;\t]+', raw_input_text.strip())
    targets = [it.strip() for it in items if it.strip()]

    if not targets:
        st.warning("입력된 항목이 없습니다.")
    else:
        st.info(f"총 {len(targets)}개 도면 객체에 대해 실시간 주소 변환 및 대장 조회를 시작합니다.")
        
        prog = st.progress(0.0)
        status_box = st.empty()
        
        resolved_parcels = []
        # 1차: 주소 변환
        for idx, t in enumerate(targets):
            status_box.text(f"[1/2단계] 공인 지번/PNU 매핑 중: {t}")
            # 도로명 결합 검색어 구성
            search_query = t
            if base_road and ("길" not in t and "로" not in t and "동" not in t):
                search_query = f"{base_road} {t}"
            
            res_addr = resolve_address_to_pnu(search_query)
            if res_addr:
                resolved_parcels.append(res_addr)
            prog.progress((idx + 1) / (len(targets) * 2))
            time.sleep(0.01)

        # 중복 PNU 제거
        unique_parcels = {p["pnu"]: p for p in resolved_parcels}.values()
        
        # 2차: 건축HUB 대장 수집
        all_records = []
        total_p = len(unique_parcels)
        
        for idx, p_info in enumerate(unique_parcels):
            status_box.text(f"[2/2단계] 대장 수집 중 ({idx+1}/{total_p}): {p_info['jibunAddr']}")
            params = {
                "serviceKey": CLEAN_KEY,
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
                r = requests.get(BASE_URL, params=params, timeout=8)
                items_res = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
                
                if isinstance(items_res, dict):
                    items_res["pnu"] = p_info["pnu"]
                    items_res["roadAddr"] = p_info["roadAddr"]
                    all_records.append(items_res)
                elif isinstance(items_res, list):
                    for it in items_res:
                        it["pnu"] = p_info["pnu"]
                        it["roadAddr"] = p_info["roadAddr"]
                        all_records.append(it)
            except Exception:
                pass
            
            prog.progress(0.5 + ((idx + 1) / (total_p * 2)))
            time.sleep(0.02)

        status_box.empty()
        prog.empty()

        if not all_records:
            st.warning("대장을 조회하지 못했습니다. 입력값을 확인해 주세요.")
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
            st.subheader(f"📊 {zone_name} 종전연면적 및 노후도 최종 집계표")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("실물 건축물 동수", f"{refined_cnt:,} 동", f"원천 대장 {raw_cnt}건")
            m2.metric("순수 종전연면적", f"{tot_m2:,.1f} ㎡", f"{tot_py:,.1f} 평")
            m3.metric("용적률산정용 연면적", f"{vl_m2:,.1f} ㎡", f"{vl_py:,.1f} 평")
            m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

            t1, t2 = st.tabs(["📑 19자리 PNU·법정동 지번 조서", "📋 대장 원천 상세 데이터"])

            with t1:
                summary = df.groupby(["pnu", "platPlc"]).agg(
                    도로명주소=("roadAddr", "first"),
                    동수=("totArea", "count"),
                    연면적_m2=("totArea", "sum"),
                    용적률면적_m2=("vlRatEstmTotArea", "sum"),
                    건물명=("bldNm", lambda x: ", ".join([str(v) for v in x if str(v).strip() != ""]))
                ).reset_index()
                summary["연면적_평"] = (summary["연면적_m2"] / 3.3058).round(1)
                summary["용적률면적_평"] = (summary["용적률면적_m2"] / 3.3058).round(1)

                st.dataframe(
                    summary.rename(columns={
                        "pnu": "19자리 PNU",
                        "platPlc": "실제 법정동 지번",
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
                label=f"📥 {zone_name}_정밀조서_다운로드.csv",
                data=df.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"{zone_name}_종전연면적조서.csv",
                mime="text/csv"
            )
