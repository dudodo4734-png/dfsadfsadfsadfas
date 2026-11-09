import streamlit as st
import pandas as pd
import requests
import json
import math
import time
from datetime import datetime
from urllib.parse import unquote
from shapely.geometry import Polygon, Point

st.set_page_config(page_title="서울시 정비구역 완전범용 정밀 분석기", layout="wide")
st.title("🏙️ 서울시 정비사업구역 전수 자동 필지추출 및 종전연면적 분석기")

# ----------------------------------------------------
# 1. 사이드바 설정
# ----------------------------------------------------
DEFAULT_SERVICE_KEY = "ewFq4vtF6VE99JJ6B3hs9hD0YRssb4Y4p0KGSpLQsIkO5tSTNhauXB2y47pTnCu00ghthtujLk7cKBVQDzC0kg=="

with st.sidebar:
    st.header("🔑 API 설정")
    service_key_input = st.text_input(
        "건축HUB Decoding 인증키",
        value=DEFAULT_SERVICE_KEY,
        type="password"
    )
    st.markdown("---")
    st.markdown("""
    **작업 프로세스**:
    1. 서울시 UPIS 정비사업 레이어(MapServer 12) 실시간 명칭 검색
    2. 구역계 외곽선 다각형(Polygon) 좌표 획득
    3. 구역 내 편입 지적 필지(PNU/지번) 전수 공간 역추출
    4. 건축물대장 핀포인트 수집 및 위계 정제(총괄/동별 중복 배제)
    """)

final_service_key = service_key_input.strip()

# ----------------------------------------------------
# 2. 서울시 GIS 서버 동적 연동 함수 (구역계 & 필지)
# ----------------------------------------------------
UPIS_ZONE_URL = "https://urban.seoul.go.kr/proxy/proxy.jsp?http://98.33.2.225:6080/arcgis/rest/services/UPIS/20200526_WFS/MapServer/12/query"
UPIS_PARCEL_URL = "https://urban.seoul.go.kr/proxy/proxy.jsp?http://98.33.2.225:6080/arcgis/rest/services/UPIS/20200526_WFS/MapServer/3/query"

def search_seoul_zone(zone_query):
    """
    정확한 정비사업구역 명칭 또는 핵심 키워드로 서울시 GIS 서버에서 구역계 폴리곤 검색
    """
    headers = {
        "Referer": "https://urban.seoul.go.kr/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    clean_name = zone_query.strip()
    params = {
        "f": "json",
        "where": f"DGM_NM LIKE '%{clean_name}%'",
        "outFields": "OBJECTID,DGM_NM,DGM_AR,SIGNGU_SE,SHAPE.AREA",
        "outSR": "5174",
        "returnGeometry": "true"
    }
    try:
        res = requests.get(UPIS_ZONE_URL, params=params, headers=headers, timeout=12)
        data = res.json()
        features = data.get("features", [])
        return features
    except Exception as e:
        st.error(f"서울시 GIS 서버 연동 오류: {e}")
        return []

def fetch_parcels_by_geometry(rings_coords):
    """
    구역계 폴리곤 좌표를 서울시 지적 레이어에 공간 질의하여 포함 필지(지번) 목록 획득
    """
    headers = {
        "Referer": "https://urban.seoul.go.kr/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    geom_json = {
        "rings": [rings_coords],
        "spatialReference": {"wkid": 5174}
    }
    params = {
        "f": "json",
        "geometry": json.dumps(geom_json),
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "5174",
        "outFields": "PNU,JIBUN,BUN,JI,SIGNGU_SE,BJDONG_SE",
        "returnGeometry": "false"
    }
    try:
        res = requests.get(UPIS_PARCEL_URL, params=params, headers=headers, timeout=15)
        data = res.json()
        features = data.get("features", [])
        parcels = []
        for f in features:
            attr = f.get("attributes", {})
            pnu = str(attr.get("PNU", "")).strip()
            bun = str(attr.get("BUN", "")).strip().zfill(4)
            ji = str(attr.get("JI", "")).strip().zfill(4)
            sigungu = str(attr.get("SIGNGU_SE", "")).strip()
            bjdong = str(attr.get("BJDONG_SE", "")).strip()

            # PNU에서 시군구/법정동/본번/부번 역추출 (속성 결측 대비)
            if len(pnu) == 19:
                sigungu = pnu[:5]
                bjdong = pnu[5:10]
                bun = pnu[11:15]
                ji = pnu[15:19]

            if bun and bun != "0000":
                parcels.append({
                    "sigunguCd": sigungu,
                    "bjdongCd": bjdong,
                    "bun": bun,
                    "ji": ji,
                    "jibun": attr.get("JIBUN", f"{int(bun)}-{int(ji)}")
                })
        return parcels
    except Exception:
        return []

# ----------------------------------------------------
# 3. 실무형 대장 위계 정제 함수 (총괄 우선)
# ----------------------------------------------------
def clean_and_aggregate_registers(raw_df):
    df = raw_df.copy()

    str_cols = ["mgmBldrgstPk", "bldNm", "platPlc", "regstrKindCd", "regstrKindCdNm", "mainPurpsCdNm", "useAprDay", "ji", "bun"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    if "mgmBldrgstPk" in df.columns:
        df = df.drop_duplicates(subset=["mgmBldrgstPk"])

    df["totArea"] = pd.to_numeric(df.get("totArea", 0), errors="coerce").fillna(0.0)
    df["vlRatEstmTotArea"] = pd.to_numeric(df.get("vlRatEstmTotArea", 0), errors="coerce").fillna(0.0)
    df["platArea"] = pd.to_numeric(df.get("platArea", 0), errors="coerce").fillna(0.0)

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
# 4. 사용자 UI
# ----------------------------------------------------
col_in, col_btn = st.columns([3, 1])
with col_in:
    target_zone_input = st.text_input(
        "정비사업구역 명칭 입력",
        value="성수전략정비구역 제3지구",
        placeholder="예: 불광8, 성수전략정비구역 제3지구, 갈현1, 한남3 등"
    )
with col_btn:
    st.write("")
    st.write("")
    run_btn = st.button("🚀 범용 자동 분석 실행", use_container_width=True)

if run_btn:
    if not final_service_key:
        st.error("좌측 사이드바에 건축HUB 디코딩 인증키를 먼저 입력해 주세요.")
    elif not target_zone_input.strip():
        st.warning("조회할 구역 명칭을 입력해 주세요.")
    else:
        zone_query = target_zone_input.strip()

        # 1. 서울시 GIS 서버에서 구역 검색
        with st.spinner(f"서울시 공간서버에서 '{zone_query}' 구역계 검색 중..."):
            matched_zones = search_seoul_zone(zone_query)

        if not matched_zones:
            st.error(f"서울시 정비구역 레이어에서 '{zone_query}'에 매칭되는 구역을 찾을 수 없습니다. 명칭의 핵심 키워드(예: '성수3', '불광8')로 검색해 보세요.")
        else:
            target_feat = matched_zones[0]
            attrs = target_feat.get("attributes", {})
            geom = target_feat.get("geometry", {})
            rings = geom.get("rings", [])

            zone_nm = attrs.get("DGM_NM", zone_query)
            zone_area = attrs.get("DGM_AR", 0.0)
            sigungu_cd = attrs.get("SIGNGU_SE", "")

            st.success(f"📍 **{zone_nm}** 구역계 확인 완료 (시군구: {sigungu_cd}, 고시면적: {zone_area:,.1f} ㎡)")

            if not rings:
                st.error("구역 경계선 폴리곤 좌표를 불러올 수 없습니다.")
            else:
                ring_coords = rings[0]

                # 2. 구역계 폴리곤으로 지적도 공간 교차 질의 (구역 내 필지 자동 추출)
                with st.spinner(f"'{zone_nm}' 구역계 내부 지적 필지(지번) 전수 공간 역추출 중..."):
                    parcels = fetch_parcels_by_geometry(ring_coords)

                # 고유 지번 중복 정제
                unique_parcels = []
                seen_keys = set()
                for p in parcels:
                    key = f"{p['sigunguCd']}_{p['bjdongCd']}_{p['bun']}_{p['ji']}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        unique_parcels.append(p)

                if not unique_parcels:
                    st.warning("해당 구역계 경계선 내에서 식별된 지적 필지가 없습니다. 구역 위치 및 지적 레이어를 확인해 주세요.")
                else:
                    st.info(f"📋 **{zone_nm}** 구역 내 편입 필지 **총 {len(unique_parcels)}개소** 자동 식별 완료")

                    # 3. 건축HUB 대장 수집
                    clean_key = unquote(final_service_key)
                    base_url = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

                    all_items = []
                    prog = st.progress(0.0)
                    status_txt = st.empty()

                    for idx, p in enumerate(unique_parcels):
                        status_txt.text(f"대장 수집 중 ({idx+1}/{len(unique_parcels)}): {p['sigunguCd']} 본번 {int(p['bun'])}-부번 {int(p['ji'])}")
                        params = {
                            "serviceKey": clean_key,
                            "sigunguCd": p["sigunguCd"],
                            "bjdongCd": p["bjdongCd"],
                            "platGbCd": "0",
                            "bun": p["bun"],
                            "ji": p["ji"],
                            "numOfRows": 50,
                            "pageNo": 1,
                            "_type": "json"
                        }
                        try:
                            r = requests.get(base_url, params=params, timeout=10)
                            res_json = r.json()
                            items = res_json.get("response", {}).get("body", {}).get("items", {}).get("item", [])
                            if isinstance(items, dict):
                                all_items.append(items)
                            elif isinstance(items, list):
                                all_items.extend(items)
                        except Exception:
                            pass

                        prog.progress((idx + 1) / len(unique_parcels))
                        time.sleep(0.02)

                    status_txt.empty()
                    prog.empty()

                    if not all_items:
                        st.warning("수집된 건축물대장이 없습니다.")
                    else:
                        df_raw = pd.DataFrame(all_items)
                        raw_cnt = len(df_raw)

                        # 총괄-동별 위계 정제
                        df = clean_and_aggregate_registers(df_raw)
                        refined_cnt = len(df)

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

                        tot_area_m2 = float(df["totArea"].sum())
                        tot_area_py = tot_area_m2 / 3.3058
                        vl_area_m2 = float(df["vlRatEstmTotArea"].sum())
                        vl_area_py = vl_area_m2 / 3.3058

                        valid_age = df[df["age"].notnull()]
                        old_cnt = int(df["is_old_30"].sum())
                        old_ratio = (old_cnt / len(valid_age) * 100) if len(valid_age) > 0 else 0.0

                        st.markdown("---")
                        st.subheader(f"📊 {zone_nm} 종전연면적 및 노후도 최종 분석표")

                        if raw_cnt != refined_cnt:
                            st.info(f"💡 대장 위계 정제: 원천 대장 {raw_cnt}건 중 총괄-동별 중복 {raw_cnt - refined_cnt}건을 배제하고 실물 {refined_cnt}동 기준으로 산출했습니다.")

                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("실물 건축물 동수", f"{refined_cnt:,} 동")
                        m2.metric("순수 종전연면적", f"{tot_area_m2:,.1f} ㎡", f"{tot_area_py:,.1f} 평")
                        m3.metric("용적률산정용 연면적", f"{vl_area_m2:,.1f} ㎡", f"{vl_area_py:,.1f} 평")
                        m4.metric("30년 이상 노후도 비율", f"{old_ratio:.1f} %", f"{old_cnt}동 / {len(valid_age)}동")

                        t1, t2, t3 = st.tabs(["🔍 지번별 면적 소계 (실무 대조용)", "📋 상세 대장 목록", "🛠️ 대장 채택 감사(Audit)"])

                        with t1:
                            summary = df.groupby("platPlc").agg(
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
                            avail = [c for c in disp_cols if c in df.columns]
                            st.dataframe(df[avail], use_container_width=True)

                        with t3:
                            audit_cols = ["platPlc", "bldNm", "regstrKindCdNm", "totArea", "mgmBldrgstPk"]
                            avail_audit = [c for c in audit_cols if c in df.columns]
                            audit_df = df[avail_audit].copy()
                            for ac in audit_df.columns:
                                audit_df[ac] = audit_df[ac].astype(str)
                            st.dataframe(audit_df, use_container_width=True)

                        st.download_button(
                            label=f"📥 {zone_nm}_최종집계데이터.csv 다운로드",
                            data=df.to_csv(index=False, encoding="utf-8-sig"),
                            file_name=f"{zone_nm}_종전연면적집계.csv",
                            mime="text/csv"
                        )
