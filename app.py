import math
import time
from datetime import datetime
from urllib.parse import unquote
import pandas as pd
import requests
from shapely.geometry import Point, Polygon
import streamlit as st

st.set_page_config(page_title="정비사업 2단계 공간정제 분석기", layout="wide")
st.title("🏙️ 정비사업 지번 일대 수집 및 구역계 자동 정밀 분석기")

# ----------------------------------------------------
# 1. 사이드바 설정
# ----------------------------------------------------
DEFAULT_SERVICE_KEY = ""  # 공공데이터 Decoding 키 입력 (고정용)

with st.sidebar:
  st.header("🔑 API 설정")
  service_key_input = st.text_input(
      "건축HUB Decoding 인증키",
      value=DEFAULT_SERVICE_KEY,
      type="password",
  )
  st.markdown("---")
  st.markdown("""
  **작업 프로세스**:
  1. 본번 일대 1차 전수 수집
  2. 서울시 GIS 구역계 호출 (불광8 폴리곤 내장 백업)
  3. 공간 교차(Point in Polygon)로 구역 내 필지만 자동 최종 추출
  """)

final_service_key = service_key_input.strip()

# ----------------------------------------------------
# 2. 서울시 UPIS 정비구역 폴리곤 조회 함수 (보강형)
# ----------------------------------------------------
SEOUL_PROXY_BASE = "https://urban.seoul.go.kr/proxy/proxy.jsp?http://98.33.2.225:6080/arcgis/rest/services/UPIS/20200526_WFS/MapServer/12/query"

# 이미 확보된 불광8구역 75개 점 정밀 폴리곤 좌표 (EPSG:5174)
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

def fetch_zone_polygon(zone_name):
  # 1. '불광8' 검색 시 기확보된 정밀 공간데이터 즉시 반환 (프록시 세션에러 원천 방지)
  if "불광8" in zone_name:
    return FALLBACK_BULGWANG8, {"DGM_NM": "불광8구역", "DGM_AR": 13145.1, "SIGNGU_SE": "11380"}

  # 2. 기타 구역명 조회 시 서울시 프록시 서버 호출
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
    if features:
      target = features[0]
      rings = target.get("geometry", {}).get("rings", [])
      attrs = target.get("attributes", {})
      if rings:
        return rings[0], attrs
  except Exception:
    pass

  return None, None

# ----------------------------------------------------
# 3. 주소 -> EPSG:5174 좌표 변환 (V-World 오픈 지오코더)
# ----------------------------------------------------
def geocode_address(address):
  try:
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getcoord",
        "version": "2.0",
        "crs": "epsg:5174",
        "address": address,
        "refine": "true",
        "simple": "false",
        "format": "json",
        "type": "PARCEL",
        "key": "CEB52025-E065-364C-9DBA-44880E3B02B8"  # V-World 공용 검색키
    }
    r = requests.get(url, params=params, timeout=5)
    res = r.json()
    if res.get("response", {}).get("status") == "OK":
      point = res["response"]["result"]["point"]
      return float(point["x"]), float(point["y"])
  except Exception:
    pass
  return None, None

# ----------------------------------------------------
# 4. 실무형 대장 위계 정제 함수 (총괄 우선)
# ----------------------------------------------------
def clean_and_aggregate_registers(raw_df):
  df = raw_df.copy()

  # 문자열 형변환 (PyArrow Overflow 방지)
  str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun"]
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

  # 지번별 총괄/동별 중복 정제
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
# 5. 사용자 입력 UI
# ----------------------------------------------------
st.subheader("1️⃣ 사업지 기본 행정코드 및 본번 입력")
col1, col2, col3, col4 = st.columns(4)
with col1:
  sigungu_cd = st.text_input("시군구코드", value="11380", help="서울시 은평구: 11380")
with col2:
  bjdong_cd = st.text_input("법정동코드", value="10300", help="불광동: 10300")
with col3:
  bun = st.text_input("본번 (4자리 자동보정)", value="0281", help="예: 281, 600 등")
with col4:
  zone_name_input = st.text_input("사업지 구역 명칭", value="불광8", help="예: 불광8, 대조1 등")

execute_btn = st.button("🚀 2단계 필터링 분석 시작 (본번 수집 ➔ 구역계 자동 분별)", use_container_width=True)

if execute_btn:
  if not final_service_key:
    st.error("좌측 사이드바에 건축HUB 디코딩 인증키를 입력해 주세요.")
  elif not bun.strip() or not zone_name_input.strip():
    st.warning("본번과 사업지 구역 명칭을 모두 입력해 주세요.")
  else:
    clean_bun = bun.strip().zfill(4)
    target_zone = zone_name_input.strip()

    # --- [1단계] 구역계 폴리곤 확보 ---
    with st.spinner(f"구역계 폴리곤 확인 중... ({target_zone})"):
      polygon_rings, zone_attrs = fetch_zone_polygon(target_zone)

    if not polygon_rings:
      st.error(f"서울시 GIS 서버에서 '{target_zone}' 구역계를 찾지 못했습니다. 구역명을 다시 확인해 주세요.")
    else:
      zone_poly = Polygon(polygon_rings)
      st.success(f"📍 **{zone_attrs.get('DGM_NM', target_zone)}** 구역계 확보 완료 (고시면적: {zone_attrs.get('DGM_AR', 0):,.1f} ㎡)")

      # --- [2단계] 국토부 건축HUB 본번 일대 전수 1차 수집 ---
      clean_key = unquote(final_service_key)
      base_url = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

      params = {
          "serviceKey": clean_key,
          "sigunguCd": sigungu_cd.strip(),
          "bjdongCd": bjdong_cd.strip(),
          "platGbCd": "0",
          "bun": clean_bun,
          "numOfRows": 100,
          "pageNo": 1,
          "_type": "json",
      }

      with st.spinner(f"{clean_bun}번지 일대 대장 1차 전수 수집 중..."):
        res = requests.get(base_url, params=params, timeout=15)
        data = res.json()
        body = data.get("response", {}).get("body", {})
        raw_total = body.get("totalCount", 0)
        try:
          total_count = int(raw_total)
        except:
          total_count = 0

      if total_count == 0:
        st.warning(f"해당 지번({clean_bun}번지)에 등록된 건축물대장이 없습니다.")
      else:
        st.info(f"1차 수집: {clean_bun}번지 일대 대장 총 {total_count}건을 확인했습니다. 전수 수집을 시작합니다.")

        all_items = []
        total_pages = math.ceil(total_count / 100)
        prog1 = st.progress(0.0)

        for p in range(1, total_pages + 1):
          params["pageNo"] = p
          r = requests.get(base_url, params=params, timeout=15)
          p_data = r.json()
          items = p_data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
          if isinstance(items, dict):
            all_items.append(items)
          elif isinstance(items, list):
            all_items.extend(items)
          prog1.progress(p / total_pages)
          time.sleep(0.04)
        prog1.empty()

        df_raw = pd.DataFrame(all_items)
        df_refined = clean_and_aggregate_registers(df_raw)

        # --- [3단계] 공간 좌표 판정 (구역계 내부 건물만 자동 필터링) ---
        st.write("📐 구역계 경계선 좌표와 건물 위치 공간 교차(Point in Polygon) 분석 중...")
        unique_addresses = df_refined["platPlc"].unique()
        addr_coords = {}

        coord_prog = st.progress(0.0)
        for idx, addr in enumerate(unique_addresses):
          x, y = geocode_address(addr)
          if x and y:
            addr_coords[addr] = (x, y)
          coord_prog.progress((idx + 1) / len(unique_addresses))
          time.sleep(0.02)
        coord_prog.empty()

        # 폴리곤 내부 포함 여부 판정
        inside_indices = []
        for idx, row in df_refined.iterrows():
          addr = row.get("platPlc", "")
          if addr in addr_coords:
            pt = Point(addr_coords[addr])
            if zone_poly.contains(pt):
              inside_indices.append(idx)

        # 최종 선별 데이터프레임
        if inside_indices:
          df_final = df_refined.loc[inside_indices].copy()
          st.success(f"🎯 2차 정제 완료: {clean_bun}번지 일대 {len(df_refined)}동 중, **{target_zone} 구역 경계 내부 {len(df_final)}동**만 최종 선별되었습니다! (외곽 {len(df_refined) - len(df_final)}동 자동 제외)")
        else:
          st.warning("⚠️ 지오코더 자동 좌표 변환으로 확인되지 않은 필지가 있습니다. 전체 수집분으로 기본 표시합니다.")
          df_final = df_refined.copy()

        # 노후도 계산
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

        df_final["age"] = df_final["useAprDay"].apply(calc_age) if "useAprDay" in df_final.columns else None
        df_final["is_old_30"] = df_final["age"].apply(lambda x: True if x is not None and x >= 30 else False)

        # 핵심 지표 산출
        tot_area_m2 = float(df_final["totArea"].sum())
        tot_area_py = tot_area_m2 / 3.3058
        vl_area_m2 = float(df_final["vlRatEstmTotArea"].sum())
        vl_area_py = vl_area_m2 / 3.3058

        valid_age = df_final[df_final["age"].notnull()]
        old_cnt = int(df_final["is_old_30"].sum())
        old_ratio = (old_cnt / len(valid_age) * 100) if len(valid_age) > 0 else 0.0

        st.markdown("---")
        st.subheader(f"📊 {target_zone} 종전연면적 및 노후도 최종 집계표")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("구역 내 확정 건축물", f"{len(df_final):,} 동")
        m2.metric("순수 종전연면적", f"{tot_area_m2:,.1f} ㎡", f"{tot_area_py:,.1f} 평")
        m3.metric("용적률산정용 연면적", f"{vl_area_m2:,.1f} ㎡", f"{vl_area_py:,.1f} 평")
        m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

        # 결과 탭 구성
        t1, t2, t3 = st.tabs(["🔍 지번별 면적 소계 (대조용)", "📋 상세 대장 목록", "🛠️ 대장 채택 감사(Audit)"])

        with t1:
          summary = df_final.groupby("platPlc").agg(
              동수=("totArea", "count"),
              연면적_m2=("totArea", "sum"),
              용적률산정면적_m2=("vlRatEstmTotArea", "sum"),
              건물명=("bldNm", lambda x: ", ".join([str(v) for v in x if str(v).strip() != ""]))
          ).reset_index()
          summary["연면적_평"] = (summary["연면적_m2"] / 3.3058).round(1)
          summary["용적률산정면적_평"] = (summary["용적률산정면적_m2"] / 3.3058).round(1)
          st.dataframe(summary, use_container_width=True)

        with t2:
          disp_cols = ["platPlc", "bldNm", "mainPurpsCdNm", "totArea", "vlRatEstmTotArea", "useAprDay", "age", "is_old_30"]
          avail = [c for c in disp_cols if c in df_final.columns]
          st.dataframe(df_final[avail], use_container_width=True)

        with t3:
          audit_cols = ["platPlc", "bldNm", "regstrKindCdNm", "totArea", "mgmBldrgstPk"]
          avail_audit = [c for c in audit_cols if c in df_final.columns]
          audit_df = df_final[avail_audit].copy()
          for ac in audit_df.columns:
            audit_df[ac] = audit_df[ac].astype(str)
          st.dataframe(audit_df, use_container_width=True)

        st.download_button(
            label=f"📥 {target_zone}_최종집계데이터.csv 다운로드",
            data=df_final.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{target_zone}_종전연면적집계.csv",
            mime="text/csv"
        )
