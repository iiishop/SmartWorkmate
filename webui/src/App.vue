<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { VueFlow } from '@vue-flow/core'

const form = reactive({
  mode: 'execute_daemon',
  user: 'iiishop',
  root: '',
  interval: 300,
  opencode_global: true,
})

const state = reactive({
  running: false,
  mode: 'execute_daemon',
  user: 'iiishop',
  root: '',
  interval: 300,
  opencode_global: true,
  cycle: 0,
  next_run_in: 0,
  last_updated: '',
  last_error: '',
  stats: { dispatch: 0, active: 0, auto: 0, pr: 0, error: 0, git_sync: 0 },
  dispatch: [],
  active: [],
  auto: [],
  pr: [],
  pr_tracking: [],
  pr_breakdown: { open: 0, merged: 0, rejected: 0, followup: 0 },
  git_sync: [],
  errors: [],
  policies: [],
  projects: {},
  history: [],
  logs: [],
})

const selectedFlowNode = ref('dispatch')
const syncTimer = ref(null)

const metrics = computed(() => [
  { key: 'dispatch', label: '派发执行', accent: 'cobalt', value: state.stats.dispatch, note: '本轮下发任务数' },
  { key: 'active', label: '活跃任务', accent: 'teal', value: state.stats.active, note: '等待完成或回写' },
  { key: 'auto', label: '自动任务', accent: 'amber', value: state.stats.auto, note: '自动生成与跳过' },
  { key: 'pr', label: 'PR 事件', accent: 'violet', value: state.stats.pr, note: '自动创建与更新' },
  { key: 'error', label: '异常阻塞', accent: 'crimson', value: state.stats.error, note: '需要优先处理' },
])

const flowNodes = computed(() => {
  const selected = selectedFlowNode.value
  const node = (id, x, y, label, tone, width = 176) => ({
    id,
    position: { x, y },
    data: { label },
    class: `flow-node ${tone}${selected === id ? ' is-selected' : ''}`,
    style: { width: `${width}px` },
  })
  return [
    node('scan', 20, 86, `扫描项目\nactive=${state.stats.active}`, 'steel', 164),
    node('sync', 196, 86, `Git 同步\nsync=${state.stats.git_sync || 0}`, 'teal', 168),
    node('dispatch', 388, 86, `派发执行\ndispatch=${state.stats.dispatch}`, 'cobalt', 174),
    node('auto', 588, 30, `自动任务\nauto=${state.stats.auto}`, 'amber', 156),
    node('pr', 588, 144, `PR 生命周期\npr=${state.stats.pr}`, 'violet', 164),
    node('err', 778, 86, `异常\nerror=${state.stats.error}`, 'crimson', 148),
  ]
})

const flowEdges = computed(() => [
  { id: 'e-scan-sync', source: 'scan', target: 'sync', animated: true },
  { id: 'e-sync-dispatch', source: 'sync', target: 'dispatch', animated: true },
  { id: 'e-dispatch-auto', source: 'dispatch', target: 'auto', animated: true },
  { id: 'e-dispatch-pr', source: 'dispatch', target: 'pr', animated: true },
  { id: 'e-auto-err', source: 'auto', target: 'err' },
  { id: 'e-pr-err', source: 'pr', target: 'err' },
])

const flowDetailTitle = computed(() => {
  const titleMap = {
    scan: '扫描项目',
    sync: 'Git 同步',
    dispatch: '派发执行',
    auto: '自动任务',
    pr: 'PR 生命周期',
    err: '异常阻塞',
  }
  return titleMap[selectedFlowNode.value] || '节点详情'
})

const flowDetailItems = computed(() => {
  const sourceMap = {
    scan: state.active,
    sync: state.git_sync,
    dispatch: state.dispatch,
    auto: state.auto,
    pr: [...state.pr, ...state.pr_tracking],
    err: state.errors,
  }
  return (sourceMap[selectedFlowNode.value] || []).map((text) => ({
    text,
    links: extractLinks(text),
  }))
})

const projectEntries = computed(() => Object.entries(state.projects || {}))

const strategicStatus = computed(() => {
  if (state.running) {
    return {
      label: '守护运行中',
      description: state.next_run_in > 0 ? `${state.next_run_in}s 后进入下一轮` : '当前正在处理最新周期',
    }
  }
  if (state.last_error) {
    return {
      label: '待处理阻塞',
      description: state.last_error,
    }
  }
  return {
    label: '待命',
    description: '配置参数后即可发起执行或干跑巡检',
  }
})

function extractLinks(text) {
  return (text?.match(/https:\/\/[^\s)]+/g) || []).map((item) => item.trim())
}

function onFlowNodeClick(event) {
  const id = event?.node?.id
  if (id) selectedFlowNode.value = id
}

async function fetchState() {
  const response = await fetch('/api/state')
  if (!response.ok) throw new Error(`state fetch failed: ${response.status}`)
  const payload = await response.json()
  Object.assign(state, payload)
}

async function startRunner() {
  const response = await fetch('/api/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form),
  })
  if (!response.ok) {
    const message = await response.text()
    window.alert(`启动失败: ${message}`)
    return
  }
  await fetchState()
}

async function stopRunner() {
  await fetch('/api/stop', { method: 'POST' })
  await fetchState()
}

onMounted(async () => {
  await fetchState()
  syncTimer.value = window.setInterval(() => {
    fetchState().catch((error) => {
      state.last_error = error instanceof Error ? error.message : String(error)
    })
  }, 1000)
})

onBeforeUnmount(() => {
  if (syncTimer.value) window.clearInterval(syncTimer.value)
})
</script>

<template>
  <div class="shell">
    <div class="ambient ambient-left"></div>
    <div class="ambient ambient-right"></div>

    <header class="hero">
      <div class="hero-copy">
        <p class="eyebrow">SMARTWORKMATE / FLOW DASHBOARD</p>
        <h1>现代化执行大屏</h1>
        <p class="hero-text">
          以 worktree 隔离执行为核心，统一展示 Git 同步、任务派发、PR 生命周期和异常阻塞。
        </p>
      </div>

      <div class="hero-status panel-glass">
        <div class="hero-status-top">
          <span class="status-dot" :class="{ live: state.running }"></span>
          <span>{{ strategicStatus.label }}</span>
        </div>
        <p>{{ strategicStatus.description }}</p>
        <div class="hero-meta">
          <span>轮次 {{ state.cycle }}</span>
          <span>最近更新 {{ state.last_updated || '-' }}</span>
        </div>
      </div>
    </header>

    <main class="layout">
      <section class="command-board panel-glass panel-grid">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Command Setup</p>
            <h2>执行参数</h2>
          </div>
          <div class="mode-pill" :class="{ running: state.running }">{{ state.running ? 'RUNNING' : 'IDLE' }}</div>
        </div>

        <div class="command-grid">
          <label class="field">
            <span>运行模式</span>
            <select v-model="form.mode">
              <option value="execute_daemon">执行守护模式</option>
              <option value="execute_once">执行一次</option>
              <option value="dry_run_once">干跑一次</option>
            </select>
          </label>

          <label class="field">
            <span>用户</span>
            <input v-model="form.user" type="text" placeholder="iiishop" />
          </label>

          <label class="field">
            <span>巡检间隔</span>
            <input v-model.number="form.interval" type="number" min="30" placeholder="300" />
          </label>

          <label class="field field-wide">
            <span>根目录</span>
            <input v-model="form.root" type="text" placeholder="不填则按 OpenCode 索引发现" />
          </label>
        </div>

        <div class="command-footer">
          <label class="toggle">
            <input v-model="form.opencode_global" type="checkbox" />
            <span>启用 OpenCode 全局发现</span>
          </label>
          <div class="actions">
            <button class="btn btn-primary" :disabled="state.running" @click="startRunner">启动</button>
            <button class="btn btn-danger" :disabled="!state.running" @click="stopRunner">停止</button>
          </div>
        </div>
      </section>

      <section class="metrics-grid">
        <article v-for="metric in metrics" :key="metric.key" class="metric-card panel-glass" :data-accent="metric.accent">
          <p class="metric-label">{{ metric.label }}</p>
          <div class="metric-value">{{ metric.value }}</div>
          <p class="metric-note">{{ metric.note }}</p>
        </article>
      </section>

      <section class="main-grid">
        <section class="panel-glass flow-panel">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Execution Graph</p>
              <h2>实时执行流</h2>
            </div>
          </div>
          <VueFlow
            class="flow-canvas"
            :nodes="flowNodes"
            :edges="flowEdges"
            :fit-view-on-init="true"
            :nodes-draggable="false"
            :elements-selectable="false"
            :zoom-on-scroll="false"
            :zoom-on-double-click="false"
            :prevent-scrolling="false"
            @node-click="onFlowNodeClick"
          />
        </section>

        <section class="panel-glass detail-panel">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Node Briefing</p>
              <h2>{{ flowDetailTitle }}</h2>
            </div>
          </div>

          <div v-if="flowDetailItems.length" class="detail-list">
            <article v-for="(item, index) in flowDetailItems" :key="index" class="detail-card">
              <p>{{ item.text }}</p>
              <div v-if="item.links.length" class="detail-links">
                <a v-for="(link, linkIndex) in item.links" :key="linkIndex" :href="link" target="_blank" rel="noreferrer">{{ link }}</a>
              </div>
            </article>
          </div>
          <p v-else class="empty-state">当前节点暂无可展示条目。</p>
        </section>
      </section>

      <section class="intel-grid">
        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Git Sync</p><h2>同步结果</h2></div></div>
          <ul><li v-for="item in state.git_sync" :key="item">{{ item }}</li><li v-if="!state.git_sync.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Dispatch</p><h2>当前派发</h2></div></div>
          <ul><li v-for="item in state.dispatch" :key="item">{{ item }}</li><li v-if="!state.dispatch.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Active</p><h2>执行中的任务</h2></div></div>
          <ul><li v-for="item in state.active" :key="item">{{ item }}</li><li v-if="!state.active.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Automation</p><h2>自动发现与任务生成</h2></div></div>
          <ul><li v-for="item in state.auto" :key="item">{{ item }}</li><li v-if="!state.auto.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Pull Request</p><h2>PR 状态</h2></div></div>
          <ul><li v-for="item in state.pr" :key="item">{{ item }}</li><li v-if="!state.pr.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">PR Lifecycle</p><h2>PR 跟踪</h2></div></div>
          <div class="pr-breakdown">
            <span class="pr-chip">打开 {{ state.pr_breakdown.open || 0 }}</span>
            <span class="pr-chip merged">合并 {{ state.pr_breakdown.merged || 0 }}</span>
            <span class="pr-chip rejected">拒绝 {{ state.pr_breakdown.rejected || 0 }}</span>
            <span class="pr-chip followup">后续 {{ state.pr_breakdown.followup || 0 }}</span>
          </div>
          <ul><li v-for="item in state.pr_tracking" :key="item">{{ item }}</li><li v-if="!state.pr_tracking.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel danger-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Exception</p><h2>异常与阻塞</h2></div></div>
          <ul><li v-for="item in state.errors" :key="item" class="error-item">{{ item }}</li><li v-if="!state.errors.length" class="empty-state">无</li></ul>
        </article>

        <article class="panel-glass intel-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Policy</p><h2>执行策略</h2></div></div>
          <ul><li v-for="item in state.policies" :key="item">{{ item }}</li><li v-if="!state.policies.length" class="empty-state">无</li></ul>
        </article>
      </section>

      <section class="secondary-grid">
        <section class="panel-glass">
          <div class="section-heading compact"><div><p class="section-kicker">Projects</p><h2>项目态势</h2></div></div>
          <div class="project-grid">
            <article v-for="([name, meta]) in projectEntries" :key="name" class="project-card">
              <div class="project-title-row"><h3>{{ name }}</h3><span class="project-total">{{ (meta.dispatch || 0) + (meta.active || 0) + (meta.auto || 0) }}</span></div>
              <div class="project-chip-row">
                <span class="project-chip">同步 {{ meta.sync || 0 }}</span>
                <span class="project-chip">派发 {{ meta.dispatch || 0 }}</span>
                <span class="project-chip">活跃 {{ meta.active || 0 }}</span>
                <span class="project-chip">自动 {{ meta.auto || 0 }}</span>
                <span class="project-chip danger">异常 {{ meta.error || 0 }}</span>
              </div>
            </article>
            <article v-if="!projectEntries.length" class="project-card empty-state">暂无项目数据</article>
          </div>
        </section>

        <section class="panel-glass history-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Trace</p><h2>历史摘要</h2></div></div>
          <ul><li v-for="item in state.history" :key="item">{{ item }}</li><li v-if="!state.history.length" class="empty-state">无</li></ul>
        </section>

        <section class="panel-glass log-panel">
          <div class="section-heading compact"><div><p class="section-kicker">Output</p><h2>运行日志</h2></div></div>
          <pre class="log-stream">{{ state.logs.join('\n') }}</pre>
        </section>
      </section>
    </main>
  </div>
</template>
