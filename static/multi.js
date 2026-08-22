/* 多级别联动(区间套):大级别定方向+关键位,小级别找确认 */
(function () {
  const PERIODS = ['1m', '5m', '15m', '1H', '4H', '1D', '1W'];
  const RANK = { '1m': 0, '5m': 1, '15m': 2, '1H': 3, '4H': 4, '1D': 5, '1W': 6 };
  const SMALL_DEFAULT = { '1W': '1D', '1D': '1H', '4H': '15m', '1H': '5m', '15m': '1m', '5m': '1m' };
  const state = {
    inst: localStorage.getItem('chan_inst') || 'ETH-USDT-SWAP',
    big: '4H', small: '15m',
    data: null, loading: false,
  };

  /* ---------------- overlay 注册(与 app.js 同款 + 关键位横线) ---------------- */
  klinecharts.registerOverlay({
    name: 'biLine',
    totalStep: 3,
    lock: true,
    createPointFigures: ({ coordinates, overlay }) => {
      if (coordinates.length < 2) return [];
      const d = overlay.extendData || {};
      return [{
        type: 'line',
        attrs: { coordinates },
        styles: { color: d.color || '#f0b90b', size: 1.4, style: d.dashed ? 'dashed' : 'solid', dashedValue: [4, 4] },
        ignoreEvent: true,
      }];
    },
  });

  klinecharts.registerOverlay({
    name: 'zsRect',
    totalStep: 3,
    lock: true,
    createPointFigures: ({ coordinates }) => {
      if (coordinates.length < 2) return [];
      const [a, b] = coordinates;
      return [{
        type: 'rect',
        attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) },
        styles: { style: 'stroke_fill', color: 'rgba(76,141,255,0.10)', borderColor: 'rgba(76,141,255,0.85)', borderSize: 1 },
        ignoreEvent: true,
      }];
    },
  });

  klinecharts.registerOverlay({
    name: 'chanMark',
    totalStep: 2,
    lock: true,
    createPointFigures: ({ coordinates, overlay }) => {
      const c = coordinates[0];
      if (!c) return [];
      const d = overlay.extendData || {};
      const above = d.pos === 'above';
      const off = 6 + (d.offset || 0);
      return [{
        type: 'text',
        attrs: { x: c.x, y: above ? c.y - off : c.y + off, text: d.text || '', align: 'center', baseline: above ? 'bottom' : 'top' },
        styles: {
          color: d.textColor || '#fff', size: d.size || 11, weight: 'bold', family: 'sans-serif',
          backgroundColor: d.bg || 'transparent', borderRadius: 3,
          paddingLeft: 4, paddingRight: 4, paddingTop: 2, paddingBottom: 2,
        },
        ignoreEvent: true,
      }];
    },
  });

  // 大级别关键位横线:整宽虚线 + 右侧标签
  klinecharts.registerOverlay({
    name: 'levelLine',
    totalStep: 2,
    lock: true,
    createPointFigures: ({ coordinates, bounding, overlay }) => {
      const c = coordinates[0];
      if (!c) return [];
      const d = overlay.extendData || {};
      return [
        {
          type: 'line',
          attrs: { coordinates: [{ x: 0, y: c.y }, { x: bounding.width, y: c.y }] },
          styles: { color: d.color || '#8250df', size: d.bold ? 1.6 : 1, style: 'dashed', dashedValue: [6, 4] },
          ignoreEvent: true,
        },
        {
          type: 'text',
          attrs: { x: bounding.width - 4, y: c.y - 3, text: d.text || '', align: 'right', baseline: 'bottom' },
          styles: {
            color: d.color || '#8250df', size: 10, family: 'sans-serif',
            backgroundColor: 'rgba(11,14,17,0.75)', borderRadius: 2,
            paddingLeft: 3, paddingRight: 3, paddingTop: 1, paddingBottom: 1,
          },
          ignoreEvent: true,
        },
      ];
    },
  });

  /* ---------------- 图表 ---------------- */
  function makeChart(elId) {
    const chart = klinecharts.init(elId);
    chart.setStyles({
      grid: { horizontal: { color: '#161b22' }, vertical: { color: '#161b22' } },
      candle: {
        bar: {
          upColor: '#2ebd85', downColor: '#f6465d', noChangeColor: '#8b949e',
          upBorderColor: '#2ebd85', downBorderColor: '#f6465d', noChangeBorderColor: '#8b949e',
          upWickColor: '#2ebd85', downWickColor: '#f6465d', noChangeWickColor: '#8b949e',
        },
        priceMark: {
          high: { color: '#8b949e' }, low: { color: '#8b949e' },
          last: { upColor: '#2ebd85', downColor: '#f6465d', noChangeColor: '#8b949e' },
        },
        tooltip: { text: { color: '#c9d1d9' } },
      },
      indicator: {
        ohlc: { upColor: '#2ebd85', downColor: '#f6465d' },
        bars: [{ style: 'fill', borderStyle: 'solid', borderSize: 1, borderDashedValue: [2, 2], upColor: 'rgba(46,189,133,.7)', downColor: 'rgba(246,70,93,.7)', noChangeColor: '#8b949e' }],
        tooltip: { text: { color: '#c9d1d9' } },
      },
      xAxis: { axisLine: { color: '#2a2e35' }, tickText: { color: '#8b949e' }, tickLine: { color: '#2a2e35' } },
      yAxis: { axisLine: { color: '#2a2e35' }, tickText: { color: '#8b949e' }, tickLine: { color: '#2a2e35' } },
      separator: { color: '#2a2e35' },
      crosshair: {
        horizontal: { line: { color: '#4b5563' }, text: { backgroundColor: '#2a2e35' } },
        vertical: { line: { color: '#4b5563' }, text: { backgroundColor: '#2a2e35' } },
      },
    });
    chart.createIndicator({
      name: 'EMA', calcParams: [5, 10, 20, 40],
      styles: {
        // 必须给完整线型对象:只给 color 会丢 dashedValue 导致每帧异常、图表冻结
        lines: ['#ffffff', '#f6465d', '#6fb3ff', '#ff9f1a'].map((color) => ({
          style: 'solid', smooth: false, size: 1, color, dashedValue: [2, 2],
        })),
      },
    }, true, { id: 'candle_pane' });
    chart.createIndicator('VOL', false, { height: 55 });
    chart.createIndicator('MACD', false, { height: 70 });
    return { chart, key: null, lastList: null };
  }

  const big = makeChart('chartBig');
  const small = makeChart('chartSmall');
  window._chartBig = big.chart;   // 调试用
  window._chartSmall = small.chart;

  /* ---------------- 工具栏 ---------------- */
  const $ = (id) => document.getElementById(id);
  const fmtP = (v) => (v == null ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 }));
  const fmtTs = (ts) => {
    const dt = new Date(ts);
    const pad = (n) => String(n).padStart(2, '0');
    return `${dt.getMonth() + 1}/${dt.getDate()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
  };

  function renderCoins(list) {
    const box = $('coins');
    box.innerHTML = '';
    list.forEach((inst) => {
      const b = document.createElement('button');
      b.textContent = inst.split('-')[0];
      b.dataset.inst = inst;
      if (inst === state.inst) b.classList.add('active');
      b.onclick = () => { state.inst = inst; syncActive(); loadData(false); };
      box.appendChild(b);
    });
  }

  function fillBarSelects() {
    const bigSel = $('bigSel'), smallSel = $('smallSel');
    bigSel.innerHTML = '';
    smallSel.innerHTML = '';
    PERIODS.forEach((p) => {
      if (RANK[p] >= 1) bigSel.add(new Option(p, p));   // 大级别至少 5m
      if (RANK[p] <= 5) smallSel.add(new Option(p, p)); // 小级别最多 1D
    });
    bigSel.value = state.big;
    smallSel.value = state.small;
    bigSel.onchange = () => {
      state.big = bigSel.value;
      // 保证 小级别 < 大级别:不满足时自动切到推荐搭配
      if (RANK[state.small] >= RANK[state.big]) {
        state.small = SMALL_DEFAULT[state.big] || '15m';
        smallSel.value = state.small;
      }
      loadData(false);
    };
    smallSel.onchange = () => {
      state.small = smallSel.value;
      if (RANK[state.small] >= RANK[state.big]) {
        const next = PERIODS[Math.min(RANK[state.small] + 1, 6)];
        state.big = next;
        bigSel.value = next;
      }
      loadData(false);
    };
  }

  function syncActive() {
    document.querySelectorAll('#coins button').forEach((b) => b.classList.toggle('active', b.dataset.inst === state.inst));
    $('title').textContent = state.inst.replace('-SWAP', ' 永续') + ' · 多级别';
    document.title = `${state.inst} ${state.big}/${state.small} · 多级别联动`;
    localStorage.setItem('chan_inst', state.inst);
  }

  $('goBtn').onclick = () => {
    const v = $('instInput').value.trim().toUpperCase();
    if (!v) return;
    state.inst = v.includes('-') ? v : `${v}-USDT-SWAP`;
    syncActive();
    loadData(false);
  };
  $('instInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('goBtn').onclick(); });
  $('refreshBtn').onclick = () => loadData(true);
  ['ckBi', 'ckZs', 'ckSig', 'ckLv'].forEach((id) => { $(id).onchange = drawAll; });

  function setStatus(msg, err) {
    const el = $('status');
    el.textContent = msg;
    el.className = err ? 'err' : '';
  }

  /* ---------------- 数据加载 ---------------- */
  async function loadData(force) {
    if (state.loading) return;
    state.loading = true;
    const btn = $('refreshBtn');
    btn.disabled = true;
    setStatus('加载中…(拉两个级别)');
    try {
      const r = await fetch(`/api/multi?inst=${encodeURIComponent(state.inst)}&big=${state.big}&small=${state.small}&force=${force ? 1 : 0}`);
      const j = await r.json();
      if (j.code !== 0) throw new Error(j.msg || '未知错误');
      state.data = j;
      applyChart(big, j.big, j.big_bar);
      applyChart(small, j.small, j.small_bar);
      $('headBig').innerHTML = `<b>${j.big_bar}</b> 大级别 · 定方向 / 找关键位`;
      $('headSmall').innerHTML = `<b>${j.small_bar}</b> 小级别 · 找确认(横线 = ${j.big_bar} 关键位)`;
      drawAll();
      renderVerdict();
      renderPanel();
      setStatus(`更新于 ${new Date(j.fetched_at).toLocaleTimeString()}`);
    } catch (e) {
      setStatus(`加载失败: ${e.message}`, true);
    } finally {
      state.loading = false;
      btn.disabled = false;
    }
  }

  function applyChart(box, payload, bar) {
    const last = payload.candles[payload.candles.length - 1];
    const p = last.c;
    const prec = p >= 1000 ? 2 : p >= 10 ? 3 : p >= 0.1 ? 5 : 8;
    const newList = payload.candles.map((c) => ({
      timestamp: c.ts, open: c.o, high: c.h, low: c.l, close: c.c, volume: c.vol,
    }));
    const key = `${state.inst}|${bar}`;
    const prevLast = box.lastList && box.lastList[box.lastList.length - 1];
    // 同币同周期刷新走增量,保留缩放/拖动;切币切周期才整体重载
    if (box.key === key && prevLast && newList.some((k) => k.timestamp === prevLast.timestamp)) {
      newList.filter((k) => k.timestamp >= prevLast.timestamp).forEach((k) => box.chart.updateData(k));
    } else {
      box.chart.setPriceVolumePrecision(prec, 2);
      box.chart.applyNewData(newList);
    }
    box.key = key;
    box.lastList = newList;
  }

  /* ---------------- 标注 ---------------- */
  function drawAll() {
    if (!state.data) return;
    drawChan(big, state.data.big);
    drawChan(small, state.data.small);
    drawLevels();
  }

  function drawChan(box, payload) {
    const chart = box.chart;
    chart.removeOverlay({ groupId: 'chan' });
    const ch = payload.chan;
    const mk = (o) => chart.createOverlay(Object.assign({ groupId: 'chan', lock: true }, o));

    if ($('ckBi').checked) {
      ch.bi.forEach((b) => mk({
        name: 'biLine',
        points: [
          { timestamp: b.start_ts, value: b.start_price },
          { timestamp: b.end_ts, value: b.end_price },
        ],
        extendData: { color: b.unfinished ? '#8b949e' : '#f0b90b', dashed: b.unfinished },
      }));
    }
    if ($('ckZs').checked) {
      ch.zhongshu.forEach((z) => mk({
        name: 'zsRect',
        points: [
          { timestamp: z.start_ts, value: z.zg },
          { timestamp: z.end_ts, value: z.zd },
        ],
      }));
    }
    if ($('ckSig').checked) {
      ch.beichi.forEach((b) => {
        const unlocked = b.locked === false;
        const bg = unlocked ? 'rgba(154,103,0,0.55)' : '#9a6700';
        const tail = unlocked ? ' ?' : '';
        mk({
          name: 'chanMark',
          points: [{ timestamp: b.ts, value: b.price }],
          extendData: b.dir === 'down'
            ? { text: `⚡底背驰 ${b.area_ratio}${tail}`, pos: 'below', bg, textColor: '#fff', offset: 20 }
            : { text: `⚡顶背驰 ${b.area_ratio}${tail}`, pos: 'above', bg, textColor: '#fff', offset: 20 },
        });
      });
      (ch.beili || []).forEach((s) => {
        const buy = s.type === 'DB';
        const unlocked = s.locked === false;
        const hidden = s.subtype === 'hidden';
        const solid = buy ? '#1b7c83' : '#bf3989';
        const faded = buy ? 'rgba(27,124,131,0.5)' : 'rgba(191,57,137,0.5)';
        mk({
          name: 'chanMark',
          points: [{ timestamp: s.ts, value: s.price }],
          extendData: {
            text: (hidden ? '隐' : '') + (buy ? '背离B' : '背离S') + (unlocked ? '?' : ''),
            pos: buy ? 'below' : 'above', offset: 34,
            bg: unlocked ? faded : solid, textColor: '#fff',
          },
        });
      });
      ch.bsp.forEach((s) => {
        const buy = s.type[0] === 'B';
        const unlocked = s.locked === false;
        mk({
          name: 'chanMark',
          points: [{ timestamp: s.ts, value: s.price }],
          extendData: {
            text: s.type + (unlocked ? '?' : ''), pos: buy ? 'below' : 'above',
            bg: unlocked ? (buy ? 'rgba(26,127,55,0.5)' : 'rgba(207,34,46,0.5)')
                         : (buy ? '#1a7f37' : '#cf222e'),
            textColor: '#fff',
          },
        });
      });
    }
  }

  function drawLevels() {
    // 大级别关键位横线,画在两张图上;小级别图上是核心(测试位置一目了然)
    [big, small].forEach((box) => box.chart.removeOverlay({ groupId: 'lvl' }));
    if (!$('ckLv').checked) return;
    const m = state.data.multi;
    if (!m || !m.levels) return;
    [big, small].forEach((box) => {
      const lastTs = box.lastList && box.lastList.length ? box.lastList[box.lastList.length - 1].timestamp : null;
      if (lastTs == null) return;
      m.levels.forEach((lv) => {
        const ma = lv.kind === 'ma';
        const color = ma ? '#8b949e' : (lv.role === 'support' ? '#2ebd85' : '#f6465d');
        box.chart.createOverlay({
          groupId: 'lvl', lock: true, name: 'levelLine',
          points: [{ timestamp: lastTs, value: lv.price }],
          extendData: {
            color, bold: lv.testing,
            text: `${m.big_bar} ${lv.name} ${fmtP(lv.price)}` + (lv.testing ? ' ◀测试中' : ''),
          },
        });
      });
    });
  }

  /* ---------------- 决策横幅 ---------------- */
  function renderVerdict() {
    const m = state.data.multi;
    const el = $('verdict');
    if (!m) { el.innerHTML = '<span class="muted">无数据</span>'; return; }
    const colorMap = { long: '#2ebd85', short: '#f6465d', wait: '#8b949e' };
    const nameMap = { long: '可考虑做多', short: '可考虑做空', wait: '观望' };
    const c = colorMap[m.action];
    el.style.borderLeftColor = c;
    const blk = (lbl, val) => `<span class="blk"><span class="lbl">${lbl}</span>${val}</span>`;
    const scColor = m.score >= 75 ? '#2ebd85' : m.score >= 60 ? '#4c8dff' : m.score >= 45 ? '#d29922' : '#6e7681';

    let html = `<span class="act" style="color:${c}">${nameMap[m.action]}</span>`;
    html += blk(`${m.big_bar} 大级别方向`, m.dir_desc);
    html += blk('场景', m.scenario);
    if (m.action !== 'wait') {
      const s = m.signal;
      html += blk(`${m.small_bar} 确认信号`, `${s.tag} @ ${fmtP(s.price)}(评分 ${s.score ?? '-'})${s.locked === false ? ' <span class="warn">未锁定·或移动</span>' : ''}`);
      html += blk('验证位置', `${m.big_bar} ${s.verify_level} ${fmtP(s.verify_price)}`);
      html += blk('综合评分', `<b style="color:${scColor}">${m.score}</b> / 100`);
      html += blk('入场参考', fmtP(m.entry));
      html += blk('止损参考(关键位外0.2%)', `<span class="down">${fmtP(m.stop)}</span>`);
      if (m.target != null) html += blk('目标(下一关键位)', fmtP(m.target));
      if (m.rr != null) html += blk('盈亏比', `1:${m.rr}`);
      if (m.warning) html += `<span class="blk warn" style="white-space:normal;max-width:320px">⚠ ${m.warning}</span>`;
    } else {
      html += `<span class="blk" style="white-space:normal;max-width:720px">${m.reason}</span>`;
      if (m.better_entry != null) html += blk('等待入场价(满足1:2)', `<b>${fmtP(m.better_entry)}</b>`);
    }
    if (m.breakout) {
      const bc = m.breakout.dir === 'up' ? '#2ebd85' : '#f6465d';
      html += `<span class="blk" style="white-space:normal;max-width:420px;border-left:3px solid ${bc};padding-left:8px"><span class="lbl">场景三 · 观察信号(非入场依据)</span>${m.breakout.note}</span>`;
    }
    html += `<span class="blk muted" style="white-space:normal;max-width:360px">先大后小:小级别信号必须在大级别关键位(容差±${m.tol_pct}%)得到验证才有效。仅供参考,非投资建议。</span>`;
    el.innerHTML = html;
  }

  /* ---------------- 面板 ---------------- */
  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span>${v}</span></div>`;

  function renderPanel() {
    const m = state.data.multi;
    if (!m) return;

    /* 大级别方向 + 关键位表 */
    const dirCls = m.dir === 'long' ? 'up' : m.dir === 'short' ? 'down' : 'flat';
    const dirTxt = m.dir === 'long' ? '偏多' : m.dir === 'short' ? '偏空' : '震荡/中性';
    let h1 = kv('方向判定', `<span class="${dirCls}"><b>${dirTxt}</b></span>(强度 ${m.dir_strength})`);
    h1 += `<div class="muted" style="margin-bottom:6px">${m.dir_desc}</div>`;
    let rows = '';
    (m.levels || []).forEach((lv) => {
      const roleTxt = lv.testing
        ? '<span class="tag" style="background:#d29922">测试中</span>'
        : (lv.role === 'support' ? '<span class="up">下方支撑</span>' : '<span class="down">上方压力</span>');
      rows += `<tr class="${lv.testing ? 'hot' : ''}">
        <td>${lv.name}${lv.kind === 'ma' ? ' <span class="muted">辅</span>' : ''}</td>
        <td>${fmtP(lv.price)}</td>
        <td class="${lv.dist_pct >= 0 ? 'up' : 'down'}">${lv.dist_pct > 0 ? '+' : ''}${lv.dist_pct}%</td>
        <td>${roleTxt}</td></tr>`;
    });
    h1 += rows
      ? `<table><tr><th>关键位</th><th>价格</th><th>现价距离</th><th>状态</th></tr>${rows}</table>`
      : '<div class="muted">大级别结构不足,无法给出关键位。</div>';
    h1 += `<div class="muted" style="margin-top:4px">结构位(中枢/笔端点)优先,均线只是辅助;测试判定容差 ±${m.tol_pct}%(按${m.big_bar}近20根平均振幅自适应)。</div>`;
    $('colDir').innerHTML = `<h3>${m.big_bar} 大级别 · 方向与关键位</h3>` + h1;

    /* 小级别信号 */
    let rows2 = '';
    (m.signals || []).slice(-12).reverse().forEach((s) => {
      const buy = s.side === 'long';
      const unlocked = s.locked === false;
      const bg = s.tag.includes('背离') ? (buy ? '#1b7c83' : '#bf3989')
        : s.tag.includes('背驰') ? '#9a6700'
        : (buy ? '#1a7f37' : '#cf222e');
      const verify = s.verify_level
        ? `<span class="up">✓ ${s.verify_level}</span>`
        : '<span class="muted">✗ 远离关键位·杂波</span>';
      const scColor = s.score >= 75 ? '#2ebd85' : s.score >= 60 ? '#4c8dff' : s.score >= 45 ? '#d29922' : '#6e7681';
      rows2 += `<tr class="row" data-ts="${s.ts}">
        <td><span class="tag" style="background:${bg};${unlocked ? 'opacity:.55' : ''}">${s.tag}${unlocked ? '?' : ''}</span></td>
        <td>${fmtP(s.price)}</td>
        <td>${s.score != null ? `<b style="color:${scColor}">${s.score}</b>` : '-'}</td>
        <td>${verify}</td>
        <td class="muted">${fmtTs(s.ts)}</td></tr>`;
    });
    $('colSig').innerHTML = `<h3>${m.small_bar} 小级别 · 确认信号(点击跳转)</h3>` +
      (rows2
        ? `<table><tr><th>信号</th><th>价格</th><th>评分</th><th>关键位验证</th><th>时间</th></tr>${rows2}</table>`
        : '<div class="muted">最近两笔范围内无活跃信号。等价格接触大级别关键位后再看这里。</div>') +
      '<div class="muted" style="margin-top:4px">只有落在大级别关键位附近的信号才算确认;远离关键位的信号是杂波,忽略。</div>';
    document.querySelectorAll('#colSig tr.row').forEach((tr) => {
      tr.onclick = () => small.chart.scrollToTimestamp(Number(tr.dataset.ts), 300);
    });

    /* 刹车印 */
    const bk = m.brakes;
    let h3 = '';
    if (bk) {
      h3 += (bk.checks || []).map((ck) => `<div class="kv">
        <span class="k">${ck.name}</span>
        <span>${ck.hit ? '<span class="down"><b>✓ 出现</b></span>' : '<span class="muted">—</span>'}</span>
      </div><div class="muted" style="margin-bottom:4px">${ck.note}</div>`).join('');
      h3 += `<div class="card" style="border-left:3px solid ${bk.color}">
        <div style="font-weight:700;color:${bk.color}">情况 ${bk.case}</div>
        <div style="margin-top:2px">${bk.advice}</div>
      </div>`;
      h3 += `<div class="muted">小级别生命线 EMA20 = ${fmtP(bk.ema20)}。只要没被连续收盘跌破/收复,背离一百次也还在原趋势里。</div>`;
      $('colBrake').innerHTML = `<h3>${m.small_bar} 刹车印 · ${bk.title}</h3>` + h3;
    } else {
      $('colBrake').innerHTML = '<h3>刹车印 · 持仓检查</h3><div class="muted">小级别K线不足。</div>';
    }
  }

  /* ---------------- 启动 ---------------- */
  window.addEventListener('resize', () => { big.chart.resize(); small.chart.resize(); });
  setInterval(() => {
    if ($('ckAuto').checked && !state.loading) loadData(true);
  }, 60 * 1000);
  fillBarSelects();
  fetch('/api/instruments').then((r) => r.json()).then((j) => renderCoins(j.instruments))
    .catch(() => renderCoins(['BTC-USDT-SWAP', 'ETH-USDT-SWAP']));
  syncActive();
  loadData(false);
})();
