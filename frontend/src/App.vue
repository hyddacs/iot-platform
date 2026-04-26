<script setup>
import { computed } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { authState, clearAuth } from "./lib/auth";

const route = useRoute();
const router = useRouter();

const isAuthenticated = computed(() => Boolean(authState.token && authState.user));
const navigationItems = [
  { label: "工作台", to: "/dashboard" },
  { label: "设备管理", to: "/devices" },
  { label: "历史数据", to: "/history" },
  { label: "告警中心", to: "/alerts" }
];

async function logout() {
  clearAuth();
  await router.push("/login");
}
</script>

<template>
  <div class="app-shell" :class="{ 'auth-shell': isAuthenticated }">
    <template v-if="isAuthenticated">
      <aside class="sidebar">
        <div class="brand-block">
          <h1>数据监控平台</h1>
        </div>

        <nav class="sidebar-nav">
          <RouterLink
            v-for="item in navigationItems"
            :key="item.to"
            :to="item.to"
            class="nav-link"
            :class="{ active: route.path.startsWith(item.to) }"
          >
            {{ item.label }}
          </RouterLink>
        </nav>

        <div class="sidebar-footer">
          <p class="mini-label">当前用户</p>
          <strong>{{ authState.user?.username }}</strong>
          <span class="muted-text">{{ authState.user?.email }}</span>
          <button class="ghost-btn full-width" type="button" @click="logout">退出登录</button>
        </div>
      </aside>

      <main class="main-shell">
        <RouterView />
      </main>
    </template>

    <RouterView v-else />
  </div>
</template>
