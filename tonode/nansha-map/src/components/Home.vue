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

    <div
        class="map-compass"
        ref="compassRef"
        @mousedown.prevent="startCompassDrag"
        title="点击复位，拖拽旋转"
    >
      <div class="compass-ring" :style="{ transform: `rotate(${rotationValue}rad)` }">
        <div class="compass-north">N</div>
        <el-icon class="compass-arrow"><Top /></el-icon>
      </div>
    </div>

    <el-dialog
        v-model="dialogVisible"
        :title="currentFeatureName"
        width="500px"
        destroy-on-close
        append-to-body
    >
      <div class="dialog-content">
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
        </el-image>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, shallowRef, onUnmounted } from "vue";
import { Picture, Top } from "@element-plus/icons-vue";
import http from "../utils/http";

import "ol/ol.css";
import Map from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import OSM from "ol/source/OSM";
import XYZ from "ol/source/XYZ";
import VectorSource from "ol/source/Vector";
import GeoJSON from "ol/format/GeoJSON";
import Polygon from "ol/geom/Polygon";
import MultiPolygon from "ol/geom/MultiPolygon";
import { fromLonLat } from "ol/proj";
import { Style, Stroke, Fill, Text, Icon } from "ol/style";
import type { FeatureLike } from "ol/Feature";

// 交互模块
import { DragRotate, defaults as defaultInteractions } from 'ol/interaction';
import { shiftKeyOnly } from 'ol/events/condition';

// 类型定义
interface ImageOption {
  label: string;
  value: string;
  mbtilesPath: string;
  issue: string;
  [key: string]: any;
}

const selectValue = ref<ImageOption | null>(null);
const selectOptions = ref<ImageOption[]>([]);

const map = shallowRef<Map | null>(null);
const xyzLayer = shallowRef<TileLayer<XYZ> | null>(null);
const geojsonLayer = shallowRef<VectorLayer<VectorSource> | null>(null);

const tileUrl = ref<string>("");
const geojsonUrl = ref<string>("https://tb-1256849727.cos.ap-beijing.myqcloud.com/NANSHA/track_P.geojson");

const dialogVisible = ref(false);
const currentFeatureName = ref("");
const currentFeatureDesc = ref("");
const currentPhotoUrl = ref("");

// 旋转角度 (弧度)
const rotationValue = ref(0);

// 指南针 DOM 引用
const compassRef = ref<HTMLElement | null>(null);
// 标记是否发生过拖拽（用于区分点击和拖拽）
let isDraggingCompass = false;

// --- 业务逻辑 ---

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
      initMap();
    }
  } catch (e) {
    console.error("获取列表失败", e);
    initMap();
  }
};

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

const geoJsonStyleFunction = (feature: FeatureLike): Style[] => {
  const styles: Style[] = [];
  const nameVal = feature.get("name");
  const labelText = nameVal ? String(nameVal) : "";

  styles.push(new Style({
    stroke: new Stroke({ color: "#ff0000", width: 3 }),
    fill: new Fill({ color: "rgba(255,0,0,0.1)" }),
    text: new Text({
      text: labelText,
      font: 'bold 14px "Microsoft YaHei", sans-serif',
      fill: new Fill({ color: '#333' }),
      stroke: new Stroke({ color: '#fff', width: 3 }),
      offsetY: -25,
      overflow: true,
    })
  }));

  const geometry = feature.getGeometry();
  if (geometry) {
    const type = geometry.getType();
    let pointGeometry = null;
    if (type === 'Polygon') {
      pointGeometry = (geometry as Polygon).getInteriorPoint();
    } else if (type === 'MultiPolygon') {
      pointGeometry = (geometry as MultiPolygon).getPolygon(0).getInteriorPoint();
    }
    if (pointGeometry) {
      styles.push(new Style({
        geometry: pointGeometry,
        image: new Icon({
          src: 'https://cdn-icons-png.flaticon.com/512/3687/3687416.png',
          scale: 0.06,
          anchor: [0.5, 0.5],
        }),
        zIndex: 100
      }));
    }
  }
  return styles;
};

const initMap = () => {
  const target = document.getElementById("map");
  if (!target) return;

  const view = new View({
    center: fromLonLat([113.640418, 22.616928]),
    zoom: 13,
    rotation: 0,
  });

  map.value = new Map({
    target: "map",
    interactions: defaultInteractions({
      shiftDragZoom: false, // 禁用选框放大
    }).extend([
      new DragRotate({ condition: shiftKeyOnly }), // Shift+拖拽旋转
    ]),
    layers: [
      new TileLayer({
        source: new OSM(),
        zIndex: 0,
      }),
    ],
    view: view,
  });

  view.on("change:rotation", () => {
    rotationValue.value = view.getRotation();
  });

  initInteractions();

  if (selectOptions.value.length > 0) {
    selectValue.value = selectOptions.value[0]!;
    mapAddLayer();
  }

  mapAddGeoJsonLayer();
};

const initInteractions = () => {
  if (!map.value) return;

  map.value.on("pointermove", (evt) => {
    if (!map.value) return;
    const hit = map.value.hasFeatureAtPixel(evt.pixel);
    map.value.getTargetElement().style.cursor = hit ? "pointer" : "";
  });

  map.value.on("singleclick", (evt) => {
    if (!map.value) return;
    const feature = map.value.forEachFeatureAtPixel(evt.pixel, (feat) => feat);

    if (feature) {
      const props = feature.getProperties();

      currentFeatureName.value = props.name || "未命名区域";
      currentFeatureDesc.value = props.description || "";
      currentPhotoUrl.value = props.img || "https://fuss10.elemecdn.com/a/3f/3302e58f9a181d2509f3dc0fa68b0jpeg.jpeg";

      dialogVisible.value = true;
    }
  });
};

const mapAddLayer = async () => {
  await fetchTileData();
  if (!map.value || !tileUrl.value) return;

  if (xyzLayer.value) {
    map.value.removeLayer(xyzLayer.value);
  }

  xyzLayer.value = new TileLayer({
    zIndex: 10,
    source: new XYZ({
      url: tileUrl.value,
      maxZoom: 18,
    }),
  });

  map.value.addLayer(xyzLayer.value);
};

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
    zIndex: 999999,
    style: geoJsonStyleFunction,
  });
  map.value.addLayer(geojsonLayer.value);
  source.once("change", () => {
    if (source.getState() === "ready") {
      const extent = source.getExtent();
      if (extent && extent.length >= 4 && isFinite(extent[0]!)) {
        map.value?.getView().fit(extent, {
          padding: [50, 50, 50, 50],
          duration: 500,
          maxZoom: 18,
        });
      }
    }
  });
};

const handleChangeTile = () => {
  if (selectValue.value) {
    mapAddLayer();
  }
};

// ==========================================
// ⭐ 指南针交互逻辑 (核心部分)
// ==========================================

// 1. 开始拖拽
const startCompassDrag = (e: MouseEvent) => {
  isDraggingCompass = false; // 重置标记
  // 绑定全局事件
  window.addEventListener('mousemove', onCompassRotate);
  window.addEventListener('mouseup', stopCompassDrag);
  console.log(e)
};

// 2. 拖拽中计算角度
const onCompassRotate = (e: MouseEvent) => {
  if (!compassRef.value || !map.value) return;

  isDraggingCompass = true; // 标记为正在拖拽

  // 获取指南针中心点
  const rect = compassRef.value.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;

  // 计算鼠标相对于中心的角度
  const deltaX = e.clientX - centerX;
  const deltaY = e.clientY - centerY;

  // Math.atan2 返回的是与 X 轴正向的夹角 (-PI 到 PI)
  // 而我们希望 Y 轴负向 (正上方) 为 0，且顺时针为正
  // 需要加上 PI/2 的偏移量
  const rotation = Math.atan2(deltaY, deltaX) + Math.PI / 2;

  // 设置地图 View 的旋转
  map.value.getView().setRotation(rotation);
};

// 3. 结束拖拽
const stopCompassDrag = () => {
  window.removeEventListener('mousemove', onCompassRotate);
  window.removeEventListener('mouseup', stopCompassDrag);

  // 如果没有发生拖拽 (仅仅是点击)，则执行复位逻辑
  if (!isDraggingCompass) {
    resetNorth();
  }
};

// 4. 复位正北逻辑
const resetNorth = () => {
  if (!map.value) return;
  const view = map.value.getView();
  if (view.getRotation() !== 0) {
    view.animate({
      rotation: 0,
      duration: 500,
      easing: (t) => t * (2 - t), // 简单的缓动效果
    });
  }
};

// 组件卸载时清理事件，防止内存泄漏
onUnmounted(() => {
  window.removeEventListener('mousemove', onCompassRotate);
  window.removeEventListener('mouseup', stopCompassDrag);
});

onMounted(fetchData);
</script>

<style>
/* ... 全局布局保持不变 ... */
html, body, #app, .home {
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  overflow: hidden;
}
#map {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  background-color: #f0f0f0;
}
.home .el-select {
  position: absolute;
  top: 30px;
  left: 30px;
  z-index: 100;
  width: 260px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}
.ol-attribution, .ol-zoom, .ol-rotate {
  display: none;
}

/* 指南针样式 */
.map-compass {
  position: absolute;
  top: 30px;
  right: 30px;
  width: 50px;
  height: 50px;
  background-color: white;
  border-radius: 50%;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  z-index: 100;
  cursor: grab; /* 鼠标手势 */
  display: flex;
  align-items: center;
  justify-content: center;
  user-select: none; /* 防止拖拽时选中文字 */
}

/* 拖拽时改变鼠标样式 */
.map-compass:active {
  cursor: grabbing;
}

.map-compass:hover {
  background-color: #f9f9f9;
  transform: scale(1.05);
}

.compass-ring {
  width: 100%;
  height: 100%;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none; /* 让鼠标事件穿透到父级 .map-compass */
}

.compass-north {
  position: absolute;
  top: 2px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 10px;
  font-weight: bold;
  color: #ff4d4f;
  z-index: 2;
}

.compass-arrow {
  font-size: 30px;
  color: #333;
}
.compass-arrow svg path {
  fill: #2c3e50;
}

/* 弹窗样式 */
.dialog-content { text-align: center; }
.dialog-image { width: 100%; height: 400px; background: #f5f7fa; }
.image-slot { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #909399; }
</style>