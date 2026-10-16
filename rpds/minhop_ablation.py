"""新RPDS的STPF与方向受限minimum-hop路由响应时间消融。"""
import argparse
import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import platform
import shutil
import statistics

from .ablation import digest
from .ablation_suite import evaluate


WEIGHTS = {"ge": 0.4, "random": 0.3, "fft": 0.3}


def save_provenance(source, output, status, runs):
    """冻结本次实现和上游实验标识，便于以后只读复核而无需重跑。"""
    snapshot = output / "source" / "rpds"
    snapshot.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in Path(__file__).parent.glob("*.py"):
        target = snapshot / path.name
        if not target.exists():
            shutil.copy2(path, target)
        hashes[path.name] = digest(target)
    manifest = {"status": status, "created_or_updated_utc": datetime.now(timezone.utc).isoformat(),
                "python": platform.python_version(), "platform": platform.platform(),
                "source_results": str(source.resolve()),
                "source_manifest_sha256": digest(source / "manifest.json"),
                "weights": WEIGHTS, "new_minhop_runs": runs,
                "source_sha256": hashes, "historical_reproduction": False}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def weighted(rows):
    groups = defaultdict(dict)
    for row in rows:
        groups[(row["vehicles"], row["seed"])][row["family"]] = row["reduction_percent"]
    result = []
    for (vehicles, seed), families in sorted(groups.items()):
        if set(families) != set(WEIGHTS):
            raise ValueError(f"n={vehicles}, seed={seed} 缺少工作负载")
        result.append({"vehicles": vehicles, "seed": seed,
                       "response_reduction_percent": sum(WEIGHTS[f] * families[f] for f in WEIGHTS)})
    return result


def summarize(rows):
    result = []
    for vehicles in sorted({row["vehicles"] for row in rows}):
        values = [row["response_reduction_percent"] for row in rows if row["vehicles"] == vehicles]
        result.append({"vehicles": vehicles, "observations": len(values),
                       "mean_reduction_percent": statistics.mean(values),
                       "sd_reduction_percent": statistics.stdev(values)})
    return result


def plot(output, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4.5, 3.4))
    ax.errorbar([row["vehicles"] for row in rows],
                [row["mean_reduction_percent"] for row in rows],
                yerr=[row["sd_reduction_percent"] for row in rows],
                color="#538b3d", marker="o", markersize=4, linewidth=1.4, capsize=2.5)
    ax.axhline(0, color="#777777", linewidth=0.8)
    ax.set_xlabel("Vehicles")
    ax.set_ylabel("Mean response-time reduction (%)")
    ax.set_title("STPF vs. minimum-hop routing\n40% GE, 30% Random, 30% FFT", fontsize=10)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output / "stpf_minhop_ablation.pdf")
    fig.savefig(output / "stpf_minhop_ablation.png", dpi=220)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请选择新目录")
    args.output.mkdir(parents=True)
    save_provenance(args.source, args.output, "running", 0)

    saved = [row for row in read_csv(args.source / "runs.csv") if row["variant"] == "path_slack"]
    if len(saved) != 420:
        raise ValueError("冻结验证集应包含420个新RPDS运行")
    family_rows = []
    path_changes = 0
    for index, row in enumerate(saved, 1):
        case_dir = args.source / "cases" / row["case"]
        input_path = case_dir / "input.json"
        if digest(input_path) != row["input_sha256"]:
            raise ValueError(f"输入哈希不匹配: {row['case']}")
        data = json.loads(input_path.read_text(encoding="utf-8"))
        with gzip.open(case_dir / "path_slack.json.gz", "rt", encoding="utf-8") as stream:
            stpf = json.load(stream)
        minhop = evaluate(data, "path_slack_minhop")
        if minhop["status"] != "complete" or minhop["validation_issues"] or minhop["reliability_violations"]:
            raise ValueError(f"minimum-hop运行未通过验证: {row['case']}")
        raw_dir = args.output / "cases" / row["case"]
        raw_dir.mkdir(parents=True)
        with gzip.open(raw_dir / "path_slack_minhop.json.gz", "wt", encoding="utf-8", compresslevel=1) as stream:
            json.dump(minhop, stream, ensure_ascii=False, allow_nan=False)
        stpf_paths = [(copy["dag"], copy["task"], copy["vehicle"], tuple(copy["path"]))
                      for copy in stpf["copies"]]
        minhop_paths = [(copy["dag"], copy["task"], copy["vehicle"], tuple(copy["path"]))
                        for copy in minhop["copies"]]
        changed = stpf_paths != minhop_paths
        path_changes += changed
        stpf_response = stpf["metrics"]["eq14_all_vehicles"]
        minhop_response = minhop["metrics"]["eq14_all_vehicles"]
        family_rows.append({"case": row["case"], "family": row["family"],
                            "vehicles": int(row["vehicles"]), "seed": int(row["seed"]),
                            "input_sha256": row["input_sha256"], "paths_or_assignments_changed": changed,
                            "stpf_response_s": stpf_response, "minhop_response_s": minhop_response,
                            "reduction_percent": 100 * (minhop_response - stpf_response) / minhop_response})
        if index % 20 == 0:
            write_csv(args.output / "family_pairs_partial.csv", family_rows)
            print(f"minimum-hop路径消融 {index}/420", flush=True)

    combined = weighted(family_rows)
    summary = summarize(combined)
    write_csv(args.output / "family_pairs.csv", family_rows)
    write_csv(args.output / "weighted_pairs.csv", combined)
    write_csv(args.output / "summary_by_vehicles.csv", summary)
    plot(args.output, summary)
    values = [row["response_reduction_percent"] for row in combined]
    lines = ["# STPF与minimum-hop路径消融", "",
             "固定新的任务选择、动态可靠性分配和EFT车辆分配，只将方向受限子图内的最短传输时延路径换成最少跳数路径。同跳数时按传输时延、节点序列打破平局。",
             "按相同车辆数和种子形成40% GE、30% Random、30% FFT的140个加权观测。正值表示STPF响应时间更低。",
             "该汇总不表示三类DAG在同一车队中并发竞争。", "",
             f"- 平均响应时间降幅：{statistics.mean(values):.4f}%",
             f"- 标准差：{statistics.stdev(values):.4f}%",
             f"- 改善/相同/变差：{sum(v > 1e-10 for v in values)}/{sum(abs(v) <= 1e-10 for v in values)}/{sum(v < -1e-10 for v in values)}",
             f"- 420个原始场景中路径或分配记录发生变化：{path_changes}",
             "- 新增运行的约束及可靠性违规：0", "",
             "所有输入哈希、逐场景完整排程、分项和加权结果均已保存；算法和输入不变时无需重跑。"]
    (args.output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    save_provenance(args.source, args.output, "complete", len(family_rows))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
