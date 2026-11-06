import math
import time
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="도시정비 건축물대장 분석기", layout="wide"
)

st.title("🏢 도시정비 건축물대장 수집 및 종전연면적 분석기")
st.caption(
    "국토교통부 건축HUB OpenAPI를 연동하여 지번 일대의 현황을 전수 수집하고 분석합니다."
)

with st.sidebar:
  st.header("🔑 API 설정")
  service_key = st.text_input(
      "공공데이터 일반 인증키",
      placeholder="발급받은 인증키를 입력하세요",
      type="password",
  )

col1, col2, col3, col4 = st.columns(4)
with col1:
  sigungu_cd = st.text_input("시군구코드", value="11380", help="은평구: 11380")
with col2:
  bjdong_cd = st.text_input("법정동코드", value="10300", help="불광동: 10300")
with col3:
  bun = st.text_input("본번 (4자리)", value="0281")
with col4:
  ji = st.text_input("부번 (생략 시 본번 일대 전수)", value="")

if st.button("🚀 데이터 전수 수집 및 분석 실행", use_container_width=True):
  if not service_key:
    st.error("공공데이터포털 일반 인증키(ServiceKey)를 입력해 주세요.")
  else:
    base_url = (
        "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
    )
    params = {
        "serviceKey": service_key,
        "sigunguCd": sigungu_cd,
        "bjdongCd": bjdong_cd,
        "platGbCd": "0",
        "bun": bun.zfill(4),
        "numOfRows": 100,
        "pageNo": 1,
        "_type": "json",
    }
    if ji:
      params["ji"] = ji.zfill(4)

    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
      status_text.text("데이터 현황 확인 중...")
      res = requests.get(base_url, params=params, timeout=10).json()
      body = res.get("response", {}).get("body", {})
      total_count = body.get("totalCount", 0)

      if total_count == 0:
        st.warning("조회된 건축물대장 데이터가 없습니다.")
      else:
        total_pages = math.ceil(total_count / 100)
        status_text.text(f"총 {total_count}건 발견 (수집 중)...")

        all_items = []
        items = body.get("items", {}).get("item", [])
        if isinstance(items, dict):
          all_items.append(items)
        elif isinstance(items, list):
          all_items.extend(items)

        for p in range(2, total_pages + 1):
          params["pageNo"] = p
          try:
            p_res = requests.get(base_url, params=params, timeout=10).json()
            p_items = (
                p_res.get("response", {})
                .get("body", {})
                .get("items", {})
                .get("item", [])
            )
            if isinstance(p_items, dict):
              all_items.append(p_items)
            elif isinstance(p_items, list):
              all_items.extend(p_items)
          except:
            pass
          progress_bar.progress(p / total_pages)
          time.sleep(0.05)

        records = []
        for it in all_items:
          records.append({
              "대지위치": it.get("platPlc"),
              "본번": it.get("bun"),
              "부번": it.get("ji"),
              "건물명": it.get("bldNm"),
              "동명칭": it.get("dongNm"),
              "대장구분": it.get("regstrGbCdNm"),
              "주부속구분": it.get("mainAtchGbCdNm"),
              "주용도": it.get("mainPurpsCdNm"),
              "기타용도": it.get("etcPurps"),
              "구조": it.get("strctCdNm"),
              "대지면적(㎡)": (
                  float(it.get("platArea", 0)) if it.get("platArea") else 0.0
              ),
              "연면적(㎡)": (
                  float(it.get("totArea", 0)) if it.get("totArea") else 0.0
              ),
              "건폐율(%)": (
                  float(it.get("bcRat", 0)) if it.get("bcRat") else 0.0
              ),
              "용적률(%)": (
                  float(it.get("vlRat", 0)) if it.get("vlRat") else 0.0
              ),
              "세대수": int(it.get("hhldCnt", 0)) if it.get("hhldCnt") else 0,
              "사용승인일": it.get("useAprDay", ""),
          })

        df = pd.DataFrame(records)
        status_text.success(f"수집 완료! 총 {len(df)}건 처리됨")

        st.divider()
        st.subheader("📊 종전 현황 기초 분석 요약")

        total_bld_cnt = len(df)
        total_gross_area = df["연면적(㎡)"].sum()

        df["준공연도"] = (
            df["사용승인일"].str.slice(0, 4).apply(pd.to_numeric, errors="coerce")
        )
        old_bld_cnt = (df["준공연도"] <= 1996).sum()
        old_ratio = (old_bld_cnt / total_bld_cnt * 100) if total_bld_cnt else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("총 동수", f"{total_bld_cnt:,} 동")
        m2.metric("종전 연면적 합계", f"{total_gross_area:,.2f} ㎡")
        m3.metric("30년 이상 노후 동수", f"{old_bld_cnt:,} 동")
        m4.metric("노후도 비율", f"{old_ratio:.1f} %")

        st.write("##### ■ 주요 용도별 연면적 및 동수 현황")
        usage_df = (
            df.groupby("주용도")
            .agg({"연면적(㎡)": ["count", "sum"]})
            .reset_index()
        )
        usage_df.columns = ["주용도", "동수(개)", "연면적 합계(㎡)"]
        st.dataframe(usage_df, use_container_width=True)

        csv_data = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 정제 데이터 엑셀(CSV) 다운로드",
            data=csv_data,
            file_name=f"건축물대장_전수_{bun}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.write("##### ■ 수집 상세 데이터 미리보기")
        st.dataframe(df, use_container_width=True)

    except Exception as e:
      st.error(f"오류가 발생했습니다: {e}")
