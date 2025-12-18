import cv2
import json
import subprocess
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import platform
from datetime import datetime, timedelta
from collections import deque, defaultdict
from ultralytics import YOLO
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 基础配置
# ==========================================
matplotlib.use('Agg')
system_name = platform.system()
# 设置中文字体
if system_name == "Windows":
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
elif system_name == "Darwin":
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC']
else:
    plt.rcParams['font.sans-serif'] = ['SimHei', 'WenQuanYi Micro Hei']
plt.rcParams['axes.unicode_minus'] = False


# ==========================================
# 2. 工具类
# ==========================================
class ChineseTextDrawer:
    def __init__(self):
        self.font = None
        possible_paths = [
            "simhei.ttf", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc", "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
        ]
        for path in possible_paths:
            try:
                self.font = ImageFont.truetype(path, 20, encoding="utf-8")
                self.font.getbbox("测试")
                break
            except:
                self.font = None; continue

    def draw_text(self, img_cv2, text, position, text_color=(255, 255, 255), size=20):
        if self.font is None:
            cv2.putText(img_cv2, text, (int(position[0]), int(position[1])), cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color,
                        2)
            return img_cv2

        font_obj = self.font
        if size != 20:
            try:
                font_obj = ImageFont.truetype(font_obj.path, size, encoding="utf-8")
            except:
                pass

        img_pil = Image.fromarray(cv2.cvtColor(img_cv2, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        x, y = position
        # 黑色描边
        for off_x in [-1, 0, 1]:
            for off_y in [-1, 0, 1]:
                if off_x == 0 and off_y == 0: continue
                draw.text((x + off_x, y + off_y), text, font=font_obj, fill='black')
        draw.text(position, text, font=font_obj, fill=text_color)
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


def get_video_metadata(video_path):
    meta = {"start_time": datetime.now(), "fps": 30.0, "width": 1920}
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', '-select_streams',
               'v:0', video_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        num, den = map(int, stream.get('avg_frame_rate', '30/1').split('/'))
        meta['fps'] = num / den if den > 0 else 30.0
        ct = data['format'].get('tags', {}).get('creation_time')
        if ct:
            meta['start_time'] = datetime.strptime(ct.split('.')[0], "%Y-%m-%dT%H:%M:%S") + timedelta(hours=8)
        meta['width'] = int(stream.get('width', 1920))
    except:
        pass
    return meta


# ==========================================
# 3. 渲染器
# ==========================================
class DashboardRenderer:
    def __init__(self, width=600, height=200):  # 稍微调小一点，适合右上角
        self.width = width
        self.height = height
        self.fig, (self.ax1, self.ax2) = plt.subplots(1, 2, figsize=(width / 100, height / 100), dpi=100)
        self.fig.tight_layout(pad=2.0)

    def update_charts(self, time_series, flow_data, type_counts):
        self.ax1.clear()
        self.ax2.clear()

        # 实时折线图
        if len(time_series) > 0:
            marker_size = 4 if len(time_series) < 50 else 0
            line_width = 2 if len(time_series) < 100 else 1
            self.ax1.plot(time_series, flow_data, color='#00ff00', marker='o', markersize=marker_size,
                          linewidth=line_width)
            self.ax1.set_title("车流趋势 (全时段)", color='white', fontsize=10)
            self.ax1.set_facecolor('#202020')
            self.ax1.tick_params(axis='y', colors='white', labelsize=8)
            self.ax1.grid(True, linestyle='--', alpha=0.3)

            # 智能X轴
            total_points = len(time_series)
            if total_points > 5:  # 右上角空间紧凑，少显示几个标签
                indices = np.linspace(0, total_points - 1, 5).astype(int)
                ticks = [time_series[i] for i in indices]
                self.ax1.set_xticks(ticks)
                self.ax1.set_xticklabels(ticks, rotation=30, color='white', fontsize=8)
            else:
                self.ax1.tick_params(axis='x', colors='white', labelsize=8, rotation=30)

        # 实时柱状图
        types = list(type_counts.keys())
        counts = list(type_counts.values())
        if not types: types, counts = ['无'], [0]
        colors = ['#3498db', '#e74c3c', '#f1c40f', '#9b59b6', '#1abc9c']
        self.ax2.bar(types, counts, color=colors[:len(types)])
        self.ax2.set_title("车型分布", color='white', fontsize=10)
        self.ax2.set_facecolor('#202020')
        self.ax2.tick_params(colors='white', labelsize=8)
        for p in self.ax2.patches:
            self.ax2.annotate(f'{int(p.get_height())}', (p.get_x() + p.get_width() / 2., p.get_height()),
                              ha='center', va='bottom', color='white', fontsize=8)

        self.fig.patch.set_facecolor('#101010')
        self.fig.canvas.draw()
        return cv2.cvtColor(np.asarray(self.fig.canvas.buffer_rgba()), cv2.COLOR_RGBA2BGR)

    def generate_final_summary_image(self, times, total_flows, type_series, total_types):
        # (保持原有的高清汇总图逻辑不变)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), dpi=100)

        # 1. 绘制总流量 + 分类折线
        ax1.plot(times, total_flows, color='#e74c3c', linewidth=3, label='总流量', zorder=10)
        colors_map = {'轿车': '#3498db', '巴士': '#f1c40f', '货车': '#9b59b6', '摩托': '#1abc9c', '其他': '#95a5a6'}
        for t_name, t_flows in type_series.items():
            if sum(t_flows) > 0:
                c = colors_map.get(t_name, '#bdc3c7')
                ax1.plot(times, t_flows, color=c, linewidth=1.5, linestyle='--', marker='.', markersize=4, label=t_name)

        ax1.set_title("全时段分类交通流量趋势 (辆/分)", fontsize=14)
        ax1.grid(True, alpha=0.5)
        ax1.legend(loc='upper right')
        if len(times) > 15:
            indices = np.linspace(0, len(times) - 1, 15).astype(int)
            ticks = [times[i] for i in indices]
            ax1.set_xticks(ticks)
            ax1.set_xticklabels(ticks, rotation=45)
        else:
            ax1.tick_params(rotation=45)

        # 2. 绘制饼图
        labels = list(total_types.keys())
        sizes = list(total_types.values())
        total_count = sum(sizes)
        if not labels: labels, sizes, total_count = ['无数据'], [1], 0
        colors = ['#3498db', '#e74c3c', '#f1c40f', '#9b59b6', '#1abc9c']
        wedges, _ = ax2.pie(sizes, startangle=90, colors=colors[:len(labels)], radius=0.8)
        ax2.set_title(f"总体车型占比 (Total: {total_count}辆)", fontsize=14)

        bbox_props = dict(boxstyle="square,pad=0.3", fc="w", ec="k", lw=0.72)
        kw = dict(arrowprops=dict(arrowstyle="-", color='k'), bbox=bbox_props, zorder=0, va="center")
        for i, p in enumerate(wedges):
            ang = (p.theta2 - p.theta1) / 2. + p.theta1
            y = np.sin(np.deg2rad(ang))
            x = np.cos(np.deg2rad(ang))
            horizontalalignment = {-1: "right", 1: "left"}[int(np.sign(x))]
            connectionstyle = f"angle,angleA=0,angleB={ang}"
            kw["arrowprops"].update({"connectionstyle": connectionstyle})
            percentage = sizes[i] / total_count * 100 if total_count > 0 else 0
            label_text = f"{labels[i]}: {percentage:.1f}% ({sizes[i]}辆)"
            ax2.annotate(label_text, xy=(x * 0.8, y * 0.8), xytext=(1.35 * np.sign(x), 1.4 * y),
                         horizontalalignment=horizontalalignment, fontsize=10, **kw)

        plt.tight_layout()
        fig.canvas.draw()
        img = cv2.cvtColor(np.asarray(fig.canvas.buffer_rgba()), cv2.COLOR_RGBA2BGR)
        plt.close(fig)
        return img


# ==========================================
# 4. 主逻辑
# ==========================================
class TrafficAIAnalyzer:
    def __init__(self, model_path='yolo11n.pt', video_source='traffic.mp4'):
        self.video_source = video_source
        self.meta = get_video_metadata(video_source)
        self.video_start_time = self.meta['start_time']
        self.fps = self.meta['fps']

        print(f"🚀 系统启动 | 布局: 图表右上角 | 标签: 仅显示车型")

        self.model = YOLO(model_path)
        self.cap = cv2.VideoCapture(video_source)
        # 初始化图表 (宽600, 高200)
        self.dashboard = DashboardRenderer(width=600, height=200)
        self.cn_drawer = ChineseTextDrawer()

        self.total_unique_ids = set()
        self.total_type_counts = defaultdict(int)
        self.id_type_map = {}
        self.minute_traffic_stats = defaultdict(set)

        self.chart_time_history = []
        self.chart_flow_history = []
        self.last_chart_img = None

        self.class_map = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
        self.frame_count = 0

    def run(self):
        while self.cap.isOpened():
            success, frame = self.cap.read()
            if not success: break

            seconds = self.frame_count / self.fps
            current_dt = self.video_start_time + timedelta(seconds=seconds)
            time_str = current_dt.strftime("%Y-%m-%d %H:%M:%S")
            minute_str = current_dt.strftime("%H:%M")

            results = self.model.track(frame, persist=True, conf=0.5, classes=[2, 3, 5, 7], verbose=False)
            current_ids = []

            if results[0].boxes.id is not None:
                boxes = results[0].boxes.xywh.cpu()
                track_ids = results[0].boxes.id.int().cpu().tolist()
                cls_ids = results[0].boxes.cls.int().cpu().tolist()
                current_ids = track_ids

                for box, track_id, cls_id in zip(boxes, track_ids, cls_ids):
                    en_type = self.class_map.get(cls_id, "Other")
                    cn_type_map = {"car": "轿车", "bus": "巴士", "truck": "货车", "motorcycle": "摩托"}
                    cn_type = cn_type_map.get(en_type, "其他")

                    if track_id not in self.total_unique_ids:
                        self.total_unique_ids.add(track_id)
                        self.total_type_counts[cn_type] += 1
                        self.id_type_map[track_id] = cn_type
                    self.minute_traffic_stats[minute_str].add(track_id)

                    # === 绘制逻辑优化 ===
                    x, y, w, h = box
                    cv2.rectangle(frame, (int(x - w / 2), int(y - h / 2)), (int(x + w / 2), int(y + h / 2)),
                                  (0, 255, 0), 2)

                    # 仅显示车型名称 (去掉了 track_id)
                    frame = self.cn_drawer.draw_text(
                        frame,
                        f"{cn_type}",  # 修改处：只传 cn_type
                        (int(x - w / 2), int(y - h / 2) - 25),
                        text_color=(0, 255, 0),
                        size=18
                    )

            # 更新图表
            if self.frame_count % int(self.fps) == 0:
                self.chart_time_history.append(time_str)
                self.chart_flow_history.append(len(current_ids))
                curr_types = [self.id_type_map.get(tid, "其他") for tid in current_ids]
                curr_type_counts = {t: curr_types.count(t) for t in set(curr_types)}
                self.last_chart_img = self.dashboard.update_charts(
                    self.chart_time_history, self.chart_flow_history, curr_type_counts
                )

            # === 核心修改：图表放置在右上角 ===
            if self.last_chart_img is not None:
                h, w, _ = self.last_chart_img.shape
                frame_h, frame_w, _ = frame.shape

                # 计算右上角位置 (留出20像素边距)
                y_pos = 20
                x_pos = frame_w - w - 20

                # 边界检查，防止报错
                if x_pos > 0 and (y_pos + h) < frame_h:
                    frame[y_pos:y_pos + h, x_pos:x_pos + w] = self.last_chart_img

            # 状态栏文字 (左上角)
            frame = self.cn_drawer.draw_text(
                frame,
                f"实时分析 | 时间: {time_str} | 累计车辆: {len(self.total_unique_ids)}",
                (20, 20), text_color=(0, 255, 255), size=24
            )

            cv2.imshow("Urban Traffic AI - Top Right Dashboard", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'): break
            self.frame_count += 1

        self.cap.release()
        cv2.destroyAllWindows()
        self.generate_final_report()

    def generate_final_report(self):
        print("\n" + "=" * 40)
        print("🎬 生成最终报告...")

        final_minute_counts = {k: len(v) for k, v in self.minute_traffic_stats.items()}
        sorted_times = sorted(final_minute_counts.keys())
        sorted_total_flows = [final_minute_counts[t] for t in sorted_times]

        all_types = ["轿车", "巴士", "货车", "摩托"]
        type_series = {t: [] for t in all_types}
        for t_str in sorted_times:
            ids = self.minute_traffic_stats[t_str]
            counts = defaultdict(int)
            for vid in ids: counts[self.id_type_map.get(vid, "其他")] += 1
            for vtype in all_types: type_series[vtype].append(counts[vtype])

        summary_img = self.dashboard.generate_final_summary_image(
            sorted_times, sorted_total_flows, type_series, self.total_type_counts
        )

        cv2.imwrite("final_traffic_report.png", summary_img)
        print(f"✅ 统计完成: {dict(self.total_type_counts)}")
        print("✅ 报告已保存: final_traffic_report.png")

        cv2.imshow("Final Report", summary_img)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        analyzer = TrafficAIAnalyzer(video_source="2025-12-05 171419.mov")
        analyzer.run()
    except Exception as e:
        print(f"Error: {e}")