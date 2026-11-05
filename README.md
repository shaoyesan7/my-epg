# 我的 EPG（多源合成版）

把几个每天更新的免费 EPG 源按优先级合并成一个 XMLTV 文件：
zsdc（过去7天+未来约2天）+ kuke31/erw（过去7天、频道多）+ plsy1（运营商数据）+ 112114（当天、频道多）。
按频道名对齐、去重，只保留「过去7天（含当天）＋未来3天」窗口内的节目。

## 现在就能用（今天已生成好的文件）

- `epg.xml.gz`（约 2MB，推荐）或 `epg.xml`
- 2026-11-05 实测：930 个频道、180,439 条节目，日期 2026-10-30 → 2026-11-07
  - 过去 7 天：完整
  - 未来：11-06 完整，11-07 目前只有凌晨部分，11-08 暂无
    （免费源里暂时没有一家公开发布国内频道未来第 3 天的完整节目单，
     随着每天滚动更新，远端那天会逐渐补齐）

把文件放到任何能直链下载的地方（自己的服务器 / NAS / GitHub 仓库），
然后在播放器或 TVBox 直播配置里把 EPG 地址填成那个链接即可，例如：

```json
{"name":"直播","type":0,"url":"你的直播源地址","epg":"https://你的地址/epg.xml.gz","logo":"https://epg.112114.xyz/logo/{name}.png"}
```

注意：频道名要对得上。这个 EPG 的频道 ID 用原名，如 `CCTV1`、`湖南卫视`（没有横杠）；
直播源里的 tvg-id / 频道名要改成一致，否则节目单匹配不上。
另外 EPG 只提供节目单，能否真正回看还取决于直播源本身是否支持回放/时移。

## 让它每天自动更新（GitHub 免费方案）

1. 在 GitHub 新建一个仓库（比如就叫 `epg`），把本文件夹里的
   `build_epg.py` 和 `.github/workflows/epg.yml` 传上去
2. 仓库 Settings → Actions → General → Workflow permissions 选
   "Read and write permissions"
3. 完事。之后每天北京时间 06:30、18:30 自动重新生成并提交，
   你的 EPG 地址就是（把用户名/仓库名换成你的）：
   `https://raw.githubusercontent.com/<用户名>/<仓库>/main/epg.xml.gz`

## 本地手动生成

```bash
python3 build_epg.py epg.xml   # 会同时产出 epg.xml.gz 和 stats.json
```

`stats.json` 里有本次生成的频道数、节目数和实际日期范围，方便检查。
