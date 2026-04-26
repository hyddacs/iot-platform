import { createRouter, createWebHistory } from "vue-router";

import { hasAuth } from "../lib/auth";
import { setAuthExpiredHandler } from "../lib/api";

const routes = [
  {
    path: "/",
    redirect: () => (hasAuth() ? "/dashboard" : "/login")
  },
  {
    path: "/login",
    name: "登录",
    component: () => import("../pages/LoginPage.vue"),
    meta: { public: true, title: "登录" }
  },
  {
    path: "/dashboard",
    name: "工作台",
    component: () => import("../pages/DashboardPage.vue"),
    meta: { requiresAuth: true, title: "工作台" }
  },
  {
    path: "/devices",
    name: "设备管理",
    component: () => import("../pages/DevicesPage.vue"),
    meta: { requiresAuth: true, title: "设备管理" }
  },
  {
    path: "/monitor",
    redirect: "/dashboard"
  },
  {
    path: "/history",
    name: "历史数据",
    component: () => import("../pages/HistoryPage.vue"),
    meta: { requiresAuth: true, title: "历史数据" }
  },
  {
    path: "/alerts",
    name: "告警中心",
    component: () => import("../pages/AlertsPage.vue"),
    meta: { requiresAuth: true, title: "告警中心" }
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !hasAuth()) {
    return "/login";
  }

  if (to.path === "/login" && hasAuth()) {
    return "/dashboard";
  }

  return true;
});

setAuthExpiredHandler(() => {
  if (router.currentRoute.value.path !== "/login") {
    router.push({
      path: "/login",
      query: { redirect: router.currentRoute.value.fullPath }
    });
  }
});

export default router;
