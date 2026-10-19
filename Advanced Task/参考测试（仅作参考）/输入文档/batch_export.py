"""工作室批量报告导出示例，供模型分析与改造。

输入 reports 为字典列表，每条包含唯一 id、title 和 rows。
rows 是字典列表，CSV 列顺序由 fields 指定。
示例当前在内存中生成全部结果，由调用方进一步保存。
"""

import csv
import io
import json


def render_csv(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def export_reports(reports, fields):
    exported = []
    for report in reports:
        exported.append({
            "id": report["id"],
            "title": report["title"],
            "csv": render_csv(report["rows"], fields),
        })
    return exported


def main():
    reports = [
        {
            "id": "week-01",
            "title": "项目周报",
            "rows": [
                {"owner": "Yu", "task": "接口联调", "status": "closed"},
                {"owner": "Luo", "task": "页面调整", "status": "processing"},
            ],
        },
        {
            "id": "week-02",
            "title": "资料整理",
            "rows": [{"owner": "Wen", "task": "目录更新", "status": "open"}],
        },
    ]
    result = export_reports(reports, ["owner", "task", "status"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
