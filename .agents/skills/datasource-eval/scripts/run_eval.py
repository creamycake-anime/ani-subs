#!/usr/bin/env python3
"""批量评测: 对 subs/web 下全部 CSS selector 数据源, 跨 meta.json 指定的番剧,
跑全流程解析 (all_channels) + **每条 resolved 线路**全量 VLC 实播 (采集分辨率/码率/起播).

用法: python3 run_eval.py <report_dir> [只跑指定源名...]
读 <report_dir>/meta.json: {evalDate, subjects:[{subjectId, episodeId, name}], mcpBin?}
产物: <report_dir>/subjects/<subjectId>-<name>/{sources/*.json, frames/, summary.json, driver.log}

断点续跑安全: 已有 summary 行的源会跳过.
"""
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import Mcp, load_meta, repo_root, require_mcp_bin

SUBS_ROOT = repo_root() / "subs" / "web"
MAX_CANDIDATES = 100  # 实际上不设限: 每条线路都要解析并实播


def collect_sources(only):
    out = []
    for tier_dir in sorted(SUBS_ROOT.iterdir()):
        if tier_dir.is_dir():
            for f in sorted(tier_dir.glob("*.json")):
                if not only or f.stem in only:
                    out.append((tier_dir.name, f))
    return out


def stage_timings(resolve):
    agg = {}
    for s in resolve.get("steps", []):
        if s.get("durationMillis") is not None:
            agg[s["name"]] = agg.get(s["name"], 0) + s["durationMillis"]
    return agg


def main():
    report_dir = pathlib.Path(sys.argv[1])
    only = set(sys.argv[2:])
    meta = load_meta(report_dir)
    mcp_bin = require_mcp_bin(meta)
    subjects = meta["subjects"]

    for sub in subjects:
        sid, ep, name = sub["subjectId"], sub["episodeId"], sub["name"]
        rd = report_dir / "subjects" / f"{sid}-{name}"
        rd.joinpath("sources").mkdir(parents=True, exist_ok=True)
        log_f = open(rd / "driver.log", "a", buffering=1)

        def log(m):
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
            print(line, flush=True)
            log_f.write(line + "\n")

        server = Mcp(mcp_bin, log)
        server.start()
        summary_path = rd / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else []
        done = {r["source"] for r in summary}

        sources = collect_sources(only)
        log(f"=== [{name}] {len(sources)} 源, 已完成 {len(done)}")
        for tier, f in sources:
            src = f.stem
            if src in done:
                continue
            log(f"[{tier}] {src} ...")
            config = json.loads(f.read_text())
            record = {"source": src, "tier": tier, "file": str(f.relative_to(repo_root())),
                      "testedAt": datetime.now(timezone.utc).isoformat(),
                      "subjectId": sid, "episodeId": ep}
            record["validate"] = server.call("validate_selector_config", {"config": config}, 120)
            resolve = server.call("selector_resolve_episode", {
                "subjectId": sid, "episodeId": ep, "config": config,
                "extractVideo": True, "probeVideo": True, "extractMode": "all_channels",
                "maxCandidatesToExtract": MAX_CANDIDATES, "maxSubjectsPerName": 2,
                "probeTimeoutMillis": 12000,
            }, 20 * 60)
            record["resolve"] = resolve
            log(f"  resolve ok={resolve.get('ok')} | {str(resolve.get('summary') or resolve.get('_error'))[:90]}")

            media_by_id = {m["mediaId"]: m for m in resolve.get("medias", [])}
            probes = []
            resolved = [r for r in resolve.get("extractResults", []) if r.get("resolvedVideo")]
            for idx, r in enumerate(resolved):  # 全部线路都实播, 不截断
                media = media_by_id.get(r["candidate"]["mediaId"], {})
                ch = str(media.get("channel") or f"ch{idx}").replace("/", "_")
                fdir = report_dir / "subjects" / f"{sid}-{name}" / "frames" / f"{tier}-{src}" / ch
                rv = r["resolvedVideo"]
                probe = server.call("probe_video", {
                    "videoUrl": rv["url"], "headers": rv.get("headers") or {},
                    "showWindow": False, "playSeconds": 4, "probeTimeoutMillis": 12000,
                    "detectAds": True, "captureFramesDir": str(fdir),
                }, 4 * 60)
                probes.append({"mediaId": r["candidate"]["mediaId"], "channel": media.get("channel"),
                               "videoUrl": rv["url"], "probe": probe})
            record["playerProbes"] = probes
            (rd / "sources" / f"{tier}-{src}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))

            # 汇总行
            er = resolve.get("extractResults", [])
            ok_ch = [media_by_id.get(r["candidate"]["mediaId"], {}).get("channel") or r["candidate"]["mediaId"]
                     for r in er if r.get("resolvedVideo")]
            fail_ch = [media_by_id.get(r["candidate"]["mediaId"], {}).get("channel") or r["candidate"]["mediaId"]
                       for r in er if not r.get("resolvedVideo")]
            per_channel = []
            for p in probes:
                ma = p["probe"].get("mediaAnalysis") or {}
                v = ma.get("video") or {}
                pb = ma.get("playback") or {}
                per_channel.append({
                    "channel": p["channel"], "playerOk": p["probe"].get("ok"),
                    "resolution": f"{v.get('width')}x{v.get('height')}" if v.get("width") else None,
                    "bitrate": ma.get("overallBitrate") or v.get("bitrate"),
                    "adSuspicion": (p["probe"].get("adAnalysis") or {}).get("suspicion"),
                    "timeToPlayingMillis": pb.get("timeToPlayingMillis"),
                })
            # 行级快照 (首条可播线路), 仅供人肉翻 summary.json; 报告总表的"最佳线路"由 gen_report 按
            # 无广告>分辨率>码率>起播 从 perChannel 重新算, 不用这几个字段.
            best = next((p for p in probes if p["probe"].get("ok")), probes[0] if probes else None)
            bma = ((best or {}).get("probe", {}) or {}).get("mediaAnalysis") or {}
            bv = bma.get("video") or {}
            bpb = bma.get("playback") or {}
            summary.append({
                "source": src, "tier": tier,
                "configValid": (record["validate"] or {}).get("ok"),
                "resolveOk": resolve.get("ok"),
                "resolveSummary": resolve.get("summary") or resolve.get("_error"),
                "mediasFound": len(resolve.get("medias", [])),
                "channelsResolved": ok_ch, "channelsFailed": fail_ch,
                "stageTimings": stage_timings(resolve),
                "totalResolveMillis": resolve.get("totalDurationMillis"),
                "resolution": f"{bv.get('width')}x{bv.get('height')}" if bv.get("width") else None,
                "bitrate": bma.get("overallBitrate") or bv.get("bitrate"),
                "timeToPlayingMillis": bpb.get("timeToPlayingMillis"),
                "perChannel": per_channel,
            })
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            done.add(src)
        server.stop()
        log(f"=== [{name}] 完成: {len(summary)} 源")


if __name__ == "__main__":
    main()
