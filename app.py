import math
import time
from datetime import datetime
from urllib.parse import unquote
import pandas as pd
import requests
from shapely.geometry import Point, Polygon
import streamlit as st

st.set_page_config(page_title="서울시 정비구역 자동 필지 분석기", layout="wide")
st.title("🏙️ 서울시 정비구역 자동 필지 선별 및 대장 분석기")

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
  st.info("서울시 UPIS ArcGIS 공간 서버와 국토부 건축HUB API를 연동합니다.")

final_service_key = service_key_input.strip()

# ----------------------------------------------------
# 2. 서울시 UPIS 정비구역 폴리곤 획득 함수
# ----------------------------------------------------
SEOUL_PROXY_BASE = "https://urban.seoul.go.kr/proxy/proxy.jsp?http://98.33.2.225:6080/arcgis/rest/services/UPIS/20200526_WFS/MapServer/12/query"


def fetch_seoul_zone_polygon(zone_name):
  headers = {
      "Referer": "https://urban.seoul.go.kr/",
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      ),
  }
  params = {
      "f": "json",
      "where": f"DGM_NM LIKE '%{zone_name}%'",
      "outFields": "OBJECTID,DGM_NM,DGM_AR,SIGNGU_SE,SHAPE.AREA",
      "outSR": "5174",
      "returnGeometry": "true",
  }

  try:
    res = requests.get(
        SEOUL_PROXY_BASE, params=params, headers=headers, timeout=10
    )
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
  except Exception as e:
    st.error(f"서울시 GIS 서버 연동 오류: {e}")
    return None, None


# 불광8구역 백업 폴리곤 좌표 (서버 프록시 세션 만료 시 대체용)
FALLBACK_BULGWANG8 = [
    [193985.07, 456530.87],
    [193987.77, 456520.22],
    [193988.80, 456518.58],
    [193994.36, 456509.67],
    [193988.63, 456505.27],
    [193988.67, 456503.16],
    [193990.05, 456500.08],
    [193991.90, 456488.85],
    [193991.93, 456488.70],
    [193993.33, 456481.22],
    [193994.74, 456481.29],
    [194000.00, 456480.62],
    [194003.27, 456480.20],
    [194003.33, 456478.26],
    [194000.00, 456474.56],
    [193995.13, 456469.15],
    [193986.48, 456467.94],
    [193977.95, 456466.48],
    [193965.43, 456464.67],
    [193964.39, 456464.40],
    [193947.27, 456461.74],
    [193943.64, 456461.09],
    [193931.45, 456459.58],
    [193916.86, 456456.53],
    [193914.75, 456465.25],
    [193913.37, 456472.18],
    [193912.88, 456474.67],
    [193910.08, 456490.73],
    [193908.94, 456499.09],
    [193908.79, 456506.24],
    [193908.40, 456522.81],
    [193908.35, 456535.51],
    [193908.73, 456555.66],
    [193908.88, 456563.73],
    [193908.43, 456570.28],
    [193908.56, 456571.46],
    [193909.64, 456581.94],
    [193910.90, 456590.53],
    [193913.07, 456601.01],
    [193914.66, 456608.35],
    [193916.04, 456620.49],
    [193916.67, 456626.51],
    [193918.14, 456627.51],
    [193922.42, 456627.08],
    [193926.57, 45662
