async function clearStatsCache() {
    if (!confirm('Are you sure you want to clear the telemetry cache? This will reset all historical data.')) return;
    const resp = await fetchWithAuth('/api/stats/clear', { method: 'POST' });
    if (resp) {
        window.location.reload();
    }
}

const socket = io();
let currentConfig = {};
let models = [];
let isRunning = false;

let sessionStartTime = null;
let uptimeInterval = null;

// --- Auth Wrapper for API Calls ---
async function fetchWithAuth(url, options = {}) {
    const resp = await fetch(url, options);
    if (resp.status === 401) {
        window.location.href = '/login';
        return null;
    }
    if (!resp.ok) {
        throw new Error(`HTTP error! status: ${resp.status}`);
    }
    return resp;
}

function startSessionTimer(startTime) {
    if (uptimeInterval) clearInterval(uptimeInterval);
    sessionStartTime = startTime || Date.now();
    uptimeInterval = setInterval(() => {
        if (!isRunning) return;
        const diff = Math.floor((Date.now() - sessionStartTime) / 1000);
        document.getElementById('session-uptime').textContent = formatUptime(diff);
    }, 1000);
}

socket.on('connect', () => {
    console.log('Connected to server');
    const logDisplay = document.getElementById('log-display');
    const line = document.createElement('div');
    line.className = 'text-blue-400';
    line.textContent = '>>> Connection established with Control Center.\n';
    logDisplay.appendChild(line);

    fetchWithAuth('/api/worker/status')
        .then(r => r ? r.json() : null)
        .then(data => {
            if (data) {
                isRunning = data.running;
                updateStatusUI();
            }
        });

    fetchWithAuth('/api/stats')
        .then(r => r ? r.json() : null)
        .then(data => {
            if (data) updateStatsUI(data);
        });
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
});

function showTab(tabId) {
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.remove('hidden');
    event.currentTarget.classList.add('active');

    // Close terminal if open
    const body = document.getElementById('console-body');
    if (body && !body.classList.contains('h-0')) {
        toggleConsole();
    }
}

socket.on('log', (data) => {
    const logDisplay = document.getElementById('log-display');
    const scrollContainer = document.getElementById('console-body');
    const shouldScroll = scrollContainer.scrollTop + scrollContainer.offsetHeight >= scrollContainer.scrollHeight - 50;

    const line = document.createElement('div');
    line.innerHTML = ansiToHtml(data.line);

    if (data.line.includes("Please run update-runtime.cmd") && !data.line.includes('echo "')) {
        line.className = 'text-red-500 font-bold bg-red-500/10 p-1 rounded';
        if (!document.getElementById('env-warning')) {
            const warning = document.createElement('div');
            warning.id = 'env-warning';
            warning.className = 'bg-red-500 text-white p-4 rounded-lg flex justify-between items-center mb-4 animate-pulse';
            warning.innerHTML = `
                <div class="flex items-center gap-3">
                    <span class="mono font-bold">⚠️ SYSTEM ERROR:</span>
                    <span class="text-xs uppercase">Worker environment is corrupt or out of date.</span>
                </div>
                <button onclick="repairAndRestart()" class="bg-white text-red-500 px-4 py-2 rounded font-bold mono text-xs shadow-lg hover:bg-gray-100 transition-all">REPAIR_AND_RESTART</button>
            `;
            document.getElementById('tab-dashboard').prepend(warning);
        }
    }

    logDisplay.appendChild(line);
    parseLogActivity(data.line);

    if (shouldScroll) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight;
    }
    if (logDisplay.childNodes.length > 1000) logDisplay.removeChild(logDisplay.firstChild);
});

function stripAnsi(text) {
    return text.replace(/[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g, '');
}

function ansiToHtml(text) {
    // Basic ANSI to HTML converter
    const colors = {
        '30': 'text-gray-900', '31': 'text-red-500', '32': 'text-green-500', '33': 'text-amber-500',
        '34': 'text-blue-500', '35': 'text-purple-500', '36': 'text-cyan-500', '37': 'text-gray-200',
        '90': 'text-gray-500', '91': 'text-red-400', '92': 'text-green-400', '93': 'text-amber-400',
        '94': 'text-blue-400', '95': 'text-purple-400', '96': 'text-cyan-400', '97': 'text-white'
    };

    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Handle standard SGR codes
    html = html.replace(/\x1b\[(\d+)(?:;\d+)*m/g, (match, code) => {
        if (code === '0') return '</span>';
        const cls = colors[code];
        return cls ? `<span class="${cls}">` : '<span>';
    });

    // Count open spans and close them if needed
    const openSpans = (html.match(/<span/g) || []).length;
    const closeSpans = (html.match(/<\/span/g) || []).length;
    for (let i = 0; i < openSpans - closeSpans; i++) html += '</span>';

    // Fallback for common Horde markers if ANSI is missing but brackets are there
    if (!text.includes('\x1b')) {
        html = html.replace(/\[ \+ \]/g, '<span class="text-green-500 font-bold">[ + ]</span>');
        html = html.replace(/\[ - \]/g, '<span class="text-red-500 font-bold">[ - ]</span>');
        html = html.replace(/\[ % \]/g, '<span class="text-cyan-500 font-bold">[ % ]</span>');
        html = html.replace(/\[ ! \]/g, '<span class="text-amber-500 font-bold">[ ! ]</span>');
    }

    return html;
}

function parseLogActivity(rawLine) {
    const line = stripAnsi(rawLine);
    const ts = new Date().toLocaleTimeString();
    document.getElementById('activity-timestamp').textContent = ts;

    if (line.includes("[ - ]") || line.includes("Submitted generation") || line.includes("Inference finished")) {
        updateActivity('Job Finished', 'Successfully submitted to Horde', 'idle');
    } else if (line.includes("[ + ]") || line.includes("Popped job")) {
        const modelMatch = line.match(/model: (.*?)[,)]/);
        const model = modelMatch ? modelMatch[1] : 'Unknown Model';
        updateActivity('Processing Job', `Rendering on ${model}`, 'busy');
    } else if (line.includes("[ % ]") && (line.includes("Starting inference") || line.includes("Inference finished"))) {
        if (line.includes("Starting")) updateActivity('Inference Start', 'Generating pixels...', 'busy');
        else updateActivity('Job Complete', 'Inference finished', 'idle');
    } else if (line.includes("Successfully downloaded")) {
        const fileMatch = line.match(/downloaded the file (.*?)$/);
        const file = fileMatch ? fileMatch[1] : 'Resource';
        updateActivity('Downloading Resource', file, 'download');
    } else if (line.includes("Process 1 is downloading")) {
        updateActivity('Syncing Models', 'Fetching required assets...', 'download');
    } else if (line.includes("Shutting down after current jobs")) {
        updateActivity('Graceful Shutdown', 'Finishing active jobs...', 'wait');
    } else if (line.includes("Session job info:")) {
        updateActivity('System Healthy', 'Pulse check complete', 'idle');
    }

    const progressMatch = line.match(/(\d+)%/);
    if (progressMatch) {
        const pct = progressMatch[1];
        const progContainer = document.getElementById('activity-progress-container');
        const progBar = document.getElementById('activity-progress-bar');
        progContainer.classList.remove('hidden');
        progBar.style.width = pct + '%';
        if (pct >= 100) setTimeout(() => progContainer.classList.add('hidden'), 2000);
    }
}

function updateActivity(text, subtext, type) {
    document.getElementById('activity-text').textContent = text;
    document.getElementById('activity-subtext').textContent = subtext;

    const iconContainer = document.getElementById('activity-icon');
    let iconHtml = '';

    switch (type) {
        case 'busy':
            iconContainer.className = 'w-10 h-10 rounded-full bg-amber-500/20 flex items-center justify-center text-amber-500 animate-pulse';
            iconHtml = '<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>';
            break;
        case 'download':
            iconContainer.className = 'w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center text-blue-400 animate-bounce';
            iconHtml = '<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M9 19l3 3m0 0l3-3m-3 3V10" /></svg>';
            break;
        case 'wait':
            iconContainer.className = 'w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center text-red-400';
            iconHtml = '<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>';
            break;
        default:
            iconContainer.className = 'w-10 h-10 rounded-full bg-gray-800 flex items-center justify-center text-gray-500';
            iconHtml = '<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h.01M12 12h.01M19 12h.01M6 12a1 1 0 11-2 0 1 1 0 012 0zm7 0a1 1 0 11-2 0 1 1 0 012 0zm7 0a1 1 0 11-2 0 1 1 0 012 0z" /></svg>';
    }
    iconContainer.innerHTML = iconHtml;
}

function toggleConsole() {
    const body = document.getElementById('console-body');
    const chevron = document.getElementById('console-chevron');
    if (body.classList.contains('h-0')) {
        body.classList.remove('h-0', 'opacity-0', 'pointer-events-none');
        body.classList.add('h-[40vh]');
        chevron.classList.add('rotate-180');
    } else {
        body.classList.add('h-0', 'opacity-0', 'pointer-events-none');
        body.classList.remove('h-[40vh]');
        chevron.classList.remove('rotate-180');
    }
}

function updateStatusUI() {
    const btn = document.getElementById('toggle-worker');
    const orb = document.getElementById('status-orb');
    const statusText = document.getElementById('worker-status-text');

    if (isRunning) {
        isStopping = false;
        const warning = document.getElementById('env-warning');
        if (warning) warning.remove();

        btn.textContent = 'STOP_WORKER';
        btn.classList.remove('bg-amber-500', 'animate-pulse');
        btn.classList.add('bg-red-500', 'text-white');
        orb.classList.remove('bg-red-500');
        orb.classList.add('bg-green-500', 'shadow-[0_0_10px_rgba(16,185,129,0.5)]');

        statusText.textContent = 'Running';
        statusText.className = 'text-2xl font-bold mono text-green-500 uppercase';
        if (!sessionStartTime) startSessionTimer();
    } else {
        isStopping = false;
        if (uptimeInterval) clearInterval(uptimeInterval);
        sessionStartTime = null;
        sessionJobs = 0;
        document.getElementById('session-jobs').textContent = "0";
        document.getElementById('session-uptime').textContent = "0h 0m";

        btn.textContent = 'START_WORKER';
        btn.classList.remove('bg-red-500', 'text-white', 'bg-amber-500', 'animate-pulse');
        btn.classList.add('bg-amber-500', 'text-black');
        orb.classList.remove('bg-green-500');
        orb.classList.add('bg-red-500', 'shadow-[0_0_10px_rgba(239,68,68,0.5)]');

        statusText.textContent = 'Stopped';
        statusText.className = 'text-2xl font-bold mono text-red-500 uppercase';
        updateActivity('System Idle', 'Waiting for worker to start...', 'idle');
    }
}

socket.on('worker_status', (data) => {
    isRunning = data.running;
    updateStatusUI();
});

socket.on('sysinfo', (data) => {
    document.getElementById('cpu-bar').style.width = data.cpu_pct + '%';
    document.getElementById('cpu-val').textContent = Math.round(data.cpu_pct) + '%';
    document.getElementById('ram-bar').style.width = data.ram_pct + '%';
    document.getElementById('ram-val').textContent = Math.round(data.ram_pct) + '%';

    const gpuContainer = document.getElementById('gpu-info-container');
    gpuContainer.innerHTML = '';

    // Multi-GPU Support: Use grid if more than 1 GPU
    if (data.gpus.length > 1) {
        gpuContainer.className = 'grid grid-cols-1 md:grid-cols-2 gap-4';
    } else {
        gpuContainer.className = 'flex flex-col gap-6';
    }

    data.gpus.forEach(gpu => {
        const div = document.createElement('div');
        div.className = 'flex flex-col gap-2 p-3 rounded bg-white/5 border border-white/5 hover:border-amber-500/20 transition-all';
        div.innerHTML = `
            <div class="flex justify-between items-center">
                <span class="text-[10px] uppercase font-bold text-white tracking-widest truncate max-w-[150px]">${gpu.name}</span>
                <span class="text-xs mono text-amber-500 font-bold">${gpu.util}%</span>
            </div>
            <div class="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden">
                <div class="h-full bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.4)]" style="width: ${gpu.util}%"></div>
            </div>
            <div class="flex justify-between items-center">
                <span class="text-[9px] mono text-gray-500 uppercase">VRAM Usage</span>
                <span class="text-[9px] mono text-gray-400">${gpu.used}MB / ${gpu.total}MB</span>
            </div>
        `;
        gpuContainer.appendChild(div);
    });
});

function formatNumber(num) {
    if (num == null) return "0";
    return Math.round(num).toLocaleString('de-DE');
}

function updateStatsUI(data) {
    if (data.last_sync) {
        const syncEl = document.getElementById('last-sync-time');
        if (syncEl) {
            syncEl.textContent = 'LAST SYNC: ' + data.last_sync;
            syncEl.classList.remove('animate-pulse', 'text-gray-600');
            syncEl.classList.add('text-amber-500/50');
        }
    }

    document.getElementById('stat-total-kudos').textContent = formatNumber(data.kudos);
    document.getElementById('stat-kudos-generated').textContent = formatNumber(data.kudos_generated);

    let avgKudosHr = data.kudos_hr_avg;
    if ((!avgKudosHr || avgKudosHr === 0) && data.kudos_generated > 0 && data.uptime > 0) {
        avgKudosHr = data.kudos_generated / (data.uptime / 3600);
    }
    document.getElementById('stat-kudos-hr-avg').textContent = formatNumber(avgKudosHr);
    if (data.username) document.getElementById('worker-name-display').textContent = data.username;

    if (data.session_jobs != null) {
        document.getElementById('session-jobs').textContent = formatNumber(data.session_jobs);
    }
    if (data.session_kudos != null) {
        document.getElementById('stat-session-kudos').textContent = formatNumber(data.session_kudos);
    }
    if (data.session_kudos_hr != null) {
        document.getElementById('stat-session-kudos-hr').textContent = formatNumber(data.session_kudos_hr);
    }

    if (data.activity_text) {
        document.getElementById('activity-text').textContent = data.activity_text;
        document.getElementById('activity-timestamp').textContent = new Date().toLocaleTimeString();
    }
    if (data.activity_subtext) {
        document.getElementById('activity-subtext').textContent = data.activity_subtext;
    }

    if (data.processes) {
        const grid = document.getElementById('process-grid');
        grid.innerHTML = '';
        data.processes.forEach(proc => {
            const item = document.createElement('div');
            item.className = 'flex items-center justify-between p-3 border-b border-white/5 last:border-0 hover:bg-white/5 transition-all';

            const parts = proc.split(' ');
            const id = parts[1];
            const state = proc.match(/\((.*?)\)/) ? proc.match(/\((.*?)\)/)[1] : 'Idle';
            const model = proc.includes(')') ? proc.split(')').slice(-2, -1)[0].replace('(', '').trim() : 'None';

            item.innerHTML = `
                <div class="flex flex-col">
                    <span class="text-[10px] text-gray-500 font-bold uppercase mono tracking-widest">Thread #${id}</span>
                    <span class="text-xs font-bold text-white truncate max-w-[150px]">${model}</span>
                </div>
                <span class="text-[10px] px-2 py-1 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 font-bold mono uppercase">${state}</span>
            `;
            grid.appendChild(item);
        });
    }

    if (data.worker_start_time) {
        const backendStartMs = data.worker_start_time * 1000;
        if (!sessionStartTime || Math.abs(sessionStartTime - backendStartMs) > 2000) {
            startSessionTimer(backendStartMs);
        }
    }

    if (data.uptime) document.getElementById('stat-uptime-total').textContent = formatUptime(data.uptime);
    if (data.requests_fulfilled != null) document.getElementById('stat-jobs').textContent = data.requests_fulfilled.toLocaleString();
}

socket.on('stats_update', (data) => {
    updateStatsUI(data);
});

socket.on('models_update', (data) => {
    models = data;
    renderModels();
});

async function loadConfig() {
    try {
        const resp = await fetchWithAuth('/api/config');
        if (!resp) return;
        currentConfig = await resp.json();

        const form = document.getElementById('config-form');
        for (const [key, value] of Object.entries(currentConfig)) {
            const input = form.elements[key];
            if (input) {
                if (input.type === 'checkbox') {
                    input.checked = !!value;
                } else if (input.type === 'range') {
                    input.value = value;
                    const display = document.getElementById('val-' + key);
                    if (display) display.textContent = value;
                } else {
                    input.value = value || '';
                    if (value === '********') {
                        input.disabled = true;
                        input.title = "Protected field (Remote Mode)";
                        input.classList.add('opacity-50', 'cursor-not-allowed');
                    } else {
                        input.disabled = false;
                        input.title = "";
                        input.classList.remove('opacity-50', 'cursor-not-allowed');
                    }
                }
            }
        }
        renderModelsToLoad();

        if (currentConfig.dreamer_name) {
            document.getElementById('worker-name-display').textContent = currentConfig.dreamer_name;
        }
    } catch (e) {
        console.error('Failed to load config:', e);
    }
}

async function saveConfig() {
    const form = document.getElementById('config-form');
    const data = {};

    Array.from(form.elements).forEach(input => {
        if (!input.name) return;
        if (input.type === 'checkbox') data[input.name] = input.checked;
        else if (input.type === 'range') data[input.name] = parseInt(input.value);
        else data[input.name] = input.value;
    });

    const resp = await fetchWithAuth('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (resp) {
        alert('Configuration Saved');
        currentConfig = data;
    }
}

let isStopping = false;

async function toggleWorker() {
    const btn = document.getElementById('toggle-worker');

    if (!isRunning && !isStopping) {
        btn.disabled = true;
        btn.textContent = 'STARTING...';
        try {
            const resp = await fetchWithAuth('/api/worker/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ args: currentConfig.extra_cmdline_args || "" })
            });
        } catch (e) {
            alert('Start failed: ' + e);
        } finally {
            btn.disabled = false;
        }
    } else {
        const action = isStopping ? 'FORCE_KILL' : 'STOP_WORKER';
        const force = isStopping;

        if (force && !confirm('Force kill the worker? Current jobs will be lost.')) return;

        btn.disabled = true;
        btn.textContent = force ? 'KILLING...' : 'STOPPING...';

        try {
            const resp = await fetchWithAuth('/api/worker/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force: force })
            });
            if (!resp) return;
            const data = await resp.json();

            if (data.status === 'stopping_gracefully') {
                isStopping = true;
                btn.disabled = false;
                btn.textContent = 'GRACEFUL_STOP...';
                btn.classList.remove('bg-red-500');
                btn.classList.add('bg-amber-500', 'animate-pulse');
            }
        } catch (e) {
            alert('Stop action failed: ' + e);
        } finally {
            if (!isStopping) btn.disabled = false;
        }
    }
}

async function repairAndRestart() {
    if (!confirm('This will run git pull, update the runtime environment, and then restart the worker. This may take 10-20 minutes. Proceed?')) return;

    const warning = document.getElementById('env-warning');
    if (warning) warning.remove();

    clearLogs();
    showTab('dashboard');

    const overlay = document.createElement('div');
    overlay.id = 'maintenance-overlay';
    overlay.className = 'fixed inset-0 bg-black/80 backdrop-blur-md z-[100] flex flex-col items-center justify-center gap-6 p-8 text-center';
    overlay.innerHTML = `
        <div class="w-16 h-16 border-4 border-amber-500 border-t-transparent rounded-full animate-spin"></div>
        <div class="flex flex-col gap-2">
            <h2 class="text-2xl font-bold mono text-amber-500 uppercase tracking-tighter">System Maintenance In Progress</h2>
            <p class="text-xs text-gray-400 uppercase tracking-widest">Rebuilding environment and verifying dependencies...</p>
        </div>
        <p class="text-[10px] text-gray-500 uppercase italic">This may take 10-20 minutes. Please do not close this window.</p>
    `;
    document.body.appendChild(overlay);

    const logDisplay = document.getElementById('log-display');
    const infoLine = document.createElement('div');
    infoLine.className = 'text-amber-500 font-bold';
    infoLine.textContent = '>>> INITIALIZING SMART REPAIR SEQUENCE...\n';
    logDisplay.appendChild(infoLine);

    try {
        await fetchWithAuth('/api/update', { method: 'POST' });
    } catch (e) {
        alert('Repair failed to start: ' + e);
        overlay.remove();
    }
}

socket.on('maintenance_complete', (data) => {
    const overlay = document.getElementById('maintenance-overlay');
    if (overlay) {
        if (data.status === 'success') {
            overlay.innerHTML = `
                <div class="w-16 h-16 bg-green-500 rounded-full flex items-center justify-center shadow-[0_0_20px_rgba(16,185,129,0.5)]">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                    </svg>
                </div>
                <div class="flex flex-col gap-2">
                    <h2 class="text-2xl font-bold mono text-green-500 uppercase tracking-tighter">Maintenance Complete</h2>
                    <p class="text-xs text-gray-400 uppercase tracking-widest">Environment is stable. Restarting worker...</p>
                </div>
            `;
            setTimeout(() => {
                overlay.remove();
                toggleWorker();
            }, 2000);
        } else {
            overlay.innerHTML = `
                <div class="w-16 h-16 bg-red-500 rounded-full flex items-center justify-center shadow-[0_0_20px_rgba(239,68,68,0.5)]">
                    <span class="text-2xl font-bold text-white">X</span>
                </div>
                <div class="flex flex-col gap-2">
                    <h2 class="text-2xl font-bold mono text-red-500 uppercase tracking-tighter">Maintenance Failed</h2>
                    <p class="text-xs text-gray-400 uppercase tracking-widest">Check the logs for details.</p>
                </div>
                <button onclick="document.getElementById('maintenance-overlay').remove()" class="mt-4 px-4 py-2 bg-white/10 rounded hover:bg-white/20">Close</button>
            `;
        }
    }
});

async function triggerAction(action) {
    if (!confirm(`Trigger ${action}? This will stream output to the console.`)) return;
    await fetchWithAuth(`/api/${action}`, { method: 'POST' });
    showTab('dashboard');
}

function renderModels() {
    const tbody = document.getElementById('models-table-body');
    const search = document.getElementById('model-search').value.toLowerCase();
    tbody.innerHTML = '';

    const filtered = models.filter(m => m.name.toLowerCase().includes(search));

    filtered.sort((a, b) => b.queued - a.queued).forEach(m => {
        const tr = document.createElement('tr');
        tr.className = 'border-b border-white/5 hover:bg-white/5 transition-all';
        tr.innerHTML = `
            <td class="p-3">${m.name}</td>
            <td class="p-3 text-center">${m.queued}</td>
            <td class="p-3 text-center">${m.count}</td>
            <td class="p-3 text-right">
                <button onclick="toggleModel('${m.name}')" class="px-3 py-1 rounded border border-amber-500/50 text-amber-500 hover:bg-amber-500 hover:text-black transition-all">
                    ${isModelLoaded(m.name) ? 'REMOVE' : 'ADD'}
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function renderModelsToLoad() {
    const list = document.getElementById('models-to-load-list');
    list.innerHTML = '';

    const modelsToLoad = currentConfig.models_to_load || [];
    modelsToLoad.forEach(m => {
        const badge = document.createElement('span');
        badge.className = 'px-3 py-1 bg-amber-500 text-black rounded-full text-[10px] font-bold mono uppercase flex items-center gap-2';
        badge.innerHTML = `${m} <button onclick="toggleModel('${m}')" class="hover:text-white">×</button>`;
        list.appendChild(badge);
    });
}

function isModelLoaded(name) {
    return (currentConfig.models_to_load || []).includes(name);
}

async function toggleModel(name) {
    let list = [...(currentConfig.models_to_load || [])];
    if (list.includes(name)) list = list.filter(m => m !== name);
    else list.push(name);

    currentConfig.models_to_load = list;
    await saveConfig();
    renderModelsToLoad();
    renderModels();
}

async function applyTopN() {
    const val = document.getElementById('top-n-selector').value;
    if (!val) return;
    currentConfig.models_to_load = [val];
    await saveConfig();
    renderModelsToLoad();
    alert(`Applied ${val}`);
}

function formatUptime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${h}h ${m}m ${s}s`;
}

function updateValDisplay(el) {
    document.getElementById('val-' + el.name).textContent = el.value;
}

function clearLogs() {
    document.getElementById('log-display').innerHTML = '';
}

async function toggleNgrok() {
    const btn = document.getElementById('ngrok-btn');
    const status = document.getElementById('ngrok-status');
    const isStarting = btn.textContent.toLowerCase().includes('start');

    btn.disabled = true;
    status.textContent = isStarting ? 'Starting tunnel...' : 'Stopping tunnel...';

    try {
        const resp = await fetchWithAuth('/api/ngrok', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: isStarting ? 'start' : 'stop' })
        });
        if (!resp) return;
        const data = await resp.json();

        if (data.status === 'success') {
            if (isStarting) {
                btn.textContent = 'Stop Tunnel';
                btn.classList.remove('bg-blue-500/20', 'text-blue-400');
                btn.classList.add('bg-red-500', 'text-white');
                status.innerHTML = `Tunnel active: <a href="${data.url}" target="_blank" class="text-blue-400 underline">${data.url}</a>`;
            } else {
                btn.textContent = 'Start Tunnel';
                btn.classList.add('bg-blue-500/20', 'text-blue-400');
                btn.classList.remove('bg-red-500', 'text-white');
                status.textContent = 'Tunnel is inactive.';
            }
        } else {
            alert('Ngrok error: ' + (data.message || 'Unknown error'));
            status.textContent = 'Tunnel error.';
        }
    } catch (e) {
        alert('Request failed: ' + e);
        status.textContent = 'Request failed.';
    } finally {
        btn.disabled = false;
    }
}

document.getElementById('toggle-worker').onclick = toggleWorker;
document.getElementById('model-search').oninput = renderModels;
document.getElementById('ngrok-btn').onclick = toggleNgrok;

loadConfig();
fetchWithAuth('/api/logs').then(r => r ? r.json() : null).then(lines => {
    if (!lines) return;
    const logDisplay = document.getElementById('log-display');
    const scrollContainer = document.getElementById('console-body');
    lines.forEach(l => {
        const line = document.createElement('div');
        line.innerHTML = ansiToHtml(l);
        logDisplay.appendChild(line);
    });
    scrollContainer.scrollTop = scrollContainer.scrollHeight;
});
