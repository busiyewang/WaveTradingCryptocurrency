/* 缠论K线分析 前端逻辑 */
(function () {
  const PERIODS = ['1m', '5m', '15m', '1H', '4H', '1D', '1W'];
  const state = {
    inst: 'ETH-USDT-SWAP',
    bar: '4H',
    data: null,
    loading: false,
  };

  /* ---------------- 图表初始化 ---------------- */
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
      const off = 6 + (d.offset || 0);  // offset 用于避免与同点其他标签重叠
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

  // 国内惯例 RSI(Wilder 平滑),与 OKX 显示一致;内置 RSI 用简单均值,数值偏差大
  klinecharts.registerIndicator({
    name: 'RSI_CN',
    shortName: 'RSI',
    calcParams: [6, 12, 24],
    figures: [
      { key: 'rsi1', title: 'RSI6: ', type: 'line' },
      { key: 'rsi2', title: 'RSI12: ', type: 'line' },
      { key: 'rsi3', title: 'RSI24: ', type: 'line' },
    ],
    calc: (dataList, indicator) => {
      const result = dataList.map(() => ({}));
      indicator.calcParams.forEach((n, pi) => {
        let up = 0, total = 0;
        for (let i = 1; i < dataList.length; i++) {
          const diff = dataList[i].close - dataList[i - 1].close;
          const u = Math.max(diff, 0);
          const a = Math.abs(diff);
          up = (up * (n - 1) + u) / n;
          total = (total * (n - 1) + a) / n;
          result[i]['rsi' + (pi + 1)] = total === 0 ? 50 : (up / total) * 100;
        }
      });
      return result;
    },
  });

  const chart = klinecharts.init('chart');
  window._chart = chart;  // 调试用
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

  // 主图 EMA(5,10,20,40),配色对齐 OKX:白/红/浅蓝/橘黄;副图 VOL/MACD/KDJ/RSI
  chart.createIndicator({
    name: 'EMA', calcParams: [5, 10, 20, 40],
    styles: {
      // 注意:必须给完整线型对象,只给 color 会覆盖掉默认的 size/dashedValue 导致绘制崩溃
      lines: ['#ffffff', '#f6465d', '#6fb3ff', '#ff9f1a'].map((color) => ({
        style: 'solid', smooth: false, size: 1, color, dashedValue: [2, 2],
      })),
    },
  }, true, { id: 'candle_pane' });
  chart.createIndicator('VOL', false, { height: 70 });
  chart.createIndicator('MACD', false, { height: 90 });
  chart.createIndicator('KDJ', false, { height: 80 });
  chart.createIndicator('RSI_CN', false, { height: 80 });

  /* ---------------- 工具栏 ---------------- */
  const $ = (id) => document.getElementById(id);

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

  function renderPeriods() {
    const box = $('periods');
    PERIODS.forEach((p) => {
      const b = document.createElement('button');
      b.textContent = p;
      b.dataset.bar = p;
      if (p === state.bar) b.classList.add('active');
      b.onclick = () => { state.bar = p; syncActive(); loadData(false); };
      box.appendChild(b);
    });
  }

  function syncActive() {
    document.querySelectorAll('#coins button').forEach((b) => b.classList.toggle('active', b.dataset.inst === state.inst));
    document.querySelectorAll('#periods button').forEach((b) => b.classList.toggle('active', b.dataset.bar === state.bar));
    $('title').textContent = state.inst.replace('-SWAP', ' 永续');
    document.title = `${state.inst} ${state.bar} · 缠论分析`;
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
  ['ckFx', 'ckBi', 'ckZs', 'ckBc', 'ckBsp', 'ckPat', 'ckTri', 'ckHp'].forEach((id) => { $(id).onchange = drawOverlays; });

  /* ---------------- 数据加载 ---------------- */
  async function loadData(force) {
    if (state.loading) return;
    state.loading = true;
    const btn = $('refreshBtn');
    btn.disabled = true;
    setStatus('加载中…');
    try {
      const r = await fetch(`/api/kline?inst=${encodeURIComponent(state.inst)}&bar=${state.bar}&force=${force ? 1 : 0}`);
      const j = await r.json();
      if (j.code !== 0) throw new Error(j.msg || '未知错误');
      state.data = j;
      applyData();
      if (state.scalpOn) fetchScalp(force);
      setStatus(`${j.count} 根K线 · 更新于 ${new Date(j.fetched_at).toLocaleTimeString()}`);
    } catch (e) {
      setStatus(`加载失败: ${e.message}`, true);
    } finally {
      state.loading = false;
      btn.disabled = false;
    }
  }

  function setStatus(msg, err) {
    const el = $('status');
    el.textContent = msg;
    el.className = err ? 'err' : '';
  }

  function applyData() {
    const d = state.data;
    const last = d.candles[d.candles.length - 1];
    const p = last.c;
    const prec = p >= 1000 ? 2 : p >= 10 ? 3 : p >= 0.1 ? 5 : 8;
    const newList = d.candles.map((c) => ({
      timestamp: c.ts, open: c.o, high: c.h, low: c.l, close: c.c, volume: c.vol,
    }));
    const key = `${d.inst}|${d.bar}`;
    const prevLast = state.lastList && state.lastList[state.lastList.length - 1];
    // 同币种同周期的刷新走增量更新(只改最后一根和新增K线),
    // 保留用户当前的缩放与拖动位置;整体重载只在切币/切周期时发生
    if (state.chartKey === key && prevLast
        && newList.some((k) => k.timestamp === prevLast.timestamp)) {
      newList.filter((k) => k.timestamp >= prevLast.timestamp)
        .forEach((k) => chart.updateData(k));
    } else {
      chart.setPriceVolumePrecision(prec, 2);
      chart.applyNewData(newList);
    }
    state.chartKey = key;
    state.lastList = newList;
    drawOverlays();
    renderPanel();
    renderDecision();
  }

  /* ---------------- 交易决策 ---------------- */
  function renderDecision() {
    if (state.scalpOn) { renderScalpBanner(); return; }
    const d = state.data && state.data.decision;
    const el = $('decision');
    if (!d) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    const colorMap = { long: '#2ebd85', short: '#f6465d', wait: '#8b949e' };
    const nameMap = { long: '可考虑做多', short: '可考虑做空', wait: '观望' };
    const c = colorMap[d.action];
    el.style.borderLeftColor = c;

    const blk = (lbl, val) => `<span class="blk"><span class="lbl">${lbl}</span>${val}</span>`;
    let html = `<span class="act" style="color:${c}">${nameMap[d.action]}</span>`;
    html += blk(`大级别方向(${d.dir_level})`, d.dir_desc);

    if (d.action === 'wait') {
      html += `<span class="blk" style="white-space:normal;max-width:640px">${d.reason}</span>`;
    } else {
      html += blk('信号', `${d.signal.type} @ ${fmtP(d.signal.price)}(质量 ${d.signal_quality})${d.signal.locked === false ? ' <span class="warn">未锁定·或移动</span>' : ''}`);
      html += blk('指标确认', `${d.confirm_hits}/3:${d.confirms.join('、')}`);
      html += blk('质量分 q', `<b style="color:${c}">${d.q}</b> → 建议标准仓位的 ${d.position}%`);
      html += blk('入场参考', fmtP(d.entry));
      html += blk(d.stop_name, `<span class="down">${fmtP(d.stop)}</span>`);
      if (d.targets.length) {
        html += blk('目标', d.targets.map((t) => `${t[0]} ${fmtP(t[1])}`).join(' → '));
      }
      if (d.rr != null) html += blk('盈亏比', `1:${d.rr}`);
      (d.warnings || []).forEach((w) => { html += `<span class="blk warn" style="white-space:normal;max-width:360px">⚠ ${w}</span>`; });
    }
    html += `<span class="blk muted" style="white-space:normal;max-width:420px">${d.rules}。仅供参考,非投资建议。</span>`;
    el.innerHTML = html;
  }

  function renderScalpBanner() {
    const el = $('decision');
    el.style.display = 'flex';
    const s = state.scalp;
    if (!s || s.loading) {
      el.style.borderLeftColor = '#d29922';
      el.innerHTML = '<span class="act" style="color:#d29922">⚡超短线</span><span class="muted">拉取 1H / 5M / 1M 三级数据中…</span>';
      return;
    }
    if (s.error) {
      el.style.borderLeftColor = '#f6465d';
      el.innerHTML = `<span class="act" style="color:#f6465d">⚡超短线</span><span class="warn">加载失败: ${s.error},点"⟳ 刷新"重试</span>`;
      return;
    }
    const colorMap = { long: '#2ebd85', short: '#f6465d', wait: '#8b949e' };
    const nameMap = { long: '可考虑做多', short: '可考虑做空', wait: '观望' };
    const c = colorMap[s.action];
    el.style.borderLeftColor = c;
    const blk = (lbl, val) => `<span class="blk"><span class="lbl">${lbl}</span>${val}</span>`;
    let h = `<span class="act" style="color:${c}">⚡${nameMap[s.action]}</span>`;
    h += blk('① 1H 方向(40%)', s.dir_desc);
    h += blk('② 5M 信号(40%)', s.sig5m ? `${s.sig5m.type} @ ${fmtP(s.sig5m.price)}${s.sig5m.locked === false ? ' <span class="warn">未锁定</span>' : ''}` : '无活跃买卖点');
    h += blk('③ 1M 扳机(20%)', `<span style="white-space:normal;display:inline-block;max-width:300px">${s.trig1m || '—'}</span>`);
    if (s.action === 'wait') {
      h += `<span class="blk" style="white-space:normal;max-width:520px">${s.reason || ''}</span>`;
    } else {
      h += blk('质量分 q', `<b style="color:${c}">${s.q}</b> → 仓位 ${s.position}%`);
      h += blk('入场', fmtP(s.entry));
      h += blk(s.stop_name, `<span class="down">${fmtP(s.stop)}</span>`);
      h += blk('分级止盈', s.targets.map((t, i) => `止盈${i + 1} ${fmtP(t[1])}`).join(' → ') + '(40/30/30)');
    }
    h += '<button id="calcOpen">仓位计算器</button>';
    h += '<span class="blk muted" style="white-space:normal;max-width:300px">图上蓝/红/绿线=入场/止损/止盈。仅供参考,非投资建议。</span>';
    el.innerHTML = h;
    $('calcOpen').onclick = openCalc;
  }

  /* ---------------- 缠论标注 ---------------- */
  function drawOverlays() {
    chart.removeOverlay({ groupId: 'chan' });
    const d = state.data;
    if (!d) return;
    const ch = d.chan;
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
    if ($('ckFx').checked) {
      ch.fenxing.forEach((f) => mk({
        name: 'chanMark',
        points: [{ timestamp: f.ts, value: f.price }],
        extendData: f.type === 'top'
          ? { text: '▿', pos: 'above', textColor: '#8b949e', size: 10 }
          : { text: '▵', pos: 'below', textColor: '#8b949e', size: 10 },
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
    if ($('ckBc').checked) {
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
    }
    if (state.data.patterns) {
      state.data.patterns.forEach((p) => {
        if (p.type === 'triangle') {
          if (!$('ckTri').checked) return;
          // 收敛三角形:上轨 + 下轨
          const triColor = p.break_dir === 'up' ? '#2ebd85' : p.break_dir === 'down' ? '#f6465d' : '#8250df';
          [p.upper, p.lower].forEach((ln) => mk({
            name: 'biLine',
            points: [
              { timestamp: ln[0][0], value: ln[0][1] },
              { timestamp: ln[1][0], value: ln[1][1] },
            ],
            extendData: { color: triColor, dashed: true },
          }));
          mk({
            name: 'chanMark',
            points: [{ timestamp: p.upper[1][0], value: p.upper[1][1] }],
            extendData: { text: '收敛' + (p.break_dir ? (p.break_dir === 'up' ? '↑破' : '↓破') : '△'), pos: 'above', offset: 4, bg: triColor, textColor: '#fff' },
          });
          return;
        }
        if (!$('ckPat').checked) return;
        const isTop = p.type === 'double_top';
        const lastTs = d.candles[d.candles.length - 1].ts;
        // 颈线(虚线)
        mk({
          name: 'biLine',
          points: [
            { timestamp: p.neck_ts, value: p.neck },
            { timestamp: Math.min(p.p2_ts + (p.p2_ts - p.neck_ts) * 2, lastTs), value: p.neck },
          ],
          extendData: { color: isTop ? '#f6465d' : '#2ebd85', dashed: true },
        });
        // 两个顶/底标记
        [[p.p1_ts, p.p1_price], [p.p2_ts, p.p2_price]].forEach(([ts, price], idx) => mk({
          name: 'chanMark',
          points: [{ timestamp: ts, value: price }],
          extendData: {
            text: (isTop ? 'M顶' : 'W底') + (idx + 1),
            pos: isTop ? 'above' : 'below', offset: 34,
            bg: isTop ? '#8250df' : '#0969da', textColor: '#fff',
          },
        }));
      });
    }
    const hupan = state.data.signals && state.data.signals.volume && state.data.signals.volume.hupan;
    if ($('ckHp').checked && hupan) {
      const cs = d.candles;
      mk({
        name: 'biLine',
        points: [
          { timestamp: cs[Math.max(0, cs.length - 45)].ts, value: hupan.level },
          { timestamp: cs[cs.length - 1].ts, value: hupan.level },
        ],
        extendData: { color: '#2ebd85', dashed: true },
      });
      mk({
        name: 'chanMark',
        points: [{ timestamp: cs[Math.max(0, cs.length - 45)].ts, value: hupan.level }],
        extendData: { text: `护盘位 ${hupan.touches}次承接`, pos: 'below', bg: '#1a7f37', textColor: '#fff' },
      });
    }
    if ($('ckBsp').checked) {
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

  /* ---------------- 分析面板 ---------------- */
  const fmtTs = (ts) => {
    const dt = new Date(ts);
    const pad = (n) => String(n).padStart(2, '0');
    return `${dt.getMonth() + 1}/${dt.getDate()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
  };
  const fmtP = (v) => (v == null ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 }));

  function renderPanel() {
    const ch = state.data.chan;
    const s = ch.summary || {};

    /* 结构状态 */
    const posMap = { above_zg: ['中枢上方', 'up'], inside: ['中枢内部', 'flat'], below_zd: ['中枢下方', 'down'] };
    let html = '';
    if (s.last_bi) {
      const up = s.last_bi.dir === 'up';
      html += kv('最新确认笔', `<span class="${up ? 'up' : 'down'}">${up ? '↑ 向上' : '↓ 向下'}</span> ${fmtP(s.last_bi.start_price)} → ${fmtP(s.last_bi.end_price)}`);
    }
    const unf = ch.bi.find((b) => b.unfinished);
    if (unf) {
      const up = unf.dir === 'up';
      html += kv('进行中一笔', `<span class="${up ? 'up' : 'down'}">${up ? '↑' : '↓'}</span> ${fmtP(unf.start_price)} → ${fmtP(unf.end_price)} <span class="muted">(未确认)</span>`);
    }
    if (s.last_zs) {
      html += kv('最近中枢 ZG/ZD', `${fmtP(s.last_zs.zg)} / ${fmtP(s.last_zs.zd)}${s.last_zs.extending ? ' <span class="muted">(延伸中)</span>' : ''}`);
      html += kv('震荡区间 GG/DD', `${fmtP(s.last_zs.gg)} / ${fmtP(s.last_zs.dd)}`);
      if (s.price_pos) {
        const [txt, cls] = posMap[s.price_pos] || ['-', 'flat'];
        html += kv('现价位置', `<span class="${cls}">${txt}</span> (${fmtP(s.last_price)})`);
      }
    }
    html += kv('统计', `分型 ${ch.fenxing.length} · 笔 ${ch.bi.filter((b) => !b.unfinished).length} · 中枢 ${ch.zhongshu.length}`);
    html += '<div class="muted" style="margin-top:6px">分析仅使用已收盘K线;级别越大越可靠。</div>';
    $('colStruct').innerHTML = '<h3>结构状态</h3>' + html;

    /* 量价·主力 */
    const sg = state.data.signals || {};
    let volHtml = '';
    if (sg.volume) {
      const v = sg.volume;
      const vsColor = v.state === '放量' ? '#d29922' : v.state === '缩量' ? '#6e7681' : '#4c8dff';
      volHtml += kv('量比(量/MA20)', `<b>${v.ratio}</b> <span class="tag" style="background:${vsColor}">${v.state}</span>`);
      volHtml += kv('量价组合', `<b>${v.combo}</b>`);
      volHtml += `<div class="muted" style="margin-bottom:4px">${v.combo_note}</div>`;
      volHtml += kv('位置(近60根)', `${v.zone} (${v.pos}%)`);
      const zl = v.zhuli;
      const zColor = zl.conclusion.includes('吸筹') ? '#2ebd85' : zl.conclusion.includes('派发') ? '#f6465d' : '#8b949e';
      volHtml += `<div class="card" style="margin-top:6px">
        <div style="font-weight:600;color:${zColor}">${zl.conclusion}</div>
        ${zl.evidence.map((e) => `<div class="muted">· ${e}</div>`).join('')}
      </div>`;
      if (v.hupan) {
        volHtml += kv('主力护盘位', `<span class="up"><b>${fmtP(v.hupan.level)}</b></span> · 长下影承接 ${v.hupan.touches} 次${v.hupan.vol_support ? ' · 放量' : ''}`);
        volHtml += '<div class="muted">图上绿色虚线;跌破护盘位=护盘失败,警惕加速下跌。</div>';
      }
      volHtml += '<div class="muted">仅基于K线量价;CVD/持仓量/链上数据未接入。</div>';
    } else volHtml = '<div class="muted">数据不足。</div>';
    $('colVol').innerHTML = '<h3>量价 · 主力</h3>' + volHtml;

    /* KDJ · RSI */
    let krHtml = '';
    if (sg.kdj) {
      const K = sg.kdj;
      const kTag = (t) => {
        const c = t.includes('金叉') ? '#2ebd85' : t.includes('死叉') ? '#f6465d'
          : t.includes('超买') || t.includes('高位') ? '#d29922'
          : t.includes('超卖') || t.includes('低位') ? '#4c8dff' : '#6e7681';
        return `<span class="tag" style="background:${c};margin-right:4px">${t}</span>`;
      };
      krHtml += kv('KDJ(9,3,3)', `K ${K.k} · D ${K.d} · J ${K.j}`);
      krHtml += `<div style="margin:2px 0 8px">${K.states.map(kTag).join('')}</div>`;
      const R = sg.rsi;
      krHtml += kv('RSI(6/12/24)', `${R.r6} / ${R.r12} / ${R.r24}`);
      krHtml += `<div style="margin:2px 0 4px">${kTag(R.state)}</div>`;
      if (R.divergence) {
        const dv = R.divergence;
        const c = dv.side === 'buy' ? '#2ebd85' : '#f6465d';
        krHtml += `<div class="card"><div style="font-weight:600;color:${c}">RSI ${dv.type}</div>
          <div class="muted">${dv.note}</div></div>`;
      } else {
        krHtml += '<div class="muted">最近笔端点间未检测到 RSI 背离。</div>';
      }
    } else krHtml = '<div class="muted">数据不足。</div>';
    $('colKR').innerHTML = '<h3>KDJ · RSI</h3>' + krHtml;

    /* 背驰 */
    let bcHtml = '';
    const bcs = ch.beichi.slice(-2).reverse();
    if (!bcs.length) bcHtml = '<div class="muted">当前范围内未检测到有效背驰(面积比 ≥ 0.7)。</div>';
    bcs.forEach((b) => {
      const buy = b.dir === 'down';
      const scoreColor = b.score >= 85 ? '#2ebd85' : b.score >= 65 ? '#4c8dff' : b.score >= 40 ? '#d29922' : '#6e7681';
      const lvTxt = b.score >= 85 ? '强背驰' : b.score >= 65 ? '有效背驰' : b.score >= 40 ? '弱背驰' : '强度不足';
      const lockTag = b.locked === false
        ? '<span class="tag" style="background:#d29922">未锁定·或移动</span>'
        : '<span class="tag" style="background:#2a2e35;color:#8b949e">已锁定</span>';
      bcHtml += `<div class="card">
        <div class="kv"><span class="${buy ? 'up' : 'down'}" style="font-weight:600">${b.kind === 'trend' ? '趋势' : '盘整'}${buy ? '底' : '顶'}背驰 ${lockTag}</span>
        <span class="muted">${fmtTs(b.ts)}</span></div>
        ${kv('价格', fmtP(b.price))}
        ${kv('MACD 面积比', `<b>${b.area_ratio}</b> <span class="muted">(&lt;0.7 有效)</span>`)}
        ${kv('评分', `<b style="color:${scoreColor}">${b.score}</b> · ${lvTxt} <span class="muted">(区间套未验证)</span>`)}
        <div class="scorebar"><div style="width:${Math.min(b.score, 100)}%;background:${scoreColor}"></div></div>
      </div>`;
    });
    /* 形态卡片(双顶/双底 + 收敛三角形) */
    (state.data.patterns || []).slice(-3).reverse().forEach((p) => {
      if (p.type === 'triangle') {
        const c = p.break_dir === 'up' ? '#2ebd85' : p.break_dir === 'down' ? '#f6465d' : '#8250df';
        bcHtml += `<div class="card">
          <div class="kv"><span style="font-weight:600;color:${c}">收敛三角形 · ${p.status}</span></div>
          ${kv('上轨(当前)', fmtP(p.upper_now))}
          ${kv('下轨(当前)', fmtP(p.lower_now))}
          <div class="muted" style="margin-top:4px">${p.note}</div>
        </div>`;
        return;
      }
      const isTop = p.type === 'double_top';
      const c = isTop ? '#f6465d' : '#2ebd85';
      const checkRows = p.checks.map((k) =>
        `<div class="kv"><span class="k">${k.ok ? '✓' : '✗'} ${k.name}</span>` +
        `<span class="${k.ok ? 'up' : 'muted'}">${k.val}</span></div>`).join('');
      bcHtml += `<div class="card">
        <div class="kv"><span style="font-weight:600;color:${c}">${isTop ? '双顶 M' : '双底 W'} · ${p.status}</span>
        <span class="muted">${fmtTs(p.p2_ts)}</span></div>
        ${kv('两' + (isTop ? '顶' : '底'), `${fmtP(p.p1_price)} / ${fmtP(p.p2_price)}`)}
        ${kv('颈线 / 量度目标', `${fmtP(p.neck)} / ${fmtP(p.target)}`)}
        ${kv('七项检验', `<b>${p.passed}/${p.total}</b> 通过`)}
        ${checkRows}
        <div class="muted" style="margin-top:4px">${p.chan_note}</div>
      </div>`;
    });
    $('colBc').innerHTML = '<h3>背驰 · 形态</h3>' + bcHtml;

    /* 买卖点 */
    let rows = '';
    ch.bsp.slice(-14).reverse().forEach((p) => {
      const buy = p.type[0] === 'B';
      const unlocked = p.locked === false;
      const sc = p.score;
      const scColor = sc >= 75 ? '#2ebd85' : sc >= 60 ? '#4c8dff' : sc >= 45 ? '#d29922' : '#6e7681';
      rows += `<tr class="row" data-ts="${p.ts}">
        <td><span class="tag" style="background:${buy ? '#1a7f37' : '#cf222e'};${unlocked ? 'opacity:.55' : ''}">${p.type}${unlocked ? '?' : ''}</span></td>
        <td>${fmtP(p.price)}</td>
        <td>${sc != null ? `<b style="color:${scColor}">${sc}</b> <span class="muted">${p.grade || ''}</span>` : '-'}</td>
        <td>${unlocked ? '<span class="tag" style="background:#d29922">未锁定</span>' : '<span class="muted">已锁定</span>'}</td>
        <td class="muted">${fmtTs(p.ts)}</td>
        <td class="muted" style="font-size:11px">${p.note}</td></tr>`;
    });
    $('colBsp').innerHTML = '<h3>买卖点(点击跳转)</h3>' +
      (rows ? `<table><tr><th>类型</th><th>价格</th><th>评分</th><th>状态</th><th>时间</th><th>说明</th></tr>${rows}</table>`
            : '<div class="muted">当前范围内未检测到买卖点。</div>');
    document.querySelectorAll('#colBsp tr.row').forEach((tr) => {
      tr.onclick = () => chart.scrollToTimestamp(Number(tr.dataset.ts), 300);
    });
  }

  function kv(k, v) {
    return `<div class="kv"><span class="k">${k}</span><span>${v}</span></div>`;
  }

  /* ---------------- 超短线模式(开关式) ---------------- */
  $('scalpBtn').onclick = async () => {
    state.scalpOn = !state.scalpOn;
    $('scalpBtn').classList.toggle('active', state.scalpOn);
    if (state.scalpOn) {
      // 等待正在进行的加载结束,避免竞态
      while (state.loading) await new Promise((r) => setTimeout(r, 150));
      // 超短线操作级别是 5M,自动切过去
      if (state.bar !== '5m' && state.bar !== '1m') {
        state.bar = '5m';
        syncActive();
        await loadData(false);
      }
      fetchScalp(true);
    } else {
      state.scalp = null;
      chart.removeOverlay({ groupId: 'scalp' });
      renderDecision();
    }
  };
  $('modalClose').onclick = () => { $('modalMask').style.display = 'none'; };
  $('modalMask').onclick = (e) => { if (e.target === $('modalMask')) $('modalMask').style.display = 'none'; };

  async function fetchScalp(force) {
    state.scalp = { loading: true };
    renderDecision();
    try {
      const r = await fetch(`/api/scalp?inst=${encodeURIComponent(state.inst)}&force=${force ? 1 : 0}`);
      const j = await r.json();
      if (j.code !== 0) throw new Error(j.msg || '未知错误');
      state.scalp = j.scalp;
    } catch (e) {
      state.scalp = { error: e.message };
    }
    renderDecision();
    drawScalpLines();
  }

  function drawScalpLines() {
    chart.removeOverlay({ groupId: 'scalp' });
    const s = state.scalp;
    if (!state.scalpOn || !s || s.error || s.loading || s.action === 'wait' || !state.data) return;
    const cs = state.data.candles;
    const ts0 = cs[Math.max(0, cs.length - 100)].ts;
    const line = (price, text, color) => {
      if (price == null) return;
      chart.createOverlay({
        name: 'priceLine', groupId: 'scalp', lock: true,
        points: [{ timestamp: ts0, value: price }],
        styles: { line: { color }, text: { backgroundColor: color, color: '#fff' } },
      });
      chart.createOverlay({
        name: 'chanMark', groupId: 'scalp', lock: true,
        points: [{ timestamp: ts0, value: price }],
        extendData: { text, pos: 'above', bg: color, textColor: '#fff' },
      });
    };
    line(s.entry, '入场', '#1f6feb');
    line(s.stop, '止损', '#f6465d');
    (s.targets || []).forEach((t, i) => line(t[1], `止盈${i + 1}`, '#2ebd85'));
  }

  function openCalc() {
    const s = state.scalp || {};
    if (s.action && s.action !== 'wait') $('cSide').value = s.action;
    if (s.entry ?? s.live) $('cEntry').value = s.entry ?? s.live;
    if (s.stop) $('cStop').value = s.stop;
    $('modalMask').style.display = 'flex';
    runCalc();
  }

  function runCalc() {
    const side = $('cSide').value;
    const eq = +$('cEq').value, riskPct = +$('cRisk').value, lev = +$('cLev').value;
    const entry = +$('cEntry').value, stop = +$('cStop').value;
    const out = $('calcOut');
    if (!(eq > 0 && riskPct > 0 && lev > 0 && entry > 0 && stop > 0)) {
      out.innerHTML = '<div class="muted">填写完整参数后自动计算。</div>';
      return;
    }
    const D = Math.abs(entry - stop);
    if (D === 0) { out.innerHTML = '<div class="warn">入场价与止损价不能相同。</div>'; return; }
    const R = eq * riskPct / 100;          // 单笔最大亏损
    const Q = R / D;                        // 数量(币)
    const notional = Q * entry;             // 名义价值
    const M = notional / lev;               // 保证金
    const MMR = 0.005;                      // 维持保证金率(近似)
    const liq = side === 'long' ? entry * (1 - 1 / lev + MMR) : entry * (1 + 1 / lev - MMR);
    const liqDist = Math.abs(entry - liq);
    const stopOk = D < liqDist / 3;         // 手册硬约束:止损距离 < 强平距离÷3
    const f = (v, n = 4) => Number(v.toPrecision(6)).toLocaleString(undefined, { maximumFractionDigits: n });
    out.innerHTML =
      kv('最大亏损 R', `${f(R, 2)} USDT(权益的 ${riskPct}%)`) +
      kv('开仓数量 Q = R÷|入场-止损|', `${f(Q)} 币(名义 ${f(notional, 2)} USDT)`) +
      kv(`保证金 M(${lev}× 逐仓)`, `${f(M, 2)} USDT`) +
      kv('预估强平价', `${f(liq)}(距入场 ${f(liqDist / entry * 100, 2)}%)`) +
      kv('止损距离 vs 强平距离÷3',
        stopOk ? `<span class="up">✓ ${f(D / entry * 100, 2)}% < ${f(liqDist / 3 / entry * 100, 2)}%,符合手册硬约束</span>`
               : `<span class="down">✗ ${f(D / entry * 100, 2)}% ≥ ${f(liqDist / 3 / entry * 100, 2)}%,违反"止损<强平距离÷3",请降杠杆或收紧止损</span>`) +
      `<div class="muted" style="margin-top:4px">保证金不足名义价值时按数量 Q 下单即可,损失始终锁定为 R;强平价按 MMR≈0.5% 估算,以交易所实际为准。</div>`;
  }
  ['cSide', 'cEq', 'cRisk', 'cLev', 'cEntry', 'cStop'].forEach((id) => { $(id).oninput = runCalc; $(id).onchange = runCalc; });

  /* ---------------- 启动 ---------------- */
  window.addEventListener('resize', () => chart.resize());
  // 每分钟自动刷新(可用工具栏勾选框关闭)
  setInterval(() => {
    if ($('ckAuto').checked && !state.loading) loadData(true);
  }, 60 * 1000);
  renderPeriods();
  fetch('/api/instruments').then((r) => r.json()).then((j) => renderCoins(j.instruments))
    .catch(() => renderCoins(['BTC-USDT-SWAP', 'ETH-USDT-SWAP']));
  syncActive();
  loadData(false);
  if (location.hash === '#scalp') $('scalpBtn').onclick();
})();
