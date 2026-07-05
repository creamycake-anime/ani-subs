"""共享库: MCP stdio 客户端 + animeko MCP 二进制发现 + 报告目录约定.

被 run_eval.py / deep_sample.py / gen_report.py 复用.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

# 报告目录布局: <report_dir>/{meta.json, subjects/, deep/, montage/, combined/, sources/, channels/, README.md}


def repo_root():
    """ani-subs 仓库根 (脚本在 .claude/skills/datasource-eval/scripts/ 下)."""
    return pathlib.Path(__file__).resolve().parents[4]


def find_mcp_bin():
    """定位 animeko 的 datasource-test-mcp 可执行文件.

    顺序: 环境变量 ANIMEKO_MCP_BIN > sibling ../ani > 常见位置. 找不到返回 None.
    """
    env = os.environ.get("ANIMEKO_MCP_BIN")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    rel = "tools/datasource-test-mcp/build/install/datasource-test-mcp/bin/datasource-test-mcp"
    candidates = [
        repo_root().parent / "ani" / rel,          # sibling animeko/ani
        repo_root().parent / "animeko" / rel,
        pathlib.Path.home() / "Projects/animeko/ani" / rel,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def require_mcp_bin(meta=None):
    if meta and meta.get("mcpBin") and pathlib.Path(meta["mcpBin"]).exists():
        return pathlib.Path(meta["mcpBin"])
    b = find_mcp_bin()
    if not b:
        sys.exit(
            "找不到 animeko datasource-test-mcp 可执行文件.\n"
            "  期望在 sibling 目录 ../ani/tools/datasource-test-mcp/build/install/...\n"
            "  请先在 animeko/ani 仓库构建: ./gradlew :tools:datasource-test-mcp:installDist\n"
            "  或设置环境变量 ANIMEKO_MCP_BIN 指向该可执行文件, 或在 meta.json 里写 mcpBin."
        )
    return b


def load_meta(report_dir):
    return json.loads((pathlib.Path(report_dir) / "meta.json").read_text())


class Mcp:
    """极简 stdio MCP 客户端 (换行分隔 JSON-RPC). 单例串行调用, 崩溃自动重启."""

    def __init__(self, bin_path, log=None):
        self.bin = str(bin_path)
        self.proc = None
        self.next_id = 0
        self.log = log or (lambda m: None)

    def start(self):
        self.stop()
        self.proc = subprocess.Popen(
            [self.bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.next_id = 0
        self._send({"jsonrpc": "2.0", "id": self._id(), "method": "initialize"})
        self._read_until(self.next_id, time.time() + 60)

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def _id(self):
        self.next_id += 1
        return self.next_id

    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self.proc.stdin.flush()

    def _read_until(self, want_id, deadline):
        import select
        buf = b""
        fd = self.proc.stdout.fileno()
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"MCP timeout waiting id={want_id}")
            ready, _, _ = select.select([fd], [], [], 5)
            if not ready:
                if self.proc.poll() is not None:
                    raise RuntimeError("MCP server died")
                continue
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                raise RuntimeError("MCP server closed stdout")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == want_id:
                    return msg

    def call(self, tool, args, timeout_s=300):
        if self.proc is None or self.proc.poll() is not None:
            self.start()
        cid = self._id()
        self._send({"jsonrpc": "2.0", "id": cid, "method": "tools/call",
                    "params": {"name": tool, "arguments": args}})
        try:
            msg = self._read_until(cid, time.time() + timeout_s)
        except (TimeoutError, RuntimeError) as e:
            self.log(f"  !! {tool}: {e}; 重启 MCP")
            self.start()
            return {"_error": str(e)}
        sc = (msg.get("result") or {}).get("structuredContent")
        return sc if sc is not None else {"_error": json.dumps(msg.get("error") or msg)[:300]}
