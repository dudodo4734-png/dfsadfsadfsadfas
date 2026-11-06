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
      total_count = body.get("totalCount", 0)

      if total_count == 0:
        st.warning("조회된 건축물대장 데이터가 없습니다.")
      else:
        st.success(f"총 {total_count}건의 데이터를 확인했습니다. 수집을 진행합니다.")

        # 전수 데이터 수집
        all_items = []
        num_of_rows = 100
        total_pages = math.ceil(total_count / num_of_rows)

        progress_bar = st.progress(0)

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

          progress_bar.progress(page / total_pages)
          time.sleep(0.1)

        # 데이터 가공 및 수치형 변환
        df
