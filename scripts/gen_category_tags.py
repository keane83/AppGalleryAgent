#!/usr/bin/env python3
"""生成 app_gallery_category_tags.py（一次性脚本）。"""

from pathlib import Path

RAW = Path(__file__).with_name("category_tags.tsv")
if not RAW.is_file():
    raise SystemExit(f"缺少 {RAW}")

entries = []
for line in RAW.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    cat, cat_id, tag, tag_id = line.split("\t")
    entries.append(
        {
            "category": cat,
            "secondCategoryId": int(cat_id),
            "tag": tag,
            "tagId": int(tag_id),
        }
    )

out = Path(__file__).resolve().parent.parent / "app/tools/app_gallery_category_tags.py"
helpers = '''
def format_category_tag_options(*, max_lines: int = 400) -> str:
    """供 LLM 选择的分类/标签列表（TSV）。"""
    lines = ["二级分类\\t二级分类ID\\t标签\\t标签ID"]
    for item in CATEGORY_TAG_ENTRIES[:max_lines]:
        lines.append(
            f"{item['category']}\\t{item['secondCategoryId']}\\t{item['tag']}\\t{item['tagId']}"
        )
    return "\\n".join(lines)


def find_entry_by_tag_id(tag_id: int) -> dict | None:
    for item in CATEGORY_TAG_ENTRIES:
        if item["tagId"] == tag_id:
            return item
    return None


def validate_category_tag(second_category_id: int, tag_id: int) -> dict:
    entry = find_entry_by_tag_id(tag_id)
    if not entry:
        raise ValueError(f"无效 tagId: {tag_id}")
    if entry["secondCategoryId"] != second_category_id:
        raise ValueError(
            f"tagId {tag_id} 与 secondCategoryId {second_category_id} 不匹配，"
            f"应为 {entry['secondCategoryId']}（{entry['category']} / {entry['tag']}）"
        )
    return entry
'''

out.write_text(
    '"""AppGallery 二级分类与标签 ID 对照表。"""\n\n'
    "from __future__ import annotations\n\n"
    "from typing import Any\n\n"
    f"CATEGORY_TAG_ENTRIES: list[dict[str, Any]] = {entries!r}\n"
    + helpers,
    encoding="utf-8",
)
print(f"wrote {out} ({len(entries)} entries)")
