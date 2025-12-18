import axios from 'axios';
 
const http = axios.create({
  baseURL: '',
  timeout: 10000, 
});
 
// 请求拦截器
http.interceptors.request.use(
  config => {
    // if (store.getters.token) {
    //   config.headers['Authorization'] = `Bearer ${store.getters.token}`;
    // }
    return config;
  },
  error => {
    return Promise.reject(error);
  }
);
 
http.interceptors.response.use(
  response => {
    return response.data;
  },
  error => {
    return Promise.reject(error);
  }
);
 
export default http;