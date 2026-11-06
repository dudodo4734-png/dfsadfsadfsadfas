import math
import time
from datetime import datetime
from urllib.parse import unquote
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="정비사업 건축물대장 분석기", layout="wide")
st.title("🏢 정비사업 종전연면적 및 노후도 분석기")

# 사이드바 설정
with st.sidebar:
  st.header("🔑 API 설정")
  service_key = st.text_input(
      "공공데이터 일반 인증키 (Decoding 키)",
      placeholder="인증키 붙여넣기",
      type="password",
  )

# 기본 입력창
col1, col2, col3, col4 = st.columns(4)
with col1:
  sigungu_cd = st.text_input("시군구코드", value="11380")
with col2:
  bjdong_cd = st.text_input("법정동코드", value="10300")
with col3:
  bun = st.text_input("본번 (4자리 자동보정)", value="0281")
with col4:
  ji = st.text_input("부번 (전수조회는 빈칸)", value="")

if st.button("🚀 데이터 전수 수집 및 분석 실행", use_container_width=True):
  if not service_key:
    st.error("인증키를 입력해 주세요.")
  else:
    try:
      clean_key = unquote(service_key.strip())
      base_url = (
          "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
      )

      # 1페이지 호출하여 totalCount 확인
      params = {
          "serviceKey": clean_key,
          "sigunguCd": sigungu_cd.strip(),
          "bjdongCd": bjdong_cd.strip(),
          "platGbCd": "0",
          "bun": bun.strip().zfill(4),
          "numOfRows": 100,
          "pageNo": 1,
          "_type": "json",
      }
      if ji.strip():
        params["ji"] = ji.strip().zfill(4)

      with st.spinner("국토부 건축HUB 서버와 통신 중..."):
        res = requests.get(base_url, params=params, timeout=15)
        data = res.json()

      body = data.get("response", {}).get("body", {})
      
      # 🚨 핵심 수정: totalCount를 무조건 int 숫자로 강제 변환
      raw_total = body.get("totalCount", 0)
      try:
        total_count = int(raw_total)
      except (ValueError, TypeError):
        total_count = 0

      if total_count == 0:
        st.warning("조회된 건축물대장 데이터가 없습니다.")
      else:
        st.success(f"총 {total_count}건의 데이터를 확인했습니다. 수집을 진행합니다.")

        # 전수 데이터 수집
        all_items = []
        num_of_rows = 100
        # total_count가 int이므로 안전하게 계산됨
        total_pages = math.ceil(total_count / num_of_rows)

        progress_bar = st.progress(0.0)

        for page in range(1, total_pages + 1):
          params["pageNo"] = page
          res = requests.get(base_url, params=params, timeout=15)
          page_data = res.json()
          items = (
              page_data.get("response", {})
              .get("body", {})
              .get("items", {})
              .get("item", [])
          )

          if isinstance(items, dict):
            all_items.append(items)
          elif isinstance(items, list):
            all_items.extend(items)

          # progress 연산 안전 처리
          progress_val = float(page / total_pages)
          progress_bar.progress(min(progress_val, 1.0))
          time.sleep(0.1)

        # 데이터 가공 및 수치형 변환
        df = pd.DataFrame(all_items)

        # 연면적, 대지면적을 float 숫자로 강제 변환
        if "totArea" in df.columns:
          df["totArea"] = pd.to_numeric(df["totArea"], errors="coerce").fillna(0.0)
        else:
          df["totArea"] = 0.0

        if "platArea" in df.columns:
          df["platArea"] = pd.to_numeric(df["platArea"], errors="coerce").fillna(0.0)
        else:
          df["platArea"] = 0.0

        # 노후도 계산
        current_year = datetime.now().year

        def calc_age(date_val):
          if pd.isna(date_val) or str(date_val).strip() == "":
            return None
          d_str = str(date_val).strip()
          if len(d_str) >= 4:
            try:
              b_year = int(d_str[:4])
              return current_year - b_year
            except:
              return None
          return None

        if "useAprDay" in df.columns:
          df["age"] = df["useAprDay"].apply(calc_age)
        else:
          df["age"] = None

        df["is_old_30"] = df["age"].apply(lambda x: True if x is not None and x >= 30 else False)

        # 주요 지표 집계
        total_bld_count = len(df)
        total_gross_area_m2 = float(df["totArea"].sum())
        total_gross_area_py = total_gross_area_m2 / 3.3058

        valid_age_df = df[df["age"].notnull()]
        old_count = int(df["is_old_30"].sum())
        old_ratio = (old_count / len(valid_age_df) * 100) if len(valid_age_df) > 0 else 0.0

        # 결과 화면 출력
        st.subheader("📊 정비사업 기초 분석 결과")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("총 건축물 동수", f"{total_bld_count:,} 동")
        m2.metric("종전 연면적 합계", f"{total_gross_area_m2:,.1f} ㎡")
        m3.metric("종전 연면적 (평)", f"{total_gross_area_py:,.1f} 평")
        m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_count}동 / {len(valid_age_df)}동")

        # 테이블 뷰
        display_cols = [
            "platPlc",
            "bldNm",
            "mainPurpsCdNm",
            "totArea",
            "useAprDay",
            "age",
            "is_old_30",
        ]
        available_cols = [c for c in display_cols if c in df.columns]

        st.dataframe(
            df[available_cols].rename(
                columns={
                    "platPlc": "대지위치",
                    "bldNm": "건물명",
                    "mainPurpsCdNm": "주용도",
                    "totArea": "연면적(㎡)",
                    "useAprDay": "사용승인일",
                    "age": "경과년수",
                    "is_old_30": "30년노후여부",
                }
            ),
            use_container_width=True,
        )

        # CSV 다운로드 버튼
        csv = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 수집 데이터 원본 다운로드 (CSV)",
            data=csv,
            file_name=f"건축물대장_{sigungu_cd}_{bjdong_cd}_{bun}.csv",
            mime="text/csv",
        )

    except Exception as e:
      st.error(f"오류가 발생했습니다: {e}")
