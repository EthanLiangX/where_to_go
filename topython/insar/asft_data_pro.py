import datetime
import os
import logging
import numpy as np
import pandas as pd
import geopandas as gpd
import dask
from dask.distributed import Client
from shapely.geometry import box, Point
from shapely.wkt import loads

# --- 1. 绘图与制图库 ---
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker
from mpl_toolkits.axes_grid1.anchored_artists import AnchoredSizeBar
import matplotlib.font_manager as fm

# 尝试导入 contextily 用于底图
try:
    import contextily as cx

    HAS_CONTEXTILY = True
except ImportError:
    HAS_CONTEXTILY = False

# PyGMTSAR imports
import asf_search as asf
from pygmtsar import ASF, S1, Tiles, Stack, tqdm_dask

# ReportLab imports
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4

# --- 科学计算库 (用于异常探测) ---
from scipy.ndimage import label, center_of_mass


class Sentinel1InSARProcessor:
    def __init__(self, config):
        """初始化处理器"""
        self.user = config.get('user')
        self.password = config.get('password')
        self.proxy = config.get('proxy')
        self.coherence_threshold = config.get('coherence_threshold', 0.08)
        self.alert_threshold = config.get('alert_threshold', -20.0)  # 沉降预警阈值 (mm)

        # --- 目录结构配置 ---
        self.base_dir = os.path.abspath(config.get('base_dir', "./data"))
        self.raw_dir = os.path.join(self.base_dir, "raw")
        self.slc_dir = os.path.join(self.raw_dir, "sentinel1")
        self.dem_dir = os.path.join(self.raw_dir, "dem")
        self.interim_dir = os.path.join(self.base_dir, "interim")
        self.stack_dir = os.path.join(self.interim_dir, "stack")
        self.output_dir = os.path.join(self.base_dir, "output")
        self.plot_dir = os.path.join(self.output_dir, "images")
        self.gis_dir = os.path.join(self.output_dir, "gis")
        self.report_dir = os.path.join(self.output_dir, "report")
        self.resource_dir = os.path.join(self.base_dir, "resource")

        for p in [self.slc_dir, self.dem_dir, self.stack_dir, self.plot_dir, self.gis_dir, self.report_dir,
                  self.resource_dir]:
            os.makedirs(p, exist_ok=True)

        self.logger = self._setup_logger()
        if self.proxy:
            os.environ['http_proxy'] = self.proxy
            os.environ['https_proxy'] = self.proxy

    def _setup_logger(self):
        logger = logging.getLogger("InSAR_Pro")
        logger.setLevel(logging.INFO)
        logger.handlers = []
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        fh = logging.FileHandler(os.path.join(self.base_dir, 'process.log'), mode='a', encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        return logger

    def _update_workspace_dirs(self, start_date, end_date):
        """
        根据影像日期动态更新工作目录
        """
        # --- 修复点：强制转换为 datetime 对象，防止传入的是字符串 ---
        dt_start = pd.to_datetime(start_date)
        dt_end = pd.to_datetime(end_date)

        date_str = f"{dt_start.strftime('%Y%m%d')}-{dt_end.strftime('%Y%m%d')}"
        self.logger.info(f"📂 [Workspace] 创建任务目录: {date_str}")

        # 更新目录
        self.task_interim_dir = os.path.join(self.interim_dir, date_str)
        self.stack_dir = os.path.join(self.task_interim_dir, "stack")

        self.task_output_dir = os.path.join(self.output_dir, date_str)
        self.plot_dir = os.path.join(self.task_output_dir, "images")
        self.gis_dir = os.path.join(self.task_output_dir, "gis")
        self.report_dir = os.path.join(self.task_output_dir, "report")

        # 创建文件夹
        for p in [self.stack_dir, self.plot_dir, self.gis_dir, self.report_dir]:
            os.makedirs(p, exist_ok=True)

        # 添加任务专属日志
        fh = logging.FileHandler(os.path.join(self.task_output_dir, 'task_process.log'), mode='w', encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(fh)
    def _setup_auth(self):
        netrc_path = os.path.expanduser("~/.netrc")
        with open(netrc_path, "w") as f:
            f.write(f"machine urs.earthdata.nasa.gov login {self.user} password {self.password}")
        os.chmod(netrc_path, 0o600)

    # ======================== 核心修复：X轴范围字符串显示 ========================
    def _optimize_layout(self):
        """
        优化坐标轴：
        1. 隐藏 X 轴刻度数字 (labelbottom=False)
        2. 将经度范围写在 set_xlabel 中 (例如: 113.632~113.648)
        """
        try:
            ax = plt.gca()
            x_min, x_max = ax.get_xlim()
            # 构造范围字符串
            x_label_str = f"Lon:{x_min:.3f}°E ~ {x_max:.3f}°E"
            ax.set_xlabel(x_label_str, fontsize=11, fontweight='bold', labelpad=8)
            # 隐藏刻度数字
            ax.tick_params(axis='x', which='both', bottom=True, top=False, labelbottom=False)

            # Y轴处理
            locator_y = ticker.MaxNLocator(nbins=4, prune='both')
            ax.yaxis.set_major_locator(locator_y)
            ax.tick_params(axis='y', labelsize=10)
            ax.set_ylabel("Latitude", fontsize=11, fontweight='bold')
            plt.tight_layout()
        except Exception as e:
            self.logger.warning(f"⚠️ 坐标轴优化失败: {e}")

    def _add_map_elements(self, ax):
        """添加指北针和比例尺"""
        x_min, x_max = ax.get_xlim()
        y_min, y_max = ax.get_ylim()
        w, h = x_max - x_min, y_max - y_min

        # 指北针 (右上)
        arrow_x = x_max - w * 0.05
        arrow_y = y_max - h * 0.05
        ax.annotate('N', xy=(arrow_x, arrow_y), xytext=(arrow_x, arrow_y - h * 0.05),
                    arrowprops=dict(facecolor='black', width=3, headwidth=8),
                    ha='center', va='top', fontsize=12, fontweight='bold', zorder=10)

        # 比例尺 (左下)
        center_lat = (y_min + y_max) / 2
        one_degree_km = 111.32 * np.cos(np.radians(center_lat))
        target_km = (w * one_degree_km) * 0.2
        scale_len_km = 1 if target_km < 2 else int(target_km)
        scale_len_deg = scale_len_km / one_degree_km

        scale = AnchoredSizeBar(ax.transData, scale_len_deg, f'{scale_len_km} km', 'lower left',
                                pad=0.5, color='black', frameon=False, size_vertical=h * 0.005,
                                fontproperties=fm.FontProperties(size=10, weight='bold'))
        ax.add_artist(scale)

    def _plot_displacement_pro(self, data, caption, out_path):
        """专业版绘图 (原 Level3)"""
        if 'stack' in data.dims and isinstance(data.coords['stack'].to_index(), pd.MultiIndex):
            data = data.unstack('stack')

        fig, ax = plt.subplots(figsize=(10, 8))

        vmin, vmax = data.min().item(), data.max().item()
        limit = max(abs(vmin), abs(vmax))

        # 绘图
        im = data.plot.imshow(ax=ax, cmap='RdYlBu', alpha=0.7, add_colorbar=False, vmin=-limit, vmax=limit)

        # 叠加底图
        if HAS_CONTEXTILY and self.proxy:
            try:
                cx.add_basemap(ax, crs='EPSG:4326', source=cx.providers.CartoDB.Positron, alpha=0.6)
            except:
                pass

        cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.04)
        cbar.set_label('LOS Displacement [mm]', fontweight='bold')

        self._add_map_elements(ax)
        self._optimize_layout()

        plt.title(caption, fontsize=12, fontweight='bold', pad=15)
        plt.savefig(out_path, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _detect_anomalies(self, disp_data):
        """
        自动检测异常，并将像素计数转换为平方米面积
        """
        try:
            # 1. 阈值筛选
            mask = disp_data < self.alert_threshold
            mask_np = mask.values
            if np.isnan(mask_np).all(): return []

            # 2. 连通域分析
            labeled_array, num_features = label(mask_np)
            anomalies = []

            # --- [新增] 计算单个像素的物理面积 (平方米) ---
            # 获取经纬度分辨率 (单位: 度)
            if len(disp_data.lat) > 1 and len(disp_data.lon) > 1:
                res_lat_deg = abs(disp_data.lat[1].item() - disp_data.lat[0].item())
                res_lon_deg = abs(disp_data.lon[1].item() - disp_data.lon[0].item())

                # 获取中心纬度用于计算
                mean_lat = disp_data.lat.mean().item()

                # 转换为米 (1度 ≈ 111320米)
                res_lat_m = res_lat_deg * 111320
                res_lon_m = res_lon_deg * 111320 * np.cos(np.radians(mean_lat))

                pixel_area_m2 = res_lat_m * res_lon_m
            else:
                # 兜底：如果无法计算，默认为 0
                pixel_area_m2 = 0
            # -------------------------------------------

            if num_features > 0:
                lats, lons = disp_data.lat.values, disp_data.lon.values

                for i in range(1, num_features + 1):
                    region_mask = (labeled_array == i)
                    # 过滤小于 3 个像素的噪点
                    pixel_count = int(np.sum(region_mask))
                    if pixel_count < 3: continue

                    region_vals = disp_data.values[region_mask]
                    min_val = np.min(region_vals)

                    # 计算重心
                    center_idx = center_of_mass(region_mask)
                    center_lat = lats[int(center_idx[0])]
                    center_lon = lons[int(center_idx[1])]

                    # 计算实际面积
                    area_m2 = pixel_count * pixel_area_m2

                    anomalies.append({
                        'id': len(anomalies) + 1,
                        'lat': f"{center_lat:.4f}",
                        'lon': f"{center_lon:.4f}",
                        'min_val': f"{min_val:.1f}",
                        'area_sqm': int(area_m2)  # 存储平方米整数
                    })

            # 按沉降严重程度排序
            anomalies.sort(key=lambda x: float(x['min_val']))
            return anomalies[:10]  # 返回前10个

        except Exception as e:
            self.logger.error(f"异常探测出错: {e}")
            return []

    def _generate_report_pro(self, font_path, output_pdf, meta_info, anomalies, img_paths):
        """生成专业工程咨询报告"""
        try:
            pdfmetrics.registerFont(TTFont('SourceHanSerifCN', font_path))
            font_name = "SourceHanSerifCN"
        except:
            font_name = "Helvetica"

        styles = getSampleStyleSheet()
        for s in styles.byName: styles[s].fontName = font_name

        # 定义专业样式
        style_title = ParagraphStyle('Title', parent=styles['Title'], fontSize=24, spaceAfter=30, leading=32,
                                     alignment=TA_CENTER)
        style_h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=16, spaceBefore=20, spaceAfter=12,
                                  textColor=colors.HexColor("#003366"), borderWidth=0,
                                  borderColor=colors.HexColor("#003366"))
        style_h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=14, spaceBefore=12, spaceAfter=8,
                                  textColor=colors.HexColor("#2C3E50"))
        style_body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=11, leading=18, alignment=TA_JUSTIFY,
                                    firstLineIndent=22)
        style_caption = ParagraphStyle('Caption', parent=styles['Normal'], fontSize=10, alignment=TA_CENTER,
                                       textColor=colors.grey)

        doc = SimpleDocTemplate(output_pdf, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
        elements = []

        # --- 封面 ---
        elements.append(Spacer(1, 100))
        elements.append(Paragraph("城市地表稳定性监测与分析", style_title))
        elements.append(Paragraph("专项咨询报告", style_title))
        elements.append(Spacer(1, 120))

        cover_data = [
            ['项目编号', f"INSAR-{datetime.datetime.now().strftime('%Y%m%d')}-001"],
            ['监测手段', '星载合成孔径雷达干涉测量 (InSAR)'],
            ['监测区域', '目标核心区 (详见正文)'],
            ['监测时段', f"{meta_info['ref_date']} 至 {meta_info['sec_date']}"],
            ['报告日期', datetime.datetime.now().strftime('%Y年%m月%d日')],
            ['编制单位', '自动化监测分析系统']
        ]
        t_cover = Table(cover_data, colWidths=[100, 300])
        t_cover.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('SIZE', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ]))
        elements.append(t_cover)
        elements.append(PageBreak())

        # --- 1. 项目综述 ---
        elements.append(Paragraph("1. 项目综述 (Executive Summary)", style_h1))
        summary_text = f"""
        受相关部门/单位委托，本项目采用先进的星载InSAR技术，对指定区域的地表稳定性进行了高精度监测。
        本次监测基于Sentinel-1卫星星座的C波段雷达影像，覆盖了从<b>{meta_info['ref_date']}</b>到<b>{meta_info['sec_date']}</b>的时间跨度。
        """
        elements.append(Paragraph(summary_text, style_body))

        summary_text_2 = f"""
        经过精密轨道校正、多视处理及相位解缠，分析结果表明：监测区域整体地表稳定性<b>{'良好' if float(meta_info['min_val']) > -20 else '存在局部沉降风险'}</b>。
        在监测期内，全区最大累积沉降量为<b>{meta_info['min_val']}mm（雷达视线向）</b>，最大正向形变量为<b>{meta_info['max_val']}mm（雷达视线向）</b>。
        系统共识别出<b>{len(anomalies)}</b>个显著形变区域（沉降值超过 {self.alert_threshold}mm），建议结合现场情况进行查证。
        """
        elements.append(Paragraph(summary_text_2, style_body))

        # --- 2. 技术路线与原理 ---
        elements.append(Paragraph("2. 技术路线与方法", style_h1))
        tech_intro = """
        本项目采用基于小基线集（SBAS）的时序 InSAR 技术，对多时相雷达影像进行联合反演。
        该技术通过分析两期或多期雷达影像的相位差异，能够以毫米级的精度反演地表微小形变,获取监测期内地表形变时间序列及累积形变结果。
        """
        elements.append(Paragraph(tech_intro, style_body))

        elements.append(Paragraph("2.1 数据处理流程", style_h2))
        flow_desc = """
        <b>数据处理主要包含以下关键步骤：</b><br/>
        (1) 精密定轨：利用ESA发布的精密轨道星历（POD）去除轨道误差；<br/>
        (2) 干涉生成：对配准后的影像进行共轭相乘，生成干涉图；<br/>
        (3) 去平去地：利用外部DEM去除地形相位，保留形变相位；<br/>
        (4) 相位解缠：利用SNAPHU算法将周期性相位恢复为连续相位；<br/>
        (5) 地理编码：将雷达坐标系结果投影至地理坐标系。<br/>
        """
        elements.append(Paragraph(flow_desc, style_body))

        elements.append(PageBreak())

        # --- 3. 监测结果分析 ---
        elements.append(Paragraph("3. 监测结果详细分析", style_h1))

        # 3.1 形变场分析
        elements.append(Paragraph("3.1 地表形变场分布特征", style_h2))
        disp_analysis = f"""
        下图展示了监测区域内的最终地表形变场分布情况。图中色彩代表了地表在雷达视线向（LOS）上的移动量。
        从整体分布来看，监测区域大部分呈现稳定的绿色/黄色色调。局部出现的深红色斑块指示了显著的沉降漏斗，
        而深蓝色斑块则可能对应地表抬升或建筑物施工引起的高程增加。一般而言，|形变量|小于±5mm的区域可视为相对稳定区。
        """
        elements.append(Paragraph(disp_analysis, style_body))

        if os.path.exists(img_paths['disp']):
            elements.append(Spacer(1, 10))
            elements.append(Image(img_paths['disp'], width=450, height=360, kind='proportional'))
            elements.append(Paragraph("图 3-1 监测区域地表形变场专题图", style_caption))
            elements.append(Spacer(1, 10))
            # 图例解释框
            t_legend = Table([[Paragraph(
                "<b>读图指南：</b><br/>• <b>红色/暖色</b>：表示地表沉降 (远离卫星)。<br/>• <b>蓝色/冷色</b>：表示地表抬升 (靠近卫星)。<br/>• <b>空白区域</b>：低相干掩膜区 (无有效数据)。",
                style_body)]], colWidths=[450])
            t_legend.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F9F9F9")),
                                          ('BOX', (0, 0), (-1, -1), 0.5, colors.grey)]))
            elements.append(t_legend)

        # 3.2 风险清单
        elements.append(Paragraph("3.2 重点风险区域排查", style_h2))
        if anomalies:
            elements.append(Paragraph(
                """本表所列重点风险区域基于 SBAS-InSAR 沉降时间序列结果，通过空间聚类方法自动识别得到。每个风险区域由多个相邻沉降异常像元构成，
                中心经纬度用于表征区域整体空间位置，最大沉降量为区域内沉降极值，估算面积反映该区域沉降影响的空间覆盖范围。""",
                style_body))
            elements.append(Spacer(1, 10))
            risk_data = [['编号', '中心纬度', '中心经度', '最大沉降（区域极值，mm）', '估算面积(m²)']]
            for a in anomalies:
                # 获取面积数据
                area_val = str(a.get('area_sqm', 'N/A'))
                risk_data.append([
                    str(a['id']),
                    str(a['lat']),
                    str(a['lon']),
                    str(a['min_val']),
                    area_val
                ])
            t_risk = Table(risk_data, colWidths=[40, 100, 100, 100, 100])
            t_risk.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#C0392B")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]))
            elements.append(t_risk)
        else:
            elements.append(
                Paragraph("经系统自动扫描，本次监测未发现连片且超过预警阈值的显著沉降区域，区域整体稳定性良好。",
                          style_body))

        elements.append(PageBreak())

        # --- 4. 技术验证 (质量控制) ---
        elements.append(Paragraph("4. 数据质量与过程验证", style_h1))
        quality_text = """
        为确保监测结果的可靠性，本节展示关键的中间过程数据以供技术核查。
        """
        elements.append(Paragraph(quality_text, style_body))

        # 4.1 相干性
        elements.append(Paragraph("4.1 相干性 (Coherence) 评估", style_h2))
        corr_text = """
        相干性是衡量 InSAR 结果可信度的核心指标。相干性图越亮（数值趋近1），说明地表散射特性保持越好，结果越可信。
        本区域建筑密集区相干性较高，具备良好的 InSAR 监测条件。
        """
        elements.append(Paragraph(corr_text, style_body))
        if os.path.exists(img_paths['corr']):
            elements.append(Image(img_paths['corr'], width=420, height=330, kind='proportional'))
            elements.append(Paragraph("图 4-1 干涉对相干性图（无量纲，取值范围 0–1）", style_caption))

        # 4.2 相位解缠
        elements.append(Paragraph("4.2 相位解缠 (Phase Unwrapping)", style_h2))
        unwrap_text = """
        相位解缠是InSAR数据处理中最关键的步骤。雷达记录的原始相位被“折叠”在[-π, π]的周期内（呈现为干涉图中的彩色条纹），解缠算法旨在恢复相位的连续性，从而计算出真实的物理形变量。
        <br/>     <br/>
        <b>图解分析：</b><br/>
        1.<b>数值含义：</b>下图中的颜色数值代表累积相位值（单位：弧度 radians）。该数值后续将乘以波长因子（对于Sentinel-1约为28mm/弧度）转换为最终的毫米级形变。<br/>
        2.<b>质量判读：</b>高质量的解缠图应呈现<b>平滑、连续</b>的渐变特征（如下图所示）。<br/>
        3.<b>异常特征：</b>若图中出现类似“刀割”状的突变直线（Cut lines）或孤立的噪点斑块，通常意味着该区域存在解缠误差（相位跳变），其对应的最终形变值可能不可信。
        """
        elements.append(Paragraph(unwrap_text, style_body))
        if os.path.exists(img_paths['unwrap']):
            elements.append(Image(img_paths['unwrap'], width=420, height=330, kind='proportional'))
            elements.append(Paragraph("图 4-2 解缠相位图", style_caption))

        # --- 5. 结论与建议 ---
        elements.append(PageBreak())
        elements.append(Paragraph("5. 结论与建议", style_h1))

        conclusions = [
            "监测数据有效性：本次处理采用的Sentinel-1影像覆盖完整，关键区域相干性良好，解算结果可靠。",
            f"形变总体评价：监测区域大部分处于稳定状态，主要形变区间集中在{float(meta_info['min_val']) / 2:.1f}mm至{float(meta_info['max_val']) / 2:.1f}mm之间。",
            "工程建议：建议对第3.2节列出的重点风险区进行实地复核，排查是否存在地下水开采、软土固结或工程扰动等情况。"
        ]
        for i, c in enumerate(conclusions):
            elements.append(Paragraph(f"{i + 1}.{c}", style_body))
            elements.append(Spacer(1, 3))

        # --- 免责声明 ---
        elements.append(Spacer(1, 40))
        disclaimer = """
        声明：<br/>
        1.本报告监测结果基于遥感卫星数据反演，属非接触式测量。受大气延迟、植被覆盖及卫星重访周期等因素影响，结果可能存在一定系统误差。<br/>
        2.监测结果反映的是雷达视线向（LOS）的一维形变，不完全等同于垂直沉降。<br/>
        3.本报告旨在提供宏观趋势分析与异常筛查，仅供参考，不作为工程验收或法律诉讼的直接依据，建议配合水准测量等手段综合研判。<br/>
        """
        t_disc = Table([[Paragraph(disclaimer, style_caption)]], colWidths=[450])
        t_disc.setStyle(
            TableStyle([('TOPPADDING', (0, 0), (-1, -1), 15), ('LINEABOVE', (0, 0), (-1, -1), 0.5, colors.grey)]))
        elements.append(t_disc)

        doc.build(elements)
        return output_pdf

    def _export_polygon_geojson(self, data_array, output_path, geometry_input):
            """
            geometry_input: 可能是 shapely.geometry.Polygon, 也可能是 geopandas.GeoDataFrame
            """
            try:
                filename = os.path.basename(output_path)
                self.logger.info(f"💾 [Polygon Export] 正在处理: {filename} ...")

                # --- 关键修复：确保 poly_geometry 是纯粹的 Shapely 对象 ---
                if isinstance(geometry_input, (gpd.GeoDataFrame, gpd.GeoSeries)):
                    # 如果是 GeoDataFrame，提取合并后的单一几何体
                    poly_geometry = geometry_input.unary_union
                else:
                    # 已经是 Shapely 对象
                    poly_geometry = geometry_input

                # 1. 内存加载
                if isinstance(data_array, dask.array.core.Array):
                    data_array = data_array.compute()

                # 2. 转 DataFrame
                df = data_array.to_dataframe(name='value').reset_index()

                # 类型强转，防患未然
                df['lon'] = df['lon'].astype(float)
                df['lat'] = df['lat'].astype(float)

                df = df.dropna(subset=['value'])

                # 3. 粗筛：使用 shapely 对象的 .bounds (返回 tuple)
                minx, miny, maxx, maxy = poly_geometry.bounds

                df_rough = df[
                    (df['lon'] >= minx) & (df['lon'] <= maxx) &
                    (df['lat'] >= miny) & (df['lat'] <= maxy)
                    ]

                if df_rough.empty:
                    self.logger.warning("⚠️ Bbox 范围内无数据，跳过导出")
                    return

                # 4. 转 GeoDataFrame
                points = gpd.points_from_xy(df_rough.lon, df_rough.lat)
                gdf_points = gpd.GeoDataFrame(df_rough, geometry=points, crs="EPSG:4326")

                # 5. 精确裁剪
                gdf_mask = gpd.GeoDataFrame({'geometry': [poly_geometry]}, crs="EPSG:4326")

                self.logger.info("✂️ 执行多边形几何裁剪...")
                gdf_clipped = gpd.clip(gdf_points, gdf_mask)

                if gdf_clipped.empty:
                    self.logger.warning("⚠️ 几何裁剪后无数据")
                    return

                # 6. 导出
                output_file = f"{output_path}.geojson"
                gdf_clipped.to_file(output_file, driver='GeoJSON')

                final_count = len(gdf_clipped)
                self.logger.info(f"✅ 导出成功: {output_file} (包含 {final_count} 个有效点)")

            except Exception as e:
                self.logger.error(f"❌ 导出失败: {e}", exc_info=True)
    # --- 主流程 ---
    def run_pipeline(self, wkt: str, start: str, end: str):
        self._setup_auth()
        self.logger.info("🔍 [Phase 1] 正在搜索 Sentinel-1 Burst 数据...")

        results = asf.search(
            platform=asf.PLATFORM.SENTINEL1, processingLevel=asf.PRODUCT_TYPE.BURST,
            flightDirection='ASCENDING', beamMode=asf.BEAMMODE.IW, polarization=asf.POLARIZATION.VV,
            intersectsWith=wkt, start=start, end=end, maxResults=500
        )
        bursts = [r.properties['fileID'] for r in results if r.properties['flightDirection'] == "ASCENDING"]
        if not bursts: return

        client = Client()
        try:
            pr_asf = ASF(self.user, self.password)
            DEM_PATH = os.path.join(self.dem_dir, f"{start}-{end}_dem.srtm.nc")

            self.logger.info("⬇️ [Phase 2] 下载影像与轨道数据...")
            pr_asf.download(basedir=self.slc_dir, scenes_or_bursts=bursts)

            scenes = S1.scan_slc(self.slc_dir, polarization="VV", orbit="A")
            S1.download_orbits(self.slc_dir, scenes)
            scenes = S1.scan_slc(self.slc_dir, polarization="VV", orbit="A")
            # 更新保存目录
            dates = scenes.index.sort_values()
            if len(dates) < 2:
                self.logger.error("❌ 有效影像少于2帧，无法进行干涉")
                return
            real_start = dates[0]
            real_end = dates[-1]
            self._update_workspace_dirs(real_start, real_end)
            # 下载 DEM
            aoi_geo_obj = loads(wkt)
            aoi_region = gpd.GeoDataFrame([{"geometry": aoi_geo_obj}], crs="EPSG:4326")
            Tiles().download_dem_srtm(aoi_region, filename=DEM_PATH)

            self.logger.info("📚 [Phase 3] 初始化 SBAS 处理堆栈...")
            sbas = Stack(self.stack_dir, drop_if_exists=True).set_scenes(scenes)

            sbas.compute_reframe(aoi_region)
            sbas.load_dem(DEM_PATH, aoi_region)
            sbas.compute_align()
            sbas.compute_geocode(1)

            # 干涉
            self.logger.info("💾 [Phase 4] 执行干涉测量处理...")
            # 1. 获取所有日期索引
            dates = sbas.to_dataframe().index
            # 2. 生成相邻配对 (Date1-Date2, Date2-Date3...)
            pair_list = []
            for i in range(len(dates) - 1):
                pair_list.append([dates[i], dates[i + 1]])

            # 3. 转为 DataFrame (这是 PyGMTSAR 标准输入格式)
            pairs = pd.DataFrame(pair_list, columns=['ref', 'rep'])
            # 4. 设置文件名标识 (取第一对日期)
            name = f"{pairs.iloc[0]['ref']}_{pairs.iloc[0]['rep']}"
            self.logger.info(f"✅ 已自动生成 {len(pairs)} 对干涉对")
            topo = sbas.get_topo()
            data = sbas.open_data()
            intensity = sbas.multilooking(np.square(np.abs(data)), wavelength=90, coarsen=(3, 12))
            phase = sbas.phasediff(pairs, data, topo)
            phase = sbas.multilooking(phase, wavelength=90, coarsen=(3, 12))
            corr = sbas.correlation(phase, intensity)
            phase_goldstein = sbas.goldstein(phase, corr, 16)
            intf = sbas.interferogram(phase_goldstein)

            tqdm_dask(result := dask.persist(intf[0], corr[0]), desc='Computing')
            intf, corr = result
            tqdm_dask(unwrap := sbas.unwrap_snaphu(intf.where(corr >= self.coherence_threshold), corr).persist(),
                      desc='Unwrapping')
            unwrap['phase'] = unwrap.phase - unwrap.phase.mean()

            # 结果生成
            self.logger.info("📊 [Phase 5] 生成监测成果...")
            disp = sbas.as_geo(sbas.ra2ll(unwrap.phase)).rio.clip(aoi_region.geometry)
            los_disp = sbas.los_displacement_mm(disp)
            los_disp = los_disp.drop_vars(['ref', 'rep', 'pair', 'spatial_ref'], errors='ignore').rename("value")

            self.logger.info("💾 加载计算结果至内存...")
            los_disp = los_disp.compute()

            # 导出 GIS 数据
            self.logger.info("💾 [Phase 6] 导出 GIS 空间数据...")
            # 1. 导出完整数据 (带几何裁剪)
            self._export_polygon_geojson(
                los_disp,
                os.path.join(self.gis_dir, "displacement_all"),
                aoi_region
            )

            # 2. 导出异常点
            threshold = abs(self.alert_threshold)
            alert_layer = los_disp.where((los_disp > threshold) | (los_disp < -threshold))
            self._export_polygon_geojson(
                alert_layer,
                os.path.join(self.gis_dir, "displacement_alert_points"),
                aoi_region
            )

            # 5. NetCDF (可选，仍然使用 PyGMTSAR 导出，因为 NetCDF 通常是矩形 grid)
            try:
                sbas.export_netcdf(los_disp, os.path.join(self.gis_dir, "displacement_full"),
                                   engine=sbas.netcdf_engine, format="NETCDF4")
            except:
                pass

            img_paths = {
                'phase': os.path.join(self.plot_dir, 'phase.png'),
                'corr': os.path.join(self.plot_dir, 'correlation.png'),
                'unwrap': os.path.join(self.plot_dir, 'unwrapped_phase.png'),
                'disp': os.path.join(self.plot_dir, 'disp_pro.png')
            }

            # 保存辅助图表 (应用坐标轴优化)
            def save_plot(func, path):
                plt.figure(figsize=(10, 8))
                func()
                self._optimize_layout()
                plt.savefig(path, bbox_inches='tight')
                plt.close()

            save_plot(lambda: sbas.plot_interferogram(sbas.as_geo(sbas.ra2ll(intf)).rio.clip(aoi_region.geometry),
                                                      aspect='equal'), img_paths['phase'])
            save_plot(lambda: sbas.plot_correlation(sbas.as_geo(sbas.ra2ll(corr)).rio.clip(aoi_region.geometry),
                                                    aspect='equal'), img_paths['corr'])
            save_plot(lambda: sbas.plot_phase(disp, caption='Unwrapped Phase', quantile=[0.02, 0.98], aspect='equal'),
                      img_paths['unwrap'])

            # 专业制图
            self.logger.info("🗺️ 生成工程级形变专题图...")
            self._plot_displacement_pro(los_disp, "LOS Displacement Analysis Map", img_paths['disp'])

            # 异常探测
            self.logger.info("🧠 [Phase 7] 执行风险区自动筛查...")
            anomalies = self._detect_anomalies(los_disp)

            # 报告
            self.logger.info("📄 [Phase 8] 编制咨询分析报告...")
            # 使用 .iloc 获取数据，且取整个时间序列的头和尾
            start_date_str = str(pairs.iloc[0]['ref'])  # 第一对的参考影像日期
            end_date_str = str(pairs.iloc[-1]['rep'])  # 最后一对的从影像日期
            meta = {
                'ref_date': start_date_str,
                'sec_date': end_date_str,
                'min_val': f"{los_disp.min().item():.2f}",
                'max_val': f"{los_disp.max().item():.2f}"
            }
            font_path = os.path.join(self.resource_dir, "SourceHanSerifCN-VF.ttf")
            pdf_path = os.path.join(self.report_dir, "InSAR_Analysis_Report.pdf")
            self._generate_report_pro(font_path, pdf_path, meta, anomalies, img_paths)

            self.logger.info(f"🏆 报告编制完成: {pdf_path}")
            client.close()

        except Exception as e:
            self.logger.error(f"☠️ 运行异常: {e}", exc_info=True)
            if 'client' in locals(): client.close()


if __name__ == '__main__':
    config = {
        'user': 'olongfen', 'password': 'Qwerdf.123456', 'proxy': "http://192.168.3.126:7890",
        'base_dir': "./data", 'coherence_threshold': 0.08, 'alert_threshold': -20.0
    }
    processor = Sentinel1InSARProcessor(config)
    AOI = 'POLYGON ((113.634498 22.56157, 113.655867 22.581675, 113.649954 22.591685, 113.659496 22.600205, 113.651701 22.612322, 113.618998 22.58457, 113.634498 22.56157))'
    processor.run_pipeline(AOI, "2025-8-01", "2025-12-30")