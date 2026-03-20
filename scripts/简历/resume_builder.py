#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resume_builder_standalone.py

用途：
  仅根据 `简历.yaml` 直接生成中文学术简历 HTML / PDF，不依赖模版.yaml。
"""

from __future__ import annotations

import argparse
import base64
import html
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:
    print("缺少依赖 PyYAML，请先安装：pip install pyyaml", file=sys.stderr)
    raise


def read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层必须是字典：{path}")
    return data


def esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v), quote=True)


def exists(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) > 0
    return True


def mm(x: Any) -> str:
    return f"{float(x):.2f}mm"


def cm_to_mm(x: Any) -> str:
    return f"{float(x) * 10:.2f}mm"


def get_path(data: Dict[str, Any], dotted: str, default: Any = "") -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def data_uri(path: Path) -> Optional[str]:
    if not path or not path.exists():
        return None
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if not mime:
        return None
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def ensure_list_of_strings(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if exists(x)]
    if isinstance(v, str):
        text = v.strip()
        if not text:
            return []
        lines = [line.strip(" -\t") for line in text.splitlines()]
        return [line for line in lines if line]
    return [str(v)]


def normalize_resume(raw: Dict[str, Any]) -> Dict[str, Any]:
    src = raw.get("resume", raw)
    if not isinstance(src, dict):
        raise ValueError("resume 节点必须是字典")

    basic = src.get("基础信息", {}) if isinstance(src.get("基础信息"), dict) else {}
    education = src.get("教育经历", []) if isinstance(src.get("教育经历"), list) else []
    research = src.get("科研经历", []) if isinstance(src.get("科研经历"), list) else []

    publications = src.get("论文发表")
    if publications is None:
        publications = src.get("科研成果", [])
    if not isinstance(publications, list):
        publications = []

    skills_raw = src.get("技能", {}) if isinstance(src.get("技能"), dict) else {}
    skills = {
        "实验技能": ensure_list_of_strings(skills_raw.get("实验技能")),
        "计算技能": ensure_list_of_strings(skills_raw.get("计算技能")),
        "组织管理": ensure_list_of_strings(skills_raw.get("组织管理")),
    }

    return {
        "基础信息": {
            "name": basic.get("name", ""),
            "email": basic.get("email", ""),
            "phone": basic.get("phone", ""),
            "github": basic.get("github", ""),
            "selected_projects": basic.get("Selected Projects", []),
            "photo": basic.get("photo", ""),
        },
        "教育经历": education,
        "科研经历": research,
        "论文发表": publications,
        "技能": skills,
    }


def validate_resume(resume: Dict[str, Any], photo_path: Optional[Path]) -> List[str]:
    warnings: List[str] = []
    if not exists(get_path(resume, "基础信息.name")):
        warnings.append("基础信息.name 为空")
    if not exists(get_path(resume, "基础信息.email")):
        warnings.append("基础信息.email 为空")
    if not exists(resume.get("教育经历")):
        warnings.append("教育经历 为空")
    if not exists(resume.get("科研经历")):
        warnings.append("科研经历 为空")
    if not exists(resume.get("技能")):
        warnings.append("技能 为空")
    if photo_path is not None and not photo_path.exists():
        warnings.append(f"照片不存在：{photo_path}")
    return warnings


def render_header(resume: Dict[str, Any], photo_uri: Optional[str]) -> str:
    basic = resume.get("基础信息", {})
    name = esc(basic.get("name", ""))
    email = basic.get("email", "")
    phone = basic.get("phone", "")
    github = basic.get("github", "")
    selected_projects = ensure_list_of_strings(basic.get("selected_projects", []))
    selected_projects_text = " | ".join(esc(x) for x in selected_projects)

    photo_html = ""
    if photo_uri:
        photo_html = (
            "<div class='header-photo-wrap'>"
            f"<img class='header-photo' src='{photo_uri}' alt='photo'>"
            "</div>"
        )

    lines: List[str] = []
    if exists(email):
        lines.append(f"<div class='header-contact-line nowrap'><span class='header-contact-label'>邮箱：</span>{esc(email)}</div>")
    if exists(phone):
        lines.append(f"<div class='header-contact-subline nowrap'><span class='header-contact-label'>电话：</span>{esc(phone)}</div>")
    if exists(github):
        lines.append(f"<div class='header-contact-subline'><span class='header-contact-label'>GitHub：</span>{esc(github)}</div>")
    if selected_projects:
        lines.append(
            f"<div class='header-contact-subline'><span class='header-contact-label'>Selected Projects：</span>{selected_projects_text}</div>"
        )

    return (
        "<header class='resume-header'>"
        f"{photo_html}"
        "<div class='header-info-block'>"
        f"<div class='header-name'>{name}</div>"
        f"{''.join(lines)}"
        "</div>"
        "</header>"
    )


def render_section_title(title: str) -> str:
    return f"<div class='section-title'>{esc(title)}</div><div class='section-title-rule'></div>"


def render_education(resume: Dict[str, Any]) -> str:
    items = resume.get("教育经历", [])[:2]
    out: List[str] = []
    for edu in items:
        institution = esc(edu.get("institution", ""))
        degree = esc(edu.get("degree", ""))
        field = esc(edu.get("field", ""))
        start = esc(edu.get("start_year", ""))
        end = esc(edu.get("end_year", ""))
        advisor = esc(edu.get("advisor", ""))

        left = " · ".join([x for x in [institution, degree, field] if x])
        right = "–".join([x for x in [start, end] if x])
        meta = f"导师：{advisor}" if advisor else ""

        out.append(
            "<div class='entry edu-entry'>"
            f"<div class='entry-head'><div class='entry-title'>{left}</div><div class='entry-date'>{right}</div></div>"
            f"<div class='entry-meta'>{meta}</div>"
            "</div>"
        )
    return "".join(out)


def render_research(resume: Dict[str, Any]) -> str:
    items = resume.get("科研经历", [])[:2]
    out: List[str] = []
    for item in items:
        title = esc(item.get("参与项目", ""))
        work = esc(item.get("工作内容", ""))
        details = ensure_list_of_strings(item.get("具体工作"))[:4]
        achievements = ensure_list_of_strings(item.get("主要成就"))

        block: List[str] = ["<div class='entry project-entry'>"]
        if title:
            block.append(f"<div class='project-title'>{title}</div>")
        if work:
            block.append(f"<div class='project-desc'>{work}</div>")
        if details:
            joined_details = "；".join(esc(x) for x in details)
            block.append(f"<div class='detail-line'><span class='inline-label'>具体工作：</span>{joined_details}</div>")
        if achievements:
            joined = "；".join(esc(x) for x in achievements)
            block.append(f"<div class='achievement-line'><span class='inline-label'>主要成就：</span>{joined}</div>")
        block.append("</div>")
        out.append("".join(block))
    return "".join(out)


def render_publications(resume: Dict[str, Any]) -> str:
    items = resume.get("论文发表", [])[:5]
    out: List[str] = []
    for idx, pub in enumerate(items, start=1):
        authors = esc(pub.get("authors", ""))
        title = esc(pub.get("title", ""))
        journal = esc(pub.get("journal", ""))
        volume = esc(pub.get("volume", ""))
        issue = esc(pub.get("issue", ""))
        pages = esc(pub.get("pages", ""))
        year = esc(pub.get("year", ""))
        doi = esc(pub.get("doi", ""))
        impact_factor = esc(pub.get("IF", ""))
        authorship = esc(pub.get("authorship", ""))
        status = esc(pub.get("status", ""))

        authors_txt = authors if authors else "[Authors]"
        title_txt = title if title else "[Title]"
        journal_txt = journal if journal else "[Journal]"
        volume_txt = volume if volume else "[Volume]"
        pages_txt = pages if pages else "[Pages]"
        year_txt = year if year else "[Year]"

        issue_txt = f"({issue})" if issue else ""
        main_citation = (
            f"<span class='pub-index'>{idx}.</span> "
            f"{authors_txt}. {title_txt}. "
            f"<span class='pub-journal'>{journal_txt}</span> "
            f"<span class='pub-volume'>{volume_txt}</span>{issue_txt}, {pages_txt} ({year_txt})."
        )

        meta_parts = []
        if authorship:
            meta_parts.append(f"authorship: {authorship}")
        if doi:
            meta_parts.append(f"doi: {doi}")
        if impact_factor:
            meta_parts.append(f"IF: {impact_factor}")
        if status:
            meta_parts.append(f"status: {status}")
        # meta_html = f"<div class='pub-meta'>｜'.join(meta_parts)</div>" if meta_parts else ""
        meta_html = f"<div class='pub-meta'>{' ｜ '.join(meta_parts)}</div>" if meta_parts else ""

        out.append(
            "<div class='entry pub-entry'>"
            f"<div class='pub-citation'>{main_citation}</div>"
            f"{meta_html}"
            "</div>"
        )
    return "".join(out)


def render_skills(resume: Dict[str, Any]) -> str:
    groups = ["实验技能", "计算技能", "组织管理"]
    blocks: List[str] = []
    for g in groups:
        items = ensure_list_of_strings(get_path(resume, f"技能.{g}", []))[:8]
        if not items:
            continue
        joined = "；".join(esc(x) for x in items)
        blocks.append(
            "<div class='entry skill-entry'>"
            f"<div class='skill-line'><span class='inline-label'>{esc(g)}：</span>{joined}</div>"
            "</div>"
        )
    return "".join(blocks)


def render_section(name: str, resume: Dict[str, Any]) -> str:
    if name == "教育经历":
        body = render_education(resume)
    elif name == "科研经历":
        body = render_research(resume)
    elif name == "论文发表":
        body = render_publications(resume)
    elif name == "技能":
        body = render_skills(resume)
    else:
        body = ""

    if not exists(body):
        return ""
    return f"<section class='resume-section'>{render_section_title(name)}{body}</section>"


def build_css() -> str:
    return f"""
@page {{
  size: A4 portrait;
  margin: {cm_to_mm(0.8)} {cm_to_mm(1.0)} {cm_to_mm(0.8)} {cm_to_mm(1.0)};
}}

* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  color: #1F1F1F;
  background: #FFFFFF;
  font-family: "Noto Serif CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 9.4pt;
  line-height: 1.26;
}}
.page {{ width: 100%; }}
.resume {{ width: 100%; }}

.resume-header {{
  display: grid;
  grid-template-columns: 30.00mm 1fr;
  column-gap: 6.20mm;
  align-items: center;
  min-height: 40.00mm;
  margin-bottom: 2.80mm;
}}
.header-photo-wrap {{ width: 30.00mm; }}
.header-photo {{
  width: 30.00mm;
  height: 40.00mm;
  object-fit: cover;
  display: block;
  border: 0.20mm solid #D8D8D8;
  border-radius: 0.50mm;
}}
.header-info-block {{
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.60mm;
  padding-top: 0;
}}
.header-name {{
  font-family: "Noto Sans CJK SC", "PingFang SC", sans-serif;
  font-size: 21pt;
  font-weight: 700;
  color: #111111;
  letter-spacing: 0.2pt;
  line-height: 1.05;
  margin-bottom: 1.20mm;
}}
.header-contact-line, .header-contact-subline, .header-info {{
  font-family: "Noto Sans CJK SC", "PingFang SC", sans-serif;
  font-size: 9pt;
  font-weight: 400;
  color: #444444;
}}
.header-contact-line {{
  line-height: 1.15;
}}
.header-contact-subline {{
  line-height: 1.15;
  margin-top: -0.2mm;
}}
.header-contact-label {{
  color: #666666;
  font-weight: 600;
}}
.nowrap {{ white-space: nowrap; }}

.resume-section {{ margin-top: 4mm; break-inside: avoid; }}
.section-title {{
  font-family: "Noto Sans CJK SC", "PingFang SC", sans-serif;
  font-size: 12.8pt;
  font-weight: 800;
  color: #111111;
  letter-spacing: 0.5pt;
  margin-bottom: 0;
  line-height: 1.05;
}}
.section-title-rule {{
  width: 100%;
  height: 0.35mm;
  background: #CFCFCF;
  margin-top: 0.80mm;
  margin-bottom: 2.60mm;
}}

.entry {{ margin-bottom: 1.40mm; }}
.entry-head {{ display: flex; justify-content: space-between; gap: 3mm; align-items: baseline; }}
.entry-title {{
  font-family: "Noto Sans CJK SC", "PingFang SC", sans-serif;
  font-size: 10pt;
  font-weight: 700;
  color: #1A1A1A;
}}
.entry-date {{
  color: #666666;
  font-size: 8.5pt;
  white-space: nowrap;
}}
.entry-meta {{
  color: #666666;
  font-size: 8.5pt;
  margin-top: 0.4mm;
}}

.project-entry {{ margin-bottom: 3.2mm; break-inside: avoid; }}
.project-title {{
  font-family: "Noto Sans CJK SC", "PingFang SC", sans-serif;
  font-size: 10pt;
  font-weight: 700;
  color: #1A1A1A;
  margin-bottom: 0.8mm;
}}
.project-desc {{ margin-bottom: 1.40mm; text-align: justify; }}
.inline-label {{ font-weight: 700; color: #2A2A2A; }}
.detail-line {{ color: #1F1F1F; margin-top: 0.6mm; }}
.achievement-line {{ color: #1F1F1F; margin-top: 0.8mm; }}

.pub-entry {{ margin-bottom: 2.4mm; break-inside: avoid; }}
.pub-citation {{
  color: #1F1F1F;
  line-height: 1.35;
}}
.pub-index {{ font-weight: 700; }}
.pub-journal {{ font-style: italic; }}
.pub-volume {{ font-weight: 700; }}
.pub-meta {{
  color: #666666;
  font-size: 8.5pt;
  margin-top: 0.55mm;
  line-height: 1.25;
}}
.pub-doi {{
  color: #666666;
  font-size: 8.5pt;
  margin-top: 0.45mm;
}}

.skill-entry {{ margin-bottom: 1.10mm; }}
.skill-line {{
  color: #1F1F1F;
  line-height: 1.35;
}}
"""


def build_html(resume: Dict[str, Any], photo_uri: Optional[str]) -> str:
    sections = []
    for name in ["教育经历", "科研经历", "论文发表", "技能"]:
        block = render_section(name, resume)
        if block:
            sections.append(block)

    header_html = render_header(resume, photo_uri)
    css = build_css()
    title_name = esc(get_path(resume, "基础信息.name", "简历"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title_name}</title>
  <style>{css}</style>
</head>
<body>
  <div class="page">
    <div class="resume">
      {header_html}
      {''.join(sections)}
    </div>
  </div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="仅根据 简历.yaml 生成简历 HTML/PDF（不依赖模版.yaml）")
    parser.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--resume", default="doc/简历.yaml", help="简历 YAML 相对路径或绝对路径")
    parser.add_argument("--photo", default="", help="可选：强制指定照片路径")
    parser.add_argument("--output-dir", default="outputs/简历", help="输出目录（相对 root）")
    parser.add_argument("--basename", default="简历", help="输出文件名前缀")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()

    resume_path = Path(args.resume).expanduser()
    if not resume_path.is_absolute():
        resume_path = (root / resume_path).resolve()

    if not resume_path.exists():
        print(f"未找到简历 YAML：{resume_path}", file=sys.stderr)
        return 2

    raw_resume = read_yaml(resume_path)
    resume = normalize_resume(raw_resume)

    photo_path: Optional[Path] = None
    if args.photo:
        photo_path = Path(args.photo).expanduser()
        if not photo_path.is_absolute():
            photo_path = (root / photo_path).resolve()
    else:
        photo_from_yaml = get_path(resume, "基础信息.photo", "")
        candidates = [
            photo_from_yaml,
            "doc/1寸修改2.jpg",
            "doc/1寸修改2.jpeg",
            "doc/1寸修改2.png",
        ]
        for p in candidates:
            if not exists(p):
                continue
            candidate = Path(str(p)).expanduser()
            if not candidate.is_absolute():
                candidate = (root / candidate).resolve()
            if candidate.exists():
                photo_path = candidate
                break

    warnings = validate_resume(resume, photo_path)
    if warnings:
        print("提示：发现以下建议修正的问题：", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

    out_dir = (root / args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    photo_uri = data_uri(photo_path) if photo_path else None
    html_text = build_html(resume, photo_uri)

    html_path = out_dir / f"{args.basename}.html"
    html_path.write_text(html_text, encoding="utf-8")
    print(f"已生成 HTML：{html_path}")

    pdf_path = out_dir / f"{args.basename}.pdf"
    try:
        from weasyprint import HTML  # type: ignore
        HTML(string=html_text, base_url=str(root)).write_pdf(str(pdf_path))
        print(f"已生成 PDF：{pdf_path}")
    except Exception as e:
        print("未生成 PDF（通常是因为未安装 weasyprint）", file=sys.stderr)
        print("可安装：pip install weasyprint", file=sys.stderr)
        print(f"详细信息：{e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
