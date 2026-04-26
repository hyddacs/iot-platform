<script setup>
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { apiFetch, toMessage } from "../lib/api";
import { setAuth } from "../lib/auth";

const router = useRouter();
const mode = ref("login");
const loading = ref(false);
const success = ref("");
const error = ref("");

const loginForm = reactive({
  username: "",
  password: ""
});

const registerForm = reactive({
  username: "",
  email: "",
  password: "",
  confirmPassword: ""
});

async function login() {
  loading.value = true;
  error.value = "";
  success.value = "";

  try {
    const username = loginForm.username.trim();

    const body = new URLSearchParams();
    body.set("username", username);
    body.set("password", loginForm.password);

    const tokenData = await apiFetch("/api/auth/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body
    });

    const user = await apiFetch("/api/auth/me", {
      headers: {
        Authorization: `Bearer ${tokenData.access_token}`
      }
    });

    setAuth(tokenData.access_token, user);
    loginForm.username = username;
    await router.push(router.currentRoute.value.query.redirect || "/dashboard");
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    loading.value = false;
  }
}

async function register() {
  loading.value = true;
  error.value = "";
  success.value = "";

  try {
    if (registerForm.password !== registerForm.confirmPassword) {
      throw new Error("两次输入的密码不一致");
    }

    await apiFetch("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username: registerForm.username.trim(),
        email: registerForm.email.trim(),
        password: registerForm.password
      })
    });

    loginForm.username = registerForm.username.trim();
    loginForm.password = registerForm.password;
    registerForm.username = "";
    registerForm.email = "";
    registerForm.password = "";
    registerForm.confirmPassword = "";
    mode.value = "login";
    success.value = "注册成功，现在可以直接登录。";
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-hero">
      <div class="hero-copy tech-hero">
        <div class="tech-brand">
          <span class="tech-brand-mark" aria-hidden="true"></span>
          <h1>数据监控平台</h1>
        </div>

        <div class="tech-stage" aria-hidden="true">
          <div class="tech-grid">
            <span v-for="item in 36" :key="item"></span>
          </div>
          <div class="tech-ring">
            <span></span>
            <span></span>
            <span></span>
          </div>
          <div class="tech-lines">
            <span></span>
            <span></span>
            <span></span>
            <span></span>
          </div>
          <div class="tech-bars">
            <span style="--bar-height: 42%"></span>
            <span style="--bar-height: 72%"></span>
            <span style="--bar-height: 55%"></span>
            <span style="--bar-height: 88%"></span>
            <span style="--bar-height: 64%"></span>
          </div>
        </div>
      </div>

      <article class="login-card light-card tech-login-card">
        <div class="auth-switch">
          <button
            class="switch-btn"
            :class="{ active: mode === 'login' }"
            type="button"
            @click="mode = 'login'; error = ''; success = ''"
          >
            登录
          </button>
          <button
            class="switch-btn"
            :class="{ active: mode === 'register' }"
            type="button"
            @click="mode = 'register'; error = ''; success = ''"
          >
            注册
          </button>
        </div>

        <div v-if="mode === 'login'">
          <h2>登录系统</h2>
          <form class="form-grid single-column" @submit.prevent="login">
            <label>
              <span>用户名</span>
              <input v-model="loginForm.username" type="text" placeholder="请输入用户名" required />
            </label>

            <label>
              <span>密码</span>
              <input v-model="loginForm.password" type="password" placeholder="请输入密码" required />
            </label>

            <button class="primary-btn full-width" type="submit" :disabled="loading">
              {{ loading ? "登录中..." : "登录并进入平台" }}
            </button>
          </form>
        </div>

        <div v-else>
          <h2>注册账号</h2>
          <form class="form-grid single-column" @submit.prevent="register">
            <label>
              <span>用户名</span>
              <input v-model="registerForm.username" type="text" placeholder="至少 3 个字符" required />
            </label>

            <label>
              <span>邮箱</span>
              <input v-model="registerForm.email" type="email" placeholder="name@example.com" required />
            </label>

            <label>
              <span>密码</span>
              <input v-model="registerForm.password" type="password" placeholder="至少 6 个字符" required />
            </label>

            <label>
              <span>确认密码</span>
              <input
                v-model="registerForm.confirmPassword"
                type="password"
                placeholder="请再次输入密码"
                required
              />
            </label>

            <button class="primary-btn full-width" type="submit" :disabled="loading">
              {{ loading ? "注册中..." : "注册账号" }}
            </button>
          </form>
        </div>

        <p v-if="success" class="success-text">{{ success }}</p>
        <p v-if="error" class="error-text">{{ error }}</p>
      </article>
    </section>
  </main>
</template>
