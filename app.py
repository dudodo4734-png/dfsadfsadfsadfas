import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote

st.set_page_config(page_title="정비구역 실무형 정밀 종전연면적 조서기", layout="wide")
st.title("🏛️ 정비구역 실무형 종전연면적·노후도 정밀 검증 조서")

# ----------------------------------------------------
# 1. 시스템 환경 설정 (디코딩 키 내장)
# ----------------------------------------------------
SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_KEY = unquote(SERVICE_KEY)
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("⚙️ 시스템 인증 상태")
    st.success("건축HUB 공공데이터 인증키 장착 완료")
    st.markdown("---")
    st.markdown("""
    **실무 검증 핵심 기능**:
    - **외부 AI 키 불필요**: 공공데이터 API 단독 연동
    - **19자리 PNU 코드** 전수 생성 및 1:1 대조
    - **총괄/일반/동별** 위계 정제 (중복 배제)
    - **인터랙티브 필지 가감**: 불필요 필지 원클릭 제외
    """)

# ----------------------------------------------------
# 2. 실무 대장 정제 및 PNU 생성 유틸리티
# ----------------------------------------------------
def clean_and_aggregate_registers(raw_df):
    df = raw_df.copy()
    str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun", "pnu"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    # PK 기준 중복 대장 배제
    if "mgmBldrgstPk" in df.columns:
        df = df.drop_duplicates(subset=["mgmBldrgstPk"])

    df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
    df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)
    df["platArea"] = pd.to_numeric(df.get("platArea", 0), errors="coerce").fillna(0.0)

    # 총괄표제부(2) 존재 시 동별표제부(3) 중복 합산 배제 규칙
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

def parse_parcel_text(text, default_bun=""):
    results = []
    lines = re.split(r'[\n,;\t\s]+', text.strip())
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "-" in line:
            parts = line.split("-")
            b = parts[0].strip().zfill(4)
            j = parts[1].strip().zfill(4)
            results.append((b, j))
        else:
            if default_bun:
                results.append((default_bun.zfill(4), line.zfill(4)))
            else:
                results.append((line.zfill(4), "0000"))
    return list(dict.fromkeys(results))

# ----------------------------------------------------
# 3. 사용자 화면 구성 (지번 가감 및 수집)
# ----------------------------------------------------
# 도면 캡처본에서 판독된 불광8구역 기본 필지 데이터셋
DEFAULT_BULGWANG_PARCELS = [
    "281-9", "281-10", "281-15", "281-16", "281-19", "281-20",
    "281-22", "281-24", "281-25", "281-26", "281-27", "281-28", "281-29",
    "281-30", "281-37", "281-38", "281-45", "281-48", "281-52",
    "281-61", "281-62", "281-65", "281-70", "281-72"
]

st.subheader("1️⃣ 대상 사업지 행정구역 및 편입 지번 설정")

c1, c2, c3 = st.columns(3)
with c1:
    in_sigungu = st.text_input("시군구코드 (5자리)", value="11380", help="은평구: 11380, 성동구: 11200")
with c2:
    in_bjdong = st.text_input("법정동코드 (5자리)", value="10300", help="불광동: 10300, 성수동1가: 10600")
with c3:
    in_zone_nm = st.text_input("사업구역 명칭", value="불광8구역")

# 필지 가감 인터페이스
selected_parcels = st.multiselect(
    "✅ 반영 대상 지번 목록 (제외할 필지는 클릭해서 해제):",
    options=DEFAULT_BULGWANG_PARCELS,
    default=DEFAULT_BULGWANG_PARCELS,
    help="도면 외곽에 걸쳐 제외해야 할 필지는 여기서 클릭해 손쉽게 뺄 수 있습니다."
)

additional_input = st.text_input(
    "➕ 추가할 지번이 있는 경우 입력 (콤마 또는 띄어쓰기로 구분):",
    placeholder="예: 281-33, 281-35"
)

# ----------------------------------------------------
# 4. 건축HUB 수집 및 정밀 조서 산출
# ----------------------------------------------------
if st.button("🚀 종전연면적 및 노후도 전수 조서 산출", use_container_width=True):
    final_parcels = list(selected_parcels)
    if additional_input.strip():
        extras = re.split(r'[\s,]+', additional_input.strip())
        final_parcels.extend([p for p in extras if p])
    final_parcels = list(dict.fromkeys(final_parcels))

    target_tuples = parse_parcel_text("\n".join(final_parcels))

    if not target_tuples:
        st.warning("선택된 필지가 없습니다. 최소 1개 이상의 지번을 지정해 주세요.")
    else:
        st.info(f"총 {len(target_tuples)}개 필지에 대한 건축물대장 수집을 시작합니다.")
        
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
                
                # 19자리 공인 PNU 부착
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

            # 30년 법정 노후도 계산
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
