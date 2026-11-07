#!/usr/bin/env python3
"""Daily Dandong weather push to WeChat via ServerChan. Stdlib only.

Reads the SendKey from env SCT_SENDKEY. Without it, prints the composed
message instead of sending (dry run), so the format can be verified safely.
Data: Open-Meteo forecast + air-quality APIs (free, no key).
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

LAT, LON = 40.12917, 124.39472  # Dandong, Liaoning
TZ = ZoneInfo("Asia/Shanghai")

WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "暴雨阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}
WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]
COMPASS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weather-push/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def beaufort(kmh):
    for limit, level in [(1, 0), (6, 1), (12, 2), (20, 3), (29, 4), (39, 5),
                         (50, 6), (62, 7), (75, 8), (89, 9), (103, 10),
                         (118, 11)]:
        if kmh < limit:
            return level
    return 12


def aqi_level(aqi):
    for limit, label in [(50, "优"), (100, "良"), (150, "轻度污染"),
                         (200, "中度污染"), (300, "重度污染")]:
        if aqi <= limit:
            return label
    return "严重污染"


def uv_level(uv):
    for limit, label in [(2, "弱"), (5, "中等"), (7, "强"), (10, "很强")]:
        if uv <= limit:
            return label
    return "极强"


def main():
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")

    fc = fetch(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "weather_code,wind_speed_10m,wind_direction_10m"
        "&hourly=relative_humidity_2m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_sum,precipitation_probability_max,uv_index_max,"
        "wind_speed_10m_max,sunrise,sunset"
        "&timezone=Asia%2FShanghai&forecast_days=4"
    )
    aq = fetch(
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=us_aqi,pm2_5,pm10"
        "&timezone=Asia%2FShanghai&forecast_days=2"
    )

    cur = fc["current"]
    daily = fc["daily"]
    d0 = {k: v[0] for k, v in daily.items()}

    # today's humidity range from hourly values
    hums = [h for t, h in zip(fc["hourly"]["time"],
                              fc["hourly"]["relative_humidity_2m"])
            if t.startswith(today) and h is not None]
    hum_range = f"{min(hums)}% ~ {max(hums)}%" if hums else "—"

    # today's AQI: max value and the latest hour's PM readings
    aq_rows = [(t, a, p25, p10) for t, a, p25, p10 in zip(
        aq["hourly"]["time"], aq["hourly"]["us_aqi"],
        aq["hourly"]["pm2_5"], aq["hourly"]["pm10"])
        if t.startswith(today) and a is not None]
    aqi_max = max(r[1] for r in aq_rows) if aq_rows else None
    latest_aq = aq_rows[-1] if aq_rows else (None, None, None, None)

    wind_dir = COMPASS[int(((cur["wind_direction_10m"] + 22.5) % 360) // 45)]
    title = (f"丹东天气 {now.month}月{now.day}日 "
             f"星期{WEEKDAY[now.weekday()]}")

    lines = [
        f"**{WMO.get(cur['weather_code'], '—')} "
        f"{cur['temperature_2m']:.0f}°C**"
        f"（今日 {d0['temperature_2m_min']:.0f} ~ "
        f"{d0['temperature_2m_max']:.0f}°C），"
        f"体感 {cur['apparent_temperature']:.0f}°C",
        "",
        f"- 💧 湿度：当前 {cur['relative_humidity_2m']}%"
        f"（全天 {hum_range}）",
        f"- 🌬️ 风：{wind_dir}风 {cur['wind_speed_10m']:.0f} km/h"
        f"（{beaufort(cur['wind_speed_10m'])} 级），"
        f"全天最大 {d0['wind_speed_10m_max']:.0f} km/h",
        f"- 🌧️ 降水概率：{d0['precipitation_probability_max'] or 0}%"
        f"，预计降水量 {d0['precipitation_sum'] or 0} mm",
        f"- ☀️ 紫外线：最高指数 {d0['uv_index_max']}"
        f"（{uv_level(d0['uv_index_max'])}）",
    ]
    if aqi_max is not None:
        lines.append(
            f"- 🍃 空气质量：AQI 最高 {aqi_max}"
            f"（{aqi_level(aqi_max)}），"
            f"PM2.5 {latest_aq[2]}、PM10 {latest_aq[3]} μg/m³")
    lines += [
        f"- 🌅 日出 {d0['sunrise'][11:16]} · 日落 {d0['sunset'][11:16]}",
        "",
        "**未来三天**",
    ]
    for i in (1, 2, 3):
        d = datetime.strptime(daily["time"][i], "%Y-%m-%d")
        lines.append(
            f"- {d.month}月{d.day}日 星期{WEEKDAY[d.weekday()]}："
            f"{WMO.get(daily['weather_code'][i], '—')}，"
            f"{daily['temperature_2m_min'][i]:.0f} ~ "
            f"{daily['temperature_2m_max'][i]:.0f}°C，"
            f"降水概率 {daily['precipitation_probability_max'][i] or 0}%")

    tips = []
    if (d0["precipitation_probability_max"] or 0) >= 40 or \
            (d0["precipitation_sum"] or 0) >= 1:
        tips.append("出门带伞")
    if d0["temperature_2m_min"] <= 0:
        tips.append("早晚寒凉，注意保暖")
    if d0["uv_index_max"] >= 6:
        tips.append("紫外线较强，注意防晒")
    if aqi_max is not None and aqi_max > 100:
        tips.append("空气不佳，外出建议戴口罩")
    if d0["wind_speed_10m_max"] >= 39:
        tips.append("风力较大，注意防风")
    if tips:
        lines += ["", "📌 " + "；".join(tips)]

    desp = "\n".join(lines)

    # Card summary: the essentials, visible in the WeChat chat list and
    # notification without opening the message.
    short = (f"{WMO.get(cur['weather_code'], '—')} "
             f"{d0['temperature_2m_min']:.0f}~{d0['temperature_2m_max']:.0f}°C，"
             f"降水{d0['precipitation_probability_max'] or 0}%")
    if aqi_max is not None:
        short += f"，AQI {aqi_max} {aqi_level(aqi_max)}"
    if (daily["precipitation_probability_max"][1] or 0) >= 50:
        short += (f"；明日{WMO.get(daily['weather_code'][1], '')} "
                  f"{daily['temperature_2m_min'][1]:.0f}~"
                  f"{daily['temperature_2m_max'][1]:.0f}°C")

    key = os.environ.get("SCT_SENDKEY", "").strip()
    if not key:
        print("[DRY RUN] no SCT_SENDKEY in env; message would be:\n")
        print("TITLE:", title)
        print("SHORT:", short)
        print(desp)
        return 0

    body = urllib.parse.urlencode(
        {"title": title, "desp": desp, "short": short}).encode()
    req = urllib.request.Request(
        f"https://sctapi.ftqq.com/{key}.send", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    print("ServerChan response:", result)
    if result.get("code") != 0:
        print("push failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
