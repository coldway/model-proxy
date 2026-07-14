// --- HTTP 与鉴权 ---
const API = '';
let currentPriorityTarget = null;

function authHeaders() {
    const token = localStorage.getItem('mp_admin_token');
    return token ? {'Authorization': 'Bearer ' + token} : {};
}

function apiFetch(url, opts = {}) {
    opts.headers = {...(opts.headers || {}), ...authHeaders()};
    return fetch(url, opts).then(resp => {
        if (resp.status === 401) {
            const token = prompt('请输入管理令牌 (admin_token):');
            if (token) {
                localStorage.setItem('mp_admin_token', token);
                opts.headers['Authorization'] = 'Bearer ' + token;
                return fetch(url, opts);
            }
        }
        return resp;
    });
}

// --- Tab 切换 ---
function switchTab(name, el) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('panel-' + name).classList.add('active');
    location.hash = name;
    if (name === 'routing') loadRoutingLog();
    if (name === 'memory') loadMemory();
    if (name === 'logs') startLogPolling();
    else stopLogPolling();
}

// --- Toast 通知 ---
function toast(msg, type = 'info', persistent = false) {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = msg;
    container.appendChild(el);
    if (!persistent) setTimeout(() => el.remove(), 3000);
    return el;
}

// --- 弹窗 ---
function showModal(id) { document.getElementById('modal-' + id).classList.add('show'); }
function hideModal(id) { document.getElementById('modal-' + id).classList.remove('show'); }

// 点击弹窗外部关闭
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay') && e.target.classList.contains('show')) {
        e.target.classList.remove('show');
    }
});

// --- 路由决策日志 ---
async function loadRoutingLog() {
    const container = document.getElementById('routing-log');
    try {
        const resp = await apiFetch(API + '/api/routing/log');
        const data = await resp.json();
        const decisions = data.decisions || [];
        if (!decisions.length) {
            container.innerHTML = '<div class="empty-state"><h4>暂无路由记录</h4><p>发送 model=auto 的请求后，这里会显示路由决策过程</p></div>';
            return;
        }
        container.innerHTML = decisions.map(d => {
            const strategy = escapeHtml(d.strategy || '');
            const strategyColor = strategy.includes('快速') ? 'var(--accent-green)' :
                                  strategy.includes('LLM') ? 'var(--accent-blue)' :
                                  strategy.includes('缓存') ? 'var(--accent-purple)' : 'var(--accent-yellow)';
            const cachedTag = d.cached ? '<span style="font-size:0.65rem;padding:1px 6px;border-radius:8px;background:rgba(139,92,246,0.12);color:var(--accent-purple)">⚡ 缓存</span>' : '';
            const selected = escapeHtml(d.selected || '-');
            const candidates = (d.candidates || []).map(c => {
                const ce = escapeHtml(c);
                const isSelected = d.selected && d.selected.endsWith(c);
                return `<span style="font-size:0.7rem;padding:2px 6px;border-radius:6px;background:${isSelected ? 'rgba(16,185,129,0.15)' : 'rgba(100,116,139,0.1)'};color:${isSelected ? 'var(--accent-green)' : 'var(--text-muted)'};font-weight:${isSelected ? '600' : '400'}">${ce}</span>`;
            }).join(' ');
            return `<div style="padding:12px;background:var(--bg-primary);border-radius:8px;margin-bottom:8px;border:1px solid var(--border)">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
                    <div style="display:flex;align-items:center;gap:8px">
                        <span style="font-size:0.75rem;font-weight:600;color:${strategyColor}">${strategy}</span>
                        ${cachedTag}
                    </div>
                    <span style="font-size:0.7rem;color:var(--text-muted)">${escapeHtml(d.timestamp || '')}</span>
                </div>
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
                    <span style="font-size:0.7rem;color:var(--text-muted)">选择:</span>
                    <span style="font-size:0.8rem;font-weight:600;color:var(--accent-green)">${selected}</span>
                </div>
                ${d.user_hint ? `<div style="font-size:0.7rem;color:var(--text-secondary);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📝 ${escapeHtml(d.user_hint)}</div>` : ''}
                <div style="display:flex;gap:4px;flex-wrap:wrap">${candidates}</div>
            </div>`;
        }).join('');
    } catch(e) {
        container.innerHTML = `<div class="empty-state"><h4>加载失败</h4><p>${e.message}</p></div>`;
    }
}

// --- 使用量 ---
async function loadUsage() {
    try {
        const resp = await fetch(API + '/v1/usage');
        const data = await resp.json();
        const grid = document.getElementById('usage-grid');

        if (data.stats.length === 0) {
            grid.innerHTML = '<div class="empty-state"><h4>暂无使用数据</h4><p>请先配置并启用模型</p></div>';
            return;
        }

        // 拉取模型能力信息
        let capMap = {};
        try {
            const capResp = await apiFetch(API + '/api/capabilities');
            const capData = await capResp.json();
            const caps = capData.capabilities || capData;
            for (const [key, val] of Object.entries(caps)) {
                const parts = key.split('/');
                const modelId = parts.slice(1).join('/');
                capMap[modelId] = val;
            }
        } catch(e) {}

        let blacklistSet = new Set();
        let blacklistMap = {};
        try {
            const blResp = await apiFetch(API + '/api/blacklist');
            const blData = await blResp.json();
            (blData.blacklist || []).forEach(b => { blacklistSet.add(b.model); blacklistMap[b.model] = b; });
        } catch(e) {}

        let breakerMap = {};
        try {
            const bkResp = await apiFetch(API + '/api/routing/breaker');
            const bkData = await bkResp.json();
            breakerMap = bkData.breakers || {};
        } catch(e) {}

        let payloadLimits = {};
        try {
            const plResp = await apiFetch(API + '/api/payload-limits');
            const plData = await plResp.json();
            payloadLimits = plData.limits || {};
        } catch(e) {}

        grid.innerHTML = data.stats.map(s => {
            const pct = s.rpd_limit > 0 ? (s.today_requests / s.rpd_limit * 100) : 0;
            const color = pct > 80 ? 'red' : pct > 50 ? 'yellow' : 'green';
            const blKey = s.provider + ':' + s.model;
            const is429 = blacklistSet.has(blKey);
            const isBroken = !!breakerMap[s.provider];
            const breakerRemain = isBroken ? (breakerMap[s.provider].remaining_seconds || 0) : 0;
            const breakerRemainStr = breakerRemain > 60 ? Math.round(breakerRemain/60) + 'min' : breakerRemain + 's';
            const statusClass = (is429 || isBroken) ? 'unavailable' : (s.available ? 'available' : 'unavailable');
            const remaining = is429 ? (blacklistMap[blKey] || {}).remaining_seconds || 0 : 0;
            const remainStr = remaining > 3600 ? Math.round(remaining/3600) + 'h' : remaining > 60 ? Math.round(remaining/60) + 'min' : remaining + 's';
            const statusText = isBroken ? '🔥 熔断中（' + breakerRemainStr + '后恢复）' : is429 ? '🚫 429限流（' + remainStr + '后恢复）' : (s.available ? '● 可用' : '● 不可用');
            const borderStyle = isBroken ? 'border-left:3px solid #e74c3c' : is429 ? 'border-left:3px solid var(--accent-red)' : '';
            const unblockBtn = is429 ? `<button class="btn btn-ghost btn-sm" style="font-size:0.65rem;margin-top:4px" onclick="unblock429('${escapeHtml(s.provider)}','${escapeHtml(s.model)}')">🔓 立即解除</button>` : '';
            const resetBreakerBtn = isBroken ? `<button class="btn btn-ghost btn-sm" style="font-size:0.65rem;margin-top:4px;color:#e74c3c" onclick="resetBreaker('${escapeHtml(s.provider)}')">⚡ 重置熔断</button>` : '';
            const cap = capMap[s.model];
            const capHtml = cap ? buildCapBadges(cap) : '';
            return `<div class="stat-card" style="${borderStyle}">
                <div class="model-header">
                    <span class="model-name">${escapeHtml(s.model)}</span>
                    <span class="provider-tag">${escapeHtml(s.provider)}</span>
                </div>
                <span class="availability ${statusClass}">${statusText}</span>
                ${unblockBtn}${resetBreakerBtn}
                ${capHtml}
                <div class="progress-container">
                    <div class="progress-label">
                        <span>每日用量</span>
                        <span>${s.today_requests} / ${s.rpd_limit || '∞'}</span>
                    </div>
                    <div class="progress-bar"><div class="fill ${color}" style="width:${Math.min(pct,100)}%"></div></div>
                </div>
                <div class="stat-detail">
                    <div class="stat-item"><span class="label">今日请求</span><span class="value">${s.today_requests}</span></div>
                    <div class="stat-item"><span class="label">每日上限</span><span class="value">${s.rpd_limit || '无限制'}</span></div>
                    <div class="stat-item"><span class="label">本分钟</span><span class="value">${s.minute_requests}</span></div>
                    <div class="stat-item"><span class="label">RPM 上限</span><span class="value">${s.rpm_limit || '无限制'}</span></div>
                    ${(() => { const plKey = s.provider + '/' + s.model; const pl = payloadLimits[plKey]; if (!pl) return ''; const kb = (pl.max_bytes / 1024).toFixed(1); return '<div class="stat-item"><span class="label">Payload 上限</span><span class="value" style="color:var(--accent-yellow)">⚠ ' + kb + ' KB</span></div>'; })()}
                </div>
            </div>`;
        }).join('');

        document.getElementById('last-refresh').textContent = '更新于 ' + new Date().toLocaleTimeString();
    } catch (e) {
        toast('加载使用量失败: ' + e.message, 'error');
    }
}

async function unblock429(provider, model) {
    try {
        const resp = await apiFetch(API + `/api/blacklist/clear?provider=${provider}&model=${encodeURIComponent(model)}`, {method: 'DELETE'});
        const data = await resp.json();
        toast(`已解除 ${model} 的 429 限流状态`, 'success');
        loadUsage();
    } catch(e) {
        toast('解除失败: ' + e.message, 'error');
    }
}

async function resetBreaker(provider) {
    try {
        const url = provider ? API + `/api/breaker/reset?provider=${provider}` : API + '/api/breaker/reset';
        const resp = await apiFetch(url, {method: 'POST'});
        const data = await resp.json();
        toast(`已重置 ${provider || '全部'} 厂商的熔断状态（清除 ${data.cleared} 个）`, 'success');
        loadUsage();
    } catch(e) {
        toast('重置熔断失败: ' + e.message, 'error');
    }
}

// --- 厂商卡片 ---
let currentProvider = null;
let lastSearchView = 'catalog';
let providerCatalogData = {};

function isPaidProvider(providerId) {
    const cat = providerCatalogData[providerId];
    return cat && cat.billing_type === 'paid';
}

function confirmPaidTest(providerId, modelId) {
    const name = (providerMeta[providerId] || providerCatalogData[providerId] || {}).name || providerId;
    return confirm(`⚠️ ${name} 是付费厂商，能力测试将消耗约 800~2000 tokens。\n\n模型: ${modelId || '全部'}\n\n确认要继续测试吗？`);
}

const providerMeta = {
    google: { name: 'Google AI Studio', icon: 'G', color: 'icon-google', desc: 'Gemini 系列免费模型', url: 'https://aistudio.google.com/', guide: '获取 API Key: https://aistudio.google.com/apikey\n\n免费额度:\n- Gemini 2.5 Pro: 25次/天, 5次/分\n- Gemini 2.5 Flash: 500次/天, 10次/分\n- Gemini 2.0 Flash: 1500次/天, 15次/分' },
    groq: { name: 'Groq', icon: 'Q', color: 'icon-groq', desc: '高速推理开源模型', url: 'https://console.groq.com/', guide: '注册后在 https://console.groq.com/keys 获取 API Key\n\n兼容 OpenAI SDK 格式\n免费额度: 约 14400次/天, 30次/分' },
    github: { name: 'GitHub Models', icon: 'H', color: 'icon-github', desc: 'GitHub 模型市场', url: 'https://github.com/marketplace/models', guide: '使用 GitHub Personal Access Token\nSettings > Developer settings > Tokens\n\n接口: https://models.inference.ai.azure.com\n免费额度: 约 50次/天' },
    cursor: { name: 'Cursor', icon: 'C', color: 'icon-cursor', desc: 'Cursor Agent CLI（支持 plan/ask/agent）', url: 'https://www.cursor.com/', guide: '无需 API Key，安装 Cursor CLI 并登录即可\n\n安装: curl https://cursor.com/install -fsS | bash\n验证: agent --version\n登录: agent login\n\n推荐模型: auto / composer-2.5\n\n通过 model-proxy 扩展字段控制执行模式:\n- mode=plan  只读规划（--plan）\n- mode=ask   只读问答（--mode ask）\n- mode=agent 默认，可改代码/执行命令\n\n可选参数:\n- force=true    强制执行（plan/ask 下无效）\n- sandbox=enabled|disabled\n\n示例:\nPOST /v1/chat/completions\n{"model":"auto","messages":[...],"mode":"plan"}' },
    cerebras: { name: 'Cerebras', icon: 'B', color: 'icon-groq', desc: '极速推理 ~2000 tok/s', url: 'https://cloud.cerebras.ai/', guide: '注册后在 Dashboard 获取 API Key\n\n兼容 OpenAI SDK:\nbase_url = https://api.cerebras.ai/v1\n\n免费额度: 约 1000次/天, 推理速度极快' },
    sambanova: { name: 'SambaNova', icon: 'S', color: 'icon-github', desc: 'Llama/DeepSeek 免费推理', url: 'https://cloud.sambanova.ai/', guide: '注册后在 API 页面获取 Key\n\n兼容 OpenAI SDK:\nbase_url = https://api.sambanova.ai/v1\n\n支持 405B 超大模型和 DeepSeek' },
    openrouter: { name: 'OpenRouter', icon: 'R', color: 'icon-cursor', desc: '聚合平台，部分模型免费', url: 'https://openrouter.ai/', guide: 'https://openrouter.ai/keys 获取 Key\n\n兼容 OpenAI SDK:\nbase_url = https://openrouter.ai/api/v1\n\n模型名带 :free 后缀的完全免费\n约 200次/天' },
    cloudflare: { name: 'Cloudflare', icon: 'F', color: 'icon-google', desc: 'Workers AI 每日万次免费', url: 'https://ai.cloudflare.com/', guide: 'Dashboard > AI > Workers AI\n获取 Account ID 和 API Token\n\nAPI Key 格式: account_id:api_token\n\n每日 10,000 neurons 免费（约数千次请求）' },
    huggingface: { name: 'HuggingFace', icon: 'H', color: 'icon-github', desc: '免费推理数千开源模型', url: 'https://huggingface.co/inference-api', guide: 'https://huggingface.co/settings/tokens 创建 Token\n\n支持数千个开源模型免费推理\n有速率限制但无需付费\n支持 OpenAI 兼容 /v1/chat/completions' },
    mistral: { name: 'Mistral AI', icon: 'M', color: 'icon-groq', desc: '官方平台免费层', url: 'https://console.mistral.ai/', guide: 'https://console.mistral.ai/api-keys/ 获取 Key\n\n兼容 OpenAI SDK:\nbase_url = https://api.mistral.ai/v1\n\nCodestral 代码模型免费（非商业用途）' },
    ollama: { name: 'Ollama 本地', icon: 'O', color: 'icon-cursor', desc: '本地模型，无需 API Key', url: 'https://ollama.com/', guide: '无需 API Key，运行 Ollama 后自动发现本地已安装模型\n\n安装: https://ollama.com/download\n拉取模型: ollama pull qwen3:8b\n\n可在 API Key 字段填写自定义 base_url\n默认: http://localhost:11434' },
    dashscope: { name: '阿里百炼', icon: 'D', color: 'icon-google', desc: '通义千问系列（付费）', url: 'https://bailian.console.aliyun.com/', guide: '⚠️ 付费厂商：每次能力测试约消耗 800~2000 tokens\n\nhttps://bailian.console.aliyun.com/\n开通后在 API-KEY 管理页面创建\n\n兼容 OpenAI SDK:\nbase_url = https://dashscope.aliyuncs.com/compatible-mode/v1' },
    spark: { name: '讯飞星火', icon: 'X', color: 'icon-google', desc: '星火/DeepSeek/GLM/Qwen 多系列（付费）', url: 'https://maas.xfyun.cn/', guide: '⚠️ 付费厂商：每次能力测试约消耗 800~2000 tokens\n\nhttps://maas.xfyun.cn/ 注册后获取 API Key\n\n兼容 OpenAI SDK:\nbase_url = https://maas-api.cn-huabei-1.xf-yun.com/v2\n\n推荐模型:\n- Spark X2 (xsparkx2) — 星火旗舰 192K\n- DeepSeek V4 Flash (xopdeepseekv4flash) — 1M 上下文\n- DeepSeek V3.2 (xopdeepseekv32) — 支持 Function Calling\n\nFunction Calling 仅 DeepSeek V3.2 和 GLM-4.7 系列支持' },
    agnes: { name: 'Agnes AI', icon: 'A', color: 'icon-groq', desc: '免费全模态 API（文本/图像/视频）', url: 'https://agnes-ai.com/', guide: '🆓 免费厂商（Sapiens AI 推出），国内可直接访问\n\nhttps://platform.agnes-ai.com/ 注册获取 API Key\n\n兼容 OpenAI SDK:\nbase_url = https://apihub.agnes-ai.com/v1\n\n━━━ 文本模型 ━━━\nAgnes 2.0 Flash — 旗舰，支持 thinking + tool calling\n端点: POST /v1/chat/completions\n\n━━━ 图像模型（已支持）━━━\nAgnes Image 2.1 Flash — 文生图/图生图/编辑\n端点: POST /v1/images/generations\n示例: {"model":"agnes-image-2.1-flash","prompt":"a cat","size":"1024x1024"}\n\n━━━ 视频模型（已支持）━━━\nAgnes Video V2.0 — 文生视频/图生视频/关键帧\n创建: POST /v1/videos\n轮询: GET /v1/videos/{task_id}?provider_id=agnes\n参数: num_frames 需满足 8n+1 且 ≤441\n示例: {"model":"agnes-video-v2.0","prompt":"cat on beach","num_frames":121}' },
    sensenova: { name: '商汤日日新', icon: 'N', color: 'icon-google', desc: 'SenseNova 多模态 Agent + 推理（免费）', url: 'https://platform.sensenova.cn/', guide: '🆓 免费厂商（公测期每 5 小时 1500 次调用）\n\nhttps://platform.sensenova.cn/console 注册并完成实名认证\n在「管理中心 → API Key 管理」创建 Key\n\n兼容 OpenAI SDK:\nbase_url = https://token.sensenova.cn/v1\n\n━━━ 可用模型 ━━━\n\nSenseNova 6.7 Flash-Lite\n- 轻量多模态 Agent 模型，256K 上下文\n- 原生图文理解，支持 Tool Calling\n- 每 5h 1500 次免费\n\nDeepSeek V4 Flash\n- 深度推理模型，256K 上下文\n- 支持 Think/No-think 模式\n- 每 5h 150 次免费\n\nSenseNova U1 Fast\n- 图像生成模型\n- 每 5h 1500 次免费' },
};

async function loadProviderCards() {
    try {
        const [configResp, catalogResp] = await Promise.all([
            apiFetch(API + '/api/config'),
            apiFetch(API + '/api/catalog/providers'),
        ]);
        const configData = await configResp.json();
        const catalogData = await catalogResp.json();
        const grid = document.getElementById('providers-grid');

        // 合并 catalog 和 config 信息，按优先级排序
        providerCatalogData = catalogData.providers || {};
        const allProviders = {};
        for (const [id, cat] of Object.entries(providerCatalogData)) {
            const conf = configData.providers?.[id] || {};
            allProviders[id] = {
                ...cat,
                enabled: cat.enabled || conf.enabled || false,
                priority: cat.priority ?? conf.priority ?? 99,
                has_api_key: conf.has_api_key || false,
                models: conf.models || [],
            };
        }

        const sorted = Object.entries(allProviders).sort((a, b) => a[1].priority - b[1].priority);

        grid.innerHTML = sorted.map(([id, prov]) => {
            const meta = providerMeta[id] || { name: prov.name || id, icon: id[0].toUpperCase(), color: '', desc: prov.description || '', guide: '' };
            const modelCount = prov.models?.length || 0;
            const enabledCount = (prov.models || []).filter(m => m.enabled).length;
            const paidBadge = prov.billing_type === 'paid' ? '<span style="font-size:0.6rem;padding:1px 5px;background:rgba(245,158,11,0.2);color:#f59e0b;border-radius:8px;margin-left:6px;white-space:nowrap">💰 付费</span>' : '';
            return `<div class="stat-card draggable" draggable="false" data-provider-id="${id}" onclick="openProviderModal('${id}')">
                <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
                    <div style="display:flex;align-items:center;gap:10px;min-width:0">
                        <span class="priority-badge drag-handle" title="拖拽排序">${prov.priority}</span>
                        <div class="model-icon ${meta.color}">${meta.icon}</div>
                        <div style="min-width:0">
                            <div class="model-name" style="font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${meta.name}${paidBadge}</div>
                            <div style="font-size:0.7rem;color:var(--text-muted);margin-top:1px">${meta.desc}</div>
                        </div>
                    </div>
                    <label class="toggle" onclick="event.stopPropagation()">
                        <input type="checkbox" ${prov.enabled ? 'checked' : ''} onchange="toggleProvider('${id}',this.checked)">
                        <span class="slider"></span>
                    </label>
                </div>
                <div style="margin-top:auto;padding-top:10px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid rgba(51,65,85,0.3)">
                    <div style="display:flex;gap:14px;align-items:flex-end">
                        <div class="stat-item"><span class="label">模型</span><span class="value">${modelCount}</span></div>
                        <div class="stat-item"><span class="label">已启用</span><span class="value">${enabledCount}</span></div>
                        <div class="stat-item"><span class="label">Key</span><span class="value" style="color:${prov.has_api_key ? 'var(--accent-green)' : 'var(--accent-red)'}">${prov.has_api_key ? '✓' : '—'}</span></div>
                    </div>
                    ${id === 'ollama' ? '<button class="guide-btn" title="刷新本地模型" style="margin-right:4px" onclick="event.stopPropagation();refreshOllamaModels()">↻</button>' : ''}
                    <button class="guide-btn" title="接入说明" onclick="event.stopPropagation();showGuide('${id}')">?</button>
                </div>
            </div>`;
        }).join('');

        initDragAndDrop();
    } catch (e) {
        toast('加载厂商列表失败', 'error');
    }
}

// --- 模型拖拽排序 ---
let modelDragSrc = null;

function initModelDragAndDrop() {
    const container = document.getElementById('provider-models-list');
    const items = container.querySelectorAll('.model-item');
    items.forEach(item => {
        const handle = item.querySelector('.drag-handle');
        if (handle) {
            handle.addEventListener('mousedown', () => { item.setAttribute('draggable', 'true'); });
            handle.addEventListener('mouseup', () => { item.setAttribute('draggable', 'false'); });
        }
        item.addEventListener('dragstart', onModelDragStart);
        item.addEventListener('dragover', onModelDragOver);
        item.addEventListener('dragenter', onModelDragEnter);
        item.addEventListener('dragleave', onModelDragLeave);
        item.addEventListener('drop', onModelDrop);
        item.addEventListener('dragend', onModelDragEnd);
    });
}

function onModelDragStart(e) {
    modelDragSrc = this;
    this.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', this.dataset.modelId);
}
function onModelDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
function onModelDragEnter(e) { e.preventDefault(); this.classList.add('drag-over'); }
function onModelDragLeave() { this.classList.remove('drag-over'); }
function onModelDragEnd() {
    this.classList.remove('dragging');
    this.setAttribute('draggable', 'false');
    document.querySelectorAll('.model-item.drag-over').forEach(el => el.classList.remove('drag-over'));
}

function onModelDrop(e) {
    e.stopPropagation();
    e.preventDefault();
    this.classList.remove('drag-over');
    if (modelDragSrc !== this) {
        const container = document.getElementById('provider-models-list');
        const allItems = [...container.querySelectorAll('.model-item')];
        const srcIdx = allItems.indexOf(modelDragSrc);
        const dstIdx = allItems.indexOf(this);
        if (srcIdx < dstIdx) {
            this.parentNode.insertBefore(modelDragSrc, this.nextSibling);
        } else {
            this.parentNode.insertBefore(modelDragSrc, this);
        }
        saveModelOrder();
    }
}

async function saveModelOrder() {
    const container = document.getElementById('provider-models-list');
    const items = [...container.querySelectorAll('.model-item')];
    const updates = items.map((item, idx) => {
        const badge = item.querySelector('.priority-badge');
        if (badge) badge.textContent = idx + 1;
        return { model_id: item.dataset.modelId, priority: idx + 1 };
    });
    try {
        for (const u of updates) {
            await apiFetch(API + `/api/config/model/priority?provider=${currentProvider}&model_name=${u.model_id}&priority=${u.priority}`, {method:'POST'});
        }
        toast('模型优先级已更新', 'success');
    } catch (e) {
        toast('保存模型排序失败', 'error');
    }
}

// --- 打开厂商模型弹窗 ---
async function openProviderModal(providerId) {
    currentProvider = providerId;
    const meta = providerMeta[providerId] || { name: providerId };
    document.getElementById('provider-modal-title').textContent = meta.name + ' - 模型管理';
    document.getElementById('provider-search-input').value = '';
    document.getElementById('provider-search-results').innerHTML = '';
    showModal('provider-models');
    await loadProviderModels(providerId);
}

function formatCapErrorLabel(error) {
    const err = (error || '').toLowerCase();
    if (err.includes('429') || err.includes('rate_limit')) return '限流';
    if (err.includes('out of usage') || err.includes('usage limit')) return '额度用尽';
    if (err.includes('503') || err.includes('unavailable')) return '不可用';
    if (err.includes('authentication') || err.includes('login')) return '未认证';
    return '异常';
}

function buildCapBadges(cap) {
    if (!cap) return '';
    const items = [
        [cap.tool_calling, '🔧 工具调用', 'rgba(59,130,246,0.12)', 'var(--accent-blue)'],
        [cap.multi_turn_tc, '🔄 多轮对话', 'rgba(139,92,246,0.12)', 'var(--accent-purple)'],
        [cap.chinese, '🇨🇳 中文', 'rgba(245,158,11,0.12)', 'var(--accent-yellow)'],
        [cap.vision, '👁 视觉', 'rgba(6,182,212,0.12)', 'var(--accent-cyan)'],
        [cap.json_mode, '📋 JSON', 'rgba(16,185,129,0.12)', 'var(--accent-green)'],
        [cap.streaming, '⚡ 流式', 'rgba(139,92,246,0.12)', 'var(--accent-purple)'],
        [cap.reasoning, '🧠 推理', 'rgba(59,130,246,0.12)', 'var(--accent-blue)'],
    ];
    const latTag = cap.latency_ms ? `<span style="font-size:0.6rem;padding:1px 5px;border-radius:6px;background:rgba(100,116,139,0.1);color:${cap.latency_ms < 3000 ? 'var(--accent-green)' : cap.latency_ms < 8000 ? 'var(--accent-yellow)' : 'var(--accent-red)'}">${(cap.latency_ms/1000).toFixed(1)}s</span>` : '';
    const badges = items.filter(([v]) => v).map(([, text, bg, fg]) =>
        `<span style="font-size:0.6rem;padding:1px 5px;border-radius:6px;background:${bg};color:${fg}">${text}</span>`
    ).join('');
    const errTag = cap.error ? `<span style="font-size:0.6rem;color:var(--accent-red)" title="${escapeHtml(cap.error)}">⚠ ${formatCapErrorLabel(cap.error)}</span>` : '';
    return `<div style="display:flex;gap:3px;flex-wrap:wrap;align-items:center;margin-top:4px">${latTag}${badges}${errTag}</div>`;
}

function buildCapSummary(cap) {
    if (!cap || cap.error) return '';
    const descs = [];
    if (cap.tool_calling) descs.push('支持工具调用');
    if (cap.multi_turn_tc) descs.push('多轮对话稳定');
    else if (cap.tool_calling && cap.mt_issue === 'loop_call') descs.push('多轮会循环调用');
    if (cap.chinese) descs.push('中文回答优秀');
    if (cap.vision) descs.push('支持图像理解');
    if (cap.json_mode) descs.push('结构化JSON输出');
    if (cap.streaming) descs.push('支持流式输出');
    if (cap.reasoning) descs.push('逻辑推理能力强');
    if (!descs.length) return '';
    return `<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:3px">${descs.join(' · ')}</div>`;
}

async function loadProviderModels(providerId) {
    const [catalogResp, capResp, plResp] = await Promise.all([
        apiFetch(API + `/api/catalog/provider/${providerId}/models`),
        apiFetch(API + `/api/capabilities`).catch(() => null),
        apiFetch(API + `/api/payload-limits`).catch(() => null),
    ]);
    const data = await catalogResp.json();
    const allModels = data.models || [];
    const container = document.getElementById('provider-models-list');

    let capMap = {};
    if (capResp && capResp.ok) {
        const capData = await capResp.json();
        const caps = capData.capabilities || capData;
        for (const [key, val] of Object.entries(caps)) {
            const parts = key.split('/');
            if (parts[0] === providerId) {
                capMap[parts.slice(1).join('/')] = val;
            }
        }
    }

    let payloadLimits = {};
    if (plResp && plResp.ok) {
        const plData = await plResp.json();
        payloadLimits = plData.limits || {};
    }

    if (allModels.length === 0) {
        container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted)">该厂商目录中暂无模型，请从下方搜索或拉取添加</div>';
        return;
    }

    const sorted = allModels.sort((a,b) => {
        if (a.enabled && !b.enabled) return -1;
        if (!a.enabled && b.enabled) return 1;
        return (a.priority||99) - (b.priority||99);
    });

    container.innerHTML = sorted.map(m => {
        const isEnabled = m.enabled;
        const opacity = isEnabled ? '1' : '0.6';
        const borderColor = isEnabled ? 'var(--border)' : 'var(--bg-tertiary)';
        const cap = capMap[m.id];
        const capBadgesHtml = buildCapBadges(cap);
        const capSummaryHtml = buildCapSummary(cap);
        const noCap = !cap ? '<span style="font-size:0.65rem;color:var(--text-muted)">未测试</span>' : '';
        return `<div class="model-item" draggable="false" data-model-id="${m.id}" style="display:flex;align-items:center;justify-content:space-between;padding:12px;background:var(--bg-primary);border-radius:8px;margin-bottom:8px;border:1px solid ${borderColor};opacity:${opacity}">
            <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
                <span class="priority-badge drag-handle" title="拖拽排序">${isEnabled ? (m.priority || 99) : '-'}</span>
                <div style="flex:1;min-width:0">
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                        <span style="font-weight:600;font-size:0.9rem">${m.name || m.id}</span>
                        ${noCap}
                    </div>
                    ${capBadgesHtml}
                    ${capSummaryHtml}
                    <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px">RPD: ${m.default_rpd || '∞'} | RPM: ${m.default_rpm || '∞'}${(() => { const plKey = providerId + '/' + m.id; const pl = payloadLimits[plKey]; if (!pl) return ''; const kb = (pl.max_bytes / 1024).toFixed(1); return ' | <span style="color:var(--accent-yellow)" title="收到 413 后自动检测的上限（触发 ' + pl.hit_count + ' 次，' + pl.updated_at + '）">⚠ Payload 上限: ' + kb + ' KB</span>'; })()}</div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:4px;flex-shrink:0">
                <button class="btn btn-ghost btn-sm" style="font-size:0.6rem;padding:2px 6px" onclick="event.stopPropagation();testSingleModel('${providerId}','${m.id}')" title="检测此模型的 tool calling、中文、视觉等能力">🔍 测试</button>
                ${cap ? `<button class="btn btn-ghost btn-sm" style="font-size:0.6rem;padding:2px 6px" onclick="event.stopPropagation();editCapabilities('${providerId}','${m.id}')" title="手动编辑能力标记">✏️ 编辑</button>` : `<button class="btn btn-ghost btn-sm" style="font-size:0.6rem;padding:2px 6px" onclick="event.stopPropagation();editCapabilities('${providerId}','${m.id}')" title="手动标记能力（无需测试）">➕ 标记</button>`}
                ${cap ? `<button class="btn btn-ghost btn-sm" style="font-size:0.6rem;padding:2px 6px;color:var(--accent-red)" onclick="event.stopPropagation();deleteCapability('${providerId}','${m.id}')" title="清除此模型的能力缓存">🗑</button>` : ''}
                <label class="toggle" onclick="event.stopPropagation()">
                    <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleModelAndReload('${providerId}','${m.id}',this.checked)">
                    <span class="slider"></span>
                </label>
            </div>
        </div>`;
    }).join('');

    initModelDragAndDrop();
}

async function testSingleModel(providerId, modelId) {
    if (isPaidProvider(providerId) && !confirmPaidTest(providerId, modelId)) return;
    const loadingToast = toast(`正在测试 ${modelId} 的能力（约 15-30 秒）...`, 'info', true);
    try {
        const resp = await apiFetch(API + `/api/capabilities/test?provider=${providerId}&model=${encodeURIComponent(modelId)}&force=true`, {method: 'POST'});
        const data = await resp.json();
        const results = data.results || [];
        if (results.length > 0) {
            const r = results[0];
            if (r.not_installed) {
                loadingToast.remove();
                if (confirm(`模型 ${modelId} 未在本地安装。\n\n是否立即下载安装？（可能需要几分钟到几十分钟）`)) {
                    pullOllamaModel(modelId);
                }
                return;
            }
            const caps = [];
            if (r.tool_calling) caps.push('工具调用');
            if (r.multi_turn_tc) caps.push('多轮对话');
            if (r.chinese) caps.push('中文');
            if (r.vision) caps.push('视觉');
            if (r.json_mode) caps.push('JSON');
            if (r.streaming) caps.push('流式');
            if (r.reasoning) caps.push('推理');
            const capStr = caps.length > 0 ? caps.join('、') : '无特殊能力';
            const latStr = r.latency_ms ? `${r.latency_ms}ms` : '-';
            if (r.error) {
                toast(`${modelId} 测试失败: ${formatCapErrorLabel(r.error)} — ${r.error.slice(0, 80)}`, 'error');
            } else {
                toast(`${modelId} 测试完成: ${capStr} | 延迟 ${latStr}`, 'success');
            }
        } else {
            toast(`${modelId} 测试完成（无结果）`, 'info');
        }
        loadProviderModels(providerId);
    } catch(e) {
        toast(`测试失败: ${e.message}`, 'error');
    } finally {
        loadingToast.remove();
    }
}

async function editCapabilities(providerId, modelId) {
    const capResp = await apiFetch(API + `/api/capabilities`).catch(() => null);
    let existing = {};
    if (capResp && capResp.ok) {
        const capData = await capResp.json();
        const caps = capData.capabilities || capData;
        existing = caps[`${providerId}/${modelId}`] || {};
    }

    const fields = [
        {key: 'tool_calling', label: '🔧 工具调用', desc: '支持 OpenAI function calling 协议'},
        {key: 'multi_turn_tc', label: '🔄 多轮对话', desc: '支持多轮 tool calling'},
        {key: 'chinese', label: '🇨🇳 中文', desc: '支持中文输出'},
        {key: 'vision', label: '👁 视觉', desc: '支持图像输入'},
        {key: 'json_mode', label: '📋 JSON', desc: '支持结构化 JSON 输出'},
        {key: 'streaming', label: '⚡ 流式', desc: '支持流式输出'},
        {key: 'reasoning', label: '🧠 推理', desc: '具备逻辑推理能力'},
        {key: 'available', label: '✅ 可用', desc: '模型可正常调用'},
    ];

    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center';
    overlay.innerHTML = `<div style="background:var(--bg-secondary);border-radius:12px;padding:24px;width:420px;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.3)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <div>
                <div style="font-weight:700;font-size:1rem">编辑模型能力</div>
                <div style="font-size:0.75rem;color:var(--text-muted);margin-top:2px">${providerId} / ${modelId}</div>
            </div>
            <button onclick="this.closest('div[style*=fixed]').remove()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text-muted)">✕</button>
        </div>
        ${existing.manual_override ? '<div style="font-size:0.7rem;padding:4px 8px;background:rgba(245,158,11,0.1);color:var(--accent-yellow);border-radius:6px;margin-bottom:12px">⚠ 已手动覆盖（优先于自动测试结果）</div>' : ''}
        <div style="display:flex;flex-direction:column;gap:8px">
            ${fields.map(f => `<label style="display:flex;align-items:center;gap:10px;padding:8px;border-radius:8px;background:var(--bg-primary);cursor:pointer" title="${f.desc}">
                <input type="checkbox" id="cap-edit-${f.key}" ${existing[f.key] ? 'checked' : ''} style="width:16px;height:16px;accent-color:var(--accent-blue)">
                <span style="font-size:0.85rem">${f.label}</span>
                <span style="font-size:0.65rem;color:var(--text-muted);margin-left:auto">${f.desc}</span>
            </label>`).join('')}
        </div>
        <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end">
            <button class="btn btn-ghost btn-sm" onclick="this.closest('div[style*=fixed]').remove()">取消</button>
            <button class="btn btn-primary btn-sm" onclick="saveCapabilities('${providerId}','${modelId}',this.closest('div[style*=fixed]'))">保存</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

async function saveCapabilities(providerId, modelId, overlay) {
    const fields = ['tool_calling', 'multi_turn_tc', 'chinese', 'vision', 'json_mode', 'streaming', 'reasoning', 'available'];
    const body = {};
    for (const key of fields) {
        const el = document.getElementById(`cap-edit-${key}`);
        if (el) body[key] = el.checked;
    }
    try {
        const resp = await apiFetch(API + `/api/capabilities/${providerId}/${encodeURIComponent(modelId)}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            toast(`${modelId} 能力已更新`, 'success');
            overlay.remove();
            loadProviderModels(providerId);
        } else {
            const err = await resp.json().catch(() => ({}));
            toast(err.detail || '保存失败', 'error');
        }
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function deleteCapability(providerId, modelId) {
    if (!confirm(`确定清除 ${modelId} 的能力缓存？\n清除后需要重新测试才能显示能力标签。`)) return;
    try {
        const resp = await apiFetch(API + `/api/capabilities/${providerId}/${encodeURIComponent(modelId)}`, {method: 'DELETE'});
        if (resp.ok) {
            toast(`${modelId} 能力缓存已清除`, 'success');
            loadProviderModels(providerId);
        } else {
            const err = await resp.json().catch(() => ({}));
            toast(err.detail || '清除失败', 'error');
        }
    } catch (e) {
        toast('清除失败: ' + e.message, 'error');
    }
}

async function pullOllamaModel(modelId) {
    const progressToast = toast(`正在下载 ${modelId}...`, 'info', true);
    try {
        const resp = await apiFetch(API + `/api/provider/ollama/pull?model=${encodeURIComponent(modelId)}`, {method: 'POST'});
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {stream: true});
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ') || line.trim() === 'data: [DONE]') continue;
                try {
                    const ev = JSON.parse(line.slice(6));
                    if (ev.total && ev.completed) {
                        const pct = Math.round(ev.completed / ev.total * 100);
                        progressToast.querySelector('.toast-msg').textContent = `下载 ${modelId}: ${pct}%`;
                    } else if (ev.status === 'success') {
                        progressToast.remove();
                        toast(`${modelId} 下载完成！正在刷新模型列表...`, 'success');
                        await refreshOllamaModels();
                        loadProviderModels('ollama');
                        return;
                    } else if (ev.status === 'error') {
                        progressToast.remove();
                        toast(`下载失败: ${ev.error}`, 'error');
                        return;
                    } else if (ev.status) {
                        progressToast.querySelector('.toast-msg').textContent = `${modelId}: ${ev.status}`;
                    }
                } catch(_) {}
            }
        }
        progressToast.remove();
    } catch(e) {
        progressToast.remove();
        toast(`下载失败: ${e.message}`, 'error');
    }
}

async function toggleModelAndReload(providerId, modelId, enabled) {
    if (enabled) {
        const resp = await apiFetch(API + `/api/catalog/provider/${providerId}/models`);
        const data = await resp.json();
        const enabledModels = (data.models || []).filter(m => m.enabled);
        const maxPri = enabledModels.reduce((max, m) => Math.max(max, m.priority || 0), 0);
        const newPri = maxPri + 1;
        await apiFetch(API + `/api/catalog/model/activate?provider_id=${providerId}&model_id=${modelId}&priority=${newPri}`, {method:'POST'});
        toast(`${modelId} 已启用（优先级 ${newPri}）`, 'success');
    } else {
        await apiFetch(API + `/api/config/model/delete?provider=${providerId}&model_name=${modelId}`, {method:'POST'});
        toast(`${modelId} 已停用`, 'success');
    }
    loadProviderModels(providerId);
    loadProviderCards();
}

// 兼容旧调用
async function loadModels() { await loadProviderCards(); }

// --- 配置 ---
async function loadConfig() {
    try {
        const resp = await apiFetch(API + '/api/config');
        const data = await resp.json();
        const grid = document.getElementById('config-grid');

        grid.innerHTML = Object.entries(data.providers).map(([name, prov]) => {
            const statusClass = prov.has_api_key ? 'configured' : 'not-configured';
            const statusText = prov.has_api_key ? '已配置' : '未配置';
            return `<div class="config-item">
                <div class="ci-header">
                    <span class="ci-name">${name.charAt(0).toUpperCase() + name.slice(1)}</span>
                    <div style="display:flex;align-items:center;gap:10px">
                        <span class="ci-status ${statusClass}">${statusText}</span>
                        <label class="toggle">
                            <input type="checkbox" ${prov.enabled ? 'checked' : ''}
                                onchange="toggleProvider('${name}', this.checked)">
                            <span class="slider"></span>
                        </label>
                    </div>
                </div>
                <div class="input-group">
                    <input type="password" id="key-${name}" placeholder="输入 ${name} API Key...">
                    <button class="btn btn-primary btn-sm" onclick="saveKey('${name}')">保存</button>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        toast('加载配置失败', 'error');
    }
}


// --- 操作 ---
async function toggleModel(provider, model, enabled) {
    const resp = await apiFetch(API + `/api/config/model/toggle?provider=${provider}&model_name=${model}&enabled=${enabled}`, {method:'POST'});
    const data = await resp.json();
    toast(data.message || `${model} 已${enabled ? '启用' : '禁用'}`, 'success');
}

async function toggleProvider(provider, enabled) {
    const resp = await apiFetch(API + `/api/provider/toggle?provider=${provider}&enabled=${enabled}`, {method:'POST'});
    const data = await resp.json();
    toast(data.message || `${provider} 已${enabled ? '启用' : '禁用'}`, 'success');
    loadProviderCards();
}

async function refreshOllamaModels() {
    toast('正在刷新 Ollama 本地模型…', 'info');
    try {
        const resp = await apiFetch(API + '/api/provider/ollama/refresh', {method:'POST'});
        const data = await resp.json();
        if (resp.ok) {
            const msg = data.new > 0
                ? `发现 ${data.new} 个新模型（共 ${data.total} 个）: ${data.new_models.join(', ')}`
                : `已是最新（共 ${data.total} 个本地模型）`;
            toast(msg, 'success');
            loadProviderCards();
        } else {
            toast(data.detail || '刷新失败', 'error');
        }
    } catch (e) {
        toast('无法连接 Ollama 服务', 'error');
    }
}

async function saveKey(provider) {
    const input = document.getElementById('key-' + provider);
    const key = input.value.trim();
    if (!key) { toast('请输入 API Key', 'error'); return; }
    const resp = await apiFetch(API + '/api/config/apikey', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider, api_key: key})});
    const data = await resp.json();
    input.value = '';
    toast(data.message || `${provider} API Key 已保存`, 'success');
    loadConfig();
    loadProviderCards();
}

function showPriorityModal(provider, model, current) {
    currentPriorityTarget = { provider, model };
    document.getElementById('priority-value').value = current;
    showModal('priority');
}

async function savePriority() {
    if (!currentPriorityTarget) return;
    const p = document.getElementById('priority-value').value;
    const { provider, model } = currentPriorityTarget;
    await apiFetch(API + `/api/config/model/priority?provider=${provider}&model_name=${model}&priority=${p}`, {method:'POST'});
    hideModal('priority');
    loadModels();
    toast('优先级已更新', 'success');
}

async function addModel() {
    // 兼容保留
    manualAddModel();
}

async function refreshModels() {
    toast('正在刷新...', 'info');
    loadProviderCards();
}

// --- 从运行配置删除模型 ---
async function deleteConfigModel(provider, modelId) {
    if (!confirm(`确认删除 ${modelId}？`)) return;
    const resp = await apiFetch(API + `/api/config/model/delete?provider=${provider}&model_name=${modelId}`, {method:'POST'});
    if (resp.ok) {
        toast(`${modelId} 已删除`, 'success');
        loadProviderModels(currentProvider);
        loadProviderCards();
    } else {
        const err = await resp.json();
        toast(err.detail || '删除失败', 'error');
    }
}

// --- 厂商弹窗内搜索 ---
let provSearchTimer = null;
function debouncedProviderSearch() {
    clearTimeout(provSearchTimer);
    provSearchTimer = setTimeout(searchProviderModels, 300);
}

async function searchProviderModels() {
    lastSearchView = 'catalog';
    const q = document.getElementById('provider-search-input').value.trim();
    const container = document.getElementById('provider-search-results');

    try {
        const resp = await apiFetch(API + `/api/catalog/search?q=${encodeURIComponent(q)}`);
        const data = await resp.json();
        const results = data.results.filter(m => m.provider_id === currentProvider);

        if (results.length === 0) {
            container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted);font-size:0.85rem">未找到匹配模型</div>';
            return;
        }

        container.innerHTML = results.map(m => {
            const isActive = m.enabled;
            return `<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--bg-primary);border-radius:6px;margin-bottom:6px;border:1px solid var(--border)">
                <div>
                    <div style="font-weight:500;font-size:0.85rem">${m.name || m.id}</div>
                    <div style="font-size:0.75rem;color:var(--text-muted)">${m.description || ''} | RPD: ${m.default_rpd || '∞'} RPM: ${m.default_rpm || '∞'}</div>
                </div>
                ${isActive
                    ? '<span style="font-size:0.75rem;color:var(--accent-green)">✓ 已启用</span>'
                    : `<button class="btn btn-primary btn-sm" onclick="activateFromCatalog('${m.provider_id}','${m.id}')">+ 启用</button>`
                }
            </div>`;
        }).join('');
    } catch (e) {
        toast('搜索失败: ' + e.message, 'error');
    }
}

async function testAllRemoteModels() {
    if (isPaidProvider(currentProvider) && !confirmPaidTest(currentProvider, null)) return;
    const container = document.getElementById('provider-search-results');
    container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--accent-cyan)"><div style="font-size:1.2rem;margin-bottom:8px">⏳</div>正在测试所有模型的能力...<br><span style="font-size:0.75rem;color:var(--text-muted)">首次测试每个模型需要几秒，已测试的模型会从缓存读取</span></div>';
    try {
        const resp = await apiFetch(API + `/api/provider/${currentProvider}/models?auto_test=true`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            container.innerHTML = '';
            toast(err.detail || '测试失败', 'error');
            return;
        }
        toast('批量能力测试完成', 'success');
        await fetchRemoteModels();
    } catch (e) {
        toast('测试失败: ' + e.message, 'error');
    }
}

async function fetchRemoteModels() {
    lastSearchView = 'remote';
    const container = document.getElementById('provider-search-results');
    container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--accent-cyan)"><div style="font-size:1.2rem;margin-bottom:8px">⏳</div>正在拉取模型列表...</div>';
    try {
        const resp = await apiFetch(API + `/api/provider/${currentProvider}/models`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            container.innerHTML = '';
            toast(err.detail || '拉取失败：厂商未注册或 API Key 未配置', 'error');
            return;
        }
        const data = await resp.json();

        const catalogResp = await apiFetch(API + `/api/catalog/provider/${currentProvider}/models`);
        const catalogData = await catalogResp.json();
        const catalogMap = {};
        (catalogData.models || []).forEach(m => { catalogMap[m.id] = m; });

        const remoteModels = data.available_models || [];
        const capabilities = data.capabilities || {};
        const capSummary = capabilities.summary || {};
        const allCapResults = [...(capabilities.tested || []), ...(capabilities.cached || [])];
        const capMap = {};
        allCapResults.forEach(r => { capMap[r.model] = r; });

        if (remoteModels.length === 0) {
            container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted)">厂商未返回模型列表</div>';
            return;
        }

        const untested = capSummary.total - capSummary.from_cache;
        const summaryHtml = capSummary.total ? `<div style="margin-bottom:12px;padding:10px 14px;background:var(--bg-primary);border-radius:8px;border:1px solid var(--border);display:flex;gap:16px;flex-wrap:wrap;align-items:center">
            <span style="font-size:0.85rem;font-weight:600;color:var(--accent-cyan)">能力检测</span>
            <span style="font-size:0.8rem;color:var(--text-secondary)">共 ${capSummary.total} 个模型</span>
            <span style="font-size:0.8rem;color:var(--text-muted)">📦 已有缓存: ${capSummary.from_cache}</span>
            ${untested > 0 ? `<span style="font-size:0.8rem;color:var(--accent-yellow)">🔍 未测试: ${untested}</span>` : '<span style="font-size:0.75rem;padding:2px 8px;background:rgba(16,185,129,0.15);color:var(--accent-green);border-radius:10px">✓ 全部已测试</span>'}
            <button class="btn btn-primary btn-sm" onclick="testAllRemoteModels()" style="margin-left:auto" title="对未测试的模型批量运行能力检测">🔬 批量测试能力</button>
        </div>` : '';

        const paidWarning = isPaidProvider(currentProvider)
            ? `<div style="padding:8px 12px;margin-bottom:8px;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:6px;font-size:0.75rem;color:#f59e0b">⚠️ 付费厂商：每次能力测试约消耗 800~2000 tokens，点击测试按钮前会要求确认</div>`
            : '';

        container.innerHTML = summaryHtml + paidWarning +
            `<div style="margin-bottom:8px;font-size:0.8rem;color:var(--text-secondary)">厂商共 ${remoteModels.length} 个模型：</div>` +
            remoteModels.map(modelId => {
                const inCatalog = catalogMap[modelId];
                const isActive = inCatalog && inCatalog.enabled;
                const cap = capMap[modelId];

                let capBadges = '';
                if (cap) {
                    const badge = (condition, trueText, falseText, trueBg, trueFg, falseBg, falseFg) => {
                        const bg = condition ? trueBg : falseBg;
                        const fg = condition ? trueFg : falseFg;
                        const text = condition ? trueText : falseText;
                        return `<span style="font-size:0.65rem;padding:1px 5px;border-radius:6px;background:${bg};color:${fg}">${text}</span>`;
                    };
                    const availBadge = badge(cap.available, '✓ 可用', '✗ 不可用', 'rgba(16,185,129,0.1)', 'var(--accent-green)', 'rgba(239,68,68,0.1)', 'var(--accent-red)');
                    const tcBadge = badge(cap.tool_calling, '🔧 TC', '— TC', 'rgba(59,130,246,0.1)', 'var(--accent-blue)', 'rgba(100,116,139,0.1)', 'var(--text-muted)');
                    const mtBg = cap.multi_turn_tc ? 'rgba(139,92,246,0.1)' : (cap.tool_calling ? 'rgba(239,68,68,0.1)' : 'rgba(100,116,139,0.05)');
                    const mtColor = cap.multi_turn_tc ? 'var(--accent-purple)' : (cap.tool_calling ? 'var(--accent-red)' : 'var(--text-muted)');
                    const mtText = cap.multi_turn_tc ? '🔄 多轮' : (cap.tool_calling ? (cap.mt_issue === 'loop_call' ? '🔁 循环' : '— 多轮') : '');
                    const mtBadge = mtText ? `<span style="font-size:0.65rem;padding:1px 5px;border-radius:6px;background:${mtBg};color:${mtColor}">${mtText}</span>` : '';
                    const cnBadge = badge(cap.chinese, '🇨🇳 中文', '— 中文', 'rgba(245,158,11,0.1)', 'var(--accent-yellow)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                    const visBadge = badge(cap.vision, '👁 视觉', '— 视觉', 'rgba(6,182,212,0.1)', 'var(--accent-cyan)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                    const jsonBadge = badge(cap.json_mode, '📋 JSON', '— JSON', 'rgba(16,185,129,0.1)', 'var(--accent-green)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                    const streamBadge = badge(cap.streaming, '⚡ 流式', '— 流式', 'rgba(139,92,246,0.1)', 'var(--accent-purple)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                    const reasonBadge = badge(cap.reasoning, '🧠 推理', '— 推理', 'rgba(59,130,246,0.1)', 'var(--accent-blue)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                    const latencyTag = cap.latency_ms ? `<span style="font-size:0.6rem;color:${cap.latency_ms < 3000 ? 'var(--accent-green)' : cap.latency_ms < 8000 ? 'var(--accent-yellow)' : 'var(--accent-red)'}">${(cap.latency_ms/1000).toFixed(1)}s</span>` : '';
                    const cachedTag = cap.cached ? '<span style="font-size:0.6rem;color:var(--text-muted)">📦</span>' : '<span style="font-size:0.6rem;color:var(--accent-yellow)">🔬</span>';
                    const errTag = cap.error ? `<span style="font-size:0.65rem;color:var(--accent-red)" title="${escapeHtml(cap.error)}">⚠</span>` : '';

                    capBadges = `<div style="display:flex;gap:3px;align-items:center;flex-shrink:0;flex-wrap:wrap">
                        ${cachedTag}${latencyTag}
                        ${availBadge}${tcBadge}${mtBadge}${cnBadge}
                        ${visBadge}${jsonBadge}${streamBadge}${reasonBadge}
                        ${errTag}
                    </div>`;
                }

                let actionHtml;
                if (isActive) {
                    actionHtml = '<span style="font-size:0.75rem;color:var(--accent-green);flex-shrink:0">✓ 已启用</span>';
                } else if (inCatalog) {
                    actionHtml = `<button class="btn btn-primary btn-sm" onclick="activateFromCatalog('${currentProvider}','${modelId}')">启用</button>`;
                } else {
                    actionHtml = `<button class="btn btn-sm btn-secondary" onclick="pullModelToCatalog('${currentProvider}','${modelId}')">拉取到目录</button>`;
                }

                const testBtnHtml = `<button class="btn btn-ghost btn-sm" style="font-size:0.65rem;padding:2px 6px" onclick="testSingleRemoteModel('${currentProvider}','${modelId}')" title="检测此模型的 tool calling、中文、视觉等能力">🔍 测试</button>`;

                return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--bg-primary);border-radius:6px;margin-bottom:4px;border:1px solid var(--border);gap:8px;flex-wrap:wrap">
                    <span style="font-size:0.85rem;min-width:0;overflow:hidden;text-overflow:ellipsis">${modelId}</span>
                    <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
                        ${capBadges}
                        ${testBtnHtml}
                        ${actionHtml}
                    </div>
                </div>`;
            }).join('');

        const untestedCount = capSummary.total - capSummary.from_cache;
        if (untestedCount > 0) {
            toast(`已拉取 ${capSummary.total} 个模型（${untestedCount} 个未测试能力，可点击"批量测试"按钮检测）`, 'info');
        } else if (capSummary.from_cache > 0) {
            toast(`已拉取 ${capSummary.total} 个模型（全部已有缓存）`, 'info');
        }
    } catch (e) {
        toast('拉取失败: ' + e.message, 'error');
    }
}

async function testSingleRemoteModel(providerId, modelId) {
    if (isPaidProvider(providerId) && !confirmPaidTest(providerId, modelId)) return;
    const loadingToast = toast(`正在测试 ${modelId} 的能力（约 15-30 秒）...`, 'info', true);
    try {
        const resp = await apiFetch(API + `/api/capabilities/test?provider=${providerId}&model=${encodeURIComponent(modelId)}&force=true`, {method: 'POST'});
        const data = await resp.json();
        const results = data.results || [];
        if (results.length > 0) {
            const r = results[0];
            if (r.not_installed) {
                loadingToast.remove();
                if (confirm(`模型 ${modelId} 未在本地安装。\n\n是否立即下载安装？`)) {
                    pullOllamaModel(modelId);
                }
                return;
            }
            const caps = [];
            if (r.tool_calling) caps.push('工具调用');
            if (r.multi_turn_tc) caps.push('多轮对话');
            if (r.chinese) caps.push('中文');
            if (r.vision) caps.push('视觉');
            if (r.json_mode) caps.push('JSON');
            if (r.streaming) caps.push('流式');
            if (r.reasoning) caps.push('推理');
            const capStr = caps.length > 0 ? caps.join('、') : '无特殊能力';
            const latStr = r.latency_ms ? `${r.latency_ms}ms` : '-';
            if (r.error) {
                toast(`${modelId} 测试失败: ${formatCapErrorLabel(r.error)} — ${r.error.slice(0, 80)}`, 'error');
            } else {
                toast(`${modelId} 测试完成: ${capStr} | 延迟 ${latStr}`, 'success');
            }
        } else {
            toast(`${modelId} 测试完成（无结果）`, 'info');
        }
        fetchRemoteModels();
    } catch(e) {
        toast(`测试失败: ${e.message}`, 'error');
    } finally {
        loadingToast.remove();
    }
}

async function pullModelToCatalog(providerId, modelId) {
    const resp = await apiFetch(API + `/api/catalog/model/add?provider_id=${providerId}&model_id=${modelId}&name=${modelId}&category=通用`, {method:'POST'});
    if (resp.ok) {
        toast(`${modelId} 已拉取到模型目录`, 'success');
        fetchRemoteModels();
    } else {
        const err = await resp.json();
        toast(err.detail || '拉取失败', 'error');
    }
}

async function activateFromCatalog(providerId, modelId) {
    const modelsResp = await apiFetch(API + `/api/catalog/provider/${providerId}/models`);
    const modelsData = await modelsResp.json();
    const enabledModels = (modelsData.models || []).filter(m => m.enabled);
    const maxPri = enabledModels.reduce((max, m) => Math.max(max, m.priority || 0), 0);
    const defaultPri = maxPri + 1;
    const priority = prompt('设置优先级（数字越小越优先）:', String(defaultPri));
    if (priority === null) return;
    const resp = await apiFetch(API + `/api/catalog/model/activate?provider_id=${providerId}&model_id=${modelId}&priority=${priority}`, {method:'POST'});
    if (resp.ok) {
        toast(`${modelId} 已启用`, 'success');
        loadProviderModels(currentProvider);
        loadProviderCards();
        if (lastSearchView === 'remote') {
            fetchRemoteModels();
        } else {
            searchProviderModels();
        }
    } else {
        const err = await resp.json();
        toast(err.detail || '激活失败', 'error');
    }
}

async function manualAddModel() {
    const modelId = document.getElementById('manual-model-id').value.trim();
    const priority = document.getElementById('manual-priority').value;
    const rpd = document.getElementById('manual-rpd').value;
    const rpm = document.getElementById('manual-rpm').value;
    if (!modelId) { toast('请输入模型 ID', 'error'); return; }
    await apiFetch(API + `/api/config/model/add?provider=${currentProvider}&name=${modelId}&priority=${priority}&rpd=${rpd}&rpm=${rpm}`, {method:'POST'});
    document.getElementById('manual-model-id').value = '';
    loadProviderModels(currentProvider);
    loadProviderCards();
    toast(`${modelId} 已添加（可在模型列表中点击"🔍 测试能力"按钮检测能力）`, 'success');
}

// --- 接入说明 ---
function showGuide(providerId) {
    const meta = providerMeta[providerId] || {};
    document.getElementById('guide-modal-title').textContent = (meta.name || providerId) + ' - 接入说明';
    const safeUrl = meta.url && /^https?:\/\//.test(meta.url) ? escapeHtml(meta.url) : '';
    document.getElementById('guide-modal-url').innerHTML = safeUrl ? `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-blue);font-size:0.85rem">🔗 ${safeUrl}</a>` : '';
    document.getElementById('guide-modal-content').textContent = meta.guide || '暂无接入说明';
    showModal('guide');
}

// --- 拖拽排序（通过 handle 触发） ---
let dragSrcEl = null;

function initDragAndDrop() {
    const grid = document.getElementById('providers-grid');
    const cards = grid.querySelectorAll('.draggable');
    cards.forEach(card => {
        const handle = card.querySelector('.drag-handle');
        if (handle) {
            handle.addEventListener('mousedown', () => { card.setAttribute('draggable', 'true'); });
            handle.addEventListener('mouseup', () => { card.setAttribute('draggable', 'false'); });
        }
        card.addEventListener('dragstart', handleDragStart);
        card.addEventListener('dragover', handleDragOver);
        card.addEventListener('dragenter', handleDragEnter);
        card.addEventListener('dragleave', handleDragLeave);
        card.addEventListener('drop', handleDrop);
        card.addEventListener('dragend', handleDragEnd);
    });
}

function handleDragStart(e) {
    dragSrcEl = this;
    this.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', this.dataset.providerId);
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
}

function handleDragEnter(e) {
    e.preventDefault();
    this.classList.add('drag-over');
}

function handleDragLeave(e) {
    this.classList.remove('drag-over');
}

function handleDrop(e) {
    e.stopPropagation();
    e.preventDefault();
    this.classList.remove('drag-over');
    if (dragSrcEl !== this) {
        const grid = document.getElementById('providers-grid');
        const allCards = [...grid.querySelectorAll('.draggable')];
        const srcIdx = allCards.indexOf(dragSrcEl);
        const dstIdx = allCards.indexOf(this);
        if (srcIdx < dstIdx) {
            this.parentNode.insertBefore(dragSrcEl, this.nextSibling);
        } else {
            this.parentNode.insertBefore(dragSrcEl, this);
        }
        saveProviderOrder();
    }
}

function handleDragEnd(e) {
    this.classList.remove('dragging');
    this.setAttribute('draggable', 'false');
    document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
}

async function saveProviderOrder() {
    const grid = document.getElementById('providers-grid');
    const cards = [...grid.querySelectorAll('.draggable')];
    const orderedIds = cards.map(c => c.dataset.providerId);

    // 更新显示的优先级数字
    cards.forEach((card, idx) => {
        const badge = card.querySelector('.priority-badge');
        if (badge) badge.textContent = idx + 1;
    });

    try {
        await apiFetch(API + '/api/provider/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(orderedIds),
        });
        toast('厂商优先级已更新', 'success');
    } catch (e) {
        toast('保存排序失败: ' + e.message, 'error');
    }
}

// --- 聊天功能（会话管理 + 流式 + Markdown） ---
let currentSessionId = null;

async function loadChatModelSelector() {
    try {
        const resp = await fetch(API + '/v1/models');
        const data = await resp.json();
        const select = document.getElementById('chat-model');
        const currentVal = select.value;
        select.innerHTML = '<option value="auto" data-provider="">🤖 自动选择模型</option>';
        data.models.filter(m => m.enabled).forEach(m => {
            select.innerHTML += `<option value="${escapeHtml(m.id)}" data-provider="${escapeHtml(m.provider)}">[${escapeHtml(m.provider)}] ${escapeHtml(m.id)}</option>`;
        });
        select.value = currentVal || 'auto';
        select.onchange = updateCursorModePanel;
        updateCursorModePanel();
    } catch(e) {}
}

async function loadSessionList() {
    try {
        const resp = await apiFetch(API + '/api/chat/sessions');
        const data = await resp.json();
        const list = document.getElementById('session-list');
        const sessions = data.sessions || [];
        if (!sessions.length) {
            list.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:0.8rem">暂无会话</div>';
            return;
        }
        list.innerHTML = sessions.map(s => {
            const isActive = s.id === currentSessionId;
            const bg = isActive ? 'var(--accent-blue)' : 'transparent';
            const color = isActive ? '#fff' : 'var(--text-primary)';
            const t = new Date(s.updated_at * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
            return '<div class="session-item" onclick="switchSession(\''+s.id+'\')" style="background:'+bg+';color:'+color+'">'
                + '<div style="font-size:0.82rem;font-weight:'+(isActive?'600':'400')+';overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:20px">'+escapeHtml(s.title)+'</div>'
                + '<div style="font-size:0.65rem;color:'+(isActive?'rgba(255,255,255,0.7)':'var(--text-muted)')+';margin-top:2px;display:flex;justify-content:space-between">'
                + '<span>'+s.message_count+' 条</span><span>'+t+'</span></div>'
                + '<button class="session-delete-btn" onclick="event.stopPropagation();softDeleteSession(\''+s.id+'\')" title="删除会话">&times;</button>'
                + '</div>';
        }).join('');
    } catch(e) {
        document.getElementById('session-list').innerHTML = '<div style="padding:12px;color:var(--accent-red);font-size:0.8rem">加载失败</div>';
    }
}

async function loadTrashList() {
    try {
        const resp = await apiFetch(API + '/api/chat/trash');
        const data = await resp.json();
        const list = document.getElementById('trash-list');
        const sessions = data.sessions || [];
        if (!sessions.length) {
            list.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:0.8rem">回收站为空</div>';
            return;
        }
        list.innerHTML = sessions.map(s => {
            const delDate = new Date(s.deleted_at * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
            return '<div class="trash-item">'
                + '<div style="font-size:0.82rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-secondary)">'+escapeHtml(s.title)+'</div>'
                + '<div style="font-size:0.65rem;color:var(--text-muted);margin-top:2px">'+s.message_count+' 条 · 删除于 '+delDate+' · '+s.expires_in_days+'天后过期</div>'
                + '<div class="trash-item-actions">'
                + '<button class="trash-restore-btn" onclick="restoreSession(\''+s.id+'\')">恢复</button>'
                + '<button class="trash-perm-del-btn" onclick="permanentDeleteSession(\''+s.id+'\')">永久删除</button>'
                + '</div></div>';
        }).join('');
    } catch(e) {
        document.getElementById('trash-list').innerHTML = '<div style="padding:12px;color:var(--accent-red);font-size:0.8rem">加载失败</div>';
    }
}

function switchSessionTab(tab) {
    const activeBtn = document.getElementById('tab-active-sessions');
    const trashBtn = document.getElementById('tab-trash-sessions');
    const sessionList = document.getElementById('session-list');
    const trashList = document.getElementById('trash-list');
    if (tab === 'active') {
        activeBtn.classList.add('active');
        trashBtn.classList.remove('active');
        sessionList.style.display = '';
        trashList.style.display = 'none';
        loadSessionList();
    } else {
        activeBtn.classList.remove('active');
        trashBtn.classList.add('active');
        sessionList.style.display = 'none';
        trashList.style.display = '';
        loadTrashList();
    }
}

async function softDeleteSession(sessionId) {
    await apiFetch(API + '/api/chat/sessions/' + sessionId, {method:'DELETE'});
    if (sessionId === currentSessionId) {
        currentSessionId = null;
        localStorage.removeItem('mp_current_session');
        document.getElementById('chat-messages').innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:40px 0"><p>会话已移至回收站</p></div>';
        document.getElementById('chat-title').textContent = '新对话';
    }
    loadSessionList();
    toast('会话已移至回收站', 'success');
}

async function restoreSession(sessionId) {
    await apiFetch(API + '/api/chat/trash/' + sessionId + '/restore', {method:'POST'});
    loadTrashList();
    toast('会话已恢复', 'success');
}

async function permanentDeleteSession(sessionId) {
    if (!confirm('确认永久删除？此操作不可恢复。')) return;
    await apiFetch(API + '/api/chat/trash/' + sessionId, {method:'DELETE'});
    loadTrashList();
    toast('会话已永久删除', 'success');
}

async function createNewSession() {
    const model = document.getElementById('chat-model').value;
    const resp = await apiFetch(API + '/api/chat/sessions?model=' + encodeURIComponent(model), {method:'POST'});
    const data = await resp.json();
    currentSessionId = data.session.id;
    localStorage.setItem('mp_current_session', currentSessionId);
    await loadSessionList();
    renderSessionMessages([]);
    updateContextInfo(0, 0);
    document.getElementById('chat-title').textContent = data.session.title;
}

async function switchSession(sessionId) {
    currentSessionId = sessionId;
    localStorage.setItem('mp_current_session', sessionId);
    await loadSessionList();
    try {
        const resp = await apiFetch(API + '/api/chat/sessions/' + sessionId);
        if (!resp.ok) { currentSessionId = null; localStorage.removeItem('mp_current_session'); loadSessionList(); return; }
        const data = await resp.json();
        const s = data.session;
        document.getElementById('chat-title').textContent = s.title;
        renderSessionMessages(s.messages || []);
        updateContextInfo(s.tokens_est, (s.messages||[]).length);
    } catch(e) { toast('加载会话失败', 'error'); }
}

async function deleteCurrentSession() {
    if (!currentSessionId) return;
    await softDeleteSession(currentSessionId);
}

async function renameCurrentSession() {
    if (!currentSessionId) return;
    const title = prompt('请输入新的会话标题:', document.getElementById('chat-title').textContent);
    if (!title) return;
    await apiFetch(API + '/api/chat/sessions/' + currentSessionId + '/title?title=' + encodeURIComponent(title), {method:'PUT'});
    document.getElementById('chat-title').textContent = title;
    loadSessionList();
}

function renderSessionMessages(messages) {
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';
    if (!messages.length) {
        const empty = document.createElement('div');
        empty.style.cssText = 'text-align:center;color:var(--text-muted);padding:40px 0';
        empty.innerHTML = '<p>开始输入消息，对话历史会自动保存</p><p style="font-size:0.8rem;margin-top:8px">刷新页面不丢失 · 支持多会话管理 · 流式输出</p>';
        container.appendChild(empty);
        return;
    }
    messages.forEach(m => appendMessage(m.role, m.content, m.model, false));
}

function updateContextInfo(tokensEst, msgCount) {
    const el = document.getElementById('chat-context-info');
    if (!tokensEst && !msgCount) { el.textContent = ''; return; }
    const pct = Math.min(100, Math.round(tokensEst / 320));
    const color = pct > 80 ? 'var(--accent-red)' : pct > 50 ? 'var(--accent-yellow)' : 'var(--accent-green)';
    el.innerHTML = '<span style="color:'+color+'">~'+tokensEst+'</span> tokens · '+msgCount+' 条';
}

// --- Markdown 简易渲染 ---
function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    // 代码块 ```lang ... ```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
        return '<pre style="background:var(--bg-tertiary);padding:12px;border-radius:8px;overflow-x:auto;margin:8px 0;position:relative;font-size:0.82rem;line-height:1.5">'
            + (lang ? '<div style="position:absolute;top:4px;right:8px;font-size:0.65rem;color:var(--text-muted)">'+lang+'</div>' : '')
            + '<code>'+code+'</code></pre>';
    });
    // 行内代码 `code`
    html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-tertiary);padding:1px 5px;border-radius:4px;font-size:0.85em">$1</code>');
    // 加粗 **text**
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // 斜体 *text*
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // 标题 ### text
    html = html.replace(/^### (.+)$/gm, '<div style="font-size:1rem;font-weight:700;margin:8px 0 4px">$1</div>');
    html = html.replace(/^## (.+)$/gm, '<div style="font-size:1.1rem;font-weight:700;margin:10px 0 4px">$1</div>');
    // 无序列表
    html = html.replace(/^- (.+)$/gm, '<div style="padding-left:16px">• $1</div>');
    html = html.replace(/^\* (.+)$/gm, '<div style="padding-left:16px">• $1</div>');
    // 有序列表
    html = html.replace(/^(\d+)\. (.+)$/gm, '<div style="padding-left:16px">$1. $2</div>');
    // 普通换行
    html = html.replace(/\n/g, '<br>');
    return html;
}

function appendMessage(role, content, model, withActions) {
    const container = document.getElementById('chat-messages');
    const empty = container.querySelector('[style*="text-align:center"]');
    if (empty) empty.remove();

    const isUser = role === 'user';
    const bgColor = isUser ? 'var(--accent-blue)' : 'var(--bg-primary)';
    const textColor = isUser ? '#fff' : 'var(--text-primary)';
    const align = isUser ? 'flex-end' : 'flex-start';

    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;justify-content:'+align;

    const outerDiv = document.createElement('div');
    outerDiv.style.cssText = 'max-width:80%;display:flex;flex-direction:column;gap:4px;align-items:'+align;

    const bubble = document.createElement('div');
    bubble.style.cssText = 'padding:12px 16px;border-radius:12px;background:'+bgColor+';color:'+textColor+';border:1px solid var(--border);word-break:break-word;line-height:1.6';
    bubble.className = 'msg-bubble';

    if (isUser) {
        bubble.innerHTML = escapeHtml(content || '').replace(/\n/g, '<br>');
    } else {
        bubble.innerHTML = renderMarkdown(content || '');
    }

    outerDiv.appendChild(bubble);

    if (!isUser && model) {
        const modelTag = document.createElement('div');
        modelTag.style.cssText = 'font-size:0.6rem;color:var(--text-muted);padding:0 4px';
        modelTag.textContent = '🤖 ' + model;
        outerDiv.appendChild(modelTag);
    }

    if (!isUser && withActions !== false) {
        const actions = document.createElement('div');
        actions.style.cssText = 'display:flex;gap:6px;opacity:0;transition:opacity 0.15s';
        outerDiv.onmouseenter = () => actions.style.opacity = '1';
        outerDiv.onmouseleave = () => actions.style.opacity = '0';

        const copyBtn = document.createElement('button');
        copyBtn.className = 'btn btn-ghost btn-sm';
        copyBtn.style.cssText = 'font-size:0.65rem;padding:2px 6px';
        copyBtn.textContent = '📋 复制';
        copyBtn.onclick = () => {
            navigator.clipboard.writeText(content || '').then(() => toast('已复制到剪贴板', 'success'));
        };
        actions.appendChild(copyBtn);

        if (model) {
            const modelSpan = document.createElement('span');
            modelSpan.style.cssText = 'font-size:0.65rem;color:var(--text-muted);align-self:center';
            modelSpan.textContent = model;
            actions.appendChild(modelSpan);
        }
        outerDiv.appendChild(actions);
    }

    wrapper.appendChild(outerDiv);
    container.appendChild(wrapper);
    container.scrollTop = container.scrollHeight;
    return bubble;
}

function escapeHtml(text) {
    if (!text) return '';
    return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// --- Cursor Mode 管理 ---
let currentCursorMode = 'agent';

function selectCursorMode(mode) {
    currentCursorMode = mode;
    document.querySelectorAll('.cursor-mode-btn').forEach(btn => btn.classList.remove('active'));
    const selectedBtn = document.querySelector(`.cursor-mode-btn[data-mode="${mode}"]`);
    if (selectedBtn) selectedBtn.classList.add('active');

    const forceCheckbox = document.getElementById('cursor-force');
    if (forceCheckbox) {
        if (mode === 'plan' || mode === 'ask') {
            forceCheckbox.checked = false;
            forceCheckbox.disabled = true;
        } else {
            forceCheckbox.disabled = false;
        }
    }

    const input = document.getElementById('chat-input');
    if (input) {
        const hints = {
            agent: '输入任务，AI 将执行完整的代码修改...',
            plan: '输入需求，AI 将只分析不修改代码...',
            ask: '提问关于代码的问题...',
        };
        input.placeholder = hints[mode] || hints.agent;
    }
}

// 折叠/展开高级选项
function toggleCursorAdvanced() {
    const panel = document.getElementById('cursor-advanced-panel');
    const toggle = document.getElementById('cursor-adv-toggle');
    if (!panel || !toggle) return;
    const isHidden = panel.style.display === 'none';
    panel.style.display = isHidden ? 'block' : 'none';
    toggle.textContent = isHidden ? '高级 ▲' : '高级 ▼';
}

// 新建 Cursor 会话
async function createCursorSession() {
    const toggle = document.getElementById('cursor-adv-toggle');
    const origText = toggle?.textContent || '';
    if (toggle) toggle.textContent = '创建中...';
    try {
        const apiKey = localStorage.getItem('apiKey') || '';
        const res = await fetch('/api/cursor/sessions/create', {
            method: 'POST',
            headers: { 'Authorization': apiKey ? `Bearer ${apiKey}` : '' },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        alert(`会话创建成功！\nSession ID: ${data.session_id}`);
        await loadCursorSessions();
        // 自动选中新会话
        const sel = document.getElementById('cursor-session');
        if (sel) sel.value = data.session_id;
    } catch (err) {
        console.error('createCursorSession error:', err);
        alert(`创建会话失败: ${err.message}`);
    } finally {
        if (toggle) toggle.textContent = origText;
    }
}

// 加载会话列表
async function loadCursorSessions() {
    const sel = document.getElementById('cursor-session');
    if (!sel) return;
    const currentVal = sel.value;
    try {
        const apiKey = localStorage.getItem('apiKey') || '';
        const res = await fetch('/api/cursor/sessions', {
            method: 'GET',
            headers: { 'Authorization': apiKey ? `Bearer ${apiKey}` : '' },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        // 清空并重新填充
        sel.innerHTML = '<option value="">新会话</option>';
        (data.sessions || []).forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.title || s.id.slice(0, 8);
            sel.appendChild(opt);
        });
        // 恢复选中
        if (currentVal) sel.value = currentVal;
    } catch (err) {
        console.error('loadCursorSessions error:', err);
    }
}

function isCursorChatModel(model) {
    // auto 模式也可能路由到 Cursor，所以允许选择 mode
    if (!model) return false;
    if (model === 'auto') return true;
    const sel = document.getElementById('chat-model');
    if (!sel) return false;
    const opt = sel.querySelector(`option[value="${CSS.escape(model)}"]`);
    if (!opt) return false;
    return (opt.dataset.provider || '') === 'cursor';
}

function updateCursorModePanel() {
    const panel = document.getElementById('cursor-mode-panel');
    const modelSel = document.getElementById('chat-model');
    if (!panel || !modelSel) return;
    const show = isCursorChatModel(modelSel.value);
    panel.classList.toggle('show', show);
    if (show) selectCursorMode(currentCursorMode);
}

function buildCursorChatExtras() {
    if (!isCursorChatModel(document.getElementById('chat-model')?.value)) return {};
    const sandbox = document.getElementById('cursor-sandbox')?.value || '';
    const workspacePath = document.getElementById('cursor-workspace')?.value.trim() || undefined;
    const cursorSessionId = document.getElementById('cursor-session')?.value || undefined;
    const cursorContinue = document.getElementById('cursor-continue')?.checked || false;
    const worktreeName = document.getElementById('cursor-worktree')?.value.trim() || undefined;
    const worktreeBase = document.getElementById('cursor-worktree-base')?.value.trim() || undefined;
    const skipWorktreeSetup = document.getElementById('cursor-skip-worktree-setup')?.checked || false;
    const approveMcps = document.getElementById('cursor-approve-mcps')?.checked || false;

    return {
        mode: currentCursorMode,
        force: document.getElementById('cursor-force')?.checked || false,
        sandbox: sandbox || undefined,
        workspace_path: workspacePath,
        cursor_session_id: cursorSessionId,
        cursor_continue: cursorContinue,
        worktree_name: worktreeName,
        worktree_base: worktreeBase,
        skip_worktree_setup: skipWorktreeSetup,
        approve_mcps: approveMcps,
    };
}

// --- 流式发送 ---
async function sendChat() {
    if (!currentSessionId) {
        await createNewSession();
    }
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    const model = document.getElementById('chat-model').value;
    input.value = '';

    appendMessage('user', msg);

    const btn = document.getElementById('chat-send-btn');
    btn.disabled = true;
    btn.textContent = '思考中...';

    // 创建占位气泡
    const bubble = appendMessage('assistant', '', null, false);
    let fullText = '';
    let usedModel = '';
    const cursorExtras = buildCursorChatExtras();

    try {
        const resp = await apiFetch(API + '/api/chat/sessions/' + currentSessionId + '/stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                model: model,
                messages: [{role: 'user', content: msg}],
                temperature: 0.7,
                ...cursorExtras,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            bubble.innerHTML = '❌ ' + escapeHtml(err.detail || '请求失败');
            return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const {value, done} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {stream: true});

            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6).trim();
                if (payload === '[DONE]') continue;

                try {
                    const chunk = JSON.parse(payload);
                    if (chunk.meta) {
                        usedModel = chunk.meta.model || '';
                        if (chunk.meta.title) {
                            document.getElementById('chat-title').textContent = chunk.meta.title;
                            loadSessionList();
                        }
                        continue;
                    }
                    if (chunk.session_info) {
                        updateContextInfo(chunk.session_info.tokens_est, chunk.session_info.total_messages);
                    }
                    const delta = chunk.choices && chunk.choices[0] && chunk.choices[0].delta;
                    if (delta && delta.content) {
                        fullText += delta.content;
                        bubble.innerHTML = renderMarkdown(fullText);
                        const container = document.getElementById('chat-messages');
                        container.scrollTop = container.scrollHeight;
                    }
                } catch(pe) {}
            }
        }

        const outerDiv = bubble.parentElement;
        if (outerDiv) {
            const tagsContainer = document.createElement('div');
            tagsContainer.style.cssText = 'display:flex;gap:8px;align-items:center;margin-top:4px';
            
            if (usedModel) {
                const modelTag = document.createElement('div');
                modelTag.style.cssText = 'font-size:0.65rem;color:var(--text-muted);padding:2px 6px;background:rgba(59,130,246,0.1);border-radius:4px;border:1px solid rgba(59,130,246,0.2)';
                modelTag.textContent = '🤖 ' + usedModel;
                tagsContainer.appendChild(modelTag);
            }
            
            // 显示 Cursor mode 标签
            if (cursorExtras.mode && cursorExtras.mode !== 'agent') {
                const modeTag = document.createElement('div');
                const modeColors = {
                    plan: { bg: 'rgba(139,92,246,0.1)', border: 'rgba(139,92,246,0.3)', icon: '📐' },
                    ask: { bg: 'rgba(16,185,129,0.1)', border: 'rgba(16,185,129,0.3)', icon: '💬' }
                };
                const modeStyle = modeColors[cursorExtras.mode] || modeColors.ask;
                modeTag.style.cssText = `font-size:0.65rem;color:var(--text-primary);padding:2px 6px;background:${modeStyle.bg};border-radius:4px;border:1px solid ${modeStyle.border};font-weight:500`;
                modeTag.textContent = `${modeStyle.icon} ${cursorExtras.mode.toUpperCase()}`;
                tagsContainer.appendChild(modeTag);
            }
            
            outerDiv.appendChild(tagsContainer);
            
            const actions = document.createElement('div');
            actions.style.cssText = 'display:flex;gap:6px;opacity:0;transition:opacity 0.15s;margin-top:4px';
            outerDiv.onmouseenter = () => actions.style.opacity = '1';
            outerDiv.onmouseleave = () => actions.style.opacity = '0';
            const copyBtn = document.createElement('button');
            copyBtn.className = 'btn btn-ghost btn-sm';
            copyBtn.style.cssText = 'font-size:0.65rem;padding:2px 6px';
            copyBtn.textContent = '📋 复制';
            copyBtn.onclick = () => navigator.clipboard.writeText(fullText).then(() => toast('已复制', 'success'));
            actions.appendChild(copyBtn);
            outerDiv.appendChild(actions);
        }
    } catch (e) {
        bubble.innerHTML = '❌ ' + escapeHtml(e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '发送';
    }
}

// --- 移动端侧栏切换 ---
function toggleChatSidebar() {
    document.getElementById('chat-sidebar').classList.toggle('show');
    document.getElementById('chat-sidebar-overlay').classList.toggle('show');
}

// --- 实时日志模块 ---
let _logSeq = 0;
let _logTimer = null;
let _logEntries = [];
let _logTraceFilter = '';
const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];
let _historyDates = [];
let _historyDateIdx = -1;
let _historyLoading = false;
let _historyExhausted = false;
let _historyOffset = 0;
let _preserveScroll = false;

function startLogPolling() {
    if (_logTimer) return;
    fetchLogs();
    loadServerLogLevel();
    _loadHistoryDates();
    _logTimer = setInterval(fetchLogs, 1000);

    const container = document.getElementById('log-container');
    container.addEventListener('scroll', _onLogScroll);
}
function stopLogPolling() {
    if (_logTimer) { clearInterval(_logTimer); _logTimer = null; }
}

async function _loadHistoryDates() {
    try {
        const resp = await apiFetch(`${API}/api/logs/history`);
        const data = await resp.json();
        if (data.dates) {
            _historyDates = data.dates
                .filter(d => d.label !== '当前')
                .sort((a, b) => b.label.localeCompare(a.label));
        }
    } catch (e) {}
}

function _onLogScroll() {
    const container = document.getElementById('log-container');
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    if (nearBottom && !_historyLoading && !_historyExhausted) {
        _loadMoreHistory();
    }
}

function _parseLogLine(line) {
    const m = line.match(/^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})\s\[(\w+)\s*\]\s([\w.]+):\s(.*)$/s);
    if (m) {
        return { ts: 0, time: m[1], level: m[2], logger: m[3], message: m[4], seq: -1 };
    }
    return { ts: 0, time: '', level: 'INFO', logger: '(raw)', message: line, seq: -1 };
}

async function _loadMoreHistory() {
    if (_historyLoading || _historyExhausted) return;
    _historyLoading = true;
    _preserveScroll = true;
    renderLogEntries();
    const statusEl = document.getElementById('log-status');
    statusEl.textContent = '加载历史日志...';

    let added = false;
    try {
        if (_historyDateIdx < 0) {
            const resp = await apiFetch(`${API}/api/logs/history?date=current&tail=2000`);
            const data = await resp.json();
            if (data.lines && data.lines.length > 0) {
                const parsed = data.lines.map(_parseLogLine);
                const existingMsgs = new Set(_logEntries.slice(0, 200).map(e => e.message));
                const newEntries = parsed.filter(e => !existingMsgs.has(e.message));
                if (newEntries.length > 0) {
                    _logEntries = newEntries.concat(_logEntries);
                    added = true;
                }
            }
            _historyDateIdx = 0;
        } else {
            if (_historyDateIdx >= _historyDates.length) {
                _historyExhausted = true;
                _historyLoading = false;
                _preserveScroll = true;
                renderLogEntries();
                statusEl.textContent = `共 ${_logEntries.length} 条 | 历史已全部加载`;
                return;
            }
            const dateInfo = _historyDates[_historyDateIdx];
            const resp = await apiFetch(`${API}/api/logs/history?date=${dateInfo.label}&tail=2000`);
            const data = await resp.json();
            if (data.lines && data.lines.length > 0) {
                const parsed = data.lines.map(_parseLogLine);
                _logEntries = parsed.concat(_logEntries);
                added = true;
            }
            _historyDateIdx++;
        }
    } catch (e) {
        console.warn('加载历史日志失败:', e);
    }
    _historyLoading = false;
    if (added) _preserveScroll = true;
    renderLogEntries();
    statusEl.textContent = `共 ${_logEntries.length} 条 | seq=${_logSeq}`;
}

async function fetchLogs() {
    try {
        const resp = await apiFetch(`${API}/api/logs?after=${_logSeq}&limit=300`);
        const data = await resp.json();
        if (data.entries && data.entries.length > 0) {
            _logEntries = _logEntries.concat(data.entries);
            _logSeq = data.latest_seq;
            renderLogEntries();
        }
        document.getElementById('log-status').textContent = `共 ${_logEntries.length} 条 | seq=${_logSeq}`;
    } catch (e) {
        document.getElementById('log-status').textContent = '拉取失败';
    }
}

function renderLogEntries() {
    const container = document.getElementById('log-container');
    const filterLevel = document.getElementById('log-level-filter').value;
    const searchText = (document.getElementById('log-search').value || '').toLowerCase();

    const levelIdx = filterLevel === 'all' ? -1 : LOG_LEVELS.indexOf(filterLevel);
    const filtered = _logEntries.filter(e => {
        if (levelIdx >= 0 && LOG_LEVELS.indexOf(e.level) < levelIdx) return false;
        if (searchText && !e.message.toLowerCase().includes(searchText) && !e.logger.toLowerCase().includes(searchText)) return false;
        if (_logTraceFilter && !e.message.includes('trace=' + _logTraceFilter)) return false;
        return true;
    });

    const maxRender = Math.min(10000, filtered.length);
    const toRender = filtered.slice(-maxRender).reverse();

    const prevScrollTop = container.scrollTop;
    const prevScrollHeight = container.scrollHeight;

    const fragment = document.createDocumentFragment();
    for (const entry of toRender) {
        const div = document.createElement('div');
        div.className = 'log-line';
        div.innerHTML = formatLogLine(entry);
        fragment.appendChild(div);
    }

    if (!_historyExhausted && _historyDates.length > 0) {
        const loadMore = document.createElement('div');
        loadMore.style.cssText = 'color:var(--accent-blue);font-size:0.7rem;padding:8px 0;text-align:center;cursor:pointer';
        loadMore.textContent = _historyLoading ? '加载中...' : `↓ 加载更多历史日志（还有 ${Math.max(0, _historyDates.length - _historyDateIdx)} 天）`;
        loadMore.onclick = () => { if (!_historyLoading) _loadMoreHistory(); };
        fragment.appendChild(loadMore);
    } else if (_historyExhausted) {
        const endHint = document.createElement('div');
        endHint.style.cssText = 'color:var(--text-muted);font-size:0.65rem;padding:4px 0;text-align:center';
        endHint.textContent = '── 已到达最早日志 ──';
        fragment.appendChild(endHint);
    }

    container.innerHTML = '';
    container.appendChild(fragment);

    if (_preserveScroll) {
        _preserveScroll = false;
        container.scrollTop = prevScrollTop;
    } else if (document.getElementById('log-auto-scroll').checked) {
        container.scrollTop = 0;
    } else {
        const delta = container.scrollHeight - prevScrollHeight;
        if (delta > 0) container.scrollTop = prevScrollTop;
    }
}

function formatLogLine(entry) {
    let time = entry.time, level = entry.level, logger = entry.logger, rawMsg = entry.message;
    if (logger === '(file)') {
        const m = rawMsg.match(/^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})\s\[(\w+)\s*\]\s([\w.]+):\s(.*)$/s);
        if (m) { time = m[1]; level = m[2]; logger = m[3]; rawMsg = m[4]; }
    }
    let msg = escapeHtml(rawMsg);
    msg = msg.replace(/trace=([a-f0-9]{12})/g, 'trace=<span class="log-trace" onclick="filterByTrace(\'$1\')" style="cursor:pointer" title="点击过滤此 trace">$1</span>');
    return `<span class="log-time">${time}</span> `
         + `<span class="log-level-${level}">[${level.padEnd(7)}]</span> `
         + `<span class="log-logger">${escapeHtml(logger)}:</span> `
         + `<span class="log-msg">${msg}</span>`;
}

function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function filterByTrace(traceId) {
    _logTraceFilter = traceId;
    document.getElementById('log-trace-tag').style.display = 'inline-block';
    document.getElementById('log-trace-val').textContent = traceId;
    renderLogEntries();
}
function clearTraceFilter() {
    _logTraceFilter = '';
    document.getElementById('log-trace-tag').style.display = 'none';
    renderLogEntries();
}

function exportLogs() {
    const filterLevel = document.getElementById('log-level-filter').value;
    const searchText = (document.getElementById('log-search').value || '').toLowerCase();
    const levelIdx = filterLevel === 'all' ? -1 : LOG_LEVELS.indexOf(filterLevel);

    const filtered = _logEntries.filter(e => {
        if (levelIdx >= 0 && LOG_LEVELS.indexOf(e.level) < levelIdx) return false;
        if (searchText && !e.message.toLowerCase().includes(searchText) && !e.logger.toLowerCase().includes(searchText)) return false;
        if (_logTraceFilter && !e.message.includes('trace=' + _logTraceFilter)) return false;
        return true;
    });

    const lines = filtered.map(e => `${e.time} [${e.level.padEnd(7)}] ${e.logger}: ${e.message}`);
    const blob = new Blob([lines.join('\n')], {type: 'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const now = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    a.download = `model-proxy-logs-${now}.log`;
    a.click();
    URL.revokeObjectURL(url);
    toast(`已导出 ${filtered.length} 条日志`, 'success');
}

async function setServerLogLevel(level) {
    try {
        await apiFetch(`${API}/api/logs/level?level=${level}`, {method: 'POST'});
        toast(`日志级别已切换为 ${level}`, 'success');
        highlightActiveLevel(level);
    } catch (e) {
        toast('切换失败', 'error');
    }
}
async function loadServerLogLevel() {
    try {
        const resp = await apiFetch(`${API}/api/logs/level`);
        const data = await resp.json();
        highlightActiveLevel(data.level);
    } catch (e) {}
}
function highlightActiveLevel(level) {
    ['DEBUG', 'INFO', 'WARNING'].forEach(l => {
        const btn = document.getElementById('btn-level-' + l);
        if (btn) {
            btn.style.background = l === level ? 'var(--accent-blue)' : '';
            btn.style.color = l === level ? '#fff' : '';
        }
    });
}

async function clearLogs() {
    try {
        await apiFetch(`${API}/api/logs/clear`, {method:'DELETE'});
        _logEntries = [];
        _logSeq = 0;
        _historyDateIdx = -1;
        _historyExhausted = false;
        _historyLoading = false;
        document.getElementById('log-container').innerHTML = '';
        document.getElementById('log-status').textContent = '已清空';
        toast('日志已清空', 'success');
    } catch (e) {
        toast('清空失败', 'error');
    }
}

// --- 接入指南 ---
function initIntegrationGuide() {
    const baseUrl = location.origin + '/v1';
    const host = location.origin;
    document.getElementById('intg-base-url').textContent = baseUrl;
    document.getElementById('intg-openclaw-url').textContent = baseUrl;
    document.getElementById('intg-lobe-url').textContent = baseUrl;
    document.getElementById('intg-next-url').textContent = baseUrl;
    document.querySelectorAll('.intg-dynamic-url').forEach(el => el.textContent = baseUrl);

    document.getElementById('intg-python-code').textContent =
`from openai import OpenAI

client = OpenAI(
    base_url="${baseUrl}",
    api_key="unused",
)

# 非流式调用
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好"}],
    extra_body={"session_id": "my-task-001"},  # 可选：会话绑定
)
print(response.choices[0].message.content)

# 流式调用
stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
    extra_body={"session_id": "my-task-001"},
)
for chunk in stream:
    if chunk.choices[0].delta.content:
print(chunk.choices[0].delta.content, end="")`;

    document.getElementById('intg-curl-code').textContent =
`# 非流式
curl ${baseUrl}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer unused" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "session_id": "my-task-001"
  }'

# 流式
curl ${baseUrl}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer unused" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true,
    "session_id": "my-task-001"
  }'`;

    document.getElementById('intg-js-code').textContent =
`import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "${baseUrl}",
  apiKey: "unused",
});

// 非流式（session_id 通过 body 扩展传入）
const response = await client.chat.completions.create({
  model: "auto",
  messages: [{ role: "user", content: "你好" }],
  session_id: "my-task-001",  // 可选：会话绑定
} as any);
console.log(response.choices[0].message.content);

// 流式
const stream = await client.chat.completions.create({
  model: "auto",
  messages: [{ role: "user", content: "你好" }],
  stream: true,
  session_id: "my-task-001",
} as any);
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content || "");
}`;

    document.getElementById('intg-cursor-python-code').textContent =
`from openai import OpenAI

client = OpenAI(
    base_url="${baseUrl}",
    api_key="unused",
)

# Plan 模式：只读分析，不修改代码
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "分析这个项目的架构"}],
    extra_body={
"mode": "plan",          # plan / ask / agent
"sandbox": "enabled",    # 可选
    },
)
print(response.choices[0].message.content)

# Agent 模式：完整编码权限（默认，可不传 mode）
agent = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "重构 auth 模块为 JWT"}],
    extra_body={"force": True},
)`;

    document.getElementById('intg-cursor-curl-code').textContent =
`curl ${baseUrl}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer unused" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "分析项目架构"}],
    "mode": "plan",
    "sandbox": "enabled"
  }'`;
}

function copyText(text) {
    navigator.clipboard.writeText(text).then(() => toast('已复制', 'success'));
}
function copyCodeBlock(btn) {
    const pre = btn.closest('.intg-code-block').querySelector('.intg-pre');
    navigator.clipboard.writeText(pre.textContent).then(() => toast('代码已复制', 'success'));
}

// --- 初始化 ---
initIntegrationGuide();
selectCursorMode('agent');
loadUsage();
loadProviderCards();
loadConfig();
loadChatModelSelector();
loadSessionList().then(() => {
    const savedId = localStorage.getItem('mp_current_session');
    if (savedId) switchSession(savedId).catch(() => {});
});
setInterval(loadUsage, 30000);

// 从 URL hash 恢复 tab 状态
(function restoreTab() {
    const hash = location.hash.replace('#', '');
    if (hash) {
        const tabs = document.querySelectorAll('.tab');
        for (const tab of tabs) {
            const onclick = tab.getAttribute('onclick') || '';
            if (onclick.includes("'" + hash + "'")) {
                switchTab(hash, tab);
                break;
            }
        }
    }
})();

// --- Memory 管理 ---
async function loadMemory() {
    try {
        const [statsResp, entriesResp] = await Promise.all([
            apiFetch('/api/memory/stats'),
            apiFetch('/api/memory/entries?limit=100')
        ]);
        const stats = await statsResp.json();
        const data = await entriesResp.json();

        document.getElementById('memory-stats').innerHTML =
            `共 <strong>${stats.total_entries}</strong> 条记忆 | ` +
            Object.entries(stats.by_type || {}).map(([k,v]) => `${k}: ${v}`).join(', ') +
            ` | 活跃会话: ${stats.active_sessions}`;

        const list = document.getElementById('memory-list');
        const empty = document.getElementById('memory-empty');
        if (!data.entries || data.entries.length === 0) {
            list.innerHTML = '';
            empty.style.display = 'block';
            return;
        }
        empty.style.display = 'none';
        list.innerHTML = data.entries.map(e => {
            const typeLabel = {preference:'偏好',semantic:'知识',episodic:'经验'}[e.type] || e.type;
            const date = new Date(e.created_at * 1000).toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
            const imp = (e.importance * 100).toFixed(0);
            return `<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border)">
                <span style="font-size:0.7rem;padding:2px 6px;border-radius:4px;background:var(--accent);color:#fff;white-space:nowrap">${typeLabel}</span>
                <span style="flex:1;font-size:0.88rem">${e.content}</span>
                <span style="font-size:0.72rem;color:var(--text-muted);white-space:nowrap">${date}</span>
                <span style="font-size:0.72rem;color:var(--text-muted)" title="重要性">⚡${imp}%</span>
                <button onclick="deleteMemory('${e.id}')" style="border:none;background:none;cursor:pointer;color:var(--text-muted);font-size:1rem" title="删除">×</button>
            </div>`;
        }).join('');
    } catch (err) {
        console.error('loadMemory error:', err);
    }
}

async function addMemory() {
    const type = document.getElementById('memory-add-type').value;
    const content = document.getElementById('memory-add-content').value.trim();
    if (!content) { toast('请输入记忆内容', 'warning'); return; }
    try {
        await apiFetch('/api/memory/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({type, content}) });
        document.getElementById('memory-add-content').value = '';
        toast('记忆已添加', 'success');
        loadMemory();
    } catch (err) { toast('添加失败: ' + err.message, 'error'); }
}

async function deleteMemory(id) {
    if (!confirm('确定删除这条记忆？')) return;
    try {
        await apiFetch('/api/memory/entries/' + id, { method: 'DELETE' });
        toast('已删除', 'success');
        loadMemory();
    } catch (err) { toast('删除失败: ' + err.message, 'error'); }
}

async function exportMemory() {
    try {
        const resp = await apiFetch('/api/memory/entries?limit=9999');
        const data = await resp.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'model_proxy_memory_' + new Date().toISOString().slice(0,10) + '.json';
        a.click(); URL.revokeObjectURL(url);
        toast('导出成功', 'success');
    } catch (err) { toast('导出失败: ' + err.message, 'error'); }
}

async function importMemory(event) {
    const file = event.target.files[0];
    if (!file) return;
    try {
        const text = await file.text();
        const data = JSON.parse(text);
        await apiFetch('/api/memory/import', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
        toast(`导入成功`, 'success');
        loadMemory();
    } catch (err) { toast('导入失败: ' + err.message, 'error'); }
    event.target.value = '';
}
