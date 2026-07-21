#!/usr/bin/env python3
"""深度采样: 对可用源 (跨番至少成功 1 次), 按 **每部番 × 每条线路** 全覆盖重新解析并用
MPV 长播 28s + 多点截图 (0/3/8/15/25s), 供 subagent 逐张看图判广告 (每条线路都必须有图可判).

用法:
  python3 deep_sample.py <report_dir>
  python3 deep_sample.py <report_dir> --backfill-quick

第二种模式会找出快测曾真实播放成功、但深采二次解析未复现截图的番剧/线路，直接复用快测
保存的视频 URL 补跑 28s；若 URL 已过期，则保留快测当时实际截到的帧作为最低限度视觉证据。
读 <report_dir>/subjects/*/summary.json 找可用源, meta.json 拿番剧/episodeId.
产物: <report_dir>/deep/<tier>-<源>/<番>/<线路>/frames/*.png + deep/<tier>-<源>/deep.json
"""
import json
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import Mcp, load_meta, repo_root, require_mcp_bin

SUBS_ROOT = repo_root() / "subs" / "web"
MAX_CANDIDATES = 100          # 实际上不设限: 每条线路都要深采
SAMPLE_SECONDS = [0, 3, 8, 15, 25]


def find_config(name):
    for f in SUBS_ROOT.glob(f"*/{name}.json"):
        return f
    return None


def usable_sources(report_dir, subject_ids):
    per = {}
    for d in sorted((report_dir / "subjects").glob("*")):
        sj = d / "summary.json"
        if not sj.exists():
            continue
        sid = d.name.split("-", 1)[0]
        for r in json.loads(sj.read_text()):
            if r.get("resolveOk"):
                per.setdefault(r["source"], {"tier": r["tier"], "sids": set()})["sids"].add(sid)
    out = []
    for name, v in per.items():
        sids = [s for s in subject_ids if s in v["sids"]]  # 按 meta 顺序
        out.append((name, v["tier"], sids))
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def _channel_key(value):
    return "None" if value is None else str(value)


def _safe_dir(value):
    return re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip() or "default"


def backfill_quick_success(report_dir, deep_dir, server, log):
    """用快测成功记录补齐深采漏掉的 (番剧, 源, 线路) 截图。

    selector 每次搜索结果可能变化，正常深采重新解析时可能无法复现快测中的线路。快测 source
    trace 保存了当时已实际播放成功的视频 URL、headers 和两张截图；优先对该 URL 重跑 28s，
    URL 失效时把快测截图并入 deep.json，确保真实成功过的线路不会完全缺少视觉证据。
    """
    attempted = added = long_ok = quick_fallback = 0
    for subject_dir in sorted((report_dir / "subjects").glob("*")):
        if not subject_dir.is_dir() or "-" not in subject_dir.name:
            continue
        sid, subject_name = subject_dir.name.split("-", 1)
        summary_file = subject_dir / "summary.json"
        if not summary_file.exists():
            continue
        for row in json.loads(summary_file.read_text()):
            source, tier = row["source"], row["tier"]
            trace_file = subject_dir / "sources" / f"{tier}-{source}.json"
            if not trace_file.exists():
                continue
            trace = json.loads(trace_file.read_text())
            quick_probes = [p for p in (trace.get("playerProbes") or [])
                            if (p.get("probe") or {}).get("ok")]
            if not quick_probes:
                continue

            src_dir = deep_dir / f"{tier}-{source}"
            deep_file = src_dir / "deep.json"
            if deep_file.exists():
                record = json.loads(deep_file.read_text())
            else:
                record = {"source": source, "tier": tier,
                          "testedAt": datetime.now(timezone.utc).isoformat(), "runs": []}
            run = next((r for r in record["runs"] if str(r.get("subjectId")) == sid), None)
            if run is None:
                run = {"subjectId": sid, "subjectName": subject_name, "channels": []}
                record["runs"].append(run)

            resolved_by_media = {
                (r.get("candidate") or {}).get("mediaId"): r.get("resolvedVideo") or {}
                for r in (trace.get("resolve") or {}).get("extractResults", [])
            }
            seen_quick_channels = set()
            for quick in quick_probes:
                channel = quick.get("channel")
                ckey = _channel_key(channel)
                if ckey in seen_quick_channels:
                    continue
                seen_quick_channels.add(ckey)
                if any(_channel_key(c.get("channel")) == ckey and
                       (c.get("probe") or {}).get("capturedFrames")
                       for c in run.get("channels", [])):
                    continue

                attempted += 1
                media_id = quick.get("mediaId")
                rv = resolved_by_media.get(media_id) or {}
                url = quick.get("videoUrl") or rv.get("url")
                fdir = src_dir / subject_name / f"{_safe_dir(ckey)}__quick-backfill" / "frames"
                log(f"[backfill] {source}/{ckey} · {subject_name}")
                probe = server.call("probe_video", {
                    "videoUrl": url, "headers": rv.get("headers") or {},
                    "showWindow": False, "playSeconds": 28, "playTimeoutMillis": 90000,
                    "probeTimeoutMillis": 12000, "detectAds": False,
                    "captureFramesDir": str(fdir), "captureAtSeconds": SAMPLE_SECONDS,
                }, 6 * 60)
                new_frames = probe.get("capturedFrames") or []
                used_quick = False
                if new_frames:
                    long_ok += 1
                else:
                    quick_probe = json.loads(json.dumps(quick.get("probe") or {}))
                    old_frames = [f for f in (quick_probe.get("capturedFrames") or [])
                                  if f.get("path") and pathlib.Path(f["path"]).exists()]
                    if not old_frames:
                        log(f"  !! 无新帧且快测帧已不存在: {source}/{ckey} · {subject_name}")
                        continue
                    quick_probe["capturedFrames"] = old_frames
                    probe = quick_probe
                    used_quick = True
                    quick_fallback += 1

                run.setdefault("channels", []).append({
                    "channel": channel, "mediaId": media_id, "videoUrl": url,
                    "probe": probe, "backfilledFromQuick": True,
                    "usedQuickFrames": used_quick,
                })
                added += 1
                src_dir.mkdir(parents=True, exist_ok=True)
                deep_file.write_text(json.dumps(record, ensure_ascii=False, indent=2))
                log(f"  + 帧={len(probe.get('capturedFrames') or [])} "
                    f"ok={probe.get('ok')} quickFallback={used_quick}")
    log(f"=== 快测补采完成: 尝试 {attempted}, 写入 {added}, 28s 补采有帧 {long_ok}, "
        f"回退快测帧 {quick_fallback}")


def main():
    report_dir = pathlib.Path(sys.argv[1])
    meta = load_meta(report_dir)
    mcp_bin = require_mcp_bin(meta)
    ep_of = {str(s["subjectId"]): (s["episodeId"], s["name"]) for s in meta["subjects"]}
    subject_ids = [str(s["subjectId"]) for s in meta["subjects"]]

    deep_dir = report_dir / "deep"
    deep_dir.mkdir(parents=True, exist_ok=True)
    log_f = open(deep_dir / "deep.log", "a", buffering=1)

    def log(m):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
        print(line, flush=True)
        log_f.write(line + "\n")

    server = Mcp(mcp_bin, log)
    server.start()
    if len(sys.argv) > 2 and sys.argv[2] == "--backfill-quick":
        backfill_quick_success(report_dir, deep_dir, server, log)
        server.stop()
        return
    sources = usable_sources(report_dir, subject_ids)
    log(f"=== 深采 {len(sources)} 个可用源")

    for name, tier, sids in sources:
        src_dir = deep_dir / f"{tier}-{name}"
        if (src_dir / "deep.json").exists():
            log(f"[skip] {tier}-{name}")
            continue
        cfg = find_config(name)
        if not cfg:
            continue
        config = json.loads(cfg.read_text())
        record = {"source": name, "tier": tier, "testedAt": datetime.now(timezone.utc).isoformat(), "runs": []}
        log(f"[{tier}] {name}: {[ep_of[s][1] for s in sids]}")
        for sid in sids:  # 该源解析成功过的每部番都深采
            ep, sub_name = ep_of[sid]
            resolve = server.call("selector_resolve_episode", {
                "subjectId": int(sid), "episodeId": int(ep), "config": config,
                "extractVideo": True, "probeVideo": False, "extractMode": "all_channels",
                "maxCandidatesToExtract": MAX_CANDIDATES, "maxSubjectsPerName": 2,
                "probeTimeoutMillis": 12000,
            }, 20 * 60)
            media_by_id = {m["mediaId"]: m for m in resolve.get("medias", [])}
            resolved = [r for r in resolve.get("extractResults", []) if r.get("resolvedVideo")]
            log(f"  {sub_name}: {len(resolved)} 线路")
            run = {"subjectId": sid, "subjectName": sub_name, "channels": []}
            for r in resolved:  # 每条线路都长播采样, 不截断
                media = media_by_id.get(r["candidate"]["mediaId"], {})
                ch = str(media.get("channel") or r["candidate"]["mediaId"]).replace("/", "_")
                rv = r["resolvedVideo"]
                fdir = src_dir / sub_name / ch / "frames"
                probe = server.call("probe_video", {
                    "videoUrl": rv["url"], "headers": rv.get("headers") or {},
                    "showWindow": False, "playSeconds": 28, "playTimeoutMillis": 90000,
                    "probeTimeoutMillis": 12000, "detectAds": False,
                    "captureFramesDir": str(fdir), "captureAtSeconds": SAMPLE_SECONDS,
                }, 6 * 60)
                pb = (probe.get("mediaAnalysis") or {}).get("playback") or {}
                log(f"    {ch}: ok={probe.get('ok')} 帧={len(probe.get('capturedFrames') or [])} "
                    f"起播={pb.get('timeToPlayingMillis')}ms")
                run["channels"].append({"channel": media.get("channel"),
                                        "mediaId": r["candidate"]["mediaId"],
                                        "videoUrl": rv["url"], "probe": probe})
            record["runs"].append(run)
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "deep.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    server.stop()
    log("=== 深采完成")


if __name__ == "__main__":
    main()
