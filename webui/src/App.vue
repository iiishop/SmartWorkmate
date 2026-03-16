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
  current_step: 'idle',
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
  tasks: [],
  memory_deltas: [],
  history: [],
  logs: [],
})

const selectedFlowNode = ref('idle')
const selectedTaskKey = ref('')
const syncTimer = ref(null)

const metrics = computed(() => [
  { key: 'dispatch', label: '派发执行', accent: 'cobalt', value: state.stats.dispatch, note: '本轮下发任务数' },
  { key: 'active', label: '活跃任务', accent: 'teal', value: state.stats.active, note: '等待完成或回写' },
  { key: 'auto', label: '自动任务', accent: 'amber', value: state.stats.auto, note: '自动生成与跳过' },
  { key: 'pr', label: 'PR 事件', accent: 'violet', value: state.stats.pr, note: '自动创建与更新' },
  { key: 'error', label: '异常阻塞', accent: 'crimson', value: state.stats.error, note: '需要优先处理' },
])

const cycleSteps = [
  'idle', 'discover', 'sync_state', 'memory_refresh',
  'reconcile', 'lock_task', 'dispatch', 'acceptance',
  'pr_open', 'idle_task', 'wait_active', 'error',
]

// Infer current step from live state data
const inferredStep = computed(() => {
  if (!state.running) return 'idle'
  const s = state.current_step || 'idle'
  // map backend step names to our cycle node IDs
  if (s === 'discover') return 'discover'
  if (s === 'running') {
    // guess from last cycle stats what we're likely doing
    if (state.stats.active > 0) return 'wait_active'
    if (state.stats.dispatch > 0) return 'dispatch'
    return 'sync_state'
  }
  if (s === 'apply') return 'acceptance'
  return 'idle'
})

const flowNodes = computed(() => {
  const active = inferredStep.value
  const node = (id, x, y, label, tone, width = 148) => ({
    id,
    position: { x, y },
    data: { label },
    class: `flow-node ${tone}${active === id ? ' is-active' : ''}${selectedFlowNode.value === id ? ' is-selected' : ''}`,
    style: { width: `${width}px` },
  })

  // Layout: two rows
  // Row 1 (main happy path): idle → discover → sync_state → memory_refresh → reconcile → lock_task → dispatch → acceptance → pr_open
  // Row 2 (branches):  wait_active (under reconcile), idle_task (under lock_task), error (under acceptance)
  return [
    node('idle',           20,  20, `IDLE\n等待下一轮`, 'steel', 130),
    node('discover',      170,  20, `DISCOVER\n发现项目`, 'teal', 142),
    node('sync_state',    332,  20, `SYNC STATE\n同步任务文件`, 'teal', 148),
    node('memory_refresh',500,  20, `MEMORY\n刷新项目记忆`, 'cobalt', 152),
    node('reconcile',     672,  20, `RECONCILE\n处理活跃任务`, 'violet', 158),
    node('lock_task',     854,  20, `LOCK & SELECT\n选取下一个任务`, 'cobalt', 162),
    node('dispatch',     1040,  20, `DISPATCH\n执行任务`, 'cobalt', 138),
    node('acceptance',   1200,  20, `ACCEPTANCE\n验收结果`, 'amber', 148),
    node('pr_open',      1368,  20, `PR OPEN\n推送 PR`, 'violet', 130),
    // branch nodes
    node('wait_active',   672, 120, `WAIT ACTIVE\n有任务处理中`, 'steel', 152),
    node('idle_task',     854, 120, `IDLE TASK\n无任务→自动生成`, 'amber', 160),
    node('error',        1200, 120, `ERROR\n验收失败/阻塞`, 'crimson', 140),
  ]
})

const flowEdges = computed(() => {
  const animated = (id, src, tgt, label = '') => ({
    id, source: src, target: tgt, animated: true,
    label: label || undefined,
    labelStyle: { fontSize: '10px', fill: '#64748b' },
  })
  const plain = (id, src, tgt, label = '') => ({
    id, source: src, target: tgt, animated: false,
    label: label || undefined,
    labelStyle: { fontSize: '10px', fill: '#64748b' },
  })
  return [
    animated('e1', 'idle', 'discover'),
    animated('e2', 'discover', 'sync_state'),
    animated('e3', 'sync_state', 'memory_refresh'),
    animated('e4', 'memory_refresh', 'reconcile'),
    // reconcile branches
    animated('e5a', 'reconcile', 'lock_task', '无活跃'),
    plain('e5b', 'reconcile', 'wait_active', '有活跃'),
    plain('e5c', 'wait_active', 'idle', '继续等'),
    // lock_task branches
    animated('e6a', 'lock_task', 'dispatch', '有任务'),
    plain('e6b', 'lock_task', 'idle_task', '无任务'),
    plain('e6c', 'idle_task', 'idle', '生成草稿'),
    // dispatch → acceptance → pr / error
    animated('e7', 'dispatch', 'acceptance'),
    animated('e8a', 'acceptance', 'pr_open', '通过'),
    plain('e8b', 'acceptance', 'error', '失败'),
    plain('e9a', 'pr_open', 'idle', '完成'),
    plain('e9b', 'error', 'idle', 'blocked/rework'),
  ]
})

const flowDetailTitle = computed(() => {
  const titleMap = {
    idle: '等待中',
    discover: '发现项目',
    sync_state: '同步任务文件',
    memory_refresh: '刷新项目记忆',
    reconcile: '处理活跃任务',
    lock_task: '选取下一个任务',
    dispatch: '派发执行',
    acceptance: '验收结果',
    pr_open: 'PR 推送',
    wait_active: '有任务处理中',
    idle_task: '自动生成草稿任务',
    error: '验收失败 / 阻塞',
  }
  return titleMap[selectedFlowNode.value] || '节点详情'
})

const flowDetailItems = computed(() => {
  const sourceMap = {
    idle: [],
    discover: state.active,
    sync_state: state.active,
    memory_refresh: state.memory_deltas,
    reconcile: state.active,
    lock_task: state.active,
    dispatch: state.dispatch,
    acceptance: state.dispatch,
    pr_open: [...state.pr, ...state.pr_tracking],
    wait_active: state.active,
    idle_task: state.auto,
    error: state.errors,
  }
  return (sourceMap[selectedFlowNode.value] || []).map((text) => ({
    text,
    links: extractLinks(text),
  }))
})

const projectEntries = computed(() => Object.entries(state.projects || {}))

const taskEntries = computed(() => state.tasks || [])

const selectedTask = computed(() => {
  if (!taskEntries.value.length) return null
  if (!selectedTaskKey.value) {
    selectedTaskKey.value = taskEntries.value[0].key
  }
  return taskEntries.value.find((item) => item.key === selectedTaskKey.value) || taskEntries.value[0]
})

const statusOrder = ['todo', 'in_progress', 'verify', 'pr_open', 'done', 'rework', 'blocked']
const statusLabel = {
  todo: 'TODO',
  in_progress: 'IN_PROGRESS',
  verify: 'VERIFY',
  pr_open: 'PR_OPEN',
  done: 'DONE',
  rework: 'REWORK',
  blocked: 'BLOCKED',
}

const taskFlowNodes = computed(() => {
  const task = selectedTask.value
  const observed = task?.flow || []
  const current = task?.status || ''
  return statusOrder.map((status, index) => {
    const visited = observed.includes(status)
    const active = current === status
    return {
      id: `task-${status}`,
      position: { x: 30 + index * 150, y: 72 },
      data: { label: `${statusLabel[status]}${active ? '\n(当前)' : ''}` },
      class: `task-flow-node${visited ? ' visited' : ''}${active ? ' active' : ''}`,
      style: { width: '138px' },
    }
  })
})

const taskFlowEdges = computed(() => {
  const task = selectedTask.value
  const observed = Array.isArray(task?.flow) ? task.flow : []
  const edges = []
  for (let index = 1; index < observed.length; index += 1) {
    edges.push({
      id: `task-edge-${index}`,
      source: `task-${observed[index - 1]}`,
      target: `task-${observed[index]}`,
      animated: true,
    })
  }
  return edges
})

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

      <section class="panel-glass task-flow-panel">
        <div class="section-heading compact">
          <div>
            <p class="section-kicker">Task State Machine</p>
            <h2>任务状态变换（按任务）</h2>
          </div>
        </div>

        <div class="task-flow-controls" v-if="taskEntries.length">
          <label class="field">
            <span>任务选择</span>
            <select v-model="selectedTaskKey">
              <option v-for="task in taskEntries" :key="task.key" :value="task.key">
                {{ task.project }} / {{ task.task_id }} / {{ task.status }}
              </option>
            </select>
          </label>
        </div>

        <div v-if="selectedTask" class="task-flow-meta">
          <p><strong>Task:</strong> {{ selectedTask.task_id }} ({{ selectedTask.project }})</p>
          <p><strong>当前状态:</strong> {{ selectedTask.status }}</p>
          <p><strong>最近更新:</strong> {{ selectedTask.updated_at || '-' }}</p>
          <p><strong>Notes:</strong> {{ selectedTask.notes || '-' }}</p>
          <p v-if="selectedTask.failure_detail"><strong>失败详情:</strong> {{ selectedTask.failure_detail }}</p>
          <p v-if="selectedTask.status === 'blocked' || selectedTask.manual_suggestion" class="manual-suggestion">
            <strong>人工干预建议:</strong> {{ selectedTask.manual_suggestion || '查看失败详情后修复并重试。' }}
          </p>
        </div>

        <VueFlow
          v-if="selectedTask"
          class="task-flow-canvas"
          :nodes="taskFlowNodes"
          :edges="taskFlowEdges"
          :fit-view-on-init="true"
          :nodes-draggable="false"
          :elements-selectable="false"
          :zoom-on-scroll="false"
          :zoom-on-double-click="false"
          :prevent-scrolling="false"
        />

        <p v-else class="empty-state">暂无任务状态数据。</p>
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
          <div class="section-heading compact"><div><p class="section-kicker">Memory Delta</p><h2>记忆增量</h2></div></div>
          <ul><li v-for="item in state.memory_deltas" :key="item">{{ item }}</li><li v-if="!state.memory_deltas.length" class="empty-state">无</li></ul>
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
