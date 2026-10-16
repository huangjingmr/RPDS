"""将三类工作负载的配对消融结果按预设权重汇总并绘图。"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path
import statistics


WEIGHTS = {"ge": 0.4, "random": 0.3, "fft": 0.3}


def read_pairs(path):
    """读取动态/静态可靠性分配的逐场景配对结果。"""
    with path.open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    required = {"case", "family", "vehicles", "response_reduction_percent",
                "backups_reduction_percent", "both_complete"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("输入不是 backup_ablation.py 生成的 paired.csv")
    return rows


def combine(rows):
    """相同车辆数和种子配成一组，再按40/30/30加权。"""
    grouped = defaultdict(dict)
    for row in rows:
        if row["both_complete"].lower() != "true":
            raise ValueError(f"存在未完成配对: {row['case']}")
        # case格式由冻结实验定义为 family_n{vehicles}_seed{seed}。
        seed = int(row["case"].rsplit("seed", 1)[1])
        key = int(row["vehicles"]), seed
        grouped[key][row["family"]] = row

    combined = []
    for (vehicles, seed), families in sorted(grouped.items()):
        if set(families) != set(WEIGHTS):
            raise ValueError(f"n={vehicles}, seed={seed} 缺少工作负载类型")
        response = sum(WEIGHTS[f] * float(families[f]["response_reduction_percent"])
                       for f in WEIGHTS)
        backups = sum(WEIGHTS[f] * float(families[f]["backups_reduction_percent"])
                      for f in WEIGHTS)
        combined.append({"vehicles": vehicles, "seed": seed,
                         "response_reduction_percent": response,
                         "backups_reduction_percent": backups})
    return combined


def summarize(rows):
    """按车辆数汇总20个种子，并增加全部140个组合观测。"""
    summaries = []
    for vehicles in sorted({row["vehicles"] for row in rows}):
        group = [row for row in rows if row["vehicles"] == vehicles]
        item = {"vehicles": vehicles, "observations": len(group)}
        for field in ("response_reduction_percent", "backups_reduction_percent"):
            values = [row[field] for row in group]
            item[field] = statistics.mean(values)
            item[field + "_sd"] = statistics.stdev(values)
        summaries.append(item)
    return summaries


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def plot(path, summaries):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.35))
    panels = (
        ("response_reduction_percent", "Mean response-time reduction (%)"),
        ("backups_reduction_percent", "Mean backups-per-task reduction (%)"),
    )
    x = [row["vehicles"] for row in summaries]
    for ax, (field, ylabel) in zip(axes, panels):
        ax.errorbar(x, [row[field] for row in summaries],
                    yerr=[row[field + "_sd"] for row in summaries],
                    color="#1764a3", marker="o", markersize=4,
                    linewidth=1.4, capsize=2.5)
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.set_xlabel("Vehicles")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.2)
    fig.suptitle("Dynamic vs. static reliability allocation\n"
                 "Weighted aggregate: 40% GE, 30% Random, 30% FFT")
    fig.tight_layout()
    fig.savefig(path.with_suffix(".pdf"))
    fig.savefig(path.with_suffix(".png"), dpi=220)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请选择新目录")

    combined = combine(read_pairs(args.input))
    if len(combined) != 140:
        raise ValueError(f"预期140个加权观测，实际{len(combined)}")
    summaries = summarize(combined)
    args.output.mkdir(parents=True)
    write_csv(args.output / "weighted_pairs.csv", combined)
    write_csv(args.output / "summary_by_vehicles.csv", summaries)
    plot(args.output / "mixed_backup_ablation", summaries)

    response = [row["response_reduction_percent"] for row in combined]
    backups = [row["backups_reduction_percent"] for row in combined]
    report = [
        "# 40/30/30加权可靠性分配消融",
        "",
        "该结果复用已验证的420个配对场景。每个车辆数和种子下，先分别计算GE、Random、FFT的动态相对静态降幅，再按0.4/0.3/0.3加权，形成140个组合观测。",
        "这是跨工作负载的加权汇总，不表示三类DAG在同一车队中并发竞争。",
        "",
        f"- 平均响应时间降幅：{statistics.mean(response):.4f}%",
        f"- 响应时间降幅标准差：{statistics.stdev(response):.4f}%",
        f"- 平均Backups per task降幅：{statistics.mean(backups):.4f}%",
        f"- Backups per task降幅标准差：{statistics.stdev(backups):.4f}%",
        f"- 组合观测中响应时间改善：{sum(x > 0 for x in response)}/{len(response)}",
        f"- 组合观测中备份数改善：{sum(x > 0 for x in backups)}/{len(backups)}",
        "",
        "误差条是相同车辆数下20个加权种子的样本标准差，不是置信区间。正值表示动态可靠性分配优于静态分配。",
    ]
    (args.output / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
