import datetime
import asf_search as asf
import os
import geopandas as gpd
from shapely.wkt import loads
from dask.distributed import Client
from shapely.geometry import box
import pandas as pd
from pygmtsar import ASF, S1, Tiles,Stack,tqdm_dask
import matplotlib.pyplot as plt
import numpy as np
import dask
import logging
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 确保路径正确，使用静态版 OTF / TTF
# from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle




ASF_USER = 'olongfen'  # 替换为您的 NASA 账号
ASF_PASS = 'Qwerdf.123456'  # 替换为您的 NASA 密码
PROXY_URL = "http://192.168.3.126:7890"

if PROXY_URL:
    os.environ['http_proxy'] = PROXY_URL
    os.environ['https_proxy'] = PROXY_URL
    print(f"🌐 网络代理已启用: {PROXY_URL}")

# --- 算法参数 ---
# 相干性阈值：Burst 边缘容易失相干，0.075 是经验值，0.15 较保守
COHERENCE_THRESHOLD = 0.08
# 年化沉降预警阈值 (mm/year)
YEARLY_VELOCITY_ALERT = -30.0
# 结果导出时的降采样倍数 (减小 GeoJSON 体积)
DOWNSAMPLE_FACTOR = 4

# --- 报告模板 (Markdown) ---
REPORT_TEMPLATE = """
# 🚀 城市沉降监测报告 (Burst模式)
**生成时间:** {{ gen_time }}
**监测区域 (AOI):** {{ aoi }}

## 1. 核心结论
* **监测周期:** {{ full_start }} 至 {{ full_end }}
* **最大累积沉降:** <span style="color:red">{{ max_cum }} mm</span>
* **区域稳定性:** {{ stable_pct }}% 的点位处于稳定状态。

## 2. 可视化分析
### 2.1 区域沉降分布直方图
![直方图](./images/histogram.png)

### 2.2 最大沉降点时序曲线 (坐标: {{ max_pt_coord }})
![时序图](./images/timeseries.png)

## 3. 分期详细数据表
| 监测期名 | 实际区间 | 天数 | 期间最大沉降 | 年化速率 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
{% for issue in issues %}
| **{{ issue.name }}** | {{ issue.real_range }} | {{ issue.days }} | {{ issue.max_delta }} mm | {{ issue.velocity }} mm/yr | {{ issue.status }} |
{% endfor %}
"""
# --- 结果输出路径 ---
BASE_DIR = os.path.abspath("./data")
IMG_DIR = os.path.join(BASE_DIR, "images")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
class InSARLogger:
    """日志管理类：同时输出到控制台和文件"""

    @staticmethod
    def setup():
        logger = logging.getLogger("InSAR_Burst_Pro")
        logger.setLevel(logging.INFO)
        logger.handlers = []  # 防止重复日志

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # 控制台处理器
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # 文件处理器
        if not os.path.exists(BASE_DIR): os.makedirs(BASE_DIR)
        fh = logging.FileHandler(os.path.join(BASE_DIR, 'process.log'), mode='a')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        return logger


logger = InSARLogger.setup()



FMT = "%Y-%m-%dT%H:%M:%SZ"



def download_bursts(wkt: str,start:str,end:str):
    """
    获取bursts
    :param wkt: wkt格式AOI
    :param start:  scene start time (%Y-%m-%dT%H:%M:%SZ)
    :param end: scene end time  (%Y-%m-%dT%H:%M:%SZ)
    :return: list[str]
    """
    ### search and download data
    netrc_path = os.path.expanduser("~/.netrc")
    # 无论是否存在，都强制重写一遍，确保正确
    with open(netrc_path, "w") as f:
        f.write(f"machine urs.earthdata.nasa.gov login {ASF_USER} password {ASF_PASS}")
    os.chmod(netrc_path, 0o600)
    results = asf.search(
        platform=asf.PLATFORM.SENTINEL1,
        processingLevel=asf.PRODUCT_TYPE.BURST,
        flightDirection='ASCENDING',
        beamMode=asf.BEAMMODE.IW,
        polarization=asf.POLARIZATION.VV,
        intersectsWith=wkt,
        start=start,
        end=end,
        maxResults=500
    )
    bursts = []
    for result in results:
        if result.properties['flightDirection'] == "ASCENDING":
            bursts.append(result.properties['fileID'])
    try:
        client = Client()
        data_dir = "data"
        satellite_dir = os.path.join(data_dir, "sentinel1")

        pr_asf = ASF(ASF_USER, ASF_PASS)
        DEM = os.path.join(satellite_dir, "dem.srtm.nc")
        pr_asf.download(basedir=satellite_dir, scenes_or_bursts=bursts)
        logger.info("📂 正在扫描本地 SLC 数据...")
        scenes = S1.scan_slc(satellite_dir, polarization="VV", orbit="A")
        logger.info(f"✅ 成功加载 {len(scenes)} 个场景")
        logger.info("🛰️ 正在下载精密轨道文件...")
        S1.download_orbits(satellite_dir,scenes)
        aoi_region = gpd.GeoDataFrame([{"geometry": loads(wkt)}], crs="EPSG:4326")
        logger.info("🌄 正在下载 SRTM DEM 数据...")
        Tiles().download_dem_srtm(aoi_region, filename=DEM)
        WORKDIR=os.path.join(data_dir,"sbac")
        logger.info("初始化 SBAS 堆栈")
        sbas = Stack(WORKDIR,drop_if_exists=True).set_scenes(scenes)
        sbas.plot_scenes()
        plt.savefig(f"{WORKDIR}/scene.png")

        logger.info("Reframe Scenes")
        sbas.compute_reframe(aoi_region)
        # 定义重新框定后的场景可视化
        logger.info("画图 Reframe Scenes")
        sbas.plot_scenes()
        plt.savefig(f"{WORKDIR}/reframe.scene.png")
        ## 加载 DEM
        logger.info("加载 DEM")
        sbas.load_dem(DEM, aoi_region)
        logger.info("画图 DEM Scenes")
        sbas.plot_scenes()
        plt.savefig(f"{WORKDIR}/dem.scnes.png")
        ## 计算对齐
        logger.info("Compute align")
        sbas.compute_align()

        ## 计算地形编码
        logger.info("Compute geocode")
        sbas.compute_geocode(1)

        sbas.plot_topo(quatile=[0.01, 0.99])
        plt.savefig(f"{WORKDIR}/1.topo.png")
        # sbas 对基线分析
        baseline_pairs = sbas.sbas_pairs()
        sbas.plot_baseline(baseline_pairs)
        plt.savefig(f"{WORKDIR}/2.baseline.png")

        sbas.plot_topo()
        plt.savefig('Topography in Radar Coordinates.png')

        ## Interfergram
        # for a pair of scenes only two interferograms can be produced
        # this one is selected for scenes sorted by the date in direct order
        pairs = [sbas.to_dataframe().index]
        name = "_".join(pairs[0])

        # load radar topography
        # 加载地形数据
        logger.info("Load Topo")
        topo = sbas.get_topo()
        # load Sentinel-1 data
        # 加载 Sentinel-1 数据
        logger.info("Load Data")
        data = sbas.open_data()
        # Gaussian filtering 90m cut-off wavelength with multilooking 3x12 on Sentinel-1 intensity
        # 计算多视处理后的强度图像
        logger.info("Compute Intensity")
        intensity = sbas.multilooking(np.square(np.abs(data)), wavelength=90, coarsen=(3, 12))
        # calculate phase difference with topography correction
        # 计算相位差并进行地形校正
        logger.info("Compute Phase Difference")
        phase = sbas.phasediff(pairs, data, topo)
        # Gaussian filtering 90m cut-off wavelength with multilooking
        # 多视处理后的相位图像
        logger.info("Compute Phase Multilooking")
        phase = sbas.multilooking(phase, wavelength=90, coarsen=(3, 12))
        # correlation on 3x12 multilooking data
        # 计算相关性图像
        logger.info("Compute Correlation")
        corr = sbas.correlation(phase, intensity)
        # Goldstein filter in 32 pixel patch size on square grid cells produced using 1:4 range multilooking
        #
        logger.info("Compute Goldstein Filter")
        phase_goldstein = sbas.goldstein(phase, corr, 16)
        # convert complex phase difference to interferogram
        # 生成干涉图
        logger.info("Compute Interferogram")
        intf = sbas.interferogram(phase_goldstein)
        # materialize for a single interferogram
        # 使用 Dask 进行并行计算
        logger.info("Compute Phase and Correlation")
        tqdm_dask(result := dask.persist(intf[0], corr[0]), desc='Compute Phase and Correlation')
        # unpack results
        intf, corr = result
        # SNAPHU unwrapping
        logger.info("SNAPHU Unwrapping")
        tqdm_dask(unwrap := sbas.unwrap_snaphu(intf.where(corr >= 0.075), corr).persist(),
                  desc='SNAPHU Unwrapping')
        # apply simplest detrending todo different detrend like gassion or regression
        unwrap['phase'] = unwrap.phase - unwrap.phase.mean()
        generate_result(pairs, sbas, intf, corr, aoi_region, unwrap, name)
        client.close()
    except Exception as e:
        print("报错拉",e)
def process_region(regions: list):
    """
    处理区间值问题
    :param rigions:
    :return:
    """
    data = []
    for i in range(len(regions)):
        left, right = regions[i].split(":")
        data.append(left)
        data.append(right)
    data = [float(x) for x in data]
    return sorted(set(data))
def plot_displacement(data, caption='LOS Displacement [mm]', color_list: list = None,
                      boundaries: list = None, levels=None, aspect=True, out=None):
    if 'stack' in data.dims and isinstance(data.coords['stack'].to_index(), pd.MultiIndex):
        data = data.unstack('stack')
    if not color_list:
        color_list = ["#EF311D", "#FF8127", "#FFE641", "#6CF642", "#0028F4"]
    if not levels:
        levels = ['D', 'd', 'o', 'u', 'U']
    if not boundaries:
        boundaries = [-np.inf, -30, -10, 10, 30, np.inf]
    ### 性能问题，将正负无穷换成10000
    tmp_boundaries = [-10000 if b == -np.inf else 10000 if b == np.inf else b for b in boundaries]
    # Plot the data with the custom color map
    cmap = mcolors.ListedColormap(color_list)
    norm = mcolors.BoundaryNorm(tmp_boundaries, cmap.N, clip=True)
    # 创建图
    fig, ax = plt.subplots()
    data.plot.imshow(cmap=cmap, norm=norm, add_colorbar=False)
    # 创建自定义图例
    legend_patches = [mpatches.Patch(color=cmap(norm(b1)), label=f'{levels[i]}: ({b1}, {b2})') for i, (b1, b2) in
                      enumerate(zip(boundaries[:-1], boundaries[1:]))]
    ax.legend(handles=legend_patches, title="Levels", loc='center left', bbox_to_anchor=(1, 0.5))
    # Set aspect if provided
    if aspect is not None:
        plt.gca().set_aspect(aspect)
    # ax.axis('off')
    plt.title(caption)
    # 展示图像
    if out:
        plt.savefig(out, dpi=300)
def generate_insar_report(font_path, output_pdf, reference, analysis, min_value, max_value, processing_flow_image,
                          interferogram_image, unwrapped_phase_image, coherence_image, deformation_field_image):
    """
    生成基于PyGMTSAR的InSAR分析报告。

    :param output_pdf: 输出PDF文件路径
    :param processing_flow_image: 处理流程图路径
    :param interferogram_image: 干涉图路径
    :param unwrapped_phase_image: Unwrapped Phase图路径
    :param coherence_image: 相关图路径
    :param deformation_field_image: 形变场图路径
    """
    # 注册SimSun字体
    # pdfmetrics.registerFont(TTFont('SimSun', font_path))
    pdfmetrics.registerFont(TTFont('SourceHanSerifCN', font_path))

    styles = getSampleStyleSheet()
    styles["Normal"].fontName = "SourceHanSerifCN"
    styles["Normal"].font = 16

    styles["Heading1"].fontName = "SourceHanSerifCN"
    styles["Heading2"].fontName = "SourceHanSerifCN"
    styles["Heading3"].fontName = "SourceHanSerifCN"
    styles["Title"].fontName = "SourceHanSerifCN"
    # 创建PDF文档
    doc = SimpleDocTemplate(output_pdf)
    elements = []
    caption_text = "<para align='center'>{}</para>"
    ################################# 添加引言###############################
    introduction = Paragraph("1. 引言", styles["Heading1"])
    introduction_text = Paragraph("""
    合成孔径雷达干涉测量（InSAR）是一种利用卫星雷达数据监测地表形变的重要技术。
    PyGMTSAR 是一个基于 GMTSAR 的 Python 处理框架，能够高效完成 InSAR 数据预处理、干涉图生成、时间序列分析和地表形变提取。<br></br>
    <br></br>
    本报告基于 PyGMTSAR 进行数据处理和分析，主要目标是：<br></br>
    - 处理 SAR 数据，生成干涉图<br></br>
    - 提取地表形变信息<br></br>
    - 进行误差分析和结果可视化<br></br>
    """, styles["Heading3"])
    elements.append(introduction)
    elements.append(introduction_text)
    elements.append(Spacer(1, 20))

    ############################## 添加数据处理 ####################################
    data_processing = Paragraph("2. 数据处理", styles["Heading1"])
    data_source = Paragraph("2.1 数据来源", styles["Heading2"])
    data_source_text = Paragraph(f"""
    本次研究使用 Sentinel-1 卫星雷达数据，数据集包括：<br></br>
    - 参考影像（Reference Image）：{reference}<br></br>
    - 从影像（Slave Image）：{analysis}<br></br>
    """, styles["Heading3"])

    preprocessing = Paragraph("2.2 预处理", styles["Heading2"])
    preprocessing_text = Paragraph("""
    使用 PyGMTSAR 进行数据预处理，包括：<br></br>
    1. 数据解压与格式转换<br></br>
    2. 轨道校正与配准<br></br>
    3. 干涉图生成<br></br>
    4. 相干性计算与滤波<br></br>
    5. 去除地形相位（DEM 校正）<br></br>
    """, styles["Heading3"])

    processing_flow = Paragraph("2.3 处理流程", styles["Heading2"])
    processing_flow_text = Paragraph("""处理流程图如下：""", styles["Heading3"])
    processing_flow_image = Image(processing_flow_image, width=400, height=200)
    elements.append(data_processing)
    elements.append(data_source)
    elements.append(data_source_text)
    elements.append(preprocessing)
    elements.append(preprocessing_text)
    elements.append(processing_flow)
    elements.append(processing_flow_text)
    elements.append(processing_flow_image)
    elements.append(Paragraph(caption_text.format("1. 处理流程图"), styles["Normal"]))
    elements.append(Spacer(1, 20))

    ###############################################添加结果分析###########################
    results_analysis = Paragraph("3. 结果分析", styles["Heading1"])
    interferogram_generation = Paragraph("3.1 结果图生成", styles["Heading2"])
    interferogram_image = Image(interferogram_image, width=400, height=200)
    unwrapped_phase_image = Image(unwrapped_phase_image, width=400, height=200)
    coherence_image = Image(coherence_image, width=400, height=200)

    deformation_field_extraction = Paragraph("3.2 形变场提取", styles["Heading2"])
    deformation_field_text = Paragraph(f"""
    通过分析结果，提取地表形变（mm），主要结果：<br></br>
    最大值：{max_value}<br></br>
    最小值：{min_value}<br></br>
    """, styles["Heading3"])

    error_analysis = Paragraph("3.3 误差分析", styles["Heading2"])
    error_analysis_text = Paragraph("""
    通过相干性分析，滤除了低相干区域，提高精度。<br></br>
    - 去噪方法：高斯滤波<br></br>
    """, styles["Heading3"])
    elements.append(results_analysis)
    elements.append(interferogram_generation)
    elements.append(interferogram_image)
    elements.append(Paragraph(caption_text.format("2. 干涉图"), styles["Normal"]))
    elements.append(Spacer(1, 20))
    elements.append(unwrapped_phase_image)
    elements.append(Paragraph(caption_text.format("3. 解扰图"), styles["Normal"]))
    elements.append(Spacer(1, 20))
    elements.append(coherence_image)
    elements.append(Paragraph(caption_text.format("4. 相关图"), styles["Normal"]))
    elements.append(Spacer(1, 20))
    elements.append(deformation_field_extraction)
    elements.append(deformation_field_text)
    elements.append(error_analysis)
    elements.append(error_analysis_text)
    elements.append(Spacer(1, 20))

    ######################## 添加可视化 #####################################
    visualization = Paragraph("4. 可视化", styles["Heading1"])
    visualization_text = Paragraph("""
    使用 PyGMT 进行数据可视化，生成形变场：<br></br>
    """, styles["Heading3"])
    deformation_field_image = Image(deformation_field_image, width=400, height=200)

    ######################### 添加结论###################################
    conclusion = Paragraph("5. 结论", styles["Heading1"])
    conclusion_text = Paragraph(f"""
    本次 InSAR 分析利用 PyGMTSAR 处理 Sentinel-1 数据，成功提取地表形变信息，主要结论如下：<br></br>
    - 研究区域地表形变变化范围 [{min_value}，{max_value}] mm<br></br><br></br>

    本报告提供了完整的 PyGMTSAR 处理流程，为后续 InSAR 研究提供了参考。<br></br>
    """, styles["Heading3"])

    elements.append(visualization)
    elements.append(visualization_text)
    elements.append(deformation_field_image)
    elements.append(Paragraph(caption_text.format("5. 沉降图"), styles["Normal"]))
    elements.append(Spacer(1, 20))
    elements.append(conclusion)
    elements.append(conclusion_text)
    # 生成PDF
    doc.build(elements)
    result = f"{reference}和{analysis}两期影像结果： 研究区域地表形变变化范围 [{min_value}，{max_value}]"
    return result
def generate_result( pairs, sbas, intf, corr, aoi_region, unwrap, name, aoi=None, project_type=None):
    """
    生成结果
    :param pairs:
    :param sbas:
    :param intf:
    :param corr:
    :param aoi_region:
    :param unwrap:
    :param name:
    :param aoi:
    :param project_type:
    :return:
    """
    disp = sbas.as_geo(sbas.ra2ll(unwrap.phase)).rio.clip(aoi_region.geometry)
    los_disp_mm_ll = sbas.los_displacement_mm(disp)
    ## 删除多余coord
    los_disp_mm_ll = los_disp_mm_ll.drop_vars(['ref', 'rep', 'pair', 'spatial_ref']).rename("value")
    sbas.plot_interferogram(sbas.as_geo(sbas.ra2ll(intf)).rio.clip(aoi_region.geometry), aspect='equal')
    plt.savefig(f'{OUTPUT_DIR}/phase.png')
    sbas.plot_correlation(sbas.as_geo(sbas.ra2ll(corr)).rio.clip(aoi_region.geometry), aspect='equal')
    plt.savefig(f'{OUTPUT_DIR}/correlation.png')
    sbas.plot_phase(disp, caption='Unwrapped Phase [rad]', quantile=[0.02, 0.98], aspect='equal')
    plt.savefig(f'{OUTPUT_DIR}/unwrapped_phase.png')
    cms = [
        {"color":'#EF311D','level':'D','region':'-inf:-30',},
        {"color":'#FF8127','level':'d','region':'-30:-10',},
        {"color":'#FFE641','level':'o','region':'-10:10',},
        {"color":'#6CF642','level':'u','region':'10:30',},
        {"color":'#0028F4','level':'U','region':'30:inf',}
    ]
    color_list = None
    boundaries = None
    levels = None
    if cms:
        color_list = [cm['color'] for cm in cms]
        # todo process bouonds
        boundaries = [cm['region'] for cm in cms]
        boundaries = process_region(boundaries)
        levels = [cm['level'] for cm in cms]
    plot_displacement(los_disp_mm_ll, color_list=color_list, boundaries=boundaries, levels=levels,
                      out=f'{OUTPUT_DIR}/disp.png')
    sbas.export_geojson(los_disp_mm_ll.where((los_disp_mm_ll > 15) | (los_disp_mm_ll < -15)),
                        f"{OUTPUT_DIR}/output")
    sbas.export_netcdf(los_disp_mm_ll, f"{OUTPUT_DIR}/output", engine=sbas.netcdf_engine, format="NETCDF4")
    ### statics for area
    area = aoi_region.set_crs(4326).to_crs(epsg=32649).area[0]
    # 使用 xarray 的 .plot() 方法绘制直方图
    plt.figure(figsize=(8, 6))
    h = los_disp_mm_ll.plot.hist(bins=5, edgecolor='black', rwidth=0.8)
    # 添加标题和标签
    plt.title('Histogram of los')
    plt.xlabel('Value')
    plt.ylabel('Frequency')
    # 保存图像
    plt.savefig(f"{OUTPUT_DIR}/hist.png")
    min_value = f"{los_disp_mm_ll.min().values:0.2f}"
    max_value = f"{los_disp_mm_ll.max().values:0.2f}"
    FONTPATH = os.path.join(BASE_DIR, "resource", "SourceHanSerifCN-VF.ttf")
    PROCESS_IMG = os.path.join(BASE_DIR, "resource", "pipline.jpg")
    refrence = pairs[0][0]
    analysis = pairs[0][1]
    result = generate_insar_report(
        font_path=FONTPATH,
        output_pdf=f"{OUTPUT_DIR}/report.pdf",
        reference=f"{refrence}",
        analysis=f"{analysis}",
        min_value=min_value,
        max_value=max_value,
        processing_flow_image=f"{PROCESS_IMG}",
        interferogram_image=f'{OUTPUT_DIR}/phase.png',
        unwrapped_phase_image=f'{OUTPUT_DIR}/unwrapped_phase.png',
        coherence_image=f'{OUTPUT_DIR}/correlation.png',
        deformation_field_image=f'{OUTPUT_DIR}/disp.png'
    )
if __name__ == '__main__':
    AOI = [113.6328, 22.562, 113.6481, 22.6256]
    wkt = box(*AOI).wkt
    print(wkt)
    start="2025-12-01"
    end="2025-12-30"
    start_dt = pd.to_datetime(start) - datetime.timedelta(days=12)
    end_dt = pd.to_datetime(end) + datetime.timedelta(days=12)
    min_str, max_str = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    res = download_bursts(wkt, start, end)
