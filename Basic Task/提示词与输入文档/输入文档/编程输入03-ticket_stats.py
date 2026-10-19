"""工作室工单统计程序：供代码审查与修复使用。

运行环境：Python 3.10 及以上，仅使用标准库。

功能约定
--------
输入 records 是字典列表，工单字段如下：
    id: 字符串工单编号，去除两端空格后判断；缺失或为空的编号跳过。
    owner: 处理人；缺失、None 或仅含空格时归为 unassigned。
    status: open / processing / closed，去除两端空格并忽略大小写。
            缺失或其他状态归为 other。
    created_at: ISO 8601 创建时间，例如 2026-10-12T09:00:00+08:00。
    closed_at: ISO 8601 关闭时间，可以缺失。

同一编号出现多次时，只统计第一条具有该有效编号的记录。
total 为去重后有效编号的记录数。by_status 始终包含
open、processing、closed、other 四个键。by_owner 使用规范化后的处理人，
每人的记录包含 total 和上述四种状态的数量。

平均处理耗时仅采用 closed 工单：创建和关闭时间均可解析、均含时区，
且关闭时间晚于或等于创建时间。以小时计算，结果保留两位小数。
无效时间仍保留工单计数，只跳过该工单的耗时统计。
没有可用于计算的 closed 工单时，average_closed_hours 为 None。
空输入返回 total=0、四种状态均为 0、by_owner={}、平均耗时为 None。
程序处理输入时保持原始记录内容。

主要函数及调用方式：
    parse_time(value)
    ticket_hours(record)
    summarize_tickets(records)
    render_summary(summary)
    main()

本文件是待审查版本。请结合约定、示例和边界情境检查实现。
"""

from datetime import datetime
import json


STATUSES = ("open", "processing", "closed", "other")


def parse_time(value):
    """将时间字符串转换为 datetime。"""
    return datetime.fromisoformat(value)


def ticket_hours(record):
    """读取一条工单的处理耗时，单位为小时。"""
    start = parse_time(record["created_at"])
    end = parse_time(record["closed_at"])
    return (end - start).total_seconds() / 3600


def summarize_tickets(records):
    """按状态、处理人汇总，并计算平均处理耗时。"""
    by_status = {name: 0 for name in STATUSES}
    by_owner = {}
    hours = []

    for record in records:
        owner = record["owner"]
        status = record["status"]

        if owner not in by_owner:
            by_owner[owner] = {
                "total": 0,
                "open": 0,
                "processing": 0,
                "closed": 0,
                "other": 0,
            }

        by_status[status] += 1
        by_owner[owner]["total"] += 1
        by_owner[owner][status] += 1

        if record.get("closed_at"):
            hours.append(ticket_hours(record))
        else:
            hours.append(0)

    average = int(sum(hours) / len(records))

    return {
        "total": len(records),
        "by_status": by_status,
        "by_owner": by_owner,
        "average_closed_hours": average,
    }


def render_summary(summary):
    """将统计结果转换为可打印的文本。"""
    lines = ["工单统计", f"有效工单总数：{summary['total']}"]
    lines.append("状态分布：")
    for status in STATUSES:
        lines.append(f"  {status}: {summary['by_status'][status]}")
    lines.append("处理人分布：")
    for owner, counts in sorted(summary["by_owner"].items()):
        lines.append(f"  {owner}: {counts['total']}")
    average = summary["average_closed_hours"]
    lines.append(f"平均已关闭工单耗时：{average} 小时")
    return "\n".join(lines)


SAMPLE_RECORDS = [
    {
        "id": "T001",
        "owner": "Chen",
        "status": "closed",
        "created_at": "2026-10-12T09:00:00+08:00",
        "closed_at": "2026-10-12T11:30:00+08:00",
    },
    {
        "id": "T002",
        "owner": "Song",
        "status": "processing",
        "created_at": "2026-10-12T10:00:00+08:00",
        "closed_at": None,
    },
    {
        "id": "T003",
        "owner": "Chen",
        "status": "open",
        "created_at": "2026-10-12T13:00:00+08:00",
        "closed_at": None,
    },
    {
        "id": "T004",
        "owner": "Zhou",
        "status": "closed",
        "created_at": "2026-10-12T14:00:00+08:00",
        "closed_at": "2026-10-12T18:15:00+08:00",
    },
    {
        "id": "T001",
        "owner": "Chen",
        "status": "closed",
        "created_at": "2026-10-12T09:00:00+08:00",
        "closed_at": "2026-10-12T11:30:00+08:00",
    },
]


# 以下情境供测试设计时参考，可自行构造更多组合。
EDGE_SCENARIOS = (
    "输入为空列表",
    "有效工单缺少 owner、status 或 closed_at",
    "处理人为空字符串、None 或只含空格",
    "状态含首尾空格、大小写变化或未知值",
    "相同 id 重复出现，以及 id 缺失或只含空格",
    "未关闭工单带有遗留的 closed_at 字段",
    "关闭工单缺少时间或时间格式错误",
    "创建时间和关闭时间使用不同的 UTC 偏移",
    "时间缺少时区，以及关闭时间早于创建时间",
    "输入仅包含没有有效耗时的工单",
)


def main():
    summary = summarize_tickets(SAMPLE_RECORDS)
    print(render_summary(summary))
    print("\n结构化结果：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
