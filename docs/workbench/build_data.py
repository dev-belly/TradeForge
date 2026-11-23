"""Build the offline browser snapshot from the repository's saved evidence."""

import argparse
import hashlib
import json
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def source(label, path):
    normalized = (ROOT / path).read_text(encoding="utf-8").encode("utf-8")
    return {
        "label": label,
        "url": f"{REPOSITORY}/blob/main/{path}",
        "sha256_lf": hashlib.sha256(normalized).hexdigest(),
    }


def fact(label, value, kind=None):
    return {"label": label, "value": value, "format": kind}


def column(key, title, kind=None):
    return {"key": key, "title": title, "format": kind}


def order(key, title, descending=True):
    return {"key": key, "title": title, "id": key, "descending": descending}


def metric(label, value, note):
    return {"label": label, "value": value, "note": note}


def theme(accent, muted, base, glow):
    return {
        "accent": accent,
        "accent-soft": muted,
        "hero-base": base,
        "hero-glow": glow,
    }


REPOSITORY = "https://github.com/dev-belly/TradeForge"
INPUT = "docs/public-depth/results.json"


def build():
    evidence = read_json(INPUT)
    methods = {"immediate": "立即执行", "twap_10_slices": "TWAP 十批"}
    records = []
    for row in evidence["rows"]:
        delay = row["latency_scenario_ns"]
        fee = row["taker_fee_scenario_bps"]
        method = methods[row["method"]]
        delay_label = f"{delay / 1e6:g} ms"
        fee_label = f"{fee:g} bp"
        identifier = f"{row['method']}-{delay}-{fee:g}"
        fee_cost = row["executed_cost_bps"] - row["price_shortfall_bps"]
        records.append(
            {
                "id": identifier,
                "title": f"{method} · {delay_label} · {fee_label}",
                "subtitle": f"{evidence['symbol']} · 买入 {row['requested_base']:,} DOGE",
                "values": {
                    "method": row["method"],
                    "delay": delay,
                    "fee": fee,
                    "fill": row["fill_ratio"],
                    "cost": row["executed_cost_bps"],
                    "price": row["price_shortfall_bps"],
                },
                "facts": [
                    fact("含手续费成本", row["executed_cost_bps"], "bps"),
                    fact("价格偏离", row["price_shortfall_bps"], "bps"),
                    fact("手续费折算成本", fee_cost, "bps"),
                    fact(
                        "已成交／请求", f"{row['filled_base']:,} / {row['requested_base']:,} DOGE"
                    ),
                    fact("成交金额 (USDT)", row["executed_notional_quote"], "fixed4"),
                    fact("手续费 (USDT)", row["fees_quote"], "fixed4"),
                    fact("最大盘口年龄", row["max_observed_book_age_ns"], "nsMs"),
                ],
                "charts": {
                    "components": [
                        {"label": "价格偏离", "value": row["price_shortfall_bps"]},
                        {"label": "手续费折算", "value": fee_cost},
                    ],
                },
                "sections": [
                    {
                        "title": "成本口径",
                        "text": "成本按已成交数量计算。比较两种执行方式时，同时看含费成本与成交率。",
                    }
                ],
            }
        )
    return {
        "repository": REPOSITORY,
        "slug": "tradeforge",
        "project": "TradeForge",
        "mark": "TF",
        "theme": theme("#2755a5", "#edf4ff", "#112444", "#2e61ae"),
        "workspace": "执行分析",
        "title": "交易执行回放",
        "description": "比较立即执行与十批 TWAP，切换费率和延迟，查看成交率与每一项成本。",
        "dataKind": "公开 L2 盘口 · 假设成交",
        "hero": metric("研究标的", evidence["symbol"], "同方向、同数量、同盘口片段"),
        "metrics": [
            metric("已复算情景", str(len(records)), "两种执行方式 × 三档延迟 × 三档费率"),
            metric(
                "连续盘口批次", str(evidence["continuous_depth_batches"]), "100 ms 聚合 L2 更新"
            ),
            metric(
                "每次请求",
                f"{evidence['rows'][0]['requested_base']:,} DOGE",
                "用相同请求量比较成交率",
            ),
        ],
        "recordHeading": "执行情景",
        "recordLabel": "组合",
        "detailHeading": "情景证据",
        "searchPlaceholder": "搜索方式或情景",
        "initialRecord": "immediate-0-5",
        "filters": [
            {"key": "method", "title": "执行方式", "labels": methods},
            {
                "key": "delay",
                "title": "假设延迟",
                "labels": {
                    str(value): f"{value / 1e6:g} ms"
                    for value in sorted({r["values"]["delay"] for r in records})
                },
            },
            {
                "key": "fee",
                "title": "手续费率",
                "labels": {
                    f"{value:g}": f"{value:g} bp"
                    for value in sorted({r["values"]["fee"] for r in records})
                },
            },
        ],
        "columns": [
            column("cost", "含费成本", "bps"),
            column("fill", "成交率", "percent"),
            column("price", "价格偏离", "bps"),
        ],
        "orders": [order("cost", "含费成本最高"), order("fill", "成交率最高")],
        "views": [
            {
                "id": "cost",
                "label": "含费成本",
                "title": "执行成本对比",
                "metric": "cost",
                "format": "bps",
                "limit": 8,
                "descending": True,
                "caption": "显示当前筛选中成本最高的情景及所选情景。选择一行查看成本拆分。",
            },
            {
                "id": "fill",
                "label": "成交率",
                "title": "成交率对比",
                "metric": "fill",
                "format": "percent",
                "max": 1,
                "limit": 8,
                "caption": "显示筛选后的前八个情景及所选情景。可按成交率调整明细顺序。",
            },
            {
                "id": "components",
                "label": "成本拆分",
                "title": "所选情景的成本拆分",
                "mode": "detail",
                "format": "bps",
                "caption": "以参考中间价折算价格偏离与手续费。",
            },
        ],
        "sourceNote": "样例取自一段真实公开 L2 盘口。手续费和延迟为情景假设，100 ms 聚合数据看不到批次内部更新；模拟成交沿用观测到的后续盘口。",
        "sources": [
            source("18 组结果与采集元数据", INPUT),
            source("结果 CSV", "docs/public-depth/results.csv"),
            source("实验方法与复现", "docs/PUBLIC_MARKET_CASE.md"),
        ],
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the generated snapshot")
    args = parser.parse_args()
    document = build()
    records = document.pop("records")
    lines = ",\n".join(
        "    " + json.dumps(record, ensure_ascii=False, allow_nan=False) for record in records
    )
    header = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2)
    result = (
        "// Generated by build_data.py from repository evidence.\n"
        "window.workbenchData = " + header[:-2] + ',\n  "records": [\n' + lines + "\n  ]\n};\n"
    )
    path = DIRECTORY / "data.js"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != result:
            parser.exit(1, "Snapshot differs. Run build_data.py without --check.\n")
        print(f"Verified {len(records)} records")
    else:
        path.write_text(result, encoding="utf-8", newline="\n")
        print(f"Wrote {len(records)} records")


if __name__ == "__main__":
    main()
