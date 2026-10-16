from pathlib import Path
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPORT_DIR = Path("/Users/hj/Projects/RPDS/reports")

PAPER_DIR = Path(
    "/Users/hj/Documents/My papers/"
    "Reliability-Aware Collaborative Computing for Multi-DAG "
    "Task Offloading in Vehicular Fleets"
)


def read_csv(folder_name):
    csv_path = REPORT_DIR / folder_name / "summary.csv"

    with csv_path.open(encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def plot_application_structure(family):
    """
    family 可取：
        random
        ge
        fft
    """

    fleet_rows = read_csv(
        "main_experiment_new_rpds_20261016"
    )

    reliability_rows = read_csv(
        "reliability_sensitivity_new_rpds_20261016"
    )

    methods = [
        "rpds",
        "local_maxre",
        "local_rr",
        "heft_maxre",
        "heft_rr",
    ]

    labels = [
        "RPDS",
        "Local&MaxRe",
        "Local&RR",
        "HEFT&MaxRe",
        "HEFT&RR",
    ]

    colors = [
        "#c62828",
        "#666666",
        "#e69500",
        "#2465a8",
        "#25844b",
    ]

    markers = ["o", "s", "^", "D", "v"]

    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 7.2,
        "axes.labelsize": 7.2,
        "axes.titlesize": 7.2,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 5.8,
        "pdf.fonttype": 42,
    })

    # 高度适当降低，使坐标框占据更大比例
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(3.5, 1.72),
    )

    experiments = [
        (
            axes[0],
            reliability_rows,
            "requirement",
            "Reliability requirement (%)",
        ),
        (
            axes[1],
            fleet_rows,
            "vehicles",
            "Number of vehicles",
        ),
    ]

    for ax, data, x_key, x_label in experiments:
        for method, label, color, marker in zip(
            methods,
            labels,
            colors,
            markers,
        ):
            selected = [
                row for row in data
                if row["family"] == family
                and row["method"] == method
            ]

            selected.sort(
                key=lambda row: float(row[x_key])
            )

            x_values = [
                float(row[x_key])
                * (100 if x_key == "requirement" else 1)
                for row in selected
            ]

            y_values = [
                float(row["eq14_response_s"])
                for row in selected
            ]

            y_errors = [
                float(row["eq14_response_s_sd"])
                for row in selected
            ]

            ax.errorbar(
                x_values,
                y_values,
                yerr=y_errors,
                label=label,
                color=color,
                marker=marker,
                markersize=2.4,
                linewidth=1.0,
                capsize=1.8,
                elinewidth=0.6,
            )

        ax.set_xlabel(x_label, labelpad=1.5)

        ax.grid(
            alpha=0.22,
            linewidth=0.45,
        )

        # 减少刻度与坐标轴之间的空白
        ax.tick_params(
            axis="both",
            pad=1.5,
            length=2.5,
        )

        # 减少数据两端的默认留白
        ax.margins(x=0.025)

    axes[0].set_title(
        "(a) Reliability requirement",
        pad=2,
    )

    axes[1].set_title(
        "(b) Fleet size",
        pad=2,
    )

    # 使用一个公共纵轴标题，避免第二个子图重复占空间
    fig.supylabel(
        "Average response time (s)",
        x=0.012,
        fontsize=7.2,
    )

    handles, legend_labels = (
        axes[0].get_legend_handles_labels()
    )

    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.52, 0.995),
        ncol=5,
        frameon=False,
        columnspacing=0.75,
        handlelength=1.15,
        handletextpad=0.3,
        borderaxespad=0,
    )

    # 手工控制边界，比 tight_layout 更容易精确压缩
    fig.subplots_adjust(
        left=0.115,
        right=0.995,
        bottom=0.205,
        top=0.765,
        wspace=0.25,
    )

    output_path = (
        PAPER_DIR
        / "images"
        / f"rpds_{family}_structure_20261016.pdf"
    )

    fig.savefig(
        output_path,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0.01,
    )

    plt.close(fig)

    print(f"Generated: {output_path}")


if __name__ == "__main__":
    plot_application_structure("random")
    plot_application_structure("ge")
    plot_application_structure("fft")