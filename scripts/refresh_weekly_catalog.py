from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from daily_intel.intelligence.sources.weekly_catalog import (  # noqa: E402
    build_blog_feed_configs,
    parse_weekly_docs,
    write_blog_feed_pool,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 ruanyf/weekly 提取外链并探测 RSS，写入泛读域名池")
    parser.add_argument("--repo-dir", type=Path, default=ROOT / "data" / "raw" / "ruanyf-weekly")
    parser.add_argument("--pool-file", type=Path, default=ROOT / "data" / "cache" / "weekly_blog_feeds.json")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--skip-git", action="store_true")
    args = parser.parse_args()
    repo_dir: Path = args.repo_dir
    if not args.skip_git:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if (repo_dir / ".git").exists():
            subprocess.run(["git", "-C", str(repo_dir), "pull", "--ff-only"], check=False)
        else:
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/ruanyf/weekly.git", str(repo_dir)],
                check=False,
            )
    docs_dir = repo_dir / "docs"
    rows = parse_weekly_docs(docs_dir)
    feeds = build_blog_feed_configs(rows, args.limit)
    write_blog_feed_pool(args.pool_file, feeds)
    print(f"parsed_links={len(rows)} rss_feeds={len(feeds)} path={args.pool_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
