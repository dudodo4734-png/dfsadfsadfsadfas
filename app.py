import streamlit as st
import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote
from PIL import Image
import base64
import io

st.set_page_config(page_title="AI 구역계 비전 판독 및 정밀 종전연면적 조서기", layout="wide")
st.title("🏛️ AI 구역계 도면 판독 및 종전연면적·노후도 자동 조서기")

# ----------------------------------------------------
# 1. 시스템 환경 설정 (디코딩 키 내장)
# ----------------------------------------------------
BLD_SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="
CLEAN_BLD_KEY = unquote(BLD_SERVICE_KEY)
BASE_BLD_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

with st.sidebar:
    st.header("🔑 AI 및 공공데이터 설정")
    gemini_api_key = st.text_input(
        "Google Gemini API 키 입력",
        type="password",
        help="Google AI Studio(aistudio.google.com)에서 무료 발급 가능한 API 키를 넣어주세요."
    )
    st.success("건축HUB 공공데이터 인증키 기본 내장 완료")
    st.markdown("---")
    st.markdown("""
    **원스톱 프로세스**:
    1. 도시계획포털/지도 캡처본 업로드
    2. Vision AI가 구역 내 지번/건물명 1차 자동 판독
    3. 실무자 검토 (포함/제외 체크)
    4. 19자리 PNU 생성 및 종전연면적·노후도 전수 조서 산출
    """)

# ----------------------------------------------------
# 2. 비전 분석 엔진 (Gemini Vision REST API 경량 연동)
# ----------------------------------------------------
def analyze_map_image(image_bytes, bjdong_hint, api_key):
    """
    캡처된 도면 이미지에서 구역 내 지번 및 건물명을 추출하는 경량 Vision 함수
    """
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    prompt = f"""
    당신은 도시정비사업 도시계획 및 지적 분석 전문가입니다.
    첨부된 이미지는 서울도시계획포털 또는 지적도 기반의 정비구역도 캡처본입니다.
    대상 법정동 힌트: {bjdong_hint}

    [수행 작업]
    1. 붉은색/유색 실선으로 표시된 정비구역 경계선 '내부'에 명백히 포함된 지번(본번-부번) 또는 도로명 건물번호, 주요 식별 건물명을 전수 판독하세요.
    2. 경계선에 걸쳐 있어 포함 여부가 모호한 필지는 'border_parcels'에 별도로 분류하세요.
    3. 구역 바깥(외곽)에 있는 번호나 건물은 반드시 제외하세요.

    반드시 아래와 같은 순수 JSON 형식으로만 응답하세요. 다른 설명 텍스트는 일절 출력하지 마세요:
    {{
      "zone_name": "구역명(확인되는 경우, 예: 불광8구역)",
      "base_bun": "대표 본번(확인되는 경우 4자리, 예: 0281)",
      "inside_parcels": ["본번-부번 또는 부번 목록, 예: 281-9, 281-10, 281-15..."],
      "border_parcels": ["경계 걸침 목록, 예: 281-26, GS25부지..."],
      "landmarks": ["경인에버빌", "은평파크빌", "해피하우스"]
    }}
    """
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": b64_image
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }
    
    headers = {"Content-Type": "application/json"}
    res = requests.post(endpoint, json=payload, headers=headers, timeout=30)
    
    if res.status_code != 200:
        raise Exception(f"AI 비전 분석 실패 ({res.status_code}): {res.text}")
        
    result_json = res.json()
    raw_text = result_json["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw_text)

# ----------------------------------------------------
# 3. 실무 대장 정제 및 PNU 생성 유틸리티
# ----------------------------------------------------
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

    # 필지별 총괄표제부(2) 존재 시 동별표제부(3) 중복 합산 배제
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
    lines = re.split(r'[\n,;\t]+', text.strip())
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
# 4. 사용자 화면 구성
# ----------------------------------------------------
st.subheader("1️⃣ 도면 캡처 이미지 업로드 및 기본 정보")

c1, c2, c3 = st.columns(3)
with c1:
    in_sigungu = st.text_input("시군구코드 (5자리)", value="11380", help="은평: 11380, 성동: 11200")
with c2:
    in_bjdong = st.text_input("법정동코드 (5자리)", value="10300", help="불광: 10300, 성수1가: 10600")
with c3:
    in_bjdong_nm = st.text_input("법정동 명칭", value="서울특별시 은평구 불광동")

uploaded_file = st.file_uploader("📸 정비구역 캡처본(PNG/JPG) 업로드", type=["png", "jpg", "jpeg"])

if uploaded_file is not None:
    img = Image.open(uploaded_file)
    st.image(img, caption="업로드된 도면 캡처본", use_container_width=True)

    if st.button("🔍 AI 도면 판독 실행", use_container_width=True):
        if not gemini_api_key:
            st.error("좌측 사이드바에 Google Gemini API 키를 먼저 입력해 주세요.")
        else:
            with st.spinner("AI가 도면 내 경계선과 필지 번호, 건물명을 분석 중입니다..."):
                try:
                    uploaded_file.seek(0)
                    ai_res = analyze_map_image(uploaded_file.read(), in_bjdong_nm, gemini_api_key)
                    st.session_state["ai_res"] = ai_res
                    st.success("도면 판독이 완료되었습니다. 아래 2단계에서 검토해 주세요.")
                except Exception as e:
                    st.error(f"판독 중 오류 발생: {e}")

# ----------------------------------------------------
# 5. 2단계: 실무자 검토 및 보정 인터페이스 (Human-in-the-Loop)
# ----------------------------------------------------
if "ai_res" in st.session_state:
    ai_data = st.session_state["ai_res"]
    st.markdown("---")
    st.subheader("2️⃣ AI 판독 결과 검토 및 필지 가감 (실무자 최종 확인)")

    default_bun_detected = ai_data.get("base_bun", "0281")
    inside_candidates = ai_data.get("inside_parcels", [])
    border_candidates = ai_data.get("border_parcels", [])
    all_candidates = list(dict.fromkeys(inside_candidates + border_candidates))

    col_meta1, col_meta2 = st.columns(2)
    with col_meta1:
        st.write(f"🎯 **식별된 구역명**: `{ai_data.get('zone_name', '미상')}`")
        st.write(f"🏢 **식별된 주요 건물명**: `{', '.join(ai_data.get('landmarks', []))}`")
    with col_meta2:
        default_bun_input = st.text_input("기본 본번 (부번만 있는 경우 매핑용)", value=default_bun_detected)

    # 실무자 선택 멀티셀렉트
    selected_parcels = st.multiselect(
        "✅ 최종 반영할 지번 목록 (제외할 필지는 클릭해 해제하세요):",
        options=all_candidates,
        default=inside_candidates,
        help="외곽선에 걸쳐 애매한 필지나 도로부지는 여기서 제외할 수 있습니다."
    )

    additional_input = st.text_input(
        "➕ 추가로 포함할 지번이 있다면 입력하세요 (콤마 또는 띄어쓰기 구분):",
        placeholder="예: 281-37, 281-38"
    )

    if st.button("🚀 최종 확정 및 대장 전수 수집 시작", use_container_width=True):
        final_list = list(selected_parcels)
        if additional_input.strip():
            extra_parts = re.split(r'[\s,]+', additional_input.strip())
            final_list.extend([p for p in extra_parts if p])
        final_list = list(dict.fromkeys(final_list))

        target_tuples = parse_parcel_text("\n".join(final_list), default_bun_input)

        if not target_tuples:
            st.warning("선택된 필지가 없습니다.")
        else:
            st.info(f"선별된 총 {len(target_tuples)}개 필지에 대해 건축HUB 대장을 호출합니다.")
            
            all_records = []
            prog = st.progress(0.0)
            status_box = st.empty()

            for idx, (b, j) in enumerate(target_tuples):
                status_box.text(f"대장 수집 중 ({idx+1}/{len(target_tuples)}): 본번 {int(b)}-부번 {int(j)}")
                params = {
                    "serviceKey": CLEAN_BLD_KEY,
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
                    r = requests.get(BASE_BLD_URL, params=params, timeout=8)
                    res_json = r.json()
                    items = res_json.get("response", {}).get("body", {}).get("items", {}).get("item", [])
                    if isinstance(items, dict):
                        items["pnu"] = f"{in_sigungu.strip()}{in_bjdong.strip()}1{b}{j}"
                        all_records.append(items)
                    elif isinstance(items, list):
                        for it in items:
                            it["pnu"] = f"{in_sigungu.strip()}{in_bjdong.strip()}1{b}{j}"
                            all_records.append(it)
                except Exception:
                    pass

                prog.progress((idx + 1) / len(target_tuples))
                time.sleep(0.02)

            status_box.empty()
            prog.empty()

            if not all_records:
                st.warning("수집된 건축물대장이 없습니다. 지번을 다시 확인해 주세요.")
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
                st.subheader("📊 3단계: 종전연면적 및 노후도 최종 집계 결과")

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("실물 건축물 동수", f"{refined_cnt:,} 동", f"원천 대장 {raw_cnt}건")
                m2.metric("순수 종전연면적", f"{tot_m2:,.1f} ㎡", f"{tot_py:,.1f} 평")
                m3.metric("용적률산정용 연면적", f"{vl_m2:,.1f} ㎡", f"{vl_py:,.1f} 평")
                m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

                # 조서 탭 분리
                t1, t2 = st.tabs(["📑 19자리 PNU 지번별 소계 조서", "📋 대장 원천 상세"])

                with t1:
                    summary = df.groupby(["pnu", "platPlc"]).agg(
                        동수=("totArea", "count"),
                        연면적_m2=("totArea", "sum"),
                        용적률면적_m2=("vlRatEstmTotArea", "sum"),
                        건물명=("bldNm", lambda x: ", ".join([str(v) for v in x if str(v).strip() != ""]))
                    ).reset_index()
                    summary["연면적_평"] = (summary["연면적_m2"] / 3.3058).round(1)
                    st.dataframe(
                        summary.rename(columns={
                            "pnu": "19자리 토지고유번호(PNU)",
                            "platPlc": "법정동 대지위치",
                            "연면적_m2": "연면적(㎡)",
                            "연면적_평": "연면적(평)",
                            "용적률면적_m2": "용적률면적(㎡)"
                        }),
                        use_container_width=True
                    )

                with t2:
                    disp = ["pnu", "platPlc", "bldNm", "regstrKindCdNm", "totArea", "vlRatEstmTotArea", "useAprDay", "age", "is_old_30"]
                    avail = [c for c in disp if c in df.columns]
                    st.dataframe(df[avail], use_container_width=True)

                st.download_button(
                    label="📥 최종 정밀 조서 다운로드 (CSV)",
                    data=df.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"정비구역_종전연면적_정밀조서.csv",
                    mime="text/csv"
                )
