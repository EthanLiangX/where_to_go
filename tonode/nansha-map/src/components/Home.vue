<template>
  <div class="home">
    <el-select
        v-model="selectValue"
        placeholder="Select"
        @change="handleChangeTile"
        value-key="value"
    >
      <el-option
          v-for="item in selectOptions"
          :key="item.value"
          :label="item.label"
          :value="item"
          style="height: auto; margin-bottom: 10px; text-align: center"
      >
        <div class="time">{{ item.label }}</div>
        <el-image
            style="width: 100px; height: 100px"
            :src="item.value"
            fit="fill"
        />
      </el-option>
    </el-select>

    <div id="map"></div>

    <el-dialog
        v-model="dialogVisible"
        title="现场详细信息"
        width="600px"
        destroy-on-close
        append-to-body
    >
      <div class="dialog-content">
        <h3>{{ currentFeatureName }}</h3>
        <p v-if="currentFeatureDesc" class="desc">{{ currentFeatureDesc }}</p>

        <el-image
            class="dialog-image"
            :src="currentPhotoUrl"
            :preview-src-list="[currentPhotoUrl]"
            fit="contain"
        >
          <template #error>
            <div class="image-slot">
              <el-icon><Picture /></el-icon>
              <span>暂无现场照片</span>
            </div>
          </template>
          <template #placeholder>
            <div class="image-slot">加载中...</div>
          </template>
        </el-image>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { Picture } from "@element-plus/icons-vue"; // Element Plus 图标
import http from "../utils/http"; // 你的 HTTP 工具

// --- OpenLayers CSS ---
import "ol/ol.css";

// --- OpenLayers 核心模块 ---
import Map from "ol/Map";
import View from "ol/View";

// --- 图层与数据源 ---
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import OSM from "ol/source/OSM";
import XYZ from "ol/source/XYZ";
import VectorSource from "ol/source/Vector";

// --- 几何与格式 ---
import GeoJSON from "ol/format/GeoJSON";
import Polygon from "ol/geom/Polygon";
import MultiPolygon from "ol/geom/MultiPolygon";

// --- 坐标转换 ---
import { fromLonLat } from "ol/proj";

// --- 样式 ---
import { Style, Stroke, Fill, Text, Icon } from "ol/style";

// --- 类型 ---
import type { FeatureLike } from "ol/Feature";

// ================= 类型定义 =================

interface ImageOption {
  label: string;
  value: string; // 缩略图地址
  mbtilesPath: string; // 瓦片服务地址
  issue: string;
  [key: string]: any;
}

// ================= 状态变量 =================

const selectValue = ref<ImageOption | null>(null);
const selectOptions = ref<ImageOption[]>([]);

// 地图实例与图层
const map = ref<Map | null>(null);
const xyzLayer = ref<TileLayer<XYZ> | null>(null);
const geojsonLayer = ref<VectorLayer<VectorSource> | null>(null);

// URLs
const tileUrl = ref<string>("");
const geojsonUrl = ref<string>("https://tb-1256849727.cos.ap-beijing.myqcloud.com/NANSHA/track_P.geojson");

// 弹窗状态
const dialogVisible = ref(false);
const currentFeatureName = ref("");
const currentFeatureDesc = ref("");
const currentPhotoUrl = ref("");

// ================= 核心逻辑 =================

// 1. 获取下拉列表数据
const fetchData = async () => {
  try {
    const res: any = await http.get("/api/img/getImageryList");
    if (res.data && res.data.list) {
      selectOptions.value = res.data.list.map((item: any) : ImageOption => ({
        label: item.issue.split("T")[0].replace(/-/g, ""),
        value: item.thumbPath,
        mbtilesPath: item.mbtilesPath,
        issue: item.issue,
        ...item,
      }));

      // 数据加载完成后初始化地图
      initMap();
    }
  } catch (e) {
    console.error("获取列表失败", e);
    initMap(); // 即使失败也初始化地图
  }
};

// 2. 获取瓦片 Tile URL
const fetchTileData = async () => {
  if (!selectValue.value) return;
  try {
    const tileRes: any = await http.get(selectValue.value.mbtilesPath);
    if (tileRes && tileRes.tiles) {
      tileUrl.value = tileRes.tiles[0];
    }
  } catch (e) {
    console.error("获取瓦片地址失败", e);
  }
};

// 3. 样式生成函数 (包含：红框、文字、相机图标)
const geoJsonStyleFunction = (feature: FeatureLike): Style[] => {
  const styles: Style[] = [];
  const nameVal = feature.get("name"); // 假设 GeoJSON 属性里有 name
  const labelText = nameVal ? String(nameVal) : "";

  // A. 基础多边形样式 (红框 + 半透明红底 + 文字)
  styles.push(new Style({
    stroke: new Stroke({
      color: "#ff0000",
      width: 3,
    }),
    fill: new Fill({
      color: "rgba(255,0,0,0.1)",
    }),
    text: new Text({
      text: labelText,
      font: 'bold 14px "Microsoft YaHei", sans-serif',
      fill: new Fill({ color: '#333' }),
      stroke: new Stroke({ color: '#fff', width: 3 }),
      offsetY: -25, // 文字向上偏移，避开图标
      overflow: true,
    }),

  }));

  // B. 中心图标样式
  const geometry = feature.getGeometry();
  if (geometry) {
    const type = geometry.getType();
    let pointGeometry = null;

    // 计算中心点 (getInteriorPoint 保证点在多边形内部)
    if (type === 'Polygon') {
      pointGeometry = (geometry as Polygon).getInteriorPoint();
    } else if (type === 'MultiPolygon') {
      // 如果是 MultiPolygon，取最大面积多边形的中心，或者简单取第一个
      pointGeometry = (geometry as MultiPolygon).getPolygon(0).getInteriorPoint();
    }

    if (pointGeometry) {
      styles.push(new Style({
        geometry: pointGeometry,
        image: new Icon({
          // 这里使用一个免费的在线相机图标，实际项目中建议 import 本地图片
          // 例如: src: new URL('../assets/camera.png', import.meta.url).href,
          src: 'https://cdn-icons-png.flaticon.com/512/3687/3687416.png',
          scale: 0.06, // 根据图片大小调整
          anchor: [0.5, 0.5],
        }),
        zIndex: 100 // 确保图标在最上层
      }));
    }
  }

  return styles;
};

// 4. 初始化地图
const initMap = () => {
  const target = document.getElementById("map");
  if (!target) return;

  map.value = new Map({
    target: "map",
    layers: [
      new TileLayer({
        source: new OSM(),
        zIndex: 0, // 底图层级最低
      }),
    ],
    view: new View({
      center: fromLonLat([113.640418, 22.616928]),
      zoom: 13,
    }),
  });

  // 添加交互事件
  initInteractions();

  // 默认选中第一项
  if (selectOptions.value.length > 0) {
    selectValue.value = selectOptions.value[0];
    mapAddLayer(); // 加载影像
  }

  // 加载 GeoJSON
  mapAddGeoJsonLayer();
};

// 5. 初始化交互 (点击与鼠标样式)
const initInteractions = () => {
  if (!map.value) return;

  // 鼠标移动变成小手
  map.value.on("pointermove", (evt) => {
    if (!map.value) return;
    const hit = map.value.hasFeatureAtPixel(evt.pixel);
    map.value.getTargetElement().style.cursor = hit ? "pointer" : "";
  });

  // 点击事件
  map.value.on("singleclick", (evt) => {
    if (!map.value) return;

    // 获取点击处的 Feature
    const feature = map.value.forEachFeatureAtPixel(evt.pixel, (feat) => feat);

    if (feature) {
      const props = feature.getProperties();
      console.log("Feature clicked:", props);

      // 填充弹窗数据
      currentFeatureName.value = props.name || "未命名区域";
      currentFeatureDesc.value = props.description || ""; // 假设有描述字段

      // 假设 GeoJSON 属性中有 photo 字段，如果没有则使用演示图片
      // props.photoUrl 是你 GeoJSON里的字段名
      currentPhotoUrl.value = props.photoUrl || "https://fuss10.elemecdn.com/a/3f/3302e58f9a181d2509f3dc0fa68b0jpeg.jpeg";

      dialogVisible.value = true;
    }
  });
};

// 6. 添加影像图层 (XYZ)
const mapAddLayer = async () => {
  await fetchTileData(); // 获取 url

  if (!map.value || !tileUrl.value) return;

  if (xyzLayer.value) {
    map.value.removeLayer(xyzLayer.value);
  }

  xyzLayer.value = new TileLayer({
    zIndex: 10, // 影像层级：中
    source: new XYZ({
      url: tileUrl.value,
    }),
  });

  map.value.addLayer(xyzLayer.value);
};

// 7. 添加 GeoJSON 图层
const mapAddGeoJsonLayer = () => {
  if (!map.value) return;

  if (geojsonLayer.value) {
    map.value.removeLayer(geojsonLayer.value);
  }

  const source = new VectorSource({
    url: geojsonUrl.value,
    format: new GeoJSON({
      dataProjection: "EPSG:4326",
      featureProjection: "EPSG:3857",
    }),
  });

  geojsonLayer.value = new VectorLayer({
    source: source,
    zIndex: 999999, // 矢量层级：最高 (确保覆盖在影像上)
    style: geoJsonStyleFunction, // 使用自定义样式函数
  });

  map.value.addLayer(geojsonLayer.value);

  // 加载完成后自动缩放
  source.once("change", () => {
    if (source.getState() === "ready") {
      const extent = source.getExtent();
      if (extent && isFinite(extent[0])) {
        map.value?.getView().fit(extent, {
          padding: [50, 50, 50, 50],
          duration: 500,
          maxZoom: 18,
        });
      }
    }
  });
};

// 下拉框改变事件
const handleChangeTile = () => {
  if (selectValue.value) {
    mapAddLayer();
  }
};

onMounted(fetchData);
</script>

<style>
/* 确保页面高度撑满 */
html, body, #app {
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  overflow: hidden;
}

.home {
  position: relative;
  width: 100%;
  height: 100%;
}

#map {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  background-color: #f0f0f0; /* 添加背景色防止加载时白屏 */
}

/* 下拉框定位 */
.home .el-select {
  position: absolute;
  top: 30px;
  left: 30px;
  z-index: 100; /* 确保在地图之上 */
  width: 260px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  border-radius: 4px;
}

/* OpenLayers 控件隐藏 */
.ol-attribution, .ol-zoom {
  display: none;
}

/* 弹窗样式微调 */
.dialog-content {
  text-align: center;
}
.dialog-image {
  width: 100%;
  height: 400px;
  background-color: #f5f7fa;
  border-radius: 4px;
}
.image-slot {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  width: 100%;
  height: 100%;
  color: #909399;
  font-size: 14px;
}
.desc {
  color: #666;
  margin-bottom: 15px;
  text-align: left;
}
</style>