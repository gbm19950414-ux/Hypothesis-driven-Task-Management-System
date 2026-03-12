#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visualize_targets.py

作用：
- 读取 yaml/target.yaml（若不存在则尝试 yaml/targets.yaml）
- 解析单位数据库
- 输出可视化结果到 outputs/
    - targets_dashboard.png
    - targets_table.csv
    - targets_summary.txt

依赖：
    pip install pyyaml pandas matplotlib

用法：
    python visualize_targets.py
    python visualize_targets.py --root /path/to/project
    python visualize_targets.py --input yaml/targets.yaml --out outputs
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

import yaml
import pandas as pd
import matplotlib.pyplot as plt


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层必须是字典: {path}")
    return data


def pick_input(root: Path, explicit: str | None) -> Path:
    if explicit:
        p = (root / explicit).resolve()
        if not p.exists():
            raise FileNotFoundError(f"未找到输入文件: {p}")
        return p

    candidates = [
        root / "yaml" / "target.yaml",
        root / "yaml" / "targets.yaml",
        root / "career" / "targets" / "targets.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p.resolve()
    raise FileNotFoundError(
        "未找到目标 YAML。请确保存在以下之一：\n"
        "  yaml/target.yaml\n"
        "  yaml/targets.yaml\n"
        "  career/targets/targets.yaml"
    )


def normalize_units(data: Dict[str, Any]) -> pd.DataFrame:
    units = data.get("units", [])
    if not isinstance(units, list):
        raise ValueError("YAML 中的 `units` 必须是列表")

    rows: List[Dict[str, Any]] = []
    for u in units:
        if not isinstance(u, dict):
            continue

        score = u.get("score") or u.get("scores") or {}
        if not isinstance(score, dict):
            score = {}

        rows.append({
            "name": u.get("name", ""),
            "type": u.get("type", ""),
            "city": u.get("city", ""),
            "track": u.get("track", ""),
            "role_type": u.get("role_type", ""),
            "leader": u.get("leader", ""),
            "research_match": score.get("research_match") or score.get("research_direction_match"),
            "tech_match": score.get("tech_match") or score.get("tech_platform_match"),
            "stability": score.get("stability"),
            "growth": score.get("growth"),
            "autonomy": score.get("autonomy") or score.get("autonomy_space"),
            "evidence": u.get("evidence") or u.get("evidence_source", ""),
            "conclusion": u.get("conclusion") or u.get("preliminary_conclusion", ""),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("没有解析到任何单位数据（units 为空）")

    numeric_cols = ["research_match", "tech_match", "stability", "growth", "autonomy"]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["total_score"] = df[numeric_cols].sum(axis=1, min_count=1)
    df["avg_score"] = df[numeric_cols].mean(axis=1)
    return df


def build_dashboard(df: pd.DataFrame, output_png: Path) -> None:
    plt.rcParams["font.sans-serif"] = [
        "Arial Unicode MS", "PingFang SC", "Microsoft YaHei",
        "Heiti SC", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(16, 10))
    gs = fig.add_gridspec(2, 2, hspace=0.28, wspace=0.18)

    ax1 = fig.add_subplot(gs[0, 0])
    type_counts = df["type"].fillna("未知").replace("", "未知").value_counts()
    type_counts.plot(kind="bar", ax=ax1)
    ax1.set_title("单位类型分布")
    ax1.set_xlabel("")
    ax1.set_ylabel("数量")

    ax2 = fig.add_subplot(gs[0, 1])
    city_counts = df["city"].fillna("未知").replace("", "未知").value_counts().head(12)
    city_counts.plot(kind="bar", ax=ax2)
    ax2.set_title("城市分布（Top 12）")
    ax2.set_xlabel("")
    ax2.set_ylabel("数量")

    ax3 = fig.add_subplot(gs[1, 0])
    conclusion_counts = df["conclusion"].fillna("未标记").replace("", "未标记").value_counts()
    conclusion_counts.plot(kind="bar", ax=ax3)
    ax3.set_title("初步结论分布")
    ax3.set_xlabel("")
    ax3.set_ylabel("数量")

    ax4 = fig.add_subplot(gs[1, 1])
    type_codes = {t: i for i, t in enumerate(sorted(df["type"].fillna("未知").unique()))}
    colors = df["type"].fillna("未知").map(type_codes)

    x = df["stability"]
    y = df["growth"]
    sizes = df["research_match"].fillna(1) * 60
    ax4.scatter(x, y, s=sizes, c=colors)
    ax4.set_title("稳定性 vs 成长性（点越大=研究方向匹配度越高）")
    ax4.set_xlabel("稳定性")
    ax4.set_ylabel("成长性")
    ax4.set_xlim(1.8, 5.2)
    ax4.set_ylim(1.8, 5.2)
    ax4.grid(True, alpha=0.3)

    top10 = df.sort_values(["avg_score", "total_score"], ascending=False).head(10)
    for _, row in top10.iterrows():
        if pd.notna(row["stability"]) and pd.notna(row["growth"]):
            ax4.annotate(str(row["name"]), (row["stability"], row["growth"]), fontsize=8, alpha=0.85)

    fig.suptitle("目标单位可视化概览", fontsize=16)
    fig.tight_layout()
    fig.savefig(output_png, dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_summary(df: pd.DataFrame, out_txt: Path) -> None:
    top10 = df.sort_values(["avg_score", "total_score"], ascending=False).head(10)
    lines = []
    lines.append("目标单位汇总")
    lines.append("=" * 40)
    lines.append(f"总单位数: {len(df)}")
    lines.append("")
    lines.append("按类型统计：")
    for k, v in df["type"].fillna("未知").replace("", "未知").value_counts().items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("按城市统计（Top 10）：")
    for k, v in df["city"].fillna("未知").replace("", "未知").value_counts().head(10).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Top 10（按平均分）：")
    for i, (_, r) in enumerate(top10.iterrows(), start=1):
        lines.append(
            f"{i}. {r['name']} | {r['type']} | {r['city']} | "
            f"avg={r['avg_score']:.2f} total={r['total_score']:.1f} | {r['conclusion']}"
        )
    out_txt.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="可视化 targets.yaml")
    parser.add_argument("--root", default=".", help="项目根目录，默认当前目录")
    parser.add_argument("--input", default=None, help="输入 YAML 相对路径，可省略")
    parser.add_argument("--out", default="outputs", help="输出目录，相对 root")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    in_path = pick_input(root, args.input)
    out_dir = (root / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_yaml(in_path)
    df = normalize_units(data)

    csv_path = out_dir / "targets_table.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    txt_path = out_dir / "targets_summary.txt"
    write_summary(df, txt_path)

    png_path = out_dir / "targets_dashboard.png"
    build_dashboard(df, png_path)

    print(f"已读取: {in_path}")
    print(f"已输出 CSV: {csv_path}")
    print(f"已输出摘要: {txt_path}")
    print(f"已输出图像: {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
