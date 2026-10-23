"""把当日最新运行的日报通过 SMTP（QQ邮箱）发送到 REPORT_TO。

从 .env 读取 SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/REPORT_TO。
计划任务通过 --run-log 和 --run-exit-code 选择本次实际产物，支持跨日。
手动无参数运行时，找到 output/<今日>/runs/ 下最新的成功运行：
- 成功：正文为摘要，附件为 daily_digest.html
- 无成功运行：发送失败通知（附日志尾部）
"""
from __future__ import annotations

import argparse
import smtplib
import sys
from datetime import date
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import re
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


def _read_log_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def find_today_run() -> tuple[Path | None, list[Path]]:
    day_dir = PROJECT_ROOT / "output" / date.today().isoformat()
    runs_dir = day_dir / "runs"
    candidates: list[Path] = []
    if runs_dir.is_dir():
        candidates = sorted(
            (p for p in runs_dir.iterdir() if p.is_dir() and (p / "daily_digest.html").exists()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    for run in candidates:
        if (run / "daily_digest.html").exists():
            return run, candidates
    return None, candidates


def find_logged_run(log: Path, exit_code: int) -> Path | None:
    """Select this invocation's returned artifact, including across midnight."""
    if exit_code != 0 or not log.is_file():
        return None
    matches = re.findall(r"^\s*html:\s*(.+?)\s*$", _read_log_text(log), re.MULTILINE)
    if not matches:
        return None
    report = Path(matches[-1]).resolve()
    try:
        relative = report.relative_to((PROJECT_ROOT / "output").resolve())
        date.fromisoformat(relative.parts[0])
    except (ValueError, IndexError):
        return None
    if (len(relative.parts) != 4 or relative.parts[1] != "runs"
            or report.name != "daily_digest.html" or not report.is_file()):
        return None
    return report.parent


def build_summary(run: Path) -> str:
    html = (run / "daily_digest.html").read_text(encoding="utf-8")
    lines = [f"运行：{run.name}", ""]
    if "<h2>今日速读</h2>" in html:
        body = html.split("<h2>今日速读</h2>", 1)[1]
        next_h2 = body.find("<h2>")
        excerpt = body[:next_h2] if next_h2 != -1 else body
        text = re.sub(r"<[^>]+>", " ", excerpt)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            lines.append(text[:1500])
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument("--run-exit-code", type=int, default=0)
    parser.add_argument("--report-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)
    env = load_env()
    missing = [k for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REPORT_TO") if not env.get(k)]
    if missing:
        print(f"缺少 .env 配置项: {', '.join(missing)}", file=sys.stderr)
        return 2

    if args.run_log:
        run = find_logged_run(args.run_log, args.run_exit_code)
    else:
        run, _ = find_today_run()
    if run is None:
        tail = ""
        logs = [args.run_log] if args.run_log and args.run_log.is_file() else sorted(
            (PROJECT_ROOT / "logs").glob("agent_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            tail = "\n".join(_read_log_text(logs[0]).splitlines()[-15:])
        body = f"本次日报生成失败或尚未完成（退出码 {args.run_exit_code}，未找到本次成功产物）。\n\n日志尾部：\n{tail}"
        send(env, f"【日报失败】{args.report_date.isoformat()} 科技情报日报未生成", body, [])
        print("已发送失败通知")
        return 1

    summary = build_summary(run)
    attachments = [
        ("daily_digest.html", (run / "daily_digest.html").read_bytes()),
    ]
    report_date = run.parent.parent.name
    send(env, f"科技产业情报日报 {report_date}", summary, attachments)
    print(f"已发送日报（运行 {run.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
