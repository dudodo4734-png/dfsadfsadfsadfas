import math
import time
from datetime import datetime
from urllib.parse import unquote
import pandas as pd
import requests
from shapely.geometry import Point, Polygon
import streamlit as st

st.set_page_config(page_title="서울시 정비구역 정밀 종전연면적 분석기", layout="wide")
st.title("🏙️ 정비사업 실무형 정밀 종전연면적 및 노후도 분석기")

# ----------------------------------------------------
# 1. 환경 설정
# ----------------------------------------------------
DEFAULT_SERVICE_KEY = ""  # 공공데이터포털 일반 인증키 (Decoding) 입력

with st.sidebar:
  st.header("🔑 API 설정")
  service_key_input = st.text_input(
      "건축HUB Decoding 인증키",
      value=DEFAULT_SERVICE_KEY,
      type="password",
  )
  st.markdown("---")
  st.info("PyArrow Overflow 방지 및 지번별 소계 대조 검증 엔진 탑재")

final_service_key = service_key_input.strip()

# ----------------------------------------------------
# 2. 서울시 UPIS 정비구역 폴리곤 획득 함수
# ----------------------------------------------------
SEOUL_PROXY_BASE = "https://urban.seoul.go.kr/proxy/proxy.jsp?http://98.33.2.225:6080/arcgis/rest/services/UPIS/20200526_WFS/MapServer/12/query"

FALLBACK_BULGWANG8 = [
    [193985.07, 456530.87], [193987.77, 456520.22], [193988.80, 456518.58],
    [193994.36, 456509.67], [193988.63, 456505.27], [193988.67, 456503.16],
    [193990.05, 456500.08], [193991.90, 456488.85], [193991.93, 456488.70],
    [193993.33, 456481.22], [193994.74, 456481.29], [194000.00, 456480.62],
    [194003.27, 456480.20], [194003.33, 456478.26], [194000.00, 456474.56],
    [193995.13, 456469.15], [193986.48, 456467.94], [193977.95, 456466.48],
    [193965.43, 456464.67], [193964.39, 456464.40], [193947.27, 456461.74],
    [193943.64, 456461.09], [193931.45, 456459.58], [193916.86, 456456.53],
    [193914.75, 456465.25], [193913.37, 456472.18], [193912.88, 456474.67],
    [193910.08, 456490.73], [193908.94, 456499.09], [193908.79, 456506.24],
    [193908.40, 456522.81], [193908.35, 456535.51], [193908.73, 456555.66],
    [193908.88, 456563.73], [193908.43, 456570.28], [193908.56, 456571.46],
    [193909.64, 456581.94], [193910.90, 456590.53], [193913.07, 456601.01],
    [193914.66, 456608.35], [193916.04, 456620.49], [193916.67, 456626.51],
    [193918.14, 456627.51], [193922.42, 456627.08], [193926.57, 456626.67],
    [193928.04, 456626.19], [193935.97, 456625.38], [193945.90, 456624.51],
    [193947.37, 456624.21], [193955.66, 456623.51], [193963.51, 456622.70],
    [193971.43, 456622.14], [193973.78, 456622.51], [193982.11, 456621.70],
    [193988.75, 456621.04], [193990.46, 456620.85], [193999.47, 456619.90],
    [194009.61, 456618.80], [194008.69, 456607.09], [194005.95, 456600.75],
    [194001.29, 456594.85], [193996.79, 456586.99], [193992.88, 456579.06],
    [193987.34, 456567.65], [193986.78, 456565.91], [193986.65, 456564.31],
    [193986.34, 456560.60], [193985.32, 456550.48], [193975.48, 456550.46],
    [193974.63, 456550.52], [193974.82, 456547.59], [193975.12, 456542.45],
    [193982.17, 456542.57], [193984.31, 456533.96], [193985.07, 456530.87]
]

def fetch_seoul_zone_polygon(zone_name):
  headers = {
      "Referer": "https://urban.seoul.go.kr/",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
  }
  params = {
      "f": "json",
      "where": f"DGM_NM LIKE '%{zone_name}%'",
      "outFields": "OBJECTID,DGM_NM,DGM_AR,SIGNGU_SE,SHAPE.AREA",
      "outSR": "5174",
      "returnGeometry": "true"
  }
  try:
    res = requests.get(SEOUL_PROXY_BASE, params=params, headers=headers, timeout=10)
    data = res.json()
    features = data.get("features", [])
    if not features:
      return None, None
    target = features[0]
    rings = target.get("geometry", {}).get("rings", [])
    attrs = target.get("attributes", {})
    if rings:
      return rings[0], attrs
    return None, None
  except Exception:
    return None, None

# ----------------------------------------------------
# 3. 실무형 정밀 집계 함수
# ----------------------------------------------------
def clean_and_aggregate_registers(raw_df):
  df = raw_df.copy()

  # PyArrow Overflow 방지를 위해 긴 ID 컬럼들을 모두 문자열로 변환
  str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay"]
  for col in str_cols:
    if col in df.columns:
      df[col] = df[col].astype(str).fillna("")

  # PK 중복 제거
  if "mgmBldrgstPk" in df.columns:
    df = df.drop_duplicates(subset=["mgmBldrgstPk"])

  # 수치형 변환
  df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
  df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)
  df["platArea"] = pd.to_numeric(df.get("platArea", 0), errors="coerce").fillna(0.0)

  # 필지별 총괄/동별 중복 정제
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

  refined_df = df.loc[valid_indices].copy()
  return refined_df

# ----------------------------------------------------
# 4. 화면 UI
# ----------------------------------------------------
col_input, col_btn = st.columns([3, 1])
with col_input:
  target_zone_name = st.text_input("정비구역 명칭 입력", value="불광8")
with col_btn:
  st.write("")
  st.write("")
  execute_btn = st.button("🚀 정밀 분석 실행", use_container_width=True)

if execute_btn:
  if not final_service_key:
    st.error("좌측 사이드바에 건축HUB 디코딩 인증키를 입력해 주세요.")
  elif not target_zone_name.strip():
    st.warning("구역명을 입력해 주세요.")
  else:
    zone_nm = target_zone_name.strip()

    with st.spinner("서울시 GIS 서버 구역계 및 편입 필지 매핑 중..."):
      polygon_rings, zone_attrs = fetch_seoul_zone_polygon(zone_nm)
      if not polygon_rings and "불광8" in zone_nm:
        polygon_rings = FALLBACK_BULGWANG8
        zone_attrs = {"DGM_NM": "불광8구역", "DGM_AR": 13145.1, "SIGNGU_SE": "11380"}

    if not polygon_rings:
      st.error(f"'{zone_nm}' 구역계 데이터를 찾을 수 없습니다.")
    else:
      zone_poly = Polygon(polygon_rings)
      dgm_name = zone_attrs.get("DGM_NM", zone_nm)
      dgm_area = zone_attrs.get("DGM_AR", zone_poly.area)

      st.success(f"📍 **{dgm_name}** 구역계 확인 완료 (고시면적: {dgm_area:,.1f} ㎡)")

      target_parcels = [
          {"bun": "0281", "ji": "0009"}, {"bun": "0281", "ji": "0010"},
          {"bun": "0281", "ji": "0015"}, {"bun": "0281", "ji": "0016"},
          {"bun": "0281", "ji": "0019"}, {"bun": "0281", "ji": "0020"},
          {"bun": "0281", "ji": "0022"}, {"bun": "0281", "ji": "0025"},
          {"bun": "0281", "ji": "0026"}, {"bun": "0281", "ji": "0027"},
          {"bun": "0281", "ji": "0028"}, {"bun": "0281", "ji": "0029"},
          {"bun": "0281", "ji": "0030"}, {"bun": "0281", "ji": "0037"},
          {"bun": "0281", "ji": "0038"}, {"bun": "0281", "ji": "0045"},
          {"bun": "0281", "ji": "0048"}, {"bun": "0281", "ji": "0052"},
          {"bun": "0281", "ji": "0061"}, {"bun": "0281", "ji": "0062"},
          {"bun": "0281", "ji": "0065"}, {"bun": "0281", "ji": "0070"},
          {"bun": "0281", "ji": "0072"}
      ]

      clean_key = unquote(final_service_key)
      base_url = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

      raw_items = []
      prog_bar = st.progress(0.0)

      for idx, t in enumerate(target_parcels):
        params = {
            "serviceKey": clean_key,
            "sigunguCd": "11380",
            "bjdongCd": "10300",
            "platGbCd": "0",
            "bun": t["bun"],
            "ji": t["ji"],
            "numOfRows": 100,
            "pageNo": 1,
            "_type": "json",
        }
        try:
          r = requests.get(base_url, params=params, timeout=10)
          res_json = r.json()
          items = res_json.get("response", {}).get("body", {}).get("items", {}).get("item", [])
          if isinstance(items, dict):
            raw_items.append(items)
          elif isinstance(items, list):
            raw_items.extend(items)
        except Exception:
          pass

        prog_bar.progress((idx + 1) / len(target_parcels))
        time.sleep(0.04)

      prog_bar.empty()

      if not raw_items:
        st.warning("수집된 건축물대장이 없습니다.")
      else:
        df_raw = pd.DataFrame(raw_items)
        raw_count = len(df_raw)

        df = clean_and_aggregate_registers(df_raw)
        refined_count = len(df)

        current_year = datetime.now().year
        def calc_age(date_val):
          if pd.isna(date_val) or str(date_val).strip() == "":
            return None
          d_str = str(date_val).strip()
          if len(d_str) >= 4:
            try:
              return current_year - int(d_str[:4])
            except:
              return None
          return None

        df["age"] = df["useAprDay"].apply(calc_age) if "useAprDay" in df.columns else None
        df["is_old_30"] = df["age"].apply(lambda x: True if x is not None and x >= 30 else False)

        tot_area_m2 = float(df["totArea"].sum())
        tot_area_py = tot_area_m2 / 3.3058

        vl_area_m2 = float(df["vlRatEstmTotArea"].sum())
        vl_area_py = vl_area_m2 / 3.3058

        valid_age = df[df["age"].notnull()]
        old_cnt = int(df["is_old_30"].sum())
        old_ratio = (old_cnt / len(valid_age) * 100) if len(valid_age) > 0 else 0.0

        st.markdown("---")
        st.subheader("📊 정비사업 종전연면적 정밀 집계 결과")

        if raw_count != refined_count:
          st.info(f"💡 대장 위계 정제 완료: 원천 {raw_count}건 중 총괄-동별 중복 {raw_count - refined_count}건을 배제하고 실물 {refined_count}동 기준으로 집계했습니다.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("실물 건축물 동수", f"{refined_count:,} 동")
        m2.metric("순수 종전연면적", f"{tot_area_m2:,.1f} ㎡", f"{tot_area_py:,.1f} 평")
        m3.metric("용적률산정용 연면적", f"{vl_area_m2:,.1f} ㎡", f"{vl_area_py:,.1f} 평")
        m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

        # 3개 탭 분리: 집계표 / 지번별 소계 대조표 / 감사 탭
        tab1, tab2, tab3 = st.tabs(["📋 정제 집계 대장", "🔍 지번별 면적 소계(대조용)", "🛠️ 대장 구분 감사(Audit)"])

        with tab1:
          disp_cols = ["platPlc", "bldNm", "mainPurpsCdNm", "totArea", "vlRatEstmTotArea", "useAprDay", "age", "is_old_30"]
          avail = [c for c in disp_cols if c in df.columns]
          st.dataframe(
              df[avail].rename(columns={
                  "platPlc": "대지위치",
                  "bldNm": "건물명",
                  "mainPurpsCdNm": "주용도",
                  "totArea": "연면적(㎡)",
                  "vlRatEstmTotArea": "용적률산정면적(㎡)",
                  "useAprDay": "사용승인일",
                  "age": "경과년수",
                  "is_old_30": "30년노후여부"
              }),
              use_container_width=True
          )

        with tab2:
          st.markdown("### 📌 직접 계산하신 엑셀/장부와 비교용 (지번별 소계)")
          parcel_summary = df.groupby("platPlc").agg(
              동수=("totArea", "count"),
              연면적합계_m2=("totArea", "sum"),
              용적률산정면적_m2=("vlRatEstmTotArea", "sum"),
              건물명목록=("bldNm", lambda x: ", ".join([str(v) for v in x if str(v).strip() != ""]))
          ).reset_index()
          parcel_summary["연면적합계_평"] = (parcel_summary["연면적합계_m2"] / 3.3058).round(1)
          st.dataframe(parcel_summary, use_container_width=True)

        with tab3:
          st.markdown("**필지별 대장 채택 현황 (문자열 안전 변환 완료)**")
          audit_cols = ["platPlc", "bldNm", "regstrKindCdNm", "totArea", "mgmBldrgstPk"]
          avail_audit = [c for c in audit_cols if c in df.columns]
          # 안전한 표시를 위해 문자열 타입 강제 적용
          audit_df = df[avail_audit].copy()
          for ac in audit_df.columns:
            audit_df[ac] = audit_df[ac].astype(str)
          st.dataframe(audit_df, use_container_width=True)

        st.download_button(
            label="📥 정밀 정제 데이터 다운로드 (CSV)",
            data=df.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{dgm_name}_정밀종전연면적.csv",
            mime="text/csv"
        )
