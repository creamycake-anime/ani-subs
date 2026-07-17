#!/usr/bin/env python3
"""深度采样: 对可用源 (跨番至少成功 1 次), 按 **每部番 × 每条线路** 全覆盖重新解析并用
VLC 长播 28s + 多点截图 (0/3/8/15/25s), 供 subagent 逐张看图判广告 (每条线路都必须有图可判).

用法: python3 deep_sample.py <report_dir>
读 <report_dir>/subjects/*/summary.json 找可用源, meta.json 拿番剧/episodeId.
产物: <report_dir>/deep/<tier>-<源>/<番>/<线路>/frames/*.png + deep/<tier>-<源>/deep.json
"""
import json
import pathlib
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
                    "probeTimeoutMillis": 12000, "detectAds": True,
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
