import math
import time
from datetime import datetime
from urllib.parse import unquote
import pandas as pd
import requests
from shapely.geometry import Point, Polygon
import streamlit as st

st.set_page_config(page_title="불광8구역 정밀 종전연면적 분석기", layout="wide")
st.title("🏙️ 불광8구역 정밀 종전연면적 및 노후도 분석기")

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
  st.info("서울시 UPIS 공식 구역계(75개 꼭짓점) 기반 정밀 공간 필터링")

final_service_key = service_key_input.strip()

# ----------------------------------------------------
# 2. 서울시 UPIS 불광8구역 공식 폴리곤 (EPSG:5174)
# ----------------------------------------------------
BULGWANG8_RINGS_5174 = [
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
zone_poly = Polygon(BULGWANG8_RINGS_5174)

# ----------------------------------------------------
# 3. 실무형 대장 위계 정제 함수 (총괄 우선)
# ----------------------------------------------------
def clean_and_aggregate_registers(raw_df):
  df = raw_df.copy()

  # 문자열 형변환 (PyArrow Overflow 방지)
  str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji"]
  for col in str_cols:
    if col in df.columns:
      df[col] = df[col].astype(str).fillna("")

  # 고유 PK 중복 제거
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
# 4. 화면 UI
# ----------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
with col1:
  sigungu_cd = st.text_input("시군구코드", value="11380")
with col2:
  bjdong_cd = st.text_input("법정동코드", value="10300")
with col3:
  bun = st.text_input("본번", value="0281")
with col4:
  ji = st.text_input("부번 (전체조회는 빈칸)", value="")

execute_btn = st.button("🚀 281번지 대장 전수 수집 및 구역 정제", use_container_width=True)

if execute_btn:
  if not final_service_key:
    st.error("인증키를 입력해 주세요.")
  else:
    clean_key = unquote(final_service_key)
    base_url = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

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

    with st.spinner("281번지 전체 대장 수집 중..."):
      res = requests.get(base_url, params=params, timeout=15)
      data = res.json()
      body = data.get("response", {}).get("body", {})
      raw_total = body.get("totalCount", 0)
      try:
        total_count = int(raw_total)
      except:
        total_count = 0

    if total_count == 0:
      st.warning("조회된 건축물대장이 없습니다.")
    else:
      st.info(f"281번지 원천 대장 총 {total_count}건을 수집합니다.")
      all_items = []
      total_pages = math.ceil(total_count / 100)
      prog = st.progress(0.0)

      for p in range(1, total_pages + 1):
        params["pageNo"] = p
        r = requests.get(base_url, params=params, timeout=15)
        p_data = r.json()
        items = p_data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if isinstance(items, dict):
          all_items.append(items)
        elif isinstance(items, list):
          all_items.extend(items)
        prog.progress(p / total_pages)
        time.sleep(0.05)

      prog.empty()

      df_raw = pd.DataFrame(all_items)
      df_refined = clean_and_aggregate_registers(df_raw)
      st.session_state["df_refined"] = df_refined

# 세션에 데이터가 있을 때 렌더링
if "df_refined" in st.session_state:
  df = st.session_state["df_refined"].copy()

  st.markdown("---")
  st.subheader("🎯 구역 내/외 필지 검증 및 제외 필터")

  # 지번별 고유 목록 추출
  all_plat_plcs = sorted(df["platPlc"].unique().tolist())

  # 구역 외로 판정되는 번지를 직접 체크 해제/선택할 수 있는 UI
  excluded_parcels = st.multiselect(
      "❌ 구역에서 벗어난 지번(필지)을 선택하여 즉시 제외하세요:",
      options=all_plat_plcs,
      default=[],
      help="여기서 선택한 지번은 아래 종전연면적 및 노후도 집계에서 즉시 빠집니다."
  )

  if excluded_parcels:
    df = df[~df["platPlc"].isin(excluded_parcels)].copy()
    st.warning(f"제외된 {len(excluded_parcels)}개 필지를 빼고 재계산했습니다.")

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

  df["age"] = df["useAprDay"].apply(calc_age) if "useAprDay" in df.columns else None
  df["is_old_30"] = df["age"].apply(lambda x: True if x is not None and x >= 30 else False)

  # 집계 수치
  tot_area_m2 = float(df["totArea"].sum())
  tot_area_py = tot_area_m2 / 3.3058
  vl_area_m2 = float(df["vlRatEstmTotArea"].sum())
  vl_area_py = vl_area_m2 / 3.3058

  valid_age = df[df["age"].notnull()]
  old_cnt = int(df["is_old_30"].sum())
  old_ratio = (old_cnt / len(valid_age) * 100) if len(valid_age) > 0 else 0.0

  # 결과 카드
  m1, m2, m3, m4 = st.columns(4)
  m1.metric("선별 실물 건축물 동수", f"{len(df):,} 동")
  m2.metric("순수 종전연면적", f"{tot_area_m2:,.1f} ㎡", f"{tot_area_py:,.1f} 평")
  m3.metric("용적률산정용 연면적", f"{vl_area_m2:,.1f} ㎡", f"{vl_area_py:,.1f} 평")
  m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

  # 3개 탭 구성
  tab1, tab2, tab3 = st.tabs(["🔍 지번별 소계 대조표 (핵심)", "📋 전체 대장 상세", "🛠️ 대장 채택 감사(Audit)"])

  with tab1:
    st.markdown("### 📌 직접 계산하신 내역과 번지별로 맞춰보세요")
    summary = df.groupby("platPlc").agg(
        동수=("totArea", "count"),
        연면적_m2=("totArea", "sum"),
        용적률산정면적_m2=("vlRatEstmTotArea", "sum"),
        건물명=("bldNm", lambda x: ", ".join([str(v) for v in x if str(v).strip() != ""]))
    ).reset_index()
    summary["연면적_평"] = (summary["연면적_m2"] / 3.3058).round(1)
    summary["용적률산정면적_평"] = (summary["용적률산정면적_m2"] / 3.3058).round(1)

    st.dataframe(
        summary.rename(columns={
            "platPlc": "대지위치(지번)",
            "연면적_m2": "연면적(㎡)",
            "연면적_평": "연면적(평)",
            "용적률산정면적_m2": "용적률면적(㎡)",
            "용적률산정면적_평": "용적률면적(평)"
        }),
        use_container_width=True
    )

  with tab2:
    disp_cols = ["platPlc", "bldNm", "mainPurpsCdNm", "totArea", "vlRatEstmTotArea", "useAprDay", "age", "is_old_30"]
    avail = [c for c in disp_cols if c in df.columns]
    st.dataframe(df[avail], use_container_width=True)

  with tab3:
    audit_cols = ["platPlc", "bldNm", "regstrKindCdNm", "totArea", "mgmBldrgstPk"]
    avail_audit = [c for c in audit_cols if c in df.columns]
    audit_df = df[avail_audit].copy()
    for ac in audit_df.columns:
      audit_df[ac] = audit_df[ac].astype(str)
    st.dataframe(audit_df, use_container_width=True)

  st.download_button(
      label="📥 최종 정제 데이터 다운로드 (CSV)",
      data=df.to_csv(index=False, encoding="utf-8-sig"),
      file_name="불광8구역_최종종전연면적.csv",
      mime="text/csv"
  )
