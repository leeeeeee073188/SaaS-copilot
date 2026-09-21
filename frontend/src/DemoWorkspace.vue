<template>
  <main class="demo-shell">
    <header class="demo-header">
      <div><small>FLOWFORGE CLOUD × SaaS Copilot</small><h1>企业服务工作台</h1><p>产品咨询 · API 支持 · 订阅账单 · 企业账户</p></div>
      <span class="demo-badge">{{ engine === 'llm' ? '模型模式' : '离线演示 · 非模型生成' }} · 合成数据</span>
    </header>
    <section class="demo-login">
      <label>企业<select v-model="org"><option v-for="name in ['aurora','beacon','cedar']" :key="name">{{ name }}</option></select></label>
      <label>演示身份<select v-model="role"><option v-for="name in ['owner','admin','billing_admin','developer']" :key="name">{{ name }}</option></select></label>
      <button :disabled="busy" @click="login">切换并登录</button>
      <span>{{ session ? `${session.org_id} / ${session.user_id}` : '请选择演示账号' }}</span>
      <button :disabled="busy || !session" @click="refresh">刷新业务状态</button>
    </section>
    <p v-if="error" class="demo-error" role="alert">{{ error }}</p>
    <div class="demo-layout">
      <section class="demo-product">
        <nav><button v-for="tab in tabs" :key="tab.id" :class="{ selected: active === tab.id }" @click="active = tab.id">{{ tab.label }}</button></nav>
        <article v-if="active === 'overview'" class="demo-card">
          <h2>当前权益</h2><p v-if="data.entitlements">{{ data.entitlements.plan_id }} · {{ data.entitlements.seats }} 个席位 · 每分钟 {{ data.entitlements.rate_limit }} 请求</p>
          <p v-if="data.usage">账期 {{ data.usage.period }}：{{ data.usage.used.toLocaleString() }} / {{ data.usage.limit.toLocaleString() }} 次 API 请求</p>
          <h3>套餐目录（模拟价格）</h3><div v-for="(plan, id) in data.plans" :key="id" class="demo-row"><strong>{{ plan.name }}</strong><span>¥{{ plan.price_minor / 100 }}/月 · {{ plan.seats }} 席</span></div>
        </article>
        <article v-if="active === 'integration'" class="demo-card">
          <h2>集成与 API 请求</h2><p v-if="denied.integrations">{{ denied.integrations }}</p>
          <div v-for="item in data.integrations" :key="item.id" class="demo-row"><span>{{ item.id }} · {{ item.environment }}</span><button :disabled="busy" @click="loadRequests(item.id)">查看请求</button></div>
          <div v-for="item in requests" :key="item.request_id" class="demo-request"><strong>{{ item.status_code }} · {{ item.reason_code }}</strong><p>{{ item.request_id }} · {{ item.timestamp }}</p><small>凭证标签 {{ item.credential_label }}（无真实密钥）</small></div>
        </article>
        <article v-if="active === 'billing'" class="demo-card">
          <h2>订阅与账单</h2><p v-if="denied.subscription">{{ denied.subscription }}</p>
          <template v-if="data.subscription"><p>当前 {{ data.subscription.plan_id }}</p><p>已预约 {{ data.subscription.scheduled_plan_id || '无' }} · 周期结束 {{ data.subscription.period_end }}</p>
            <div class="demo-controls"><select v-model="target"><option value="growth_v1">Growth</option><option value="starter_v1">Starter</option></select><button :disabled="busy" @click="preview">预览下周期变更</button></div>
          </template>
          <div v-for="invoice in data.invoices" :key="invoice.id" class="demo-row"><span>{{ invoice.id }}</span><strong>¥{{ invoice.total_minor / 100 }} · {{ invoice.status }}</strong></div>
          <small>仅模拟账单，无真实扣款。预约升级不改变当期权益。</small>
        </article>
        <article v-if="active === 'account'" class="demo-card">
          <h2>企业成员</h2><div v-for="member in data.members?.members" :key="member.user_id" class="demo-row"><span>{{ member.user_id }}</span><strong>{{ member.role }}</strong></div>
          <h3>待接受邀请</h3><div v-for="invite in data.members?.invitations" :key="invite.id" class="demo-row"><span>{{ invite.id }}</span><span>{{ invite.status }} · {{ invite.role }}</span></div>
          <div class="demo-controls"><input v-model="email" type="email" placeholder="colleague@aurora.example" /><button :disabled="busy || !email.trim()" @click="invite">邀请 developer</button></div>
          <small>只创建本地邀请，不发送邮件；待接受邀请占用席位。</small>
        </article>
        <article v-if="active === 'monitor'" class="demo-card">
          <h2>我的服务记录</h2><p class="demo-muted">当前企业及本人 · 最近 24 小时保留的样本；处理完成不代表问题已解决。</p>
          <button :disabled="busy" @click="loadMonitor">刷新记录</button>
          <template v-if="monitorData">
            <p>请求 {{ monitorData.sample_count }} 次 · 技术失败 {{ monitorData.technical_error_rate === null ? '暂无样本' : `${(monitorData.technical_error_rate * 100).toFixed(1)}%` }}</p>
            <p>处理耗时 P50 {{ monitorData.latency_ms.p50 ?? '—' }} ms · P95 {{ monitorData.latency_ms.p95 ?? '—' }} ms</p>
            <p v-for="alert in monitorData.alerts" :key="alert.code" class="demo-error">{{ alert.message }}</p>
          </template>
          <details v-for="trace in recentTraces" :key="trace.request_id" class="demo-request">
            <summary>{{ statusLabels[trace.status] || trace.status }} · {{ trace.domain }} · {{ trace.latency_ms }} ms</summary>
            <small>{{ new Date(trace.started_at * 1000).toLocaleString() }} · {{ trace.request_id }}</small>
            <div v-for="(stage, index) in trace.spans" :key="index">{{ stage.name }}：{{ stage.latency_ms }} ms · {{ stage.status }}</div>
            <div v-for="(tool, index) in trace.tool_traces" :key="`tool-${index}`">{{ tool.tool_name }}：{{ tool.status }} · {{ tool.latency_ms }} ms</div>
            <p v-if="trace.operation_ids.length">关联操作：{{ trace.operation_ids.join(', ') }}</p>
          </details>
          <p v-if="monitorData && !recentTraces.length">暂无记录，发送一条咨询后刷新查看。</p>
        </article>
        <article v-for="op in operations" :key="op.operation_id" class="demo-card operation-card">
          <h3>业务操作 · {{ op.status }}</h3>
          <template v-if="op.preview"><p>{{ op.preview.current_plan }} → {{ op.preview.target_plan }}</p><p>本周期增量 ¥{{ op.preview.current_period_charge_minor / 100 }}；下周期 ¥{{ op.preview.next_period_price_minor / 100 }}</p><p>生效时间 {{ op.preview.effective_at }}</p><small>预览有效至 {{ op.preview.expires_at }}</small>
            <button v-if="['requires_confirmation','prepared','confirmed'].includes(op.status)" :disabled="busy" @click="confirm(op)">确认上述变更并提交</button></template>
          <p v-if="op.effective_at">已安排 {{ op.effective_at }} 生效；当前 {{ op.current_plan }}</p>
          <p v-if="op.invitation">{{ op.invitation.email }} · {{ op.invitation.status }}（本地模拟）</p>
          <small>{{ op.operation_id }}</small>
        </article>
      </section>
      <section class="demo-chat demo-card">
        <h2>SaaS Copilot</h2><p class="demo-muted">{{ engine === 'llm' ? '使用业务工具处理请求；操作结果以回执为准。' : '确定性演示覆盖核心流程；启用模型后使用相同业务工具。' }}</p>
        <div class="demo-examples"><button v-for="q in examples" :key="q" @click="draft = q">{{ q }}</button></div>
        <div class="demo-messages" aria-live="polite"><article v-for="(item, index) in messages" :key="index" :class="item.role"><strong>{{ item.role === 'user' ? '你' : 'SaaS Copilot' }}</strong><p>{{ item.content }}</p>
          <small v-if="item.meta">{{ item.meta }}</small>
          <details v-if="item.citations?.length"><summary>查看依据（{{ item.citations.length }}）</summary><div v-for="c in item.citations" :key="c.source_id"><strong>{{ c.title || c.source_id }}</strong><small>{{ c.source_id }} · {{ c.version || '实时工具' }}</small><pre>{{ typeof c.content === 'string' ? c.content : JSON.stringify(c.content, null, 2) }}</pre></div></details>
        </article></div>
        <form @submit.prevent="send"><textarea v-model="draft" rows="3" maxlength="6000" placeholder="例如：下周期升级到 Growth" /><button :disabled="busy || !session || !draft.trim()">{{ busy ? '处理中…' : '发送' }}</button></form>
      </section>
    </div>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
const props = defineProps({ baseUrl: { type: String, required: true }, engine: String })
const org = ref('aurora'), role = ref('owner'), session = ref(null), token = ref(''), busy = ref(false), error = ref('')
const active = ref('overview'), target = ref('growth_v1'), email = ref(''), draft = ref(''), conv = ref('')
const data = reactive({}), denied = reactive({}), requests = ref([]), operations = ref([]), messages = ref([])
const monitorData = ref(null), recentTraces = ref([])
const statusLabels = { ok: '处理完成', business_rejected: '业务规则拒绝', degraded: '处理降级', error: '技术失败', timeout: '处理超时', cancelled: '请求取消' }
const tabs = [{ id: 'overview', label: '概览' }, { id: 'integration', label: 'API 集成' }, { id: 'billing', label: '订阅账单' }, { id: 'account', label: '企业账户' }, { id: 'monitor', label: '服务记录' }]
const examples = ['Starter 支持 Webhook 吗', '查询 int_aurora 的 401', '下周期升级到 Growth', '邀请 new@aurora.example 为 developer']
async function api(path, body) {
  const response = await fetch(props.baseUrl + path, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token.value}` }, ...(body ? { body: JSON.stringify(body) } : {}) })
  const result = await response.json()
  if (!response.ok) throw new Error(`${typeof result.detail === 'string' ? result.detail : result.detail?.message || JSON.stringify(result.detail)}（请求 ${response.headers.get('X-Request-ID') || '未返回编号'}）`)
  return result
}
async function run(fn) { busy.value = true; error.value = ''; try { await fn() } catch (e) { error.value = e.message } finally { busy.value = false } }
async function login() { await run(async () => {
  const result = await api('/saas/demo/login', { user_id: `${org.value}_${role.value}`, org_id: `org_${org.value}` })
  token.value = result.token; session.value = result; conv.value = ''; messages.value = []; operations.value = []; requests.value = []
  monitorData.value = null; recentTraces.value = []; active.value = 'overview'
  Object.keys(data).forEach(key => delete data[key]); await refresh()
}) }
async function refresh() {
  await Promise.all(['plans', 'entitlements', 'usage', 'integrations', 'subscription', 'invoices', 'members'].map(async name => {
    try { const result = await api(name === 'plans' ? '/saas/plans' : `/saas/me/${name}`); data[name] = result.data; delete denied[name] }
    catch (e) { delete data[name]; denied[name] = e.message }
  }))
}
async function loadRequests(id) { await run(async () => { requests.value = (await api(`/saas/integrations/${id}/requests`)).data }) }
function remember(op) { operations.value = [op, ...operations.value.filter(x => x.operation_id !== op.operation_id)] }
async function preview() { await run(async () => remember(await api('/saas/subscription/change-previews', { target_plan: target.value }))) }
async function confirm(op) { await run(async () => {
  await api(`/saas/operations/${op.operation_id}/confirm`, {})
  remember(await api('/saas/subscription/changes', { operation_id: op.operation_id })); await refresh()
}) }
let inviteAttempt = null
async function invite() { await run(async () => {
  // Retain the key for retry of the same invitation, including a lost response.
  const address = email.value.trim().toLowerCase(), scope = `${session.value.org_id}:${address}`
  if (inviteAttempt?.scope !== scope) inviteAttempt = { scope, key: crypto.randomUUID() }
  remember(await api('/saas/invitations', { email: address, idempotency_key: inviteAttempt.key })); await refresh()
}) }
async function send() { await run(async () => {
  const message = draft.value.trim(); messages.value.push({ role: 'user', content: message }); draft.value = ''
  const result = await api('/chat', { message, ...(conv.value ? { conv_id: conv.value } : {}) }); conv.value = result.conv_id
  messages.value.push({ role: 'assistant', content: result.response, citations: result.citations, meta: `${result.domain}/${result.action} · ${result.latency_ms} ms · ${(result.tools_used || []).join(', ')}` })
  result.operations.forEach(remember); await refresh()
}) }
async function loadMonitor() { await run(async () => {
  const [summary, traces] = await Promise.all([api('/monitor'), api('/trace/tools')])
  monitorData.value = summary; recentTraces.value = traces.traces
}) }
watch(active, value => { if (value === 'monitor') loadMonitor() })
onMounted(login)
</script>

<style scoped>
.demo-shell { max-width: 1440px; margin: auto; padding: 28px; color: #182c36; }
.demo-header,.demo-login,.demo-row,.demo-controls { display:flex; align-items:center; gap:16px; justify-content:space-between; }
.demo-header h1 { margin:8px 0; }.demo-header small { letter-spacing:.12em; color:#18736b; }.demo-header p,.demo-muted,small { color:#637782; }
.demo-badge { background:#e3f0ec; padding:10px 14px; border-radius:20px; font-size:13px; }
.demo-login { background:#fff; border:1px solid #dbe5e9; border-radius:12px; padding:16px; margin:24px 0; flex-wrap:wrap; justify-content:flex-start; }
label { display:flex; gap:10px; align-items:center; }select,input,textarea { padding:10px; border:1px solid #cfdbdf; border-radius:8px; background:white; color:inherit; }
button { padding:10px 14px; background:#136e65; color:white; border:0; border-radius:8px; cursor:pointer; }button:disabled { opacity:.5; cursor:default; }
.demo-layout { display:grid; grid-template-columns:1fr 1fr; gap:24px; align-items:start; }nav { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }nav button { background:#e7efee; color:#23483f; }nav button.selected { background:#136e65; color:white; }
.demo-card { background:white; padding:24px; border:1px solid #dbe5e9; border-radius:14px; margin-bottom:16px; min-width:0; }.demo-card h2 { margin-top:0; font-size:21px; }.demo-row { border-bottom:1px solid #edf1f3; padding:13px 0; flex-wrap:wrap; }.demo-controls { margin:20px 0; flex-wrap:wrap; }.demo-request { padding:16px; margin-top:12px; background:#f1f6f7; border-radius:8px; }
.demo-error { padding:14px; color:#9d2929; background:#fff0ef; border-radius:8px; }.operation-card { border-left:4px solid #c79135; }.operation-card button { display:block; margin:16px 0; }
.demo-examples { display:flex; gap:8px; flex-wrap:wrap; }.demo-examples button { background:#f0f5f6; color:#32505d; font-size:12px; }
.demo-messages { max-height:560px; overflow:auto; margin:18px 0; }.demo-messages article { padding:16px; background:#f4f7f8; margin:12px 0; border-radius:10px; }.demo-messages article.user { background:#e8f2ee; }.demo-messages p { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.6; }
details { margin-top:12px; }details div { padding:12px 0; }details small { display:block; }pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; }form textarea { width:100%; box-sizing:border-box; }form button { margin-top:10px; }
@media(max-width:900px) { .demo-layout { grid-template-columns:1fr; }.demo-shell { padding:14px; }.demo-header { align-items:flex-start; flex-direction:column; } }
</style>
