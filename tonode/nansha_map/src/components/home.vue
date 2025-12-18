<template>
  <div class="home">
    <el-select
        v-model="selectValue"
        placeholder="Select"
        @change="handleChangeTile"
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
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import http from "../utils/http";

import "ol/ol.css";
import { Map, View } from "ol";
import TileLayer from "ol/layer/Tile";
import OSM from "ol/source/OSM";
import XYZ from "ol/source/XYZ";
import { fromLonLat } from "ol/proj";

import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import GeoJSON from "ol/format/GeoJSON";
// 1. 引入 Text 样式组件
import { Style, Stroke, Fill, Text } from "ol/style";

const selectValue = ref(null);
const selectOptions = ref([]);

const map = ref(null);
const xyzLayer = ref(null);
const geojsonLayer = ref(null);

const tileGetUrl = ref("");
const tileUrl = ref("");

// GeoJSON 地址
const geojsonUrl = ref("https://tb-1256849727.cos.ap-beijing.myqcloud.com/NANSHA/track_P.geojson");

const fetchData = async () => {
  try {
    const res = await http.get("/api/img/getImageryList");
    if (res.data && res.data.list) {
      selectOptions.value = res.data.list.map((item) => ({
        label: item.issue.split("T")[0].replace(/-/g, ""),
        value: item.thumbPath,
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
  if (!tileGetUrl.value) return;
  const tileRes = await http.get(tileGetUrl.value);
  tileUrl.value = tileRes.tiles[0];
};

const initMap = () => {
  map.value = new Map({
    target: "map",
    layers: [
      new TileLayer({
        source: new OSM(),
        zIndex: 0
      }),
    ],
    view: new View({
      center: fromLonLat([113.640418, 22.616928]),
      zoom: 13,
    }),
  });

  if (selectOptions.value.length > 0) {
    selectValue.value = selectOptions.value[0];
    tileGetUrl.value = selectValue.value.mbtilesPath;
    mapAddLayer();
  }

  mapAddGeoJsonLayer();
};

const mapAddLayer = async () => {
  await fetchTileData();

  if (xyzLayer.value) {
    map.value.removeLayer(xyzLayer.value);
  }

  xyzLayer.value = new TileLayer({
    zIndex: 10,
    source: new XYZ({
      url: tileUrl.value,
    }),
  });


  map.value.addLayer(xyzLayer.value);
};

// 2. 定义样式函数
// 这个函数会为每一个 feature 执行一次，从而获取不同的 name
const geoJsonStyleFunction = (feature) => {
  // ⭐关键点：获取 GeoJSON 属性中的名称。
  // 请确保你的 GeoJSON 数据中包含 'name' 这个字段。
  // 如果你的字段名是 'projectName' 或其他，请在这里修改：feature.get('projectName')
  const labelText = feature.get("name") ? feature.get("name").toString() : "";

  return new Style({
    stroke: new Stroke({
      color: "#ff0000",
      width: 3,
    }),
    fill: new Fill({
      color: "rgba(255,0,0,0.2)",
    }),
    // 添加文本样式
    text: new Text({
      text: labelText, // 显示的文字内容
      font: 'bold 14px "Microsoft YaHei", Arial, sans-serif', // 字体样式
      fill: new Fill({
        color: '#333333' // 文字颜色（深灰）
      }),
      stroke: new Stroke({
        color: 'rgba(255, 255, 255, 0.9)', // 文字描边（白色光晕），确保在影像底图上看不清
        width: 3
      }),
      overflow: true, // 如果多边形太小放不下文字，是否强制显示。根据需要开启。
      placement: 'point', // 对于多边形，默认就是 'point' (中心点)，可以不写
    })
  });
};


const mapAddGeoJsonLayer = () => {
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
    source,
    zIndex: 999999,
    // ⭐ 3. 这里不再 new Style()，而是传入上面定义的函数
    style: geoJsonStyleFunction,
  });

  map.value.addLayer(geojsonLayer.value);

  source.once("change", () => {
    if (source.getState() === "ready") {
      const extent = source.getExtent();
      if (extent && !extent.includes(NaN)) {
        map.value.getView().fit(extent, {
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
    tileGetUrl.value = selectValue.value.mbtilesPath;
    mapAddLayer();
  }
};

onMounted(fetchData);
</script>

<style>
/* 样式保持不变 */
.home {
  position: relative;
  width: 100%;
  height: 100%;
}

html,
body {
  margin: 0;
  height: 100%;
  width: 100%;
  overflow: hidden;
}

#map {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.home .el-select {
  position: absolute;
  top: 30px;
  left: 30px;
  z-index: 2;
  width: 240px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.el-select-dropdown__item {
  padding: 10px;
  height: auto;
}

.ol-attribution,
.ol-zoom,
.ol-rotate {
  display: none;
}
</style>