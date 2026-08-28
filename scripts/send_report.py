"""把当日最新运行的日报通过 SMTP（QQ邮箱）发送到 REPORT_TO。

从 .env 读取 SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/REPORT_TO。
找到 output/<今日>/runs/ 下最新的成功运行（有 daily_digest.html）：
- 成功：正文为摘要，附件为 daily_digest.html + daily_digest.md
- 无成功运行：发送失败通知（附日志尾部）
"""
from __future__ import annotations

import smtplib
import sys
from datetime import date
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def find_today_run() -> tuple[Path | None, list[Path]]:
    day_dir = PROJECT_ROOT / "output" / date.today().isoformat()
    runs_dir = day_dir / "runs"
    candidates: list[Path] = []
    if runs_dir.is_dir():
        candidates = sorted(
            (p for p in runs_dir.iterdir() if p.is_dir() and (p / "run_meta.json").exists()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    for run in candidates:
        if (run / "daily_digest.html").exists():
            return run, candidates
    return None, candidates


def build_summary(run: Path) -> str:
    import json

    meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    intel = meta["intelligence"]
    usage = (meta.get("ai") or {}).get("usage") or {}
    lines = [
        f"运行：{run.name}",
        f"深研事件：{intel['deep_events']} 个 | 质量均分：{intel['quality'].get('average_score', '-')}",
        f"模型调用：{usage.get('calls', '-')} 次 | 错误：{len(intel['errors'])} 条",
        "",
    ]
    digest = (run / "daily_digest.md").read_text(encoding="utf-8")
    if "## 今日速读" in digest:
        body = digest.split("## 今日速读", 1)[1]
        next_h2 = body.find("\n## ")
        excerpt = body[:next_h2].strip() if next_h2 != -1 else body.strip()
        if excerpt:
            lines.append(excerpt[:1500])
    else:
        for block in digest.split("\n## "):
            if block.startswith(("新闻精选", "深度研究结论")):
                for item in block.split("\n### "):
                    head = item.strip().splitlines()
                    if head and head[0][:2].rstrip(".").isdigit():
                        lines.append(f"• {head[0][:80]}")
                break
    if intel["errors"]:
        lines.append("")
        lines.append("错误：")
        lines.extend(f"  - {e[:120]}" for e in intel["errors"][:5])
    return "\n".join(lines)


def send(env: dict[str, str], subject: str, body: str, attachments: list[tuple[str, bytes]]) -> None:
    msg = MIMEMultipart()
    msg["From"] = env["SMTP_USER"]
    msg["To"] = env["REPORT_TO"]
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for name, data in attachments:
        part = MIMEApplication(data)
        part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", name))
        msg.attach(part)
    with smtplib.SMTP_SSL(env["SMTP_HOST"], int(env["SMTP_PORT"]), timeout=60) as server:
        server.login(env["SMTP_USER"], env["SMTP_PASS"])
        server.sendmail(env["SMTP_USER"], [env["REPORT_TO"]], msg.as_string())


def main() -> int:
    env = load_env()
    missing = [k for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REPORT_TO") if not env.get(k)]
    if missing:
        print(f"缺少 .env 配置项: {', '.join(missing)}", file=sys.stderr)
        return 2

    run, candidates = find_today_run()
    if run is None:
        tail = ""
        logs = sorted((PROJECT_ROOT / "logs").glob("harness_scheduled*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            tail = "\n".join(logs[0].read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
        body = f"今日日报生成失败或尚未完成（找到 {len(candidates)} 个运行目录，均无 daily_digest.html）。\n\n日志尾部：\n{tail}"
        send(env, f"【日报失败】{date.today().isoformat()} 科技情报日报未生成", body, [])
        print("已发送失败通知")
        return 0

    summary = build_summary(run)
    attachments = [
        ("daily_digest.html", (run / "daily_digest.html").read_bytes()),
        ("daily_digest.md", (run / "daily_digest.md").read_bytes()),
    ]
    send(env, f"科技产业情报日报 {date.today().isoformat()}", summary, attachments)
    print(f"已发送日报（运行 {run.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
